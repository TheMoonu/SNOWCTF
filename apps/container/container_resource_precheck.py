"""
容器创建资源预检管理器（Container Resource Pre-check Manager）

核心功能：
1. 全局并发限流 - 防止任务堆积
2. K8s引擎资源预检 - 节点选择 + 原子预占
3. Docker引擎资源预检 - 资源检查 + 令牌桶限流
4. 智能引擎选择 - K8s优先，Docker降级
5. 异常时自动释放预占资源 - 防止资源泄漏

设计目标：
- 防止K8s节点在高并发场景下崩溃
- 最大化资源利用率
- 提供友好的错误信息和重试建议


"""

import logging
from typing import Tuple, Optional, Dict, Any
from django.conf import settings
from django.core.cache import cache
from container.models import DockerEngine
from container.k8s_service import K8sService, K8sServiceException
from container.docker_service import DockerService, DockerServiceException
from container.resource_reservation import ResourceReservationManager
from container.models import ContainerEngineConfig

logger = logging.getLogger('apps.container')


class ContainerResourcePrecheck:
    """
    容器资源预检管理器
    
    功能说明：
    1. 在容器创建任务提交到Celery队列之前，进行资源预检
    2. 避免任务堆积在队列中，但集群资源已满无法调度的情况
    3. 通过多层防护机制，确保节点稳定性和资源利用率
    
    使用场景：
    - 比赛模块（competition）：比赛开始时的高并发容器创建
    - 练习模块（practice）：日常练习题目的容器创建
    - 其他需要动态容器的模块
    """
    
    def __init__(self, memory_limit: int, cpu_limit: float, challenge=None):
        """
        初始化资源预检管理器
        
        Args:
            memory_limit: 容器内存限制（MB）
            cpu_limit: 容器CPU限制（核心数）
            challenge: 题目对象（可选，用于日志记录）
        """
        
        self.config = ContainerEngineConfig.get_config()
        
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.challenge = challenge
        
        # 计算K8s的requests（与K8s调度器一致，从数据库配置读取）
        self.memory_requests = max(int(memory_limit * self.config.k8s_requests_ratio), 64)
        self.cpu_requests = max(cpu_limit * self.config.k8s_requests_ratio, 0.1)
        
        # 预检结果
        self.selected_engine = None          # 选中的引擎对象
        self.selected_node = None            # K8s选中的目标节点（仅K8s引擎）
        self.reserve_key = None              # 资源预占标识（仅Docker引擎）
        self.engine_type = None              # 引擎类型: 'KUBERNETES' or 'DOCKER'
        
        logger.debug(
            f"初始化资源预检: limits={memory_limit}MB/{cpu_limit}核, "
            f"requests={self.memory_requests}MB/{self.cpu_requests}核"
        )
    
    def check(self, user_id: int, preferred_engine_type: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        """
        执行资源预检（多层防护）
        
        Args:
            user_id: 用户ID（用于日志记录）
            preferred_engine_type: 优先使用的引擎类型 ('KUBERNETES' or 'DOCKER')
                - None: 智能选择（优先尝试K8s，失败后尝试Docker）
                - 'KUBERNETES': 仅尝试K8s引擎
                - 'DOCKER': 仅尝试Docker引擎
        
        Returns:
            tuple: (是否通过, 错误信息)
                - (True, None): 预检通过，可以提交任务
                - (False, error_msg): 预检失败，返回友好的错误信息
        
        执行流程：
            1. 第一层防护：全局并发限流（原子预占）
            2. 第二层防护：智能引擎选择 + 资源预检
                - K8s引擎：节点选择 + 原子预占
                - Docker引擎：令牌桶 + 资源检查
            3. 失败时自动释放并发槽位
        """
        concurrency_acquired = False
        
        try:
            # ===== 第一层防护：全局并发限流（原子操作）=====
            if not self._check_global_concurrency():
                return False, "系统繁忙，请稍后再试（当前容器创建任务过多）"
            
            concurrency_acquired = True  # 标记已获取并发槽位
            
            # ===== 第二层防护：智能引擎选择 =====
            # 获取所有可用引擎（K8s和Docker都是平等的选择）
            all_engines = self._get_available_engines()
            
            if not all_engines:
                return False, "没有可用的容器引擎"
            
            # 根据preferred_engine_type过滤引擎
            if preferred_engine_type:
                all_engines = [e for e in all_engines if e.engine_type == preferred_engine_type]
                if not all_engines:
                    return False, f"没有可用的{preferred_engine_type}引擎"
            
            # 按引擎类型分组（但不设置优先级）
            k8s_engines = [e for e in all_engines if e.engine_type == 'KUBERNETES']
            docker_engines = [e for e in all_engines if e.engine_type == 'DOCKER']
            
            errors = {}
            
            # 尝试K8s引擎
            if k8s_engines:
                success, error_msg = self._check_k8s_engines(user_id, k8s_engines)
                if success:
                    concurrency_acquired = False  # 成功，不需要释放
                    return True, None
                errors['KUBERNETES'] = error_msg
            
            # 尝试Docker引擎
            if docker_engines:
                success, error_msg = self._check_docker_engines(user_id, docker_engines)
                if success:
                    concurrency_acquired = False  # 成功，不需要释放
                    return True, None
                errors['DOCKER'] = error_msg
            
            # 所有引擎都失败，返回综合错误信息
            if errors:
                error_parts = [f"{engine_type}: {msg}" for engine_type, msg in errors.items()]
                final_error = "所有容器引擎资源不足或负载过高，请稍后再试"
            else:
                final_error = "没有可用的容器引擎"
            
            return False, final_error
            
        except Exception as e:
            logger.error(f"资源预检异常: user={user_id}, error={e}", exc_info=True)
            return False, f"资源预检失败: {str(e)}"
        
        finally:
            # 如果预检失败且已获取并发槽位，需要释放
            if concurrency_acquired:
                try:
                    redis_client = cache.client.get_client()
                    redis_client.decr('active_container_creates')
                    logger.info("[并发限流] 预检失败，释放并发槽位")
                except Exception as e:
                    logger.error(f"[并发限流] 释放槽位失败: {e}")
    
    def _get_available_engines(self):
        """
        获取所有可用的容器引擎（K8s和Docker平等对待）
        
        Returns:
            QuerySet: 所有健康且激活的容器引擎
        """
        return DockerEngine.objects.filter(
            is_active=True,
            health_status__in=['HEALTHY', 'WARNING', 'UNKNOWN']
        ).order_by('id')  # 按ID排序，确保稳定的选择顺序
    
    def _check_global_concurrency(self) -> bool:
        """
        第一层防护：全局并发限流（原子操作）
        
        说明：
        - 使用Redis原子INCR先预占一个并发槽位
        - 如果超限则立即回滚并拒绝
        - 防止竞态条件：100个请求同时到达也只有20个能通过
        - 计数器带超时（由Celery任务管理），防止泄漏
        
        Returns:
            bool: True表示通过并已预占，False表示拒绝
        """
        import time
        import random
        
        MAX_CONCURRENT_CREATES = self.config.max_concurrent_creates
        
        try:
            # 使用Redis原子操作：先增加，再检查
            redis_client = cache.client.get_client()
            key = 'active_container_creates'

            # 验证连接可用（避免 IGNORE_EXCEPTIONS 吞掉连接错误后拿到失效客户端）
            redis_client.ping()

            # 原子增加计数器
            new_count = redis_client.incr(key)
            
            # 设置过期时间（防止计数器泄漏）
            if new_count == 1:
                redis_client.expire(key, 300)  # 5分钟超时
            
            # 检查是否超限
            if new_count > MAX_CONCURRENT_CREATES:
                # 超限：立即回滚并拒绝
                redis_client.decr(key)
                logger.warning(
                    f"[并发限流] 拒绝: 当前并发={new_count-1}/{MAX_CONCURRENT_CREATES} (已回滚)"
                )
                
                # 自适应延迟：告诉客户端稍后重试
                delay = random.uniform(1.0, 3.0)
                time.sleep(delay)
                return False
            
            # 通过：已原子预占一个槽位
            logger.info(
                f"[并发限流] 通过: 当前并发={new_count}/{MAX_CONCURRENT_CREATES} (已预占)"
            )
            
            # 自适应延迟：当并发接近上限时，增加随机延迟
            load_ratio = new_count / MAX_CONCURRENT_CREATES
            if load_ratio > 0.7:  # 负载超过70%
                delay = random.uniform(0.05, 0.2) * load_ratio
                time.sleep(delay)
                logger.debug(f"[并发限流] 高负载延迟: {delay:.3f}秒 (负载={load_ratio:.1%})")
            
            return True
            
        except Exception as e:
            logger.error(f"[并发限流] 检查失败: {e}")
            # 出错时保守放行（降级策略：Redis 不可用时不阻断业务）
            return True
    
    def _check_k8s_engines(self, user_id: int, k8s_engines) -> Tuple[bool, Optional[str]]:
        """
        K8s引擎资源预检
        
        核心逻辑：
        1. 遍历指定的K8s引擎列表
        2. 调用K8sResourceMonitor选择安全节点（带原子预占）
        3. 第一个通过的引擎即选中，记录target_node
        4. 失败时自动释放已预占的资源
        
        Args:
            user_id: 用户ID
            k8s_engines: K8s引擎列表（QuerySet或list）
        
        Returns:
            tuple: (是否通过, 错误信息)
        """
        if not k8s_engines:
            return False, "没有可用的K8s引擎"
        
        last_error = None
        checked_engines = []
        
        for engine in k8s_engines:
            target_node = None  # 每次循环重置
            k8s_checker = None
            
            try:
                k8s_checker = K8sService(engine)
                
                # 核心：使用资源监控器选择安全节点（原子预占）
                target_node = k8s_checker.resource_monitor.select_safe_node(
                    required_memory_mb=self.memory_requests,
                    required_cpu_cores=self.cpu_requests
                )
                
                if not target_node:
                    raise K8sServiceException(
                        f"引擎{engine.name}所有节点负载都已接近上限"
                    )
                
                # 预检通过，记录结果
                self.selected_engine = engine
                self.selected_node = target_node
                self.engine_type = 'KUBERNETES'
                
                logger.info(
                    f"K8s资源预检通过: user={user_id}, engine={engine.name}, "
                    f"node={target_node}, "
                    f"limits={self.memory_limit}MB/{self.cpu_limit}核, "
                    f"requests={self.memory_requests}MB/{self.cpu_requests}核"
                )
                
                return True, None
                
            except K8sServiceException as e:
                # 失败时释放节点预占
                if target_node and k8s_checker:
                    try:
                        k8s_checker.resource_monitor.release_node_reservation(
                            target_node, self.memory_requests, self.cpu_requests
                        )
                        logger.debug(f"已释放节点 {target_node} 的预占资源（预检失败）")
                    except Exception as release_err:
                        logger.error(f"释放K8s节点预占失败: {release_err}")
                
                last_error = str(e)
                checked_engines.append(engine.name)
                logger.debug(f"K8s引擎 {engine.name} 预检失败: {e}")
                continue  # 尝试下一个引擎
            
            except Exception as e:
                last_error = f"内部错误: {str(e)}"
                checked_engines.append(engine.name)
                logger.error(f"K8s引擎 {engine.name} 预检异常: {e}", exc_info=True)
                continue
        
        # 所有K8s引擎都失败
        engines_str = ", ".join(checked_engines)
        return False, f"引擎资源不足"
    
    def _check_docker_engines(self, user_id: int, docker_engines) -> Tuple[bool, Optional[str]]:
        """
        Docker引擎资源预检
        
        核心逻辑：
        1. 遍历指定的Docker引擎列表
        2. 获取实际资源使用情况
        3. 使用ResourceReservationManager进行令牌桶限流
        4. 第一个通过的引擎即选中
        5. 失败时自动释放已预占的资源
        
        Args:
            user_id: 用户ID
            docker_engines: Docker引擎列表（QuerySet或list）
        
        Returns:
            tuple: (是否通过, 错误信息)
        """
        if not docker_engines:
            return False, "没有可用的Docker引擎"
        
        last_error = None
        checked_engines = []
        
        for engine in docker_engines:
            reserve_key = None
            docker_checker = None
            
            try:
                # 初始化Docker连接
                if engine.host_type == 'LOCAL':
                    docker_url = "unix:///var/run/docker.sock"
                else:
                    docker_url = f"tcp://{engine.host}:{engine.port}"
                
                tls_config = None
                if engine.tls_enabled:
                    tls_config = engine.get_tls_config()
                
                docker_checker = DockerService(
                    url=docker_url,
                    tls_config=tls_config,
                    engine=engine
                )
                
       
                total_memory, total_cpu, used_memory, used_cpu = \
                    docker_checker._get_docker_resources()
      
                MAX_USAGE_THRESHOLD = self.config.docker_max_usage_threshold
                max_usable_memory = total_memory * MAX_USAGE_THRESHOLD
                max_usable_cpu = total_cpu * MAX_USAGE_THRESHOLD
                max_reserve_memory = max_usable_memory - used_memory
                max_reserve_cpu = max_usable_cpu - used_cpu
                

                
                current_memory_usage_percent = (used_memory / total_memory) * 100
                current_cpu_usage_percent = (used_cpu / total_cpu) * 100 if total_cpu > 0 else 0
                threshold_percent = MAX_USAGE_THRESHOLD * 100
                
                if max_reserve_memory <= 0:
                    raise DockerServiceException(
                        f"Docker引擎 {engine.name} 内存使用率已达 {current_memory_usage_percent:.1f}%，"
                        f"超过阈值 {threshold_percent:.0f}%，剩余可用内存不足"
                    )
                
                if max_reserve_cpu <= 0:
                    raise DockerServiceException(
                        f"Docker引擎 {engine.name} CPU使用率已达 {current_cpu_usage_percent:.1f}%，"
                        f"超过阈值 {threshold_percent:.0f}%，剩余可用CPU不足"
                    )
                
                #  Docker使用limits预占（Docker不区分requests/limits）
                success, reserve_key, error_msg = ResourceReservationManager.try_reserve(
                    memory_mb=self.memory_limit,
                    cpu_cores=self.cpu_limit,
                    max_memory_mb=max_reserve_memory,
                    max_cpu_cores=max_reserve_cpu
                )
                
                if not success:
                    raise DockerServiceException(f"资源预占失败: {error_msg}")
                
                # 预检通过，记录结果
                self.selected_engine = engine
                self.reserve_key = reserve_key
                self.engine_type = 'DOCKER'
                
                logger.info(
                    f" Docker资源预检通过: user={user_id}, engine={engine.name}, "
                    f"limits={self.memory_limit}MB/{self.cpu_limit}核, "
                    f"reserve_key={reserve_key}"
                )
                
                return True, None
                
            except DockerServiceException as e:
                # 失败时释放资源预占
                if reserve_key:
                    try:
                        ResourceReservationManager.release(reserve_key)
                    except Exception as release_err:
                        logger.error(f"释放Docker资源预占失败: {release_err}")
                
                last_error = str(e)
                checked_engines.append(engine.name)
                logger.debug(f"Docker引擎 {engine.name} 预检失败: {e}")
                continue  # 尝试下一个引擎
            
            except Exception as e:
                # 释放可能已预占的资源
                if reserve_key:
                    try:
                        ResourceReservationManager.release(reserve_key)
                    except:
                        pass
                
                last_error = f"内部错误: {str(e)}"
                checked_engines.append(engine.name)
                logger.error(f"Docker引擎 {engine.name} 预检异常: {e}", exc_info=True)
                continue
        
        # 所有Docker引擎都失败
        engines_str = ", ".join(checked_engines)
        return False, f"Docker引擎资源不足（已检查: {engines_str}）: {last_error}"
    
    def get_result_for_celery(self) -> Dict[str, Any]:
        """
        获取预检结果，用于传递给Celery任务
        
        Returns:
            dict: 包含以下字段的字典
                - engine_type: 'KUBERNETES' or 'DOCKER'
                - engine_id: 引擎ID
                - target_node: K8s目标节点（仅K8s引擎）
                - memory_requests: K8s资源请求量（仅K8s引擎）
                - cpu_requests: K8s资源请求量（仅K8s引擎）
                - reserve_key: 资源预占标识（仅Docker引擎）
        
        注意：
            此方法必须在check()返回True后调用
        """
        if not self.selected_engine or not self.engine_type:
            raise ValueError("预检未通过或未执行，无法获取结果")
        
        result = {
            'engine_type': self.engine_type,
            'engine_id': self.selected_engine.id,
        }
        
        if self.engine_type == 'KUBERNETES':
            result.update({
                'target_node': self.selected_node,
                'memory_requests': self.memory_requests,
                'cpu_requests': self.cpu_requests,
            })
        elif self.engine_type == 'DOCKER':
            result.update({
                'reserve_key': self.reserve_key,
            })
        
        return result
    
    def cleanup_on_error(self):
        """
        清理已预占的资源（当任务提交失败时调用）
        
        使用场景：
        - 资源预检通过后，但在提交Celery任务时发生异常
        - 确保预占的资源被正确释放，防止资源泄漏
        """
        if not self.selected_engine:
            return
        
        try:
            if self.engine_type == 'KUBERNETES' and self.selected_node:
                # 释放K8s节点预占
                k8s_service = K8sService(self.selected_engine)
                k8s_service.resource_monitor.release_node_reservation(
                    self.selected_node,
                    self.memory_requests,
                    self.cpu_requests
                )
                logger.warning(
                    f"已释放K8s节点预占: node={self.selected_node}, "
                    f"memory={self.memory_requests}MB, cpu={self.cpu_requests}核 "
                    f"（任务提交失败）"
                )
                
            elif self.engine_type == 'DOCKER' and self.reserve_key:
                # 释放Docker资源预占
                ResourceReservationManager.release(self.reserve_key)
                logger.warning(
                    f"已释放Docker资源预占: reserve_key={self.reserve_key} "
                    f"（任务提交失败）"
                )
                
        except Exception as e:
            logger.error(f"清理预占资源失败: {e}", exc_info=True)


# ==================== 辅助函数 ====================

def get_http_status_for_error(error_msg: str) -> int:
    """
    根据错误信息返回合适的HTTP状态码
    
    Args:
        error_msg: 错误信息
    
    Returns:
        int: HTTP状态码
            - 429: 系统繁忙、并发超限
            - 503: 资源不足、服务不可用
            - 500: 其他错误
    """
    if "系统繁忙，请稍后再试" in error_msg or "并发" in error_msg:
        return 429  # Too Many Requests
    elif "资源不足，请稍后再试" in error_msg or "负载过高" in error_msg or "负载都已接近上限" in error_msg:
        return 503  # Service Unavailable
    else:
        return 500  # Internal Server Error


from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
import json
import time
import uuid
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.core.cache import cache
from django.db.models import Count
from docker.errors import APIError, DockerException
from requests.exceptions import ConnectionError, ReadTimeout
import docker
from practice.models import PC_Challenge, CTFUser
from container.models import UserContainer,DockerEngine
from .redis_cache import UserContainerCache
# 使用统一的容器服务（支持 Docker 和 K8s）
from container.container_service_factory import ContainerServiceFactory
from container.docker_service import DockerServiceException, ContainerServiceException
from .flag_generator import get_or_generate_flag, verify_flag as verify_flag_func
from easytask.tasks import cleanup_container
from celery.exceptions import MaxRetriesExceededError
import requests
from requests.exceptions import RequestException
from urllib.parse import urlparse
from django.conf import settings
import logging
from celery import current_app

logger = logging.getLogger('apps.practice')

class ContainerManager:
    # 常量定义
    CONTAINER_CREATION_TIMEOUT = 300  # 5分钟容器创建超时
    RATE_LIMIT_SECONDS = 60  # 1分钟速率限制
    
    def __init__(self, user, challenge_uuid, request, target_node=None):
        self.user = user
        self.challenge_uuid = challenge_uuid
        self.request = request
        self.challenge = get_object_or_404(PC_Challenge, uuid=challenge_uuid)
        self.ctf_user = get_object_or_404(CTFUser, user=user)
        self.cache_key = f"{user.id}_{challenge_uuid}"
        self.rate_limit_key = f"container_rate_limit:{user.id}:{challenge_uuid}"
        self.docker_engine = None
        self.docker_url = None
        self.tls_config = None
        self.target_node = target_node  #  目标节点（由views.py预检时选择）

    def check_existing_container(self) -> Optional[Dict]:
        """检查已存在的容器"""
        cached_container = UserContainerCache.get(self.user.id, self.challenge_uuid)
        if not cached_container:
            return None

        if cached_container['challenge_uuid'] != str(self.challenge.uuid):
            raise ValueError("一个用户只能启动一个容器")
            
        if datetime.fromisoformat(cached_container['expires_at']) < timezone.now():
            raise ValueError("容器已过期，请重新启动")

        

    def check_prerequisites(self):
        """检查创建容器的前置条件"""
        if not self.challenge.is_disable:
            raise ValueError("该题目当前未启用，暂时无法访问")
        
        if self.challenge.is_member and not self.user.is_valid_member and not self.user.is_superuser:
            raise ValueError("您不是高级会员，无法访问此题目")

        # 权限检查：未激活的题目只允许作者和管理员访问
        if not self.challenge.is_active and self.user != self.challenge.author and not (self.user.is_superuser or self.user.is_staff):
            raise ValueError("该题目作者未公开此题目")

        if self.challenge.coins > self.challenge.get_coins(self.user):
            raise ValueError("您的金币不足，无法打开题目环境")

        # 只查询练习中运行的容器（不包括比赛容器）
        active_container = UserContainer.objects.filter(
            user=self.user,
            container_type='PRACTICE',  # 只检查练习容器
            status='RUNNING',
            expires_at__gt=timezone.now()
        ).first()
        
        if active_container:
            raise ValueError(f"您在练习中已有一个运行中的容器，请先关闭后再重新启动(题目标题: {active_container.challenge_title})")

        # 检查速率限制（只限制成功创建，失败可以立即重试）
        

    def _get_docker_engine(self, excluded_engines=None):
        """
        获取负载最小且健康的 引擎（智能选择 + 排除机制）
        
        Args:
            excluded_engines: 需要排除的引擎ID列表（用于降级重试）
        
        Returns:
            DockerEngine: 选中的引擎
        """
        if excluded_engines is None:
            excluded_engines = []
        
        # 获取所有激活的引擎（排除已失败的引擎）
        engine_query = DockerEngine.objects.filter(
            is_active=True
        ).exclude(id__in=excluded_engines)
        
        # 多场景题目必须使用 Kubernetes 引擎
        if hasattr(self.challenge, 'network_topology_config') and self.challenge.network_topology_config:
            engine_query = engine_query.filter(engine_type='KUBERNETES')
            logger.info(f"多场景题目 {self.challenge_uuid}，限制使用 Kubernetes 引擎")
        
        active_engines = list(engine_query)
        
        if not active_engines:
            if hasattr(self.challenge, 'network_topology_config') and self.challenge.network_topology_config:
                raise ValueError("多场景题目需要 Kubernetes 引擎，但当前没有可用的 Kubernetes 引擎")
            elif excluded_engines:
                raise ValueError(f"所有可用引擎均已尝试失败（已排除 {len(excluded_engines)} 个引擎）")
            else:
                raise ValueError("没有可用的Docker引擎")
        
        # 计算每个引擎的当前负载
        # 统计所有容器负载（包括练习和比赛）
        engine_loads = UserContainer.objects.filter(
            status='RUNNING',
            expires_at__gt=timezone.now()
        ).values('docker_engine').annotate(
            container_count=Count('id')
        )
        
        engine_loads_dict = {
            load['docker_engine']: load['container_count'] 
            for load in engine_loads
        }
        
        # 定义健康状态优先级（数字越小优先级越高）
        health_priority = {
            'HEALTHY': 1,    # 健康 - 最优先
            'WARNING': 2,    # 警告 - 次优先
            'UNKNOWN': 3,    # 未知 - 可用但不确定
            'CRITICAL': 100, # 严重 - 不推荐
            'OFFLINE': 999   # 离线 - 禁用
        }
        
        # 过滤掉离线和严重状态的引擎
        available_engines = [
            engine for engine in active_engines 
            if engine.health_status not in ('OFFLINE', 'CRITICAL')
        ]
        
        # 如果过滤后没有可用引擎，降级使用所有激活的引擎
        if not available_engines:
            logger.warning("所有引擎都处于不健康状态，降级使用所有激活引擎")
            available_engines = active_engines
        
        # 智能选择引擎：优先考虑健康状态，其次考虑负载
        docker_engine = min(
            available_engines,
            key=lambda engine: (
                health_priority.get(engine.health_status, 50),  # 第一优先级：健康状态
                engine_loads_dict.get(engine.id, 0)             # 第二优先级：负载
            )
        )

        if docker_engine.tls_enabled:
            tls_config = docker.tls.TLSConfig(
                client_cert=(
                    docker_engine.client_cert_path, 
                    docker_engine.client_key_path
                ),
                ca_cert=docker_engine.ca_cert_path,
                verify=True
            )
        else:
            tls_config = None

        if docker_engine.host_type == 'LOCAL':
            docker_url = "unix:///var/run/docker.sock"
        else:
            docker_url = f"tcp://{docker_engine.host}:{docker_engine.port}"

        self.docker_engine = docker_engine
        self.docker_url = docker_url
        self.tls_config = tls_config

        return docker_engine

    def _get_docker_service(self, docker_engine):
        """创建容器服务实例（支持 Docker 和 K8s）"""
        from container.container_service_factory import ContainerServiceFactory
        return ContainerServiceFactory.create_service(docker_engine)

    def _create_user_container(self, container, docker_engine, expires_at, containers_info=None):
        """
        创建用户容器记录（支持拓扑场景）
        
        Args:
            container: 单个容器信息
            docker_engine: Docker引擎对象
            expires_at: 过期时间
            containers_info: 所有容器信息列表（用于拓扑场景）
        """
        # 检查是否为拓扑场景
        topology_config = None
        topology_data = None
        
        if hasattr(self.challenge, 'network_topology_config') and self.challenge.network_topology_config:
            topology_config = self.challenge.network_topology_config
            
            # 保存所有容器信息（供清理时使用）
            if containers_info:
                topology_data = {
                    'containers': [
                        {
                            'id': c.get('id'),
                            'node_id': c.get('node_id'),
                            'node_label': c.get('node_label'),
                            'is_entry_point': c.get('is_entry_point', False),
                            'is_target': c.get('is_target', False),
                            'protocol': c.get('protocol', 'http'),
                            'service_name': c.get('service_name')
                        }
                        for c in containers_info
                    ],
                    'topology_config_id': topology_config.id,
                    'created_at': timezone.now().isoformat()
                }
            
            logger.debug(
                f"拓扑容器记录: topology_config={topology_config.id}, "
                f"container_count={len(containers_info) if containers_info else 1}"
            )
        
        return UserContainer.objects.create(
            user=self.user,
            challenge_title=self.challenge.title,
            challenge_uuid=self.challenge_uuid,
            docker_engine=docker_engine,
            container_id=container['id'],
            ip_address=docker_engine.host,
            domain=docker_engine.domain,
            port=json.dumps(container['ports']),
            expires_at=expires_at,
            container_type='PRACTICE',  # 练习容器
            competition=None,  # 练习题不关联比赛
            #  拓扑场景支持
            topology_config=topology_config,
            topology_data=topology_data
        )


    def _cleanup_on_error(self):
        """错误发生时清理资源（包括所有相关缓存和数据库记录）"""
        try:
            # 1. 清理容器缓存（自动清理 flag）
            UserContainerCache.delete(self.user.id, self.challenge_uuid)
            cache.delete(self.cache_key)
            
            # 2. 清理任务相关缓存
            pending_task_key = f"container_task_user:{self.user.id}:{self.challenge_uuid}"
            old_task_id = cache.get(pending_task_key)
            if old_task_id:
                cache.delete(f"container_task:{old_task_id}")
            cache.delete(pending_task_key)
            
            # 3. 清理容器创建锁（防止锁残留）
            container_lock_key = f"container_lock:{self.user.id}:{self.challenge_uuid}"
            if cache.get(container_lock_key):
                cache.delete(container_lock_key)
                logger.debug(f"清理容器创建锁: {container_lock_key}")
            
            # 4. 清理速率限制缓存
            rate_limit_key = f"container_rate_limit:{self.user.id}:{self.challenge_uuid}"
            if cache.get(rate_limit_key):
                cache.delete(rate_limit_key)
            
            # 5. 错误时清理练习中运行的容器（软删除）
            user_containers = UserContainer.objects.filter(
                user=self.user, 
                container_type='PRACTICE',  # 只清理练习容器
                status='RUNNING'
            )
            
            for container in user_containers:
                container_service = None
                try:
                    # 尝试停止容器（支持 Docker 和 K8s）
                    if container.container_id:
                        from container.container_service_factory import ContainerServiceFactory
                        container_service = ContainerServiceFactory.create_service(container.docker_engine)
                        container_service.stop_and_remove_container(container.container_id)
                        logger.info(f"清理失败容器: {container.container_id[:12]}")
                except Exception as e:
                    logger.error(f"清理容器 {container.container_id[:12]} 失败: {str(e)}")
                finally:
                    # 确保关闭容器服务连接
                    if container_service and hasattr(container_service, 'close'):
                        try:
                            container_service.close()
                        except Exception as close_err:
                            logger.debug(f"关闭容器服务连接失败: {close_err}")
                    
                    # 标记为创建失败
                    container.status = 'FAILED'
                    container.deleted_at = timezone.now()
                    container.deleted_by = 'ERROR'
                    container.save()
            
            logger.info(f"用户 {self.user.id} 的失败容器资源清理完成（包括所有缓存）")
            
        except Exception as e:
            logger.error(f"清理资源时发生异常: {str(e)}", exc_info=True)

    def create_container(self) -> Dict:
        """
        创建容器（支持智能降级重试）
        
        当遇到资源不足等错误时，自动尝试其他可用引擎
        """
        # 获取可用引擎数量（用于决定最大重试次数）
        total_engines = DockerEngine.objects.filter(is_active=True).count()
        max_retries = min(total_engines, 3)  # 最多尝试3个引擎
        
        excluded_engines = []  # 记录已失败的引擎ID
        last_error = None
        start_time = time.time()
        
        try:
            # 1. 检查前置条件（含安全检查和原子性容器数量检查）
            self.check_prerequisites()
            
            # 2. 设置创建锁（用户级别）- 速率限制只在成功后设置
            cache.set(self.cache_key, True, timeout=self.CONTAINER_CREATION_TIMEOUT)
            
            # 3. 智能引擎选择 + 降级重试
            for retry_count in range(max_retries):
                docker_service = None
                
                try:
                    # 3.1 获取容器引擎（负载均衡 + 排除已失败的引擎）
                    docker_engine = self._get_docker_engine(excluded_engines=excluded_engines)
                    
                    if excluded_engines:
                        logger.info(
                            f"智能降级: 尝试使用备用引擎 {docker_engine.name} ({docker_engine.get_engine_type_display()}) "
                            f"创建容器 (尝试 {retry_count + 1}/{max_retries}，已排除引擎: {len(excluded_engines)}个)"
                        )
                    else:
                        logger.info(
                            f"尝试使用引擎 {docker_engine.name} ({docker_engine.get_engine_type_display()}) "
                            f"创建容器 (尝试 {retry_count + 1}/{max_retries})"
                        )
                    
                    # 3.2 创建容器服务
                    docker_service = self._get_docker_service(docker_engine)
                    
                    # 3.3 生成或获取 Flag
                    flag = get_or_generate_flag(self.challenge, self.user)
                    
                    # 3.4 获取资源限制（仅用于单镜像场景，拓扑场景由各节点自己的镜像配置决定）
                    docker_image = self.challenge.docker_image
                    
                    if docker_image:
                        # 单镜像场景：使用镜像配置的资源限制
                        memory_limit = docker_image.memory_limit or 512  # 默认512MB
                        cpu_limit = docker_image.cpu_limit or 1.0  # 默认1核
                    else:
                        # 拓扑场景或未配置镜像：使用默认值（拓扑场景会被忽略，各节点使用自己的配置）
                        memory_limit = 512
                        cpu_limit = 1.0
                        if hasattr(self.challenge, 'network_topology_config') and self.challenge.network_topology_config:
                            logger.debug(
                                f"拓扑场景: {self.challenge.network_topology_config.get_node_count()} 个节点，"
                                f"各节点使用自己的镜像资源配置"
                            )
                    
                    # 3.5 创建容器
                    if docker_engine.engine_type == 'KUBERNETES':
                        # K8s引擎：必须传递target_node（由views.py预检时选定）
                        if not self.target_node:
                            logger.error(
                                f"K8s引擎缺少target_node！engine={docker_engine.name}, "
                                f"user={self.user.id}, challenge={self.challenge_uuid}"
                            )
                            raise ContainerServiceException(
                                "容器创建失败：K8s引擎缺少目标节点信息（内部错误）"
                            )
                        
                        containers_info, web_container_info = docker_service.create_containers(
                            challenge=self.challenge,
                            user=self.user,
                            flag=flag,
                            memory_limit=memory_limit,
                            cpu_limit=cpu_limit,
                            target_node=self.target_node  # 传递预选节点
                        )
                    else:
                        # Docker引擎：不需要target_node
                        containers_info, web_container_info = docker_service.create_containers(
                            challenge=self.challenge,
                            user=self.user,
                            flag=flag,
                            memory_limit=memory_limit,
                            cpu_limit=cpu_limit
                        )
                    

                    
                    # 3.6 处理容器创建结果（在事务中完成，传递 flag 用于缓存）
                    result = self._handle_container_creation(
                        containers_info, 
                        web_container_info, 
                        docker_engine,
                        flag  # 传递 flag 以便存入容器缓存
                    )
                    
                    # 3.7 记录成功日志
                    elapsed_time = time.time() - start_time
                    if retry_count > 0:
                        logger.info(
                            f" 容器创建成功（智能降级生效）: 用户={self.user.id}, 题目={self.challenge_uuid}, "
                            f"最终引擎={docker_engine.name} ({docker_engine.get_engine_type_display()}), "
                            f"耗时={elapsed_time:.2f}s, 尝试次数={retry_count + 1}, "
                            f"已排除引擎={len(excluded_engines)}个"
                        )
                    else:
                        logger.info(
                            f" 容器创建成功: 用户={self.user.id}, 题目={self.challenge_uuid}, "
                            f"引擎={docker_engine.name} ({docker_engine.get_engine_type_display()}), "
                            f"耗时={elapsed_time:.2f}s"
                        )
                    
                    # 3.8 只在成功创建后设置速率限制（防止频繁成功创建）
                    cache.set(self.rate_limit_key, True, timeout=self.RATE_LIMIT_SECONDS)
                    
                    return result
                
                except (ContainerServiceException, ValueError) as e:
                    # 捕获容器服务异常和值错误（如引擎选择失败），判断是否需要重试其他引擎
                    error_msg = str(e)
                    last_error = e
                    
                    logger.error(
                        f"捕获到容器服务异常: 引擎={docker_engine.name} ({docker_engine.get_engine_type_display()}), "
                        f"错误类型={type(e).__name__}, 错误={error_msg}",
                        exc_info=True
                    )
                    
                    # 判断是否为 K8s 资源不足错误
                    is_resource_shortage = (
                        "资源不足" in error_msg or 
                        "无法调度" in error_msg or
                        "Insufficient" in error_msg or
                        "resource quota" in error_msg.lower()
                    )
                    
                    logger.info(
                        f" 资源不足判断: is_resource_shortage={is_resource_shortage}, "
                        f"retry_count={retry_count}, max_retries={max_retries}"
                    )
                    
                    if is_resource_shortage and retry_count < max_retries - 1:
                        # 资源不足且还有重试次数，尝试其他引擎
                        logger.warning(
                            f"引擎 {docker_engine.name} ({docker_engine.get_engine_type_display()}) "
                            f"资源不足，触发智能降级机制，尝试切换到其他引擎"
                        )
                        logger.warning(f"错误详情: {error_msg}")
                        excluded_engines.append(docker_engine.id)
                        logger.info(f" 已排除引擎ID={docker_engine.id}，当前已排除引擎列表: {excluded_engines}")
                        
                        # 临时降低该引擎的健康状态（不持久化到数据库）
                        # 让后续选择时优先考虑其他引擎
                        continue
                    else:
                        # 不是资源不足错误，或已无更多引擎可尝试
                        if excluded_engines:
                            logger.error(
                                f"所有可用引擎均创建失败。已尝试引擎: {excluded_engines}, "
                                f"最后错误: {error_msg}"
                            )
                            raise ValueError(
                                "所有容器引擎均不可用或资源不足，请稍后再试。"
                                f"最后错误: {error_msg}"
                            )
                        else:
                            raise ValueError(error_msg)
            
            # 所有重试都失败了
            if last_error:
                logger.error(
                    f"容器创建失败，已尝试 {max_retries} 个引擎。"
                    f"排除的引擎: {excluded_engines}, "
                    f"最后错误类型: {type(last_error).__name__}, "
                    f"最后错误: {str(last_error)}"
                )
                raise ValueError(f"所有容器引擎均不可用或资源不足，请稍后再试。最后错误: {str(last_error)}")
            else:
                logger.error(
                    f"容器创建失败，无可用引擎。"
                    f"max_retries={max_retries}, "
                    f"excluded_engines={excluded_engines}, "
                    f"last_error=None (这不应该发生！)"
                )
                raise ValueError("容器创建失败，无可用引擎")
        
        except ContainerServiceException as e:
            # 容器服务异常（Docker、K8s 等），直接传递友好错误信息
            logger.error(f"容器服务异常: {str(e)}")
            self._cleanup_on_error()
            raise ValueError(str(e))
        
        except APIError as e:
            logger.error(f"Docker API 错误: {str(e)}")
            self._cleanup_on_error()
            raise ValueError("容器服务暂时不可用，请稍后再试")
        
        except (ConnectionError, ReadTimeout) as e:
            logger.error(f"Docker 连接错误: {str(e)}")
            self._cleanup_on_error()
            raise ValueError("无法连接到容器服务，请联系管理员")
        
        except DockerException as e:
            logger.error(f"Docker 异常: {str(e)}")
            self._cleanup_on_error()
            raise ValueError("容器创建失败，请稍后再试")
        
        except ValueError as e:
            # ValueError 是预期的业务异常，直接抛出
            self._cleanup_on_error()
            raise
        
        except Exception as e:
            # 未预期的异常，记录并清理
            logger.error(f"创建容器时发生未预期异常: {str(e)}", exc_info=True)
            self._cleanup_on_error()
            raise ValueError("创建容器失败，请稍后再试")
            
    def check_container_url(self, url, max_retries=120, timeout=4):
        """
        检查容器URL是否可访问
        """
        for _ in range(max_retries):
            try:
                #print(url)
                response = requests.get(url, timeout=timeout)
                if response.status_code == 200:
                    return True
            except RequestException:
                pass
            time.sleep(1)
        return False

    def _schedule_cleanup(self, container_id, user_id, docker_engine_id, expires_at):
        """
        调度容器清理任务（分桶批量调度，优化性能）
        
        采用分桶策略：
        - 将过期时间向上取整到最近的5分钟
        - 同一时间桶的容器由一个批量任务处理
        - 大幅减少 Celery 任务数量（100个容器 → 最多12个任务/小时）
        
        Args:
            container_id: 容器ID
            user_id: 用户ID
            docker_engine_id: 引擎ID
            expires_at: 过期时间
        """
        try:
            from easytask.tasks import cleanup_expired_containers_bucket
            from datetime import timedelta
            
            #  优化：按5分钟分桶，减少任务数量
            # 例如：14:03 → 14:05, 14:07 → 14:10
            bucket_minutes = 5
            minutes_to_add = bucket_minutes - (expires_at.minute % bucket_minutes)
            bucket_time = (expires_at + timedelta(minutes=minutes_to_add)).replace(second=0, microsecond=0)
            
            # 使用时间戳作为桶的唯一标识
            bucket_key = f"cleanup_bucket:{bucket_time.timestamp()}"
            
            #  检查该时间桶是否已经调度过任务（避免重复）
            if not cache.get(bucket_key):
                # 首次调度该时间桶的任务
                task = cleanup_expired_containers_bucket.apply_async(
                    args=[bucket_time.isoformat()],
                    eta=bucket_time
                )
                
                # 标记该时间桶已调度（3小时过期）
                cache.set(bucket_key, task.id, timeout=3600 * 3)
                
                logger.info(
                    f" 已调度批量清理任务: "
                    f"bucket_time={bucket_time.strftime('%Y-%m-%d %H:%M')}, "
                    f"task_id={task.id}"
                )
            else:
                logger.debug(
                    f" 时间桶已存在清理任务: "
                    f"container={container_id[:12]}, "
                    f"bucket_time={bucket_time.strftime('%H:%M')}"
                )
            
        except Exception as e:
            logger.error(f"调度清理任务失败: {container_id[:12]}, 错误: {str(e)}")
            # 清理任务调度失败不影响容器创建，定时任务会兜底清理
    
    def _handle_container_creation(self, containers_info, web_container_info, docker_engine, flag=None) -> Dict:
        """
        处理容器创建结果（优化版：支持多入口节点 + 缓存 flag）
        
        Args:
            containers_info: 容器信息列表（包含所有节点）
            web_container_info: Web容器信息（第一个入口节点，兼容性保留）
            docker_engine: Docker引擎
            flag: 生成的 flag（可选）
        """
        from container.models import ContainerEngineConfig
        config = ContainerEngineConfig.get_config()
        expires_at = timezone.now() + timedelta(hours=config.container_expiry_hours)
        
        #  单镜像入口类型到协议的映射（统一大小写格式）
        ENTRANCE_TO_PROTOCOL_MAP = {
            'WEB': 'http',
            'HTTPS': 'https',
            'SSH': 'ssh',
            'RDP': 'rdp',
            'VNC': 'vnc',
            'NC': 'nc',
            'FTP': 'ftp',
            'MYSQL': 'mysql',
            'REDIS': 'redis',
            'MONGODB': 'mongodb',
            'POSTGRESQL': 'postgresql',
        }
        
        def _generate_url_by_protocol(protocol: str, host: str, port: int, domain: str = None, random_prefix: str = None) -> str:
            """
            根据协议类型生成统一格式的访问URL
            
            Args:
                protocol: 协议类型（http/https/ssh/rdp/vnc/nc/ftp/mysql/redis/mongodb/postgresql等）
                host: 主机IP或域名
                port: 端口号
                domain: 域名（可选，用于http/https）
                random_prefix: 随机前缀（可选，用于域名方式）
            
            Returns:
                格式化的访问URL字符串
            """
            protocol_lower = protocol.lower()
            
            # 非HTTP协议：使用 "protocol host port" 格式（空格分隔，便于前端解析）
            if protocol_lower in ['ssh', 'rdp', 'vnc', 'nc', 'ftp', 'mysql', 'redis', 'mongodb', 'postgresql']:
                return f"{protocol_lower} {host} {port}"
            
            # HTTPS协议
            elif protocol_lower == 'https':
                if domain and random_prefix:
                    return f"https://{random_prefix}.{domain}:{port}"
                else:
                    return f"https://{host}:{port}"
            
            # HTTP协议（默认）
            else:
                if domain and random_prefix:
                    return f"http://{random_prefix}.{domain}:{port}"
                else:
                    return f"http://{host}:{port}"
        
        try:
            
            user_container = None
            web_user_container = None  
            entry_containers = []  
            
            for container in containers_info:
                temp_container = self._create_user_container(
                    container, docker_engine, expires_at, containers_info
                )
                user_container = temp_container  # 保留最后一个作为兜底
                
                #  识别所有入口节点（通过 is_entry_point 标识）
                is_entry = container.get('is_entry_point', False)
                
             
                if not is_entry and web_container_info and container['id'] == web_container_info.get('id'):
                    is_entry = True
                
                if is_entry:
                    entry_containers.append({
                        'container': temp_container,
                        'info': container
                    })
                    # 第一个入口节点作为主入口
                    if not web_user_container:
                        web_user_container = temp_container
                    
                    logger.info(
                        f"✓ 识别到入口节点容器: container_id={container['id'][:12]}, "
                        f"node_label={container.get('node_label', 'N/A')}, "
                        f"ports={container.get('ports')}"
                    )
            
            #  优先缓存主入口节点，否则缓存最后一个
            cache_container = web_user_container if web_user_container else user_container
            
            # 确保至少创建了一个容器
            if not cache_container:
                self._cleanup_on_error()
                logger.error(f"用户 {self.user} 创建容器失败")
                raise ValueError("没有成功创建任何容器")
            
            # 扣除金币（只扣一次）
            success, error_msg = self.ctf_user.deduct_coins(self.challenge.coins)
            if not success:
                raise ValueError(error_msg)
            
            #  生成所有入口节点的URL（支持多入口）
            container_urls = []
            random_prefix = uuid.uuid4().hex[:8]  # 域名方式使用相同的随机前缀
            logger.info(f"入口容器信息: {len(entry_containers)} 个节点")
            logger.debug(f"Web容器信息: {web_container_info}")
            
            if entry_containers:
                #  多入口场景：为所有入口节点生成URL（支持单镜像和多镜像编排）
                for entry in entry_containers:
                    container_info = entry['info']
                    ports = container_info.get('ports', {}).values()
                    node_label = container_info.get('node_label', '入口')
                    
                    #  获取协议：优先从 container_info (多镜像编排)，否则从 docker_image.entrance (单镜像)
                    protocol = container_info.get('protocol')
                    if not protocol:
                        # 单镜像模式：从 docker_image.entrance 映射协议
                        entrance_type = self.challenge.docker_image.entrance if self.challenge.docker_image else 'WEB'
                        protocol = ENTRANCE_TO_PROTOCOL_MAP.get(entrance_type, 'http')
                        logger.info(
                            f" 单镜像入口节点: entrance_type={entrance_type} -> protocol={protocol}"
                        )
                    else:
                        logger.info(
                            f" 多镜像编排入口节点: protocol={protocol}"
                        )
                    
                    for port in ports:
                        #  使用统一的URL生成方法
                        url = _generate_url_by_protocol(
                            protocol=protocol,
                            host=docker_engine.host,
                            port=port,
                            domain=docker_engine.domain,
                            random_prefix=random_prefix
                        )
                        
                        logger.info(
                            f" URL生成: protocol={protocol}, port={port}, "
                            f"domain={docker_engine.domain}, url={url}"
                        )
                        
                        # 附加节点标签和协议信息（多入口时便于区分）
                        if len(entry_containers) > 1:
                            container_urls.append({
                                'url': url,
                                'label': node_label,
                                'node_id': container_info.get('node_id'),
                                'protocol': protocol  # 附加协议信息
                            })
                        else:
                            # 单入口：保持向后兼容，直接返回URL字符串
                            container_urls.append(url)
            elif web_container_info:
                #  单入口场景：兼容单镜像模式（使用 DockerImage.entrance 字段）
                ports = web_container_info['ports'].values()
                
                #  将单镜像的入口类型映射为协议名（大写 -> 小写）
                entrance_type = self.challenge.docker_image.entrance
                protocol = ENTRANCE_TO_PROTOCOL_MAP.get(entrance_type, 'http')
                
                logger.info(
                    f" 单镜像模式URL生成: "
                    f"entrance_type={entrance_type}, "
                    f"mapped_protocol={protocol}, "
                    f"domain={docker_engine.domain}, "
                    f"host={docker_engine.host}, "
                    f"ports={list(ports)}, "
                    f"镜像ID={self.challenge.docker_image.id}"
                )
                
                for port in ports:
                    #  使用统一的URL生成方法
                    url = _generate_url_by_protocol(
                        protocol=protocol,
                        host=docker_engine.host,
                        port=port,
                        domain=docker_engine.domain,
                        random_prefix=random_prefix
                    )
                    container_urls.append(url)
                    
                    logger.info(
                        f"✓ 单镜像URL生成完成: "
                        f"entrance={entrance_type}, "
                        f"protocol={protocol}, "
                        f"port={port}, "
                        f"url={url}"
                    )
            
            logger.info(
                f" 用户 {self.user.username} 创建容器成功: "
                f"题目={self.challenge.title}, "
                f"入口节点数={len(entry_containers)}, "
                f"URL数={len(container_urls)}",
                extra={'request': self.request}
            )
            
            if not container_urls:
                logger.warning(
                    f"容器创建成功但无访问 URL！"
                    f"containers_info={[c.get('id', 'unknown')[:12] for c in containers_info]}"
                )
            
            #  更新缓存（缓存主入口节点 + URL信息）
            UserContainerCache.set(
                cache_container,
                url_prefix=random_prefix,
                container_urls=container_urls
            )
            logger.info(
                f"✓ 缓存容器信息: container_id={cache_container.container_id[:12]}, "
                f"is_entry_point={bool(web_user_container)}, "
                f"entry_count={len(entry_containers)}, "
                f"url_count={len(container_urls)}, "
                f"user={self.user.id}, challenge={self.challenge_uuid}"
            )
            
            try:
                cache.delete(f'user_ctf_stats_{self.user.id}')
            except Exception as e:
                logger.error(f"清除缓存失败: {e}")
            
            return {
                "container_urls": container_urls,  # 返回URL列表（可能包含多个入口）
                "expires_at": expires_at.isoformat(),
                "entry_count": len(entry_containers)  #  入口节点数量
            }


              
        
        except Exception as e:
            self._cleanup_on_error()
            logger.error(f"用户 {self.user} 创建容器失败 {e}")
            raise ValueError(f"未能创建容器，请稍后再试")

def create_container_api(challenge_uuid, user, request) -> Tuple[Dict, Optional[str]]:
    """
    创建容器API
    
    Args:
        challenge_uuid: 挑战的UUID
        user: 用户对象
        request: HTTP 请求对象
        
    Returns:
        Tuple[Dict, Optional[str]]: (结果数据, 错误信息)
    """
    try:
        #  从 request.META 中提取目标节点（如果有）
        target_node = None
        if hasattr(request, 'META') and request.META:
            target_node = request.META.get('target_node')
        
        container_manager = ContainerManager(user, challenge_uuid, request, target_node=target_node)
        
        # 检查已存在的容器
        
        existing_container = container_manager.check_existing_container()
        if existing_container:
            return existing_container, None
        # 创建新容器
        result = container_manager.create_container()
        return result, None
        
    except DockerServiceException as e:
        # Docker服务异常（包括超时等），返回具体错误信息
        logger.error(f"Docker服务异常: {str(e)}")
        return None, str(e)
    except ValueError as e:
        logger.error(f"创建容器失败: {e}")
        return None, str(e)
    except Exception as e:
        logger.error(f"创建容器失败: {e}")
        return None, f"题目环境创建失败请稍后再试"




def flag_destroy_web_container(user, challenge_uuid, challenge, request):
    """
    flag认真成功后自动摧容器
    
    Args:
        challenge_uuid: 挑战的UUID
        user: 用户对象
        
    Returns:
        Tuple[Dict, Optional[str]]: (结果数据, 错误信息)
    """
    try:
        # 只查询练习中运行的容器（不包括比赛容器）
        user_containers = UserContainer.objects.filter(
            user=user, 
            container_type='PRACTICE',  # 只处理练习容器
            status='RUNNING'
        )
        
        if not user_containers.exists():
            # 清理缓存（容器缓存会自动清理 flag）
            if UserContainerCache.get(user.id, challenge_uuid):
                UserContainerCache.delete(user.id, challenge_uuid)
            
            return True,'将自动摧毁容器'

        
        docker_services = {}
        for user_container in user_containers:
            try:
                docker_engine = user_container.docker_engine
                if docker_engine.id not in docker_services:
                    # 获取或创建容器服务实例（支持 Docker 和 K8s）
                    from container.container_service_factory import ContainerServiceFactory
                    docker_services[docker_engine.id] = ContainerServiceFactory.create_service(docker_engine)
                
                docker_service = docker_services[docker_engine.id]
                
                # 停止并移除容器
                docker_service.stop_and_remove_container(user_container.container_id)

                # 软删除：标记为已删除，保留记录用于审计和统计
                user_container.mark_deleted(deleted_by='USER')
                
              
                
            except Exception as e:
                error_msg = f"销毁容器时发生错误"
                
                logger.error(f"用户 {user}销毁题目{challenge.title}发生错误{e}", extra={'request': request})
                return False,error_msg
        
        # 清除缓存（容器缓存会自动清理 flag）
        UserContainerCache.delete(user.id, challenge_uuid)
        logger.info(f"用户 {user}销毁题目{challenge.title}的容器", extra={'request': request})
        return True,'将自动摧毁容器'
    
    except PC_Challenge.DoesNotExist:
        return JsonResponse({'error': '找不到指定的题目'}, status=404)
    
    except Exception as e:
        error_msg = f"自动销毁容器时发生错误"
        logger.error(f"用户 {user}销毁题目{challenge.title}发生错误{e}", extra={'request': request})
        return False, error_msg

    finally:
        # 确保清理缓存（容器缓存会自动清理 flag）
        if UserContainerCache.get(user.id, challenge_uuid):
            UserContainerCache.delete(user.id, challenge_uuid)
        # 只清理练习中运行的容器（软删除）
        user_containers_er = UserContainer.objects.filter(
            user=user, 
            container_type='PRACTICE',  # 只清理练习容器
            status='RUNNING'
        )
        if user_containers_er.exists():
            for containers in user_containers_er:
                try:
                    # 尝试停止容器（支持 Docker 和 K8s）
                    if containers.container_id:
                        from container.container_service_factory import ContainerServiceFactory
                        service = ContainerServiceFactory.create_service(containers.docker_engine)
                        service.stop_and_remove_container(containers.container_id)
                except Exception as e:
                    logger.error(f"清理容器失败: {str(e)}")
                finally:
                    # 标记为已删除
                    containers.mark_deleted(deleted_by='USER')
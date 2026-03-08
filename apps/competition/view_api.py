from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
import json
import time
import uuid
import threading
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.core.cache import cache
from django.db.models import Count, Q
from django.db import transaction, DatabaseError
from docker.errors import APIError, DockerException
from requests.exceptions import ConnectionError, ReadTimeout
import docker
from competition.models import Challenge
from container.models import UserContainer, DockerEngine, DockerImage
from competition.redis_cache import UserContainerCache
from container.container_service_factory import ContainerServiceFactory
from container.container_service_base import ContainerServiceException
from container.docker_service import DockerServiceException
from competition.flag_generator import get_or_generate_flag, verify_flag as verify_flag_func
from easytask.tasks import cleanup_container
from celery.exceptions import MaxRetriesExceededError
import requests
from requests.exceptions import RequestException
from urllib.parse import urlparse
from django.conf import settings
import logging

logger = logging.getLogger('apps.competition')


class ContainerCreatingException(Exception):
    """容器正在创建中异常，携带已有的任务 ID"""
    def __init__(self, task_id):
        self.task_id = task_id
        super().__init__(f"容器创建任务已存在: {task_id}")


class DistributedLock:
    """
    基于 Redis 的分布式锁
    支持自动续期和防死锁
    """
    def __init__(self, lock_key, timeout=30, retry_times=3, retry_delay=0.5):
        self.lock_key = f"distributed_lock:{lock_key}"
        self.timeout = timeout
        self.retry_times = retry_times
        self.retry_delay = retry_delay
        self.lock_value = str(uuid.uuid4())
        self._locked = False
    
    def acquire(self):
        """获取锁（带重试机制）"""
        for i in range(self.retry_times):
            # 使用 Redis 的原子操作 SET NX（仅当 key 不存在时设置）
            if cache.add(self.lock_key, self.lock_value, timeout=self.timeout):
                self._locked = True
                return True
            
            # 如果获取失败，检查锁是否过期（防止死锁）
            if i < self.retry_times - 1:
                time.sleep(self.retry_delay)
        
        return False
    
    def release(self):
        """释放锁（仅释放自己持有的锁）"""
        if self._locked:
            # 验证锁的持有者，防止误释放
            current_value = cache.get(self.lock_key)
            if current_value == self.lock_value:
                cache.delete(self.lock_key)
            self._locked = False
    
    def __enter__(self):
        if not self.acquire():
            raise ValueError("系统繁忙，请稍后再试")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False


class ContainerManager:
    """容器管理器 - 处理用户容器的创建、检查和清理"""
    
    # 配置将动态从 ContainerEngineConfig 加载
    RATE_LIMIT_SECONDS = 60  # 速率限制时间（秒）
    CONTAINER_CREATION_TIMEOUT = 120  # 容器创建超时（秒）
    
    def __init__(self, user, challenge_uuid, request, competition=None, target_node=None):
        from container.models import ContainerEngineConfig
        config = ContainerEngineConfig.get_config()
        
        # 从数据库配置加载
        self.MAX_CONTAINERS_PER_USER = config.max_containers_per_user
        self.MAX_CONTAINERS_PER_CHALLENGE = config.max_containers_per_challenge
        self.MAX_CONTAINERS_PER_TEAM = config.max_containers_per_team
        self.user = user
        self.challenge_uuid = challenge_uuid
        self.request = request
        self.competition = competition  # 当前用户正在参与的比赛
        self.challenge = get_object_or_404(Challenge, uuid=challenge_uuid)
        self.cache_key = f"container_lock:{user.id}:{challenge_uuid}"
        self.rate_limit_key = f"rate_limit:container:{user.id}"
        self.docker_engine = None
        self.docker_url = None
        self.tls_config = None
        self.target_node = target_node  #  目标节点（由views.py预检时选择）

    def check_existing_container(self) -> Optional[Dict]:
        """检查已存在的容器"""
        cached_container = UserContainerCache.get(self.user.id, self.challenge_uuid)
        if not cached_container:
            return None

        # 验证容器归属
        if cached_container['challenge_uuid'] != str(self.challenge.uuid):
            logger.warning(f"用户 {self.user.id} 尝试访问不属于自己的容器")
            raise ValueError("容器验证失败")
            
        # 检查过期时间
        #  确保时区一致性：将 ISO 字符串转换为 aware datetime
        expires_at_str = cached_container['expires_at']
        try:
            expires_at = datetime.fromisoformat(expires_at_str)
            # 如果是 naive datetime，添加时区信息
            if expires_at.tzinfo is None:
                from django.utils.timezone import make_aware
                expires_at = make_aware(expires_at)
        except (ValueError, TypeError) as e:
            logger.warning(f"解析过期时间失败: {expires_at_str}, 错误: {e}")
            UserContainerCache.delete(self.user.id, self.challenge_uuid)
            return None
        
        if expires_at < timezone.now():
            UserContainerCache.delete(self.user.id, self.challenge_uuid)
            return None

        # 返回已存在的容器信息
        ports = json.loads(cached_container['port'])
        container_urls = []
        
        #  使用缓存中保存的 url_prefix（确保一致性）
        url_prefix = cached_container.get('url_prefix')
        
        for port in ports.values():
            if cached_container['domain'] and url_prefix:
                url = f"http://{url_prefix}.{cached_container['domain']}:{port}"
            else:
                url = f"http://{cached_container['ip_address']}:{port}"
            container_urls.append(url)
        

        return {
            "status": "existing",
            "container_urls": container_urls,
            "expires_at": cached_container['expires_at']
        }

    def check_prerequisites(self):
        """
        检查创建容器的前置条件（含安全检查）
        
        优化：使用数据库行锁防止竞态条件
        """
        
        # 1. 检查题目是否启用
        if not self.challenge.is_active:
            logger.warning(f"用户 {self.user.id} 尝试访问未启用的题目: {self.challenge.uuid}")
            raise ValueError("该题目当前未启用，暂时无法访问")
        
        # 2. 检查镜像配置（支持单镜像和多场景拓扑两种类型）
        docker_image = self.challenge.docker_image 
        network_topology_config = self.challenge.network_topology_config
        
        if docker_image:
            # 单镜像场景：检查 DockerImage 配置
            if not docker_image.is_active:
                logger.warning(f"题目 {self.challenge.uuid} 使用的镜像 {docker_image.id} 未启用")
                raise ValueError("该题目的镜像配置已禁用，暂时无法创建容器")
            
            # 检查镜像审核状态
            if docker_image.review_status != 'APPROVED':
                if not self.user.is_superuser:
                    logger.warning(f"用户 {self.user.id} 尝试使用未审核的镜像: {docker_image.id}")
                    raise ValueError("该题目的镜像配置尚未通过审核，暂时无法创建容器")
            
            logger.debug(f"题目 {self.challenge.uuid} 使用单镜像模式")
            
        elif network_topology_config:
            # 多场景拓扑：检查 NetworkTopologyConfig 配置
            if hasattr(network_topology_config, 'is_active') and not network_topology_config.is_active:
                logger.warning(f"题目 {self.challenge.uuid} 使用的拓扑配置 {network_topology_config.id} 未启用")
                raise ValueError("该题目的拓扑配置已禁用，暂时无法创建容器")
            
            # 检查拓扑中的节点配置
            if hasattr(network_topology_config, 'get_node_count'):
                node_count = network_topology_config.get_node_count()
                if node_count == 0:
                    logger.error(f"题目 {self.challenge.uuid} 的拓扑配置没有节点")
                    raise ValueError("该题目的拓扑配置不完整，请联系管理员")
                logger.debug(f"题目 {self.challenge.uuid} 使用多场景拓扑模式，节点数: {node_count}")
            
        else:
            logger.error(f"题目 {self.challenge.uuid} 没有配置镜像或拓扑")
            raise ValueError("该题目未配置容器环境，请联系管理员")
        
        # 3. 速率限制检查（防止恶意创建）- 放在前面，快速失败
        if cache.get(self.rate_limit_key):
            raise ValueError(f"操作过于频繁，请等待 {self.RATE_LIMIT_SECONDS} 秒后再试")
        
        # 4. 检查是否有正在创建中的容器（防止重复创建）
        if cache.get(self.cache_key):
            # 检查是否有关联的任务 ID，如果有则返回给前端继续轮询
            pending_task_key = f"container_task_user:{self.user.id}:{self.challenge_uuid}"
            existing_task_id = cache.get(pending_task_key)
            if existing_task_id:
                # 返回一个特殊的字典，让调用者知道应该返回已有的任务
                raise ContainerCreatingException(existing_task_id)
            raise ValueError("正在创建容器中，请稍候...")
        
        # 5. 原子性检查容器数量（使用数据库行锁防止竞态条件）
        self._check_container_limits_atomic()
    
    def _verify_and_cleanup_ghost_containers(self, containers):
        """
        验证并清理"幽灵容器"（数据库中标记为RUNNING，但引擎中不存在的容器）
        
        Args:
            containers: QuerySet 或容器列表
            
        Returns:
            int: 真实存在的容器数量
        """
        from container.container_service_factory import ContainerServiceFactory
        
        valid_count = 0
        ghost_containers = []
        
        for container in containers:
            try:
                # 验证容器在引擎中是否真实存在
                docker_engine = container.docker_engine
                if not docker_engine:
                    ghost_containers.append(container)
                    continue
                
                container_service = ContainerServiceFactory.create_service(docker_engine)
                
                try:
                    status = container_service.get_container_status(container.container_id)
                    
                    # 容器真实存在且运行中
                    if status in ['RUNNING', 'STARTING']:
                        valid_count += 1
                    else:
                        # 容器不存在或已停止
                        ghost_containers.append(container)
                        logger.warning(
                            f"发现幽灵容器: {container.container_id[:12]}, "
                            f"数据库状态=RUNNING, 引擎状态={status}"
                        )
                finally:
                    # 关闭容器服务连接
                    if hasattr(container_service, 'close'):
                        try:
                            container_service.close()
                        except Exception:
                            pass
                    
            except Exception as e:
                # 无法连接引擎或容器不存在
                ghost_containers.append(container)
                logger.warning(f"验证容器失败: {container.container_id[:12]}, 错误: {str(e)}")
        
        # 清理幽灵容器（标记为已删除）
        if ghost_containers:
            for ghost in ghost_containers:
                try:
                    ghost.mark_deleted(deleted_by='SYSTEM_AUTO_CLEANUP')
                    logger.info(f"✓ 清理幽灵容器: {ghost.container_id[:12]}")
                except Exception as e:
                    logger.error(f"标记幽灵容器失败: {ghost.container_id[:12]}, 错误: {str(e)}")
        
        return valid_count
    
    def _check_container_limits_atomic(self):
        """
        原子性检查容器数量限制（优化版 - 使用快速失败策略 + 幽灵容器检测）
        
        优化：
        1. 使用 nowait=True 避免阻塞
        2. 使用 skip_locked=True 跳过已锁定的行
        3. 对题目级别使用聚合查询而不是锁定所有行
        4. 验证容器真实性，自动清理幽灵容器
        """
        now = timezone.now()
        
        # 使用数据库事务和行锁
        with transaction.atomic():
            
            if self.competition:
                try:
                    user_containers = UserContainer.objects.select_for_update(
                        nowait=True  # 不等待锁，立即失败
                    ).filter(
                        user=self.user,
                        competition=self.competition,
                        status='RUNNING',
                        expires_at__gt=now
                    )
                    
                    db_count = user_containers.count()
                    
                    #  如果数据库中有容器记录，验证真实性
                    if db_count > 0:
                        valid_count = self._verify_and_cleanup_ghost_containers(list(user_containers))
                        logger.info(
                            f"用户容器验证: 数据库={db_count}, 真实={valid_count}, "
                            f"清理幽灵容器={db_count - valid_count}"
                        )
                        
                        # 使用真实数量判断
                        if valid_count >= self.MAX_CONTAINERS_PER_USER:
                            raise ValueError(
                                "您在当前比赛中已有运行中的容器，请先关闭后再试"
                            )
                    
                except DatabaseError as e:
                    logger.warning(f"无法获取用户容器锁: {e}")
                    raise ValueError("系统繁忙，请稍后再试")
            
            # 题目级别容器数量检查（高并发优化：暂时禁用幽灵容器检测）
            # 原因：在高并发场景下，幽灵容器检测可能误杀正在启动的Pod
            # 后续可通过定时任务在低峰期清理幽灵容器
            challenge_containers = UserContainer.objects.filter(
                challenge_uuid=self.challenge_uuid,
                status='RUNNING',
                expires_at__gt=now
            ).exclude(
                user=self.user  # 排除当前用户
            )
            
            db_challenge_count = challenge_containers.count()
            
            # 直接使用数据库数量判断，不进行幽灵容器验证
            # 这样可以避免高并发下的误杀，但可能存在少量幽灵容器占用配额
            # 通过定时清理任务可以解决这个问题
            if db_challenge_count >= self.MAX_CONTAINERS_PER_CHALLENGE:
                logger.warning(
                    f"题目 {self.challenge.uuid} 达到最大容器数限制: "
                    f"{db_challenge_count}/{self.MAX_CONTAINERS_PER_CHALLENGE}"
                )
                raise ValueError("该题目当前访问量过大，请稍后再试")
            
            # 3. 检查团队容器数量（仅团队赛）
            if self.competition and self.competition.competition_type == 'team':
                self._check_team_container_limit_atomic(now)
    
    def _check_team_container_limit_atomic(self, now):
        """原子性检查团队容器数量限制（幽灵容器验证）
        
        注意：此方法由 create_container 调用，外层已有团队分布式锁保护
        """
        from competition.models import Registration
        try:
            # 获取用户的报名信息
            registration = Registration.objects.select_related('team_name').get(
                competition=self.competition,
                user=self.user
            )
            
            if registration.team_name:
                team = registration.team_name
                
                # 获取团队所有成员的 ID（包括队长）
                team_member_ids = list(team.members.values_list('id', flat=True))
                if team.leader_id and team.leader_id not in team_member_ids:
                    team_member_ids.append(team.leader_id)
                
                # 使用行锁统计团队所有成员的运行中容器数量
                team_containers = UserContainer.objects.select_for_update(
                    nowait=False
                ).filter(
                    user_id__in=team_member_ids,
                    status='RUNNING',
                    expires_at__gt=now
                )
                
                db_team_count = team_containers.count()
                
                #  验证团队容器真实性
                if db_team_count > 0:
                    valid_team_count = self._verify_and_cleanup_ghost_containers(list(team_containers))
                    logger.info(
                        f"团队 {team.name} 容器验证: 数据库={db_team_count}, 真实={valid_team_count}, "
                        f"清理幽灵容器={db_team_count - valid_team_count}"
                    )
                    
                    # 使用真实数量判断
                    if valid_team_count >= self.MAX_CONTAINERS_PER_TEAM:
                        raise ValueError(
                            f"您的团队已达到最大容器数限制（{self.MAX_CONTAINERS_PER_TEAM}个），"
                            "请等待队友关闭容器后再试"
                        )
        except Registration.DoesNotExist:
            # 用户未报名，但可能是管理员，继续执行
            pass

    def _get_docker_engine(self, excluded_engines=None):
        """
        获取负载最小且健康的容器引擎（智能选择 + 降级重试）
        
        Args:
            excluded_engines: 要排除的引擎ID列表（用于重试时避免选择已失败的引擎）
        
        Returns:
            DockerEngine: 选中的容器引擎
        """
        excluded_engines = excluded_engines or []
        
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
                logger.error(f"多场景题目 {self.challenge_uuid} 没有可用的 Kubernetes 引擎")
                raise ValueError("多场景题目需要 Kubernetes 引擎，但当前没有可用的 Kubernetes 引擎")
            elif excluded_engines:
                logger.error(f"所有引擎均已尝试失败，排除的引擎: {excluded_engines}")
                raise ValueError("所有容器引擎均不可用或资源不足，请稍后再试或联系管理员")
            else:
                logger.error("没有可用的容器引擎")
                raise ValueError("系统暂时无法提供容器服务，请联系管理员")
        
        # 计算每个引擎的当前负载
        # 只统计运行中的容器负载
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
        
        # 如果过滤后没有可用引擎，记录警告并使用所有激活的引擎
        if not available_engines:
            logger.warning("所有引擎都处于不健康状态，降级使用所有激活引擎")
            available_engines = active_engines
        
        # 智能选择引擎：优先考虑健康状态，其次考虑负载
        # 排序规则：健康状态优先级 -> 负载（容器数量）
        docker_engine = min(
            available_engines,
            key=lambda engine: (
                health_priority.get(engine.health_status, 50),  # 第一优先级：健康状态
                engine_loads_dict.get(engine.id, 0)             # 第二优先级：负载
            )
        )
        
        logger.info(
            f"选择容器引擎: {docker_engine.name} ({docker_engine.get_engine_type_display()}), "
            f"健康状态={docker_engine.get_health_status_display()}, "
            f"负载={engine_loads_dict.get(docker_engine.id, 0)}个容器, "
            f"已排除引擎={len(excluded_engines)}个"
        )
        
        # 测试引擎连接（仅对 Docker 引擎）
        if docker_engine.engine_type == 'DOCKER':
            try:
                if docker_engine.host_type == 'LOCAL':
                    docker_url = "unix:///var/run/docker.sock"
                else:
                    docker_url = f"tcp://{docker_engine.host}:{docker_engine.port}"
                
                tls_config = None
                if docker_engine.tls_enabled:
                    tls_config = docker.tls.TLSConfig(
                        client_cert=(
                            docker_engine.client_cert_path, 
                            docker_engine.client_key_path
                        ),
                        ca_cert=docker_engine.ca_cert_path,
                        verify=True
                    )
                
                # 测试连接
                test_client = docker.DockerClient(base_url=docker_url, tls=tls_config, timeout=10)
                test_client.ping()
                test_client.close()
                
                self.docker_engine = docker_engine
                self.docker_url = docker_url
                self.tls_config = tls_config
                
                return docker_engine
                
            except Exception as e:
                logger.error(
                    f"Docker 引擎 {docker_engine.name} 连接失败: {str(e)}"
                )
                # 标记引擎为不可用
                docker_engine.is_active = False
                docker_engine.save(update_fields=['is_active'])
                
                # 递归重试（排除当前引擎）
                excluded_engines.append(docker_engine.id)
                logger.info(f"尝试使用备用容器引擎")
                return self._get_docker_engine(excluded_engines=excluded_engines)
        else:
            # K8s 引擎：跳过连接测试，在创建容器时检查资源
            self.docker_engine = docker_engine
            return docker_engine

    def _get_docker_service(self, docker_engine):
        """创建容器服务实例（支持 Docker 和 K8s）"""
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
        try:
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
            
            user_container = UserContainer.objects.create(
                user=self.user,
                challenge_title=self.challenge.title,
                challenge_uuid=self.challenge_uuid,
                docker_engine=docker_engine,
                container_id=container['id'],
                ip_address=docker_engine.host,
                domain=docker_engine.domain,
                port=json.dumps(container['ports']),
                expires_at=expires_at,
                container_type='COMPETITION',  # 比赛容器
                competition=self.competition,  # 使用传入的比赛对象（准确）
                # 🆕 拓扑场景支持
                topology_config=topology_config,
                topology_data=topology_data
            )
            
            return user_container
            
        except Exception as e:
            logger.error(f"创建用户容器记录失败: {str(e)}")
            raise ValueError("容器记录创建失败")



    def _cleanup_on_error(self):
        """错误发生时清理资源（包括所有相关缓存和数据库记录）"""
        try:
            # 1. 清理容器缓存（自动清理 flag）
            UserContainerCache.delete(self.user.id, self.challenge_uuid)
            cache.delete(self.cache_key)
            
            # 2.  清理任务相关缓存
            pending_task_key = f"container_task_user:{self.user.id}:{self.challenge_uuid}"
            old_task_id = cache.get(pending_task_key)
            if old_task_id:
                cache.delete(f"container_task:{old_task_id}")
            cache.delete(pending_task_key)
            
            # 3.  清理容器创建锁（防止锁残留）
            container_lock_key = f"container_lock:{self.user.id}:{self.challenge_uuid}"
            if cache.get(container_lock_key):
                cache.delete(container_lock_key)
                logger.debug(f"清理容器创建锁: {container_lock_key}")
            
            # 4. 清理数据库中的容器记录
            #  清理所有可能占用资源的状态（RUNNING, PENDING, CREATING）
            user_containers = UserContainer.objects.filter(
                user=self.user,
                challenge_uuid=self.challenge_uuid,
                status__in=['RUNNING', 'PENDING', 'CREATING']
            )
            
            for container in user_containers:
                container_service = None
                try:
                    # 使用容器服务工厂清理容器（支持 Docker 和 K8s）
                    docker_engine = container.docker_engine
                    if docker_engine and container.container_id:
                        try:
                            container_service = ContainerServiceFactory.create_service(docker_engine)
                            container_service.stop_and_remove_container(container.container_id)
                            logger.info(f"清理失败容器: {container.container_id[:12]}")
                        except Exception as e:
                            logger.error(f"清理容器 {container.container_id[:12]} 失败: {str(e)}")
                        finally:
                            #  确保关闭容器服务连接
                            if container_service and hasattr(container_service, 'close'):
                                try:
                                    container_service.close()
                                except Exception as close_err:
                                    logger.debug(f"关闭容器服务连接失败: {close_err}")
                    
                    # 删除数据库记录
                    container.delete()
                    
                except Exception as e:
                    logger.error(f"清理容器记录失败: {str(e)}")
            
            logger.info(f"用户 {self.user.id} 的失败容器资源清理完成（包括所有缓存）")
            
        except Exception as e:
            logger.error(f"清理资源时发生异常: {str(e)}", exc_info=True)

    def create_container(self) -> Dict:
        """
        创建容器（主要入口 - 高并发优化版 + 智能引擎降级）
        
        并发优化：
        1.  移除题目级别锁，允许不同用户并发创建
        2.  保留用户级别锁，防止同一用户重复创建
        3.  使用数据库快速失败锁策略
        4.  分级限流：用户级 + 题目级（通过计数检查）
        5.  团队赛使用分布式锁，防止同队成员并发超限
        
        智能降级：
        1.  K8s 资源不足时自动切换到其他引擎
        2.  最多尝试所有可用引擎
        3.  记录每次尝试和失败原因
        """
        start_time = time.time()
        excluded_engines = []  # 记录已尝试失败的引擎
        max_retries = 3  # 最多尝试3个不同的引擎
        last_error = None
        
        # 对于团队赛，使用分布式锁保证团队容器限制检查和创建的原子性
        team_lock = None
        if self.competition and self.competition.competition_type == 'team':
            try:
                from competition.models import Registration
                registration = Registration.objects.select_related('team_name').get(
                    competition=self.competition,
                    user=self.user
                )
                if registration.team_name:
                    team = registration.team_name
                    team_lock_key = f"team_container_create:{self.competition.id}:{team.id}"
                    team_lock = DistributedLock(team_lock_key, timeout=30, retry_times=10, retry_delay=0.5)
                    logger.info(f"团队赛模式: 用户 {self.user.username} 尝试获取团队 {team.name} 的容器创建锁")
            except Exception as e:
                logger.warning(f"获取团队信息失败: {e}")
        
        try:
            # 团队赛：获取团队锁（覆盖整个检查+创建过程）
            if team_lock:
                if not team_lock.acquire():
                    raise ValueError("团队正在创建容器，请稍后再试")
                logger.info(f"已获取团队锁，开始创建容器")
            
            # 1. 检查前置条件（含安全检查和原子性容器数量检查）
            self.check_prerequisites()
            
            # 2. 设置创建锁（用户级别）- 速率限制只在成功后设置
            cache.set(self.cache_key, True, timeout=self.CONTAINER_CREATION_TIMEOUT)
            
            # 3. 智能引擎选择 + 降级重试
            for retry_count in range(max_retries):
                docker_service = None  # 初始化容器服务变量
                docker_engine = None  # 🔧 初始化引擎变量（防止 finally 块中访问未定义变量）
                try:
                    # 3.1 获取容器引擎（负载均衡 + 排除已失败的引擎）
                    docker_engine = self._get_docker_engine(excluded_engines=excluded_engines)
                    
                    if excluded_engines:
                        logger.info(
                            f" 智能降级: 尝试使用备用引擎 {docker_engine.name} ({docker_engine.get_engine_type_display()}) "
                            f"创建容器 (尝试 {retry_count + 1}/{max_retries}，已排除引擎: {len(excluded_engines)}个)"
                        )
                    else:
                        logger.info(
                            f" 尝试使用引擎 {docker_engine.name} ({docker_engine.get_engine_type_display()}) "
                            f"创建容器 (尝试 {retry_count + 1}/{max_retries})"
                        )
                    
                    # 3.2 创建容器服务
                    docker_service = self._get_docker_service(docker_engine)
                    
                    # 3.3 生成或获取 Flag
                    flag = get_or_generate_flag(self.challenge, self.user, self.competition)
                    
                    # 3.4 获取资源限制（镜像配置 or 默认值）
                    # 获取资源限制（仅用于单镜像场景，拓扑场景由各节点自己的镜像配置决定）
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
                            f"容器创建成功（智能降级生效）: 用户={self.user.id}, 题目={self.challenge_uuid}, "
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
                    
                    # 3.7 只在成功创建后设置速率限制（防止频繁成功创建）
                    cache.set(self.rate_limit_key, True, timeout=self.RATE_LIMIT_SECONDS)
                    
                    # 🔧 在 return 前关闭连接（避免 finally 块覆盖 return）
                    if docker_service and hasattr(docker_service, 'close'):
                        try:
                            docker_service.close()
                        except:
                            pass  # 忽略关闭错误
                    
                    return result
                
                except ContainerServiceException as e:
                    # 捕获容器服务异常，判断是否需要重试其他引擎
                    error_msg = str(e)
                    last_error = e
                    
                    logger.info(
                        f" 捕获到容器服务异常: 引擎={docker_engine.name} ({docker_engine.get_engine_type_display()}), "
                        f"错误={error_msg[:100]}"
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
                            f" 引擎 {docker_engine.name} ({docker_engine.get_engine_type_display()}) "
                            f"资源不足，触发智能降级机制，尝试切换到其他引擎"
                        )
                        logger.warning(f" 错误详情: {error_msg}")
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
            
           
            if last_error:
                logger.error(
                    f"容器创建失败，已尝试 {max_retries} 个引擎。"
                    f"排除的引擎: {excluded_engines}"
                )
                raise ValueError(f"所有容器引擎均不可用或资源不足，请稍后再试。最后错误: {str(last_error)}")
            else:
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
            cache.delete(self.cache_key)  # 释放创建锁
            raise
        
        except ContainerCreatingException:
            # 容器正在创建中，直接抛出让调用者处理
            raise
        
        except Exception as e:
            logger.error(f"容器创建发生未知错误: {str(e)}", exc_info=True)
            self._cleanup_on_error()
            raise ValueError("容器创建失败，请联系管理员")
        
        finally:
            #  只释放用户级别的锁
            cache.delete(self.cache_key)
            
            # 释放团队锁
            if team_lock:
                team_lock.release()
                logger.info(f"已释放团队容器创建锁")

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
        处理容器创建结果（优化版：支持多入口节点 + 多协议 + 缓存 flag）
        
        Args:
            containers_info: 容器信息列表（包含所有节点）
            web_container_info: Web容器信息（第一个入口节点，兼容性保留）
            docker_engine: Docker引擎
            flag: 生成的 flag（可选）
        """
        from django.db import close_old_connections
        from django.db.utils import OperationalError, DatabaseError
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
        
        user_container = None
        url_prefix = None
        
        #  优化：将Redis操作移出事务，避免持有数据库锁
        try:
            close_old_connections()
            
            # 1. 数据库事务：只处理数据库操作
            web_user_container = None  # 主入口节点（第一个）
            entry_containers = []  #  所有入口节点容器记录
            
            with transaction.atomic():
                #  创建所有容器的数据库记录，同时识别所有入口节点
                for container in containers_info:
                    try:
                        temp_container = self._create_user_container(
                            container, docker_engine, expires_at, containers_info  # 🆕 传入所有容器信息
                        )
                        user_container = temp_container  # 保留最后一个作为兜底
                        
                        #  识别所有入口节点（通过 is_entry_point 标识）
                        is_entry = container.get('is_entry_point', False)
                        
                        # 兼容性：如果没有 is_entry_point 标识，则通过 web_container_info 判断
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
                        
                        logger.info(
                            f"✓ 数据库记录创建成功: container_id={container.get('id', 'unknown')[:12]}, "
                            f"user={self.user.id}, challenge={self.challenge_uuid}"
                        )
                    except (OperationalError, DatabaseError) as db_err:
                        logger.error(
                            f"✗ 数据库记录创建失败: container_id={container.get('id', 'unknown')[:12]}, "
                            f"错误类型={type(db_err).__name__}, 错误={str(db_err)}",
                            exc_info=True
                        )
                        raise ValueError(f"数据库写入失败: {str(db_err)}")
                
                #  优先使用主入口节点，否则使用最后一个
                cache_container = web_user_container if web_user_container else user_container
                
                if not cache_container:
                    raise ValueError("没有成功创建任何容器")
            
            # 2. 事务外：生成 URL 随机前缀
            url_prefix = uuid.uuid4().hex[:8]  # 域名方式使用相同的随机前缀
            
            # 3. 事务外：生成所有入口节点的URL（支持多入口 + 多协议）
            container_urls = []
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
                            random_prefix=url_prefix
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
                        random_prefix=url_prefix
                    )
                    container_urls.append(url)
                    
                    logger.info(
                        f"✓ 单镜像URL生成完成: "
                        f"entrance={entrance_type}, "
                        f"protocol={protocol}, "
                        f"port={port}, "
                        f"url={url}"
                    )
            
            # 4. 更新缓存（包含URL信息）
            try:
                UserContainerCache.set(
                    cache_container,
                    url_prefix=url_prefix,
                    container_urls=container_urls
                )
                logger.info(
                    f"✓ 容器缓存设置成功: container_id={cache_container.container_id[:12]}, "
                    f"is_entry_point={bool(web_user_container)}, "
                    f"entry_count={len(entry_containers)}, "
                    f"url_count={len(container_urls)}, "
                    f"user={self.user.id}, challenge={self.challenge_uuid}"
                )
            except Exception as cache_err:
                logger.error(
                    f"✗ 设置容器缓存失败: container_id={cache_container.container_id[:12]}, "
                    f"错误类型={type(cache_err).__name__}, 错误={str(cache_err)}",
                    exc_info=True
                )
                #  缓存失败时回滚：删除已创建的数据库记录
                try:
                    cache_container.delete()
                    logger.info(f"✓ 已回滚数据库记录: container_id={cache_container.container_id[:12]}（因缓存设置失败）")
                except Exception as rollback_err:
                    logger.error(
                        f"✗ 回滚数据库记录失败: container_id={cache_container.container_id[:12]}, "
                        f"错误={str(rollback_err)}",
                        exc_info=True
                    )
                raise ValueError(f"容器缓存设置失败: {cache_err}")
            
            logger.info(
                f" 用户 {self.user.username} 创建容器成功: "
                f"题目={self.challenge.title}, "
                f"入口节点数={len(entry_containers)}, "
                f"URL数={len(container_urls)}"
            )
            
            if not container_urls:
                logger.warning(
                    f"容器创建成功但无访问 URL！"
                    f"containers_info={[c.get('id', 'unknown')[:12] for c in containers_info]}"
                )
            
            if container_urls:
                return {
                    "status": "created",
                    "container_urls": container_urls,  # 返回URL列表（可能包含多个入口）
                    "expires_at": expires_at.isoformat(),
                    "container_id": cache_container.container_id,  #  返回主入口节点的ID
                    "entry_count": len(entry_containers)  #  入口节点数量
                }
            
            raise ValueError("未能创建容器")
        
        except Exception as e:
            error_type = type(e).__name__
            logger.error(
                f"✗ 处理容器创建结果失败: "
                f"错误类型={error_type}, 错误={str(e)}, "
                f"user={self.user.id}, challenge={self.challenge_uuid}, "
                f"containers_info={[c.get('id', 'unknown')[:12] for c in containers_info] if containers_info else 'None'}",
                exc_info=True
            )
            
            # 尝试清理已创建的数据库记录（如果存在）
            if user_container:
                try:
                    logger.warning(f"尝试清理数据库记录: container_id={user_container.container_id[:12]}")
                    user_container.delete()
                    logger.info(f"✓ 已清理数据库记录: container_id={user_container.container_id[:12]}")
                except Exception as cleanup_err:
                    logger.error(
                        f"✗ 清理数据库记录失败: container_id={user_container.container_id[:12]}, "
                        f"错误={str(cleanup_err)}"
                    )
            
            self._cleanup_on_error()
            raise ValueError(f"容器配置失败: {str(e)}")


def create_container_api(challenge_uuid, user, request, competition=None) -> Tuple[Dict, Optional[str]]:
    """
    创建容器 API（公共接口）
    
    Args:
        challenge_uuid: 挑战的 UUID
        user: 用户对象
        request: HTTP 请求对象
        competition: 比赛对象（可选，用于准确关联比赛）
        
    Returns:
        Tuple[Dict, Optional[str]]: (结果数据, 错误信息)
    """
    try:
        container_manager = ContainerManager(user, challenge_uuid, request, competition)
        
        # 检查是否已有容器
        existing_container = container_manager.check_existing_container()
        if existing_container:
            return existing_container, None
        
        # 创建新容器
        result = container_manager.create_container()
        return result, None
        
    except ContainerServiceException as e:
        # 容器服务异常（包括Docker、K8s等），返回具体错误信息
        logger.error(f"容器服务异常: {str(e)}")
        return None, str(e)
    
    except ValueError as e:
        # 业务异常，返回错误消息
        logger.warning(f"容器创建失败: {str(e)}")
        return None, str(e)
    
    except Exception as e:
        # 系统异常，记录详细日志
        logger.error(f"容器创建系统异常: {str(e)}", exc_info=True)
        return None, "系统错误，请稍后再试"
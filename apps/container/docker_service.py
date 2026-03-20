

import docker
from docker.errors import DockerException, NotFound, APIError
from django.conf import settings
from django.utils import timezone
from django.core.cache import cache
from pypinyin import lazy_pinyin
import time
import uuid
import logging
import re
from container.models import ContainerEngineConfig
from .container_service_base import ContainerServiceBase, ContainerServiceException
from .docker_connection_pool import DockerConnectionPool

logger = logging.getLogger('apps.container')


class DockerServiceException(ContainerServiceException):
    """Docker 服务异常"""
    pass


class DockerService(ContainerServiceBase):
   
    def __init__(self, url, tls_config=None, security_config=None, engine=None):
        self.url = url
        self.tls_config = tls_config
        self.timeout = 300
        self.engine = engine  #  保存 engine 对象用于连接池
        
        # 安全配置（默认值：中等安全级别）
        self.security_config = security_config or {
            'allow_privileged': False,
            'drop_capabilities': 'NET_RAW,SYS_ADMIN,SYS_MODULE,SYS_PTRACE',
            'enable_seccomp': True,
            'allow_host_network': False,
            'allow_host_pid': False,
            'allow_host_ipc': False,
            'enable_network_policy': True,  # 容器间网络隔离（每个容器独立网络）
        }
        
       
        self.config = ContainerEngineConfig.get_config()
        
        logger.info(
            f"初始化 DockerService: URL={url}, "
            f"连接池={'启用' if engine else '禁用（兼容模式）'}, "
            f"容器隔离={'启用（独立网络）' if self.security_config['enable_network_policy'] else '禁用'}"
        )
    
    # ==================== 资源预检方法 ====================
    
    def check_cluster_capacity_with_limit(self, memory_limit, cpu_limit):
        """
        Docker 引擎资源预检（并发限流 + 资源检查）
        
        Args:
            memory_limit: 内存限制 (MB)
            cpu_limit: CPU 限制（核心数）
            
        Raises:
            DockerServiceException: 资源不足或并发超限
        """
    
        
        # 1. 并发限流（与 K8s 共用同一个计数器）
        MAX_CONCURRENT_CREATES = self.config.max_concurrent_creates
        
        try:
            current_count = cache.get('active_container_creates', 0)
            
            if current_count >= MAX_CONCURRENT_CREATES:
                raise DockerServiceException(
                    f"系统繁忙，当前有 {current_count} 个容器正在创建，请稍后再试"
                )
            
            logger.debug(
                f"Docker 并发检查通过: 当前={current_count}/{MAX_CONCURRENT_CREATES}"
            )
        except Exception as e:
            if isinstance(e, DockerServiceException):
                raise
            logger.warning(f"并发检查失败（放行）: {e}")
        
        # 2. Docker 资源检查
        try:
            self._check_docker_resources(memory_limit, cpu_limit)
        except Exception as e:
            raise DockerServiceException(f"Docker 资源不足: {e}")
    
    def _get_docker_resources(self):
        """
        获取Docker宿主机资源信息（用于资源预占）
        
        优化策略：
        1. 内存：使用宿主机实际可用内存（从docker info获取）
        2. CPU：使用容器的CPU使用率总和作为参考
        
        Returns:
            tuple: (total_memory_mb, total_cpu_cores, used_memory_mb, used_cpu_cores)
        """
        with self._get_docker_client() as client:
            try:
                info = client.info()
                

                total_memory_bytes = info.get('MemTotal', 0)
                total_memory_mb = total_memory_bytes / (1024 * 1024)
                total_cpu = info.get('NCPU', 0)
                
                if total_memory_mb == 0 or total_cpu == 0:
                    raise DockerServiceException("无法获取宿主机资源信息")
                
                import psutil  
                
                memory_info = psutil.virtual_memory()
                
                used_memory_bytes = total_memory_bytes - memory_info.available
                used_memory_mb = used_memory_bytes / (1024 * 1024)
                
                cpu_percent = psutil.cpu_percent(interval=0.5)  # 采样0.5秒
                used_cpu_cores = (cpu_percent / 100.0) * total_cpu  # 转换为核心数
                
                logger.info(
                    f"宿主机资源: 内存 {used_memory_mb:.0f}/{total_memory_mb:.0f}MB ({used_memory_mb/total_memory_mb*100:.1f}%), "
                    f"CPU {used_cpu_cores:.2f}/{total_cpu}核 ({cpu_percent:.1f}%)"
                )
    
                return total_memory_mb, total_cpu, used_memory_mb, used_cpu_cores
                
            except ImportError:
                
                logger.warning("psutil 未安装，使用容器统计估算内存（可能不准确）")
                return self._get_docker_resources_fallback()
            except Exception as e:
                logger.error(f"获取Docker资源失败: {e}", exc_info=True)
                raise DockerServiceException(f"无法获取Docker资源信息: {str(e)}")
    
    def _get_docker_resources_fallback(self):
        """回退方案：使用容器统计估算资源（不准确）"""
        with self._get_docker_client() as client:
            info = client.info()
            
            total_memory_bytes = info.get('MemTotal', 0)
            total_memory_mb = total_memory_bytes / (1024 * 1024)
            total_cpu = info.get('NCPU', 0)
            
            containers = client.containers.list(filters={'status': 'running'})
            total_containers = len(containers)
            
            # 如果没有运行中的容器，直接返回
            if total_containers == 0:
                return total_memory_mb, total_cpu, 0, 0
            
            # 实际使用量（基于容器stats，不准确）
            actual_memory_used_bytes = 0
            actual_cpu_percent = 0
            sampled_count = 0
            max_samples = 20
            
            for container in containers[:max_samples]:
                try:
                    stats = container.stats(stream=False)
                    
                    # 内存使用
                    memory_stats = stats.get('memory_stats', {})
                    mem_usage = memory_stats.get('usage', 0)
                    actual_memory_used_bytes += mem_usage
                    
                    # CPU使用率
                    cpu_stats = stats.get('cpu_stats', {})
                    precpu_stats = stats.get('precpu_stats', {})
                    
                    cpu_total = cpu_stats.get('cpu_usage', {}).get('total_usage', 0)
                    precpu_total = precpu_stats.get('cpu_usage', {}).get('total_usage', 0)
                    system_cpu = cpu_stats.get('system_cpu_usage', 0)
                    presystem_cpu = precpu_stats.get('system_cpu_usage', 0)
                    
                    cpu_delta = cpu_total - precpu_total
                    system_delta = system_cpu - presystem_cpu
                    
                    if system_delta > 0 and cpu_delta >= 0:
                        online_cpus = cpu_stats.get('online_cpus', total_cpu)
                        cpu_percent = (cpu_delta / system_delta) * online_cpus * 100
                        actual_cpu_percent += cpu_percent
                    
                    sampled_count += 1
                except Exception:
                    continue
            
            if sampled_count == 0:
                return total_memory_mb, total_cpu, 0, 0
            
            # 按比例估算
            if total_containers > sampled_count:
                scale_factor = total_containers / sampled_count
                actual_memory_used_bytes *= scale_factor
                actual_cpu_percent *= scale_factor
            
            used_memory_mb = actual_memory_used_bytes / (1024 * 1024)
            used_cpu_cores = actual_cpu_percent / 100.0
            
            logger.warning(
                f"使用容器统计（不准确）: 内存 {used_memory_mb:.0f}MB/{total_memory_mb:.0f}MB, "
                f"CPU {used_cpu_cores:.2f}核/{total_cpu}核"
            )
            
            return total_memory_mb, total_cpu, used_memory_mb, used_cpu_cores
    
    def _check_docker_resources(self, memory_limit, cpu_limit):
        with self._get_docker_client() as client:
            try:
                # 获取系统信息
                info = client.info()
                
               
                total_memory_bytes = info.get('MemTotal', 0)
                total_memory_mb = total_memory_bytes / (1024 * 1024)
                total_cpu = info.get('NCPU', 0)
                
                if total_memory_mb == 0 or total_cpu == 0:
                    logger.warning("无法获取宿主机资源信息，跳过检查")
                    return
                
                try:
                    containers = client.containers.list(filters={'status': 'running'})
                    
                    actual_memory_used_bytes = 0
                    actual_cpu_percent = 0
                    sampled_count = 0
                    max_samples = 20  
                    
                    for container in containers[:max_samples]:
                        try:
                            
                            stats = container.stats(stream=False, decode=True)
                            
                            
                            mem_usage = stats.get('memory_stats', {}).get('usage', 0)
                            actual_memory_used_bytes += mem_usage
                            
                            
                            cpu_stats = stats.get('cpu_stats', {})
                            precpu_stats = stats.get('precpu_stats', {})
                            
                            cpu_usage = cpu_stats.get('cpu_usage', {})
                            precpu_usage = precpu_stats.get('cpu_usage', {})
                            
                            cpu_total = cpu_usage.get('total_usage', 0)
                            precpu_total = precpu_usage.get('total_usage', 0)
                            
                            system_cpu = cpu_stats.get('system_cpu_usage', 0)
                            presystem_cpu = precpu_stats.get('system_cpu_usage', 0)
                            
                            
                            cpu_delta = cpu_total - precpu_total
                            system_delta = system_cpu - presystem_cpu
                            
                            if system_delta > 0 and cpu_delta >= 0:
                                online_cpus = cpu_stats.get('online_cpus', total_cpu)
                                cpu_percent = (cpu_delta / system_delta) * online_cpus
                                actual_cpu_percent += cpu_percent
                            
                            sampled_count += 1
                            
                        except Exception as e:
                            logger.debug(f"获取容器 {container.id[:12]} stats 失败: {e}")
                            continue
                    
                    
                    total_containers = len(containers)
                    if sampled_count > 0 and total_containers > sampled_count:
                        scale_factor = total_containers / sampled_count
                        actual_memory_used_bytes *= scale_factor
                        actual_cpu_percent *= scale_factor
                        logger.debug(
                            f"采样 {sampled_count}/{total_containers} 个容器，"
                            f"按比例估算总使用"
                        )
                    
                    
                    memory_usage_percent = (actual_memory_used_bytes / total_memory_bytes) * 100
                    cpu_usage_percent = (actual_cpu_percent / total_cpu) * 100
                    
                except Exception as e:
                    logger.warning(f"获取 Docker stats 失败: {e}，降级使用基于 limits 的估算")
                    
                   
                    from container.models import UserContainer
                    from django.db.models import Sum
                    
                    used_data = UserContainer.objects.filter(
                        status='RUNNING',
                        docker_engine=self.engine,
                        expires_at__gt=timezone.now()
                    ).aggregate(
                        total_memory=Sum('memory_limit'),
                        total_cpu=Sum('cpu_limit')
                    )
                    
                    used_memory_mb = used_data.get('total_memory') or 0
                    used_cpu = used_data.get('total_cpu') or 0
                    
                    memory_usage_percent = (used_memory_mb / total_memory_mb) * 100
                    cpu_usage_percent = (used_cpu / total_cpu) * 100
                
                
                MAX_USAGE_THRESHOLD = getattr(settings, 'DOCKER_MAX_USAGE_THRESHOLD', 85)
                
                
                if memory_usage_percent > MAX_USAGE_THRESHOLD:
                    raise DockerServiceException(
                        f"宿主机内存使用率过高: {memory_usage_percent:.1f}% > {MAX_USAGE_THRESHOLD}% "
                        f"(已用 {memory_usage_percent * total_memory_mb / 100:.0f}MB / 总 {total_memory_mb:.0f}MB)"
                    )
                
                
                if cpu_usage_percent > MAX_USAGE_THRESHOLD:
                    raise DockerServiceException(
                        f"宿主机CPU使用率过高: {cpu_usage_percent:.1f}% > {MAX_USAGE_THRESHOLD}% "
                        f"(已用 {cpu_usage_percent * total_cpu / 100:.1f}核 / 总 {total_cpu}核)"
                    )
                
               
                running_containers = UserContainer.objects.filter(
                    status='RUNNING',
                    docker_engine=self.engine,
                    expires_at__gt=timezone.now()
                ).count()
                
                logger.debug(
                    f"Docker资源检查通过: "
                    f"内存使用率={memory_usage_percent:.1f}% ({memory_usage_percent * total_memory_mb / 100:.0f}MB/{total_memory_mb:.0f}MB), "
                    f"CPU使用率={cpu_usage_percent:.1f}% ({cpu_usage_percent * total_cpu / 100:.1f}/{total_cpu}核), "
                    f"运行中容器={running_containers}"
                )
                
            except DockerException as e:
                # Docker API 调用失败，记录警告但放行（避免误杀）
                logger.warning(f"Docker 资源检查失败（放行）: {e}")
    
    
    
    def create_containers(self, challenge, user, flag, memory_limit, cpu_limit):
        with self._get_docker_client() as client:
           
            if challenge.docker_image:
                logger.info(
                    f"使用 DockerImage 模式创建容器: "
                    f"题目={challenge.title}, 用户={user.username}"
                )
                return self._create_image_container(
                    client, challenge, user, flag, memory_limit, cpu_limit
                )
        
            else:
                logger.error(f"题目 {challenge.uuid} 没有配置镜像")
                raise DockerServiceException("题目未配置容器环境，请联系管理员")
    
    def stop_and_remove_container(self, container_id):

        with self._get_docker_client() as client:
            network_name = None
            
            try:
                container = client.containers.get(container_id)
                
                # 获取容器的网络信息（从标签中）
                labels = container.labels or {}  #  防止 labels 为 None
                network_name = labels.get('ctf.network')
                
                logger.info(f"停止容器: {container_id[:12]}")
                container.stop(timeout=10)
                
                logger.info(f"删除容器: {container_id[:12]}")
                container.remove(force=True)
                
                logger.info(f"容器清理完成: {container_id[:12]}")
                
                #  清理关联的网络（只有 network_name 不为空时才清理）
                if network_name and network_name.strip():
                    try:
                        network = client.networks.get(network_name)
                        self._cleanup_network(client, network)
                    except NotFound:
                        logger.debug(f"网络不存在，跳过清理: {network_name}")
                    except Exception as e:
                        logger.warning(f"清理网络失败: {network_name}, 错误: {str(e)}")
                
            except NotFound:
                logger.warning(f"容器不存在，跳过清理: {container_id[:12]}")
                #  只有 network_name 有效时才尝试清理残留网络
                if network_name and network_name.strip():
                    try:
                        network = client.networks.get(network_name)
                        self._cleanup_network(client, network)
                    except Exception as e:
                        logger.debug(f"清理残留网络失败: {network_name}, {str(e)}")
            except Exception as e:
                logger.error(f"清理容器失败: {container_id[:12]}, 错误: {str(e)}")
                raise DockerServiceException(f"容器清理失败: {str(e)}")
    
    def get_container_status(self, container_id):
        with self._get_docker_client() as client:
            try:
                container = client.containers.get(container_id)
                return {
                    'id': container.id,
                    'status': container.status,
                    'name': container.name
                }
            except NotFound:
                return None
            except Exception as e:
                logger.error(f"获取容器状态失败: {str(e)}")
                raise DockerServiceException(f"获取容器状态失败: {str(e)}")
    
    def get_container_metrics(self, container_id):
        
        with self._get_docker_client() as client:
            try:
                container = client.containers.get(container_id)
                stats = container.stats(stream=False)
                
                # 计算 CPU 使用率
                cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - \
                           stats['precpu_stats']['cpu_usage']['total_usage']
                system_delta = stats['cpu_stats']['system_cpu_usage'] - \
                              stats['precpu_stats']['system_cpu_usage']
                cpu_percent = (cpu_delta / system_delta) * 100.0 if system_delta > 0 else 0.0
                
                # 内存使用
                memory_usage = stats['memory_stats'].get('usage', 0)
                memory_limit = stats['memory_stats'].get('limit', 0)
                
                # 网络 I/O
                networks = stats.get('networks', {})
                rx_bytes = sum(net['rx_bytes'] for net in networks.values())
                tx_bytes = sum(net['tx_bytes'] for net in networks.values())
                
                return {
                    'cpu_percent': round(cpu_percent, 2),
                    'memory_usage': memory_usage,
                    'memory_limit': memory_limit,
                    'memory_percent': round((memory_usage / memory_limit * 100), 2) if memory_limit > 0 else 0,
                    'network_rx_bytes': rx_bytes,
                    'network_tx_bytes': tx_bytes
                }
            except NotFound:
                return None
            except Exception as e:
                logger.error(f"获取容器指标失败: {str(e)}")
                raise DockerServiceException(f"获取容器指标失败: {str(e)}")
    
    # ==================== 基于 DockerImage 的容器创建 ====================
    
    def _create_image_container(self, client, challenge, user, flag, memory_limit, cpu_limit):
        
        docker_image = challenge.docker_image
        network = None
        container = None
        
        try:
            self._validate_docker_image(docker_image)
            container_name = self._generate_unique_container_name(challenge, user)
            network_name = f"{container_name}_net"
            
            self._cleanup_existing_container(client, container_name)
            
            network = self._create_isolated_network(client, network_name)
            
            image = self._ensure_image_available(client, docker_image)
            
            container_config = self._build_container_config(
                docker_image=docker_image,
                container_name=container_name,
                network_name=network_name,
                challenge=challenge,
                user=user,
                flag=flag,
                memory_limit=memory_limit,
                cpu_limit=cpu_limit
            )
            
            container = self._create_and_start_container(
                client, container_config, docker_image, flag
            )
            
            
            self._wait_for_container_ready(container, container_name)
            
            container_info = self._extract_container_info(
                container, container_name, container_config['ports']
            )
            
            container_info['network_id'] = network.id
            container_info['network_name'] = network_name
            
            logger.info(
                f" 容器创建成功: "
                f"ID={container.id[:12]}, "
                f"名称={container_name}, "
                f"网络={network_name}, "
                f"端口映射={container_info['ports']}"
            )
            if not container_info['ports']:
                logger.warning(
                    f"容器端口映射为空！请检查 DockerImage 配置。"
                    f"容器ID={container.id[:12]}"
                )
            
            return [container_info], container_info
            
        except Exception as e:
            # 清理失败的资源
            if container:
                self._cleanup_failed_container(container)
            if network:
                self._cleanup_network(client, network)
            raise
    
    def _validate_docker_image(self, docker_image):
        """验证 DockerImage 配置的安全性和可用性"""
        if docker_image.review_status != 'APPROVED':
            logger.warning(f"镜像未审核: {docker_image.id}")
            raise DockerServiceException("镜像未通过安全审核，暂时无法使用")
        
        if not docker_image.is_active:
            logger.warning(f"镜像已禁用: {docker_image.id}")
            raise DockerServiceException("镜像已被禁用")
    
    def _ensure_image_available(self, client, docker_image):
        """确保镜像在本地可用"""
        image_name = docker_image.full_name
        
        try:
            image = client.images.get(image_name)
            logger.debug(f"镜像已存在: {image_name}")
            return image
            
        except docker.errors.ImageNotFound:
          
            logger.warning(f"镜像不存在，尝试拉取: {image_name}")
            
            try:
                image = client.images.pull(image_name)
                
                docker_image.is_pulled = True
                docker_image.image_id = image.id
                docker_image.image_size = image.attrs.get('Size', 0)
                docker_image.last_pulled = timezone.now()
                docker_image.save(update_fields=[
                    'is_pulled', 'image_id', 'image_size', 'last_pulled'
                ])
                
                logger.info(f"镜像拉取成功: {image_name}")
                return image
                
            except Exception as e:
                logger.error(f"镜像拉取失败: {image_name}, 错误: {str(e)}")
                raise DockerServiceException(f"镜像拉取失败: {str(e)}")
    
    def _build_container_config(self, docker_image, container_name, network_name,
                                challenge, user, flag, memory_limit, cpu_limit):
        """构建容器配置"""
        environment = self._prepare_flag_environment(docker_image, challenge, flag)
        
        ports = self._prepare_port_mapping(docker_image)
        
        config = {
            'image': docker_image.full_name,
            'name': container_name,
            'detach': True,
            'environment': environment,
            'ports': ports,
            'mem_limit': f"{memory_limit}m",
            'cpu_quota': int(cpu_limit * 100000),
            'cpu_period': 100000,
            'network': network_name,  # 使用独立网络
            'restart_policy': {'Name': 'no'},
            'labels': {
                'ctf.system': 'secsnow',
                'ctf.user': user.username,
                'ctf.user_id': str(user.id),
                'ctf.challenge': challenge.title,
                'ctf.challenge_uuid': str(challenge.uuid),
                'ctf.image_id': str(docker_image.id),
                'ctf.network': network_name,
                'ctf.created_at': timezone.now().isoformat()
            }
        }
        
        # ==================== 应用安全配置 ====================
        self._apply_security_config(config)
        
        return config
    
    def _apply_security_config(self, config):
        
        if not self.security_config.get('allow_privileged', False):
            config['privileged'] = False
            logger.debug("安全配置: 禁用特权模式")
        else:
            config['privileged'] = True
            logger.warning("安全配置: 启用特权模式（高风险）")
        
        drop_caps = self.security_config.get('drop_capabilities', '')
        if drop_caps:
            caps_list = [cap.strip() for cap in drop_caps.split(',') if cap.strip()]
            if caps_list:
                config['cap_drop'] = caps_list
                logger.debug(f"安全配置: 移除 Capabilities: {', '.join(caps_list)}")
        
       
        security_opt = []
        if self.security_config.get('enable_seccomp', True):
    
            logger.debug("安全配置: 启用 Seccomp（使用默认 profile）")
        else:
            # 禁用 seccomp
            security_opt.append('seccomp=unconfined')
            logger.warning("安全配置: 禁用 Seccomp（降低安全性）")
      
        security_opt.append('no-new-privileges:true')
        config['security_opt'] = security_opt
        
        if self.security_config.get('allow_host_network', False):
            config['network_mode'] = 'host'
            logger.warning("安全配置: 使用宿主机网络（绕过网络隔离）")
       
        
        
        if self.security_config.get('allow_host_pid', False):
            config['pid_mode'] = 'host'
            logger.warning("安全配置: 使用宿主机 PID（可访问宿主机进程）")
        
        # IPC 命名空间
        if self.security_config.get('allow_host_ipc', False):
            config['ipc_mode'] = 'host'
            logger.warning("安全配置: 使用宿主机 IPC（高风险）")
        
       
        
        logger.info(
            f"容器安全配置已应用: "
            f"特权={'允许' if config.get('privileged') else '禁止'}, "
            f"Capabilities移除={len(config.get('cap_drop', []))}, "
            f"Seccomp={'启用' if len([opt for opt in security_opt if 'seccomp=unconfined' in opt]) == 0 else '禁用'}, "
            f"容器隔离={'启用（独立网络）' if self.security_config.get('enable_network_policy') else '禁用'}"
        )
    
    def _prepare_flag_environment(self, docker_image, challenge, flags):
        environment = {}
        
        if isinstance(flags, str):
            flags = [flags]
        elif not flags:
            flags = []
        
        flag_count = len(flags)
        is_multi_flag = flag_count > 1 
        
        if docker_image.flag_inject_method == 'INTERNAL':
        
            if flags:
                environment['SNOW_FLAG'] = flags[0]
            
            if is_multi_flag:
                environment['SNOW_FLAGS'] = ','.join(flags)
                
                environment['SNOW_FLAG_COUNT'] = str(flag_count)
                
                for i, flag in enumerate(flags, start=1):
                    environment[f'SNOW_FLAG_{i}'] = flag
                
                logger.debug(f"使用标准 SNOW_FLAG 环境变量（多flag模式，共{flag_count}个）")
            else:
                logger.debug(f"使用标准 SNOW_FLAG 环境变量（单flag模式）")
            
        elif docker_image.flag_inject_method == 'CUSTOM_ENV':
            if not docker_image.flag_env_name:
                raise DockerServiceException("自定义环境变量未配置变量名")
            
            if flags:
                environment[docker_image.flag_env_name] = flags[0]
            
            if flags:
                environment['SNOW_FLAG'] = flags[0]
            
            if is_multi_flag:
                environment['SNOW_FLAGS'] = ','.join(flags)
                environment['SNOW_FLAG_COUNT'] = str(flag_count)
                
                custom_flags_name = f"{docker_image.flag_env_name}S"
                environment[custom_flags_name] = ','.join(flags)
                
                logger.debug(f"Flag 映射: SNOW_FLAG -> {docker_image.flag_env_name}（多flag模式，共{flag_count}个）")
            else:
                logger.debug(f"Flag 映射: SNOW_FLAG -> {docker_image.flag_env_name}（单flag模式）")
            
        elif docker_image.flag_inject_method == 'SCRIPT':
            if flags:
                environment['SNOW_FLAG'] = flags[0]
            
            if is_multi_flag:
                environment['SNOW_FLAGS'] = ','.join(flags)
                environment['SNOW_FLAG_COUNT'] = str(flag_count)
                logger.debug(f"脚本注入模式（多flag，共{flag_count}个）")
            else:
                logger.debug(f"脚本注入模式（单flag）")
            
        elif docker_image.flag_inject_method == 'NONE':
            if challenge.flag_type == 'DYNAMIC':
                raise DockerServiceException("该镜像不支持动态 Flag")
            logger.debug("使用静态 Flag")
        
        return environment
    
    def _prepare_port_mapping(self, docker_image):
        """准备端口映射"""
        ports = {}
        exposed_ports = docker_image.get_ports_list()
        
        if not exposed_ports:
            raise DockerServiceException("镜像未配置暴露端口")
        
        for port in exposed_ports:
            try:
                port_num = int(port)
                if not (1 <= port_num <= 65535):
                    raise ValueError(f"端口 {port_num} 超出有效范围")
                ports[f'{port}/tcp'] = None
            except ValueError as e:
                raise DockerServiceException(f"无效的端口配置: {str(e)}")
        
        return ports
    
    def _create_and_start_container(self, client, config, docker_image, flag):
        """创建并启动容器"""
        container = None
        
        try:
            # 创建容器
            logger.debug(f"创建容器: {config['name']}")
            container = client.containers.create(**config)
            
            # 启动容器
            logger.debug(f"启动容器: {container.id[:12]}")
            container.start()
            
            # 执行 Flag 注入脚本（如果需要）
            if docker_image.flag_inject_method == 'SCRIPT':
                self._inject_flag_by_script(container, docker_image, flag)
            
            return container
            
        except Exception as e:
            logger.error(f"容器创建失败: {str(e)}")
            if container:
                self._cleanup_failed_container(container)
            raise DockerServiceException(f"容器创建失败: {str(e)}")
    
    def _inject_flag_by_script(self, container, docker_image, flags):
        """通过脚本注入 Flag - 支持多段flag"""
        if not docker_image.flag_script:
            raise DockerServiceException("脚本注入方式未配置脚本内容")
        
        # 统一处理flag格式：确保是列表
        if isinstance(flags, str):
            flags = [flags]
        elif not flags:
            flags = []
        
        main_flag = flags[0] if flags else ''  
        all_flags = ','.join(flags)  
        
        script = docker_image.flag_script
        
        for i, flag in enumerate(flags, start=1):
            script = script.replace(f'${{SNOW_FLAG_{i}}}', flag)
            script = script.replace(f'$SNOW_FLAG_{i}', flag)
        
        script = script.replace('${SNOW_FLAGS}', all_flags)
        script = script.replace('$SNOW_FLAGS', all_flags)
        script = script.replace('{SNOW_FLAGS}', all_flags)
        
        script = script.replace('${SNOW_FLAG}', main_flag)
        script = script.replace('$SNOW_FLAG', main_flag)
        script = script.replace('{SNOW_FLAG}', main_flag)
        script = script.replace('{flag}', main_flag)
        
        logger.debug(f"执行 Flag 注入脚本: {script[:100]}...")
        
        # 等待容器启动
        time.sleep(1)
        
        try:
            import signal
            import threading
            
            result_container = [None]
            error_container = [None]
            
            def execute_script():
                try:
                    result_container[0] = container.exec_run(
                        ['/bin/sh', '-c', script],
                        user='root',
                        privileged=True,
                        demux=True
                    )
                except Exception as e:
                    error_container[0] = e
            
            exec_thread = threading.Thread(target=execute_script)
            exec_thread.start()
            exec_thread.join(timeout=30)  # 30秒超时
            
            if exec_thread.is_alive():
                logger.error("Flag 脚本执行超时（30秒）")
                raise DockerServiceException("Flag 注入脚本执行超时，请检查脚本是否有死循环")
            
            if error_container[0]:
                raise error_container[0]
            
            result = result_container[0]
            if not result:
                raise DockerServiceException("Flag 脚本执行失败")
            
            if result.exit_code != 0:
                error_msg = (
                    result.output[1].decode('utf-8', errors='ignore')
                    if result.output[1] else "未知错误"
                )
                logger.error(f"Flag 脚本执行失败: {error_msg}")
                raise DockerServiceException("Flag 注入失败")
            
            logger.info(f"Flag 脚本注入成功")
            
        except Exception as e:
            logger.error(f"执行 Flag 脚本异常: {str(e)}")
            raise DockerServiceException(f"Flag 注入失败: {str(e)}")
    
    def _wait_for_container_ready(self, container, container_name):
        """等待容器启动完成"""
        max_retries = 30
        retry_interval = 1
        
        for attempt in range(max_retries):
            try:
                container.reload()
                
                if container.status == 'running':
                    logger.debug(f"容器启动成功: {container_name}")
                    return True
                
                elif container.status == 'exited':
                    exit_code = container.attrs['State']['ExitCode']
                    
                    # 尝试获取多种诊断信息
                    error_info = []
                    
                    # 1. 获取容器日志（stdout + stderr）
                    try:
                        logs = container.logs(stdout=True, stderr=True, tail=100).decode('utf-8', errors='ignore')
                        if logs.strip():
                            error_info.append(f"容器日志:\n{logs}")
                        else:
                            error_info.append("容器日志: (空)")
                    except Exception as e:
                        error_info.append(f"无法获取日志: {str(e)}")
                    
                    # 2. 获取容器状态信息
                    try:
                        state = container.attrs.get('State', {})
                        if state.get('Error'):
                            error_info.append(f"状态错误: {state['Error']}")
                        if state.get('OOMKilled'):
                            error_info.append("容器因内存不足被杀死 (OOM)")
                        error_info.append(f"启动时间: {state.get('StartedAt', 'unknown')}")
                        error_info.append(f"退出时间: {state.get('FinishedAt', 'unknown')}")
                    except Exception as e:
                        error_info.append(f"无法获取状态: {str(e)}")
                    
                    # 3. 获取容器配置（用于排查启动命令问题）
                    try:
                        config = container.attrs.get('Config', {})
                        cmd = config.get('Cmd', [])
                        entrypoint = config.get('Entrypoint', [])
                        error_info.append(f"镜像: {config.get('Image', 'unknown')}")
                        if entrypoint:
                            error_info.append(f"Entrypoint: {' '.join(entrypoint) if isinstance(entrypoint, list) else entrypoint}")
                        if cmd:
                            error_info.append(f"Cmd: {' '.join(cmd) if isinstance(cmd, list) else cmd}")
                    except Exception as e:
                        error_info.append(f"无法获取配置: {str(e)}")
                    
                    error_message = "\n".join(error_info)
                    
                    logger.error(
                        f"容器异常退出: {container_name}\n"
                        f"退出码: {exit_code}\n"
                        f"{error_message}"
                    )
                    
                    # 返回详细的错误信息给用户
                    raise DockerServiceException(
                        f"容器启动失败（退出码: {exit_code}），请检查您的镜像是否可用\n"
                    )
                
            except DockerServiceException:
                raise
            except Exception as e:
                logger.warning(f"检查容器状态失败: {str(e)}")
            
            time.sleep(retry_interval)
        
        # 超时
        logs = container.logs(tail=50).decode('utf-8', errors='ignore')
        logger.error(f"容器启动超时: {container_name}, 日志={logs[:200]}")
        raise DockerServiceException("容器启动超时")
    
    def _extract_container_info(self, container, container_name, ports):
        """提取容器信息"""
        # 重新加载容器信息以获取最新的端口映射
        container.reload()
        
        container_info = {
            'id': container.id,
            'name': container_name,
            'type': 'web',
            'ports': {}
        }
        
        if ports:
            container_ports = container.attrs['NetworkSettings']['Ports']
            logger.debug(f"容器端口映射信息: {container_ports}")
            
            for port_spec in ports:
                if port_spec in container_ports and container_ports[port_spec]:
                    host_port = int(container_ports[port_spec][0]['HostPort'])
                    port_num = port_spec.replace('/tcp', '')
                    container_info['ports'][port_num] = host_port
                    logger.debug(f"提取端口映射: {port_num} -> {host_port}")
                else:
                    logger.warning(f"端口 {port_spec} 未映射到宿主机")
        
        #  验证端口映射是否成功
        if not container_info['ports']:
            logger.error(
                f"容器端口映射失败: {container_name}\n"
                f"期望端口: {ports}\n"
                f"实际映射: {container_ports if ports else 'N/A'}"
            )
            raise DockerServiceException(
                "容器端口映射失败，无法访问容器。请检查 Docker 引擎配置或联系管理员。"
            )
        
        return container_info
    
   
    
    def _create_isolated_network(self, client, network_name):
        try:
            try:
                network = client.networks.get(network_name)
                
                network.reload()
                connected_containers = network.attrs.get('Containers', {})
                
                if connected_containers:
                   
                    logger.warning(
                        f"网络 {network_name} 已有 {len(connected_containers)} 个容器连接，"
                        f"为确保隔离性，将删除并重建"
                    )
                    try:
                        network.remove()
                        logger.info(f"✓ 已删除旧网络: {network_name}")
                    except Exception as e:
                        logger.warning(f"删除旧网络失败，将使用新名称: {e}")
                        network_name = f"{network_name}_{uuid.uuid4().hex[:6]}"
                        logger.info(f"使用新网络名称: {network_name}")
                else:
                    logger.info(f"网络已存在且为空，将重用: {network_name}")
                    return network
                    
            except NotFound:
                pass
            
            network = client.networks.create(
                name=network_name,
                driver='bridge',
                internal=False, 
                labels={
                    'ctf.system': 'secsnow',
                    'ctf.isolated': 'true',
                    'ctf.network_policy': 'container_isolation'
                }
            )
            
            logger.info(
                f" 创建独立网络: {network_name} (ID: {network.id[:12]}), "
                f"容器隔离: 已启用（每个容器使用独立网络）, "
                f"端口映射: 已启用"
            )
            return network
            
        except Exception as e:
            logger.error(f"创建网络失败: {network_name}, 错误: {str(e)}")
            raise DockerServiceException(f"创建网络失败: {str(e)}")
    
    def _cleanup_network(self, client, network):
        """清理 Docker 网络"""
        try:
            if network:
                network_name = network.name
                logger.info(f"清理网络: {network_name}")
                network.remove()
                logger.info(f"网络清理完成: {network_name}")
        except NotFound:
            logger.warning("网络不存在，跳过清理")
        except Exception as e:
            logger.warning(f"清理网络时出错: {str(e)}")
    
    def _get_docker_client(self):
        if hasattr(self, 'engine') and self.engine:
            try:
                pool = DockerConnectionPool.get_pool(self.engine)
                return pool.get_connection(timeout=10)
            except Exception as e:
                logger.warning(f"使用连接池失败，降级到直接连接: {e}", exc_info=True)
        
        class DockerClientContext:
            def __init__(self, url, tls_config, timeout):
                self.url = url
                self.tls_config = tls_config
                self.timeout = timeout
                self.client = None
            
            def __enter__(self):
                self.client = docker.DockerClient(
                    base_url=self.url,
                    tls=self.tls_config,
                    timeout=self.timeout
                )
                return self.client
            
            def __exit__(self, exc_type, exc_val, exc_tb):
                if self.client:
                    self.client.close()
        
        return DockerClientContext(self.url, self.tls_config, self.timeout)
    
    def _generate_unique_container_name(self, challenge, user):
        
        title_pinyin = ''.join(lazy_pinyin(challenge.title))
        username_pinyin = ''.join(lazy_pinyin(user.username))
        
        
        title_clean = re.sub(r'[^a-zA-Z0-9_.-]', '_', title_pinyin)
        username_clean = re.sub(r'[^a-zA-Z0-9_.-]', '_', username_pinyin)
        
        random_suffix = uuid.uuid4().hex[:8]
        
        name = f"{title_clean}_{username_clean}_{random_suffix}".lower()
        
        if len(name) > 63:
            name = name[:55] + '_' + random_suffix
        
        return name
    
    def _cleanup_existing_container(self, client, container_name):
        """清理已存在的同名容器及其网络"""
        try:
            container = client.containers.get(container_name)
            logger.info(f"发现同名容器，清理中: {container_name}")
            
            # 获取容器的网络信息
            network_name = container.labels.get('ctf.network')
            
            try:
                container.stop(timeout=5)
            except Exception as e:
                logger.warning(f"停止容器失败: {str(e)}")
            
            container.remove(force=True)
            logger.info(f"已清理同名容器: {container_name}")
            
            if network_name:
                try:
                    network = client.networks.get(network_name)
                    self._cleanup_network(client, network)
                except NotFound:
                    pass
                except Exception as e:
                    logger.warning(f"清理网络失败: {network_name}, 错误: {str(e)}")
            
        except NotFound:
           
            pass
        except Exception as e:
            logger.error(f"清理容器失败: {container_name}, 错误: {str(e)}")
            raise DockerServiceException(f"清理容器失败: {str(e)}")
    
    def _cleanup_failed_container(self, container):
        """清理失败的容器及其关联资源"""
        container_id = container.id[:12] if container.id else 'unknown'
        network_name = None
        
        try:
           
            container.reload()
            network_name = container.labels.get('ctf.network')
            
            container.stop(timeout=5)
            container.remove(force=True)
            logger.info(f"✓ 已清理失败容器: {container_id}")
            
            #  清理关联的网络
            if network_name:
                try:
                    with self._get_docker_client() as client:
                        network = client.networks.get(network_name)
                        self._cleanup_network(client, network)
                        logger.info(f"✓ 已清理失败容器的网络: {network_name}")
                except NotFound:
                    logger.debug(f"网络不存在，跳过: {network_name}")
                except Exception as e:
                    logger.warning(f"清理网络失败: {network_name}, 错误: {str(e)}")
                    
        except Exception as e:
            logger.warning(f"清理失败容器时出错: {container_id}, 错误: {str(e)}")
    
    def close(self):
        logger.debug(f"DockerService.close() 调用（连接由连接池自动管理）")
        pass
    
    @staticmethod
    def get_docker_url(docker_engine):
        if docker_engine.host_type == 'LOCAL':
            return 'unix:///var/run/docker.sock'
        else:
            return f"tcp://{docker_engine.host}:{docker_engine.port}"

def create_docker_service(docker_engine):
    docker_url = DockerService.get_docker_url(docker_engine)
    
    tls_config = None
    if docker_engine.tls_enabled:
        try:
            tls_config = docker.tls.TLSConfig(
                client_cert=(
                    docker_engine.client_cert_path,
                    docker_engine.client_key_path
                ),
                ca_cert=docker_engine.ca_cert_path,
                verify=True
            )
        except Exception as e:
            logger.error(f"创建 TLS 配置失败: {str(e)}")
            raise DockerServiceException(f"TLS 配置错误: {str(e)}")
    
    return DockerService(url=docker_url, tls_config=tls_config)


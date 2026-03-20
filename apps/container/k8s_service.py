
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream
from .container_service_base import ContainerServiceBase, ContainerServiceException
from .k8s_client_pool import K8sClientPool, K8sNamespaceManager
from .k8s_resource_monitor import K8sResourceMonitor
from container.models import ContainerEngineConfig
from django.utils import timezone
from django.core.cache import cache
import time
import uuid
import re
import logging
import urllib3
import json
from datetime import timezone as dt_timezone

logger = logging.getLogger("apps.container")

def _get_connection_pool_config():
    """获取连接池配置（从数据库）"""
    config = ContainerEngineConfig.get_config()
    return {
        'maxsize': config.k8s_connection_pool_maxsize,
        'block': config.k8s_connection_pool_block
    }

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
_http_pool_manager = None

def _get_http_pool_manager():
    global _http_pool_manager
    if _http_pool_manager is None:
        pool_config = _get_connection_pool_config()
        _http_pool_manager = urllib3.PoolManager(
            num_pools=10,
            maxsize=pool_config['maxsize'],
            block=pool_config['block'],
            retries=urllib3.Retry(
                total=3,
                backoff_factor=0.3,
                status_forcelist=[500, 502, 503, 504]
            )
        )
    return _http_pool_manager



class K8sServiceException(ContainerServiceException):
    """K8s 服务异常"""
    pass


class K8sService(ContainerServiceBase):
    
    
    def __init__(self, engine, team_namespace=None):
        
        self.engine = engine
        self.kubeconfig_path = engine.kubeconfig_file_path
      
        self.namespace = team_namespace or engine.namespace or 'ctf-challenges'
        self.is_awd_mode = bool(team_namespace)  # 标识是否为AWD模式
        self.verify_ssl = engine.verify_ssl
        
        
        self.enable_network_policy = engine.enable_network_policy
        self.enable_seccomp = engine.enable_seccomp
        self.enable_service_account = engine.enable_service_account
        self.allow_privileged = engine.allow_privileged
        self.allow_host_network = engine.allow_host_network
        self.allow_host_pid = engine.allow_host_pid
        self.allow_host_ipc = engine.allow_host_ipc
        self.config = ContainerEngineConfig.get_config()
        self.drop_capabilities = [cap.strip() for cap in engine.drop_capabilities.split(',') if cap.strip()] if engine.drop_capabilities else []
        
        try:
            pool_config = _get_connection_pool_config()
            self.core_api, self.apps_api, self.networking_api = K8sClientPool.get_clients(
                kubeconfig_path=self.kubeconfig_path,
                verify_ssl=self.verify_ssl,
                connection_pool_maxsize=pool_config['maxsize']
            )
            logger.debug(f"使用 K8s 客户端池: {self.namespace}")
                
        except Exception as e:
            logger.error(f"获取 K8s API 客户端失败: {str(e)}")
            raise K8sServiceException(f"K8s 配置加载失败: {str(e)}")
        
        K8sNamespaceManager.ensure_namespace(self.core_api, self.namespace)
        
        self.resource_monitor = K8sResourceMonitor(self.core_api, self.namespace)
        
        policy_cache_key = f'k8s:network_policy:{self.namespace}:{self.enable_network_policy}:{self.is_awd_mode}'
        policy_ensured = cache.get(policy_cache_key)
        
        if not policy_ensured:
            if self.enable_network_policy:
                if self.is_awd_mode:
                    self._ensure_awd_network_policies()
                    
                else:
                    self._ensure_network_policies()
                    
            else:
               
                try:
                    result = self.remove_network_policies()
                    if result['deleted_policies']:
                        logger.info(f"已删除网络策略（因配置已关闭）: {', '.join(result['deleted_policies'])}")
                    elif result.get('errors'):
                        logger.warning(f"删除网络策略时遇到错误: {result['errors']}")
                except Exception as e:
                    logger.warning(f"删除网络策略失败（可能不存在）: {str(e)}")
            
            cache.set(policy_cache_key, True, timeout=300)
        else:
            logger.debug(f" 使用缓存的网络策略状态: {self.namespace}")
    
    # ==================== 核心方法 ====================
    
    def create_containers(self, challenge, user, flag, memory_limit, cpu_limit, target_node=None):

        self._target_node = target_node

        if hasattr(challenge, 'network_topology_config') and challenge.network_topology_config:
            logger.info(f"检测到网络拓扑配置，创建多容器场景: {challenge.network_topology_config.name}")
            return self._create_topology_containers(
                challenge=challenge,
                user=user,
                flag=flag,
                memory_limit=memory_limit,
                cpu_limit=cpu_limit,
                target_node=target_node
            )

        docker_image = challenge.docker_image
        
        if not docker_image:
            logger.error(f"题目 {challenge.uuid} 没有配置镜像或编排")
            raise K8sServiceException("题目未配置容器环境")
        service = None
        pod_name = None
        try:
            self._validate_docker_image(docker_image)
            logger.debug("高并发模式：跳过创建时清理，由定时任务负责")
            
            target_node = getattr(self, '_target_node', None)
            
            if not target_node:
                logger.error(
                    "致命错误：未收到目标节点！这说明 views.py 的预检没有正确传递 target_node。"
                    "请检查 views.py 是否正确设置了 selected_node 变量。"
                )
                raise K8sServiceException(
                    "容器创建失败：缺少目标节点信息（内部错误）"
                )
            
            logger.info(f" 使用预选节点: {target_node}")
            
            pod_name = self._generate_pod_name(challenge, user)
            
            env_vars = self._prepare_flag_environment(docker_image, challenge, flag)
            
            pod_manifest = self._build_pod_manifest(
                pod_name=pod_name,
                docker_image=docker_image,
                env_vars=env_vars,
                challenge=challenge,
                user=user,
                memory_limit=memory_limit,
                cpu_limit=cpu_limit,
                flags=flag, 
                target_node=target_node  
            )
            
            pod = self.core_api.create_namespaced_pod(
                namespace=self.namespace,
                body=pod_manifest
            )
            
            service = None
            try:
                self._wait_for_pod_ready(pod_name)
                
                service = self._create_service(pod_name, docker_image, challenge, user)
                
              
                pod = self.core_api.read_namespaced_pod(
                    name=pod_name,
                    namespace=self.namespace
                )
                
                container_info = self._extract_container_info(pod, service, pod_name)
                
                logger.info(
                    f"Pod 创建成功: {pod_name}, "
                    f"Service={service.metadata.name}, 端口={container_info['ports']}, "
                    f"节点={pod.spec.node_name}"
                )
                
           
                
                return [container_info], container_info
                
            except Exception as e:
             
                logger.error(f"Pod/Service创建失败，开始清理资源: {pod_name}")
                
       
                if service:
                    try:
                        service_name = service.metadata.name
                        self.core_api.delete_namespaced_service(
                            name=service_name,
                            namespace=self.namespace
                        )
                        logger.info(f" 已清理失败的 Service: {service_name}")
                    except ApiException as cleanup_err:
                        if cleanup_err.status == 404:
                            logger.debug(f"Service 已不存在（可能已被清理）: {service_name}")
                        else:
                            logger.warning(f"清理 Service 失败: {cleanup_err.reason}")
                    except Exception as cleanup_err:
                        logger.warning(f"清理 Service 失败: {cleanup_err}")
                
           
                try:
                    self.core_api.delete_namespaced_pod(
                        name=pod_name,
                        namespace=self.namespace,
                        grace_period_seconds=0  
                    )
                    logger.info(f" 已清理失败的 Pod: {pod_name}")
                except ApiException as cleanup_err:
                    if cleanup_err.status == 404:
                        logger.debug(f"Pod 已不存在（可能已在检测阶段清理）: {pod_name}")
                    else:
                        logger.warning(f"清理 Pod 失败: {cleanup_err.reason}")
                except Exception as cleanup_err:
                    logger.warning(f"清理 Pod 失败: {cleanup_err}")
              
                raise
            
        except ApiException as e:
        
            error_msg = f"Pod 创建失败: {e.reason} (状态码: {e.status})"
          
            if e.status == 404:
              
                try:
                    self.core_api.read_namespace(name=self.namespace)
                   
                    error_msg = f"容器创建失败: 引用的资源不存在 - {e.reason}"
                    logger.error(error_msg)
                except ApiException as ns_err:
                    if ns_err.status == 404:
                       
                        error_msg = f"容器创建失败: 命名空间 '{self.namespace}' 不存在（缓存已过期）"
                        logger.error(f"{error_msg}")
             
                        cache.delete(f'k8s:namespace_exists:{self.namespace}')
                        logger.info(f"已清除命名空间缓存: {self.namespace}，请重试")
                    else:
                        error_msg = "容器创建失败,请稍后再试"
                        logger.error(f"Pod 创建失败: {e.reason}")
            else:
          
                logger.error(f"Pod 创建失败: {error_msg}")
                error_msg = "容器创建失败,请稍后再试"
            
            raise K8sServiceException(error_msg)
        except K8sServiceException:
    
            raise
        except Exception as e:
            logger.error(f"创建 Pod 失败: {str(e)}", exc_info=True)
            raise K8sServiceException(f"容器创建失败,请稍后再试")
    
    def stop_and_remove_container(self, container_id):
      
        try:
        
            if isinstance(container_id, str) and container_id.startswith('topology-'):
                topology_config_id = container_id.replace('topology-', '')
                self._cleanup_topology_containers(topology_config_id)
                return
            
            pod_name = container_id
            service_name = f"{pod_name}-svc"
            
            pod_existed = False
            service_existed = False
            service_delete_error = None
            
    
            logger.info(f"删除 Pod: {pod_name}")
            try:
                self.core_api.delete_namespaced_pod(
                    name=pod_name,
                    namespace=self.namespace,
                    grace_period_seconds=10
                )
                pod_existed = True
            except ApiException as e:
                if e.status == 404:
                    logger.debug(f"Pod 不存在（可能已被清理）: {pod_name}")
                else:
                    raise
            

            logger.debug(f"删除 Service: {service_name}")
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    self.core_api.delete_namespaced_service(
                        name=service_name,
                        namespace=self.namespace
                    )
                    service_existed = True
                    logger.info(f" Service 删除成功: {service_name}")
                    break
                except ApiException as e:
                    if e.status == 404:
                        logger.debug(f"Service 不存在（可能已被清理）: {service_name}")
                        break
                    elif attempt < max_retries - 1:
                        logger.warning(f"删除 Service 失败，重试 {attempt + 1}/{max_retries}: {e.reason}")
                        import time
                        time.sleep(1)
                    else:
                        service_delete_error = e.reason
                        logger.error(f"删除 Service 失败（已重试{max_retries}次）: {service_name} - {e.reason}")
                except Exception as e:
                    service_delete_error = str(e)
                    logger.error(f"删除 Service 异常: {service_name} - {str(e)}")
                    break
            

            netpol_existed = False
            netpol_name = f"{pod_name}-netpol" 
            legacy_netpol_name = f"{pod_name}-egress" 
            
            for policy_name in [netpol_name, legacy_netpol_name]:
                try:
                    self.networking_api.delete_namespaced_network_policy(
                        name=policy_name,
                        namespace=self.namespace
                    )
                    netpol_existed = True
                    logger.info(f" NetworkPolicy 删除成功: {policy_name}")
                except ApiException as e:
                    if e.status == 404:
                        logger.debug(f"NetworkPolicy 不存在: {policy_name}")
                    else:
                        logger.warning(f"删除 NetworkPolicy 失败: {policy_name} - {e.reason}")
                except Exception as e:
                    logger.warning(f"删除 NetworkPolicy 异常: {policy_name} - {str(e)}")

            if pod_existed or service_existed or netpol_existed:
                if service_delete_error:
                    logger.warning(f"Pod 清理完成但 Service 删除失败: {pod_name} (Service错误: {service_delete_error})")
                else:
                    logger.info(f" Pod、Service 和 NetworkPolicy 清理完成: {pod_name}")
            else:
                logger.debug(f"Pod、Service 和 NetworkPolicy 已不存在，无需清理: {pod_name}")

            if service_delete_error:
                self._mark_orphan_service(service_name, pod_name)
            
        except ApiException as e:
            if e.status == 404:
                logger.debug(f"资源不存在，跳过清理: {container_id}")
            else:
                logger.error(f"清理 Pod 失败: {e.reason}")
                raise K8sServiceException(f"Pod 清理失败: {e.reason}")
        except Exception as e:
            logger.error(f"清理 Pod 异常: {str(e)}")
            raise K8sServiceException(f"Pod 清理失败: {str(e)}")
    
    def get_container_status(self, container_id: str) -> str:
        
        try:
            pod = self.core_api.read_namespaced_pod(
                name=container_id,
                namespace=self.namespace
            )

            phase = pod.status.phase
            if phase == 'Running':
                return 'RUNNING'
            elif phase in ['Pending', 'ContainerCreating']:
                return 'STARTING'
            elif phase in ['Succeeded', 'Failed']:
                return 'STOPPED'
            else:
                return 'UNKNOWN'
        except ApiException as e:
            if e.status == 404:
           
                logger.debug(f"Pod 不存在: {container_id}")
                return 'NOT_FOUND'
            logger.warning(f"获取 Pod 状态失败: {container_id}, 错误: {e.reason}")
            return 'UNKNOWN'
        except Exception as e:
            logger.warning(f"获取容器状态异常: {container_id}, 错误: {str(e)}")
            return 'UNKNOWN'
    
    def get_container_details(self, container_id: str) -> dict:
        """
        获取 Pod 详细状态（增强版）
        
        Returns:
            dict: 包含详细状态、资源使用、事件等信息
        """
        try:
            pod = self.core_api.read_namespaced_pod(
                name=container_id,
                namespace=self.namespace
            )

            details = {
                'id': pod.metadata.name,
                'name': pod.metadata.name,
                'namespace': pod.metadata.namespace,
                'phase': pod.status.phase,
                'created_at': pod.metadata.creation_timestamp.isoformat() if pod.metadata.creation_timestamp else None,
                'labels': pod.metadata.labels or {},
                'ip': pod.status.pod_ip,
                'node': pod.spec.node_name,
            }

            if pod.status.container_statuses:
                container_status = pod.status.container_statuses[0]
                details['container'] = {
                    'ready': container_status.ready,
                    'restart_count': container_status.restart_count,
                    'image': container_status.image,
                    'image_id': container_status.image_id,
                }

                if container_status.state:
                    if container_status.state.running:
                        details['container']['state'] = 'running'
                        details['container']['started_at'] = container_status.state.running.started_at.isoformat() if container_status.state.running.started_at else None
                    elif container_status.state.waiting:
                        details['container']['state'] = 'waiting'
                        details['container']['reason'] = container_status.state.waiting.reason
                        details['container']['message'] = container_status.state.waiting.message
                    elif container_status.state.terminated:
                        details['container']['state'] = 'terminated'
                        details['container']['reason'] = container_status.state.terminated.reason
                        details['container']['exit_code'] = container_status.state.terminated.exit_code
                        details['container']['finished_at'] = container_status.state.terminated.finished_at.isoformat() if container_status.state.terminated.finished_at else None

            try:
                events = self.core_api.list_namespaced_event(
                    namespace=self.namespace,
                    field_selector=f"involvedObject.name={container_id}"
                )
                details['events'] = [
                    {
                        'type': event.type,
                        'reason': event.reason,
                        'message': event.message,
                        'timestamp': event.last_timestamp.isoformat() if event.last_timestamp else None
                    }
                    for event in events.items[-5:]  # 最近 5 个事件
                ]
            except Exception as e:
                logger.warning(f"获取 Pod 事件失败: {str(e)}")
                details['events'] = []
            
            return details
            
        except ApiException as e:
            if e.status == 404:
                return {'status': 'NOT_FOUND', 'message': 'Pod 不存在'}
            logger.error(f"获取 Pod 详细状态失败: {e.reason}")
            raise K8sServiceException(f"获取 Pod 详细状态失败: {e.reason}")
        except Exception as e:
            logger.error(f"获取容器详细状态失败: {str(e)}")
            raise K8sServiceException(f"获取容器详细状态失败: {str(e)}")
    
    def get_container_metrics(self, container_id: str) -> dict:
      
        metrics_data = {
            'cpu_usage': 0,
            'cpu_percent': 0,
            'memory_usage': 0,
            'memory_limit': 0,
            'memory_percent': 0,
            'network_rx_bytes': 0,
            'network_tx_bytes': 0,
            'available': False,
            'source': None
        }

        try:
            custom_api = client.CustomObjectsApi()
            metrics = custom_api.get_namespaced_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                namespace=self.namespace,
                plural="pods",
                name=container_id
            )
            
            if metrics.get('containers'):
                container = metrics['containers'][0]
                cpu_usage_str = container['usage'].get('cpu', '0')
                memory_usage_str = container['usage'].get('memory', '0')

                metrics_data['cpu_usage'] = self._parse_cpu(cpu_usage_str)

                metrics_data['memory_usage'] = self._parse_memory(memory_usage_str)
                
                metrics_data['available'] = True
                metrics_data['source'] = 'metrics-server'

                try:
                    pod = self.core_api.read_namespaced_pod(
                        name=container_id,
                        namespace=self.namespace
                    )
                    if pod.spec.containers:
                        container_spec = pod.spec.containers[0]
                        if container_spec.resources and container_spec.resources.limits:
                            # 内存限制
                            memory_limit = container_spec.resources.limits.get('memory')
                            if memory_limit:
                                metrics_data['memory_limit'] = self._parse_memory(memory_limit)
                                if metrics_data['memory_limit'] > 0:
                                    metrics_data['memory_percent'] = round(
                                        (metrics_data['memory_usage'] / metrics_data['memory_limit']) * 100, 2
                                    )
                            
                            
                            cpu_limit = container_spec.resources.limits.get('cpu')
                            if cpu_limit:
                                cpu_limit_millicores = self._parse_cpu(cpu_limit)
                                if cpu_limit_millicores > 0:
                                    metrics_data['cpu_percent'] = round(
                                        (metrics_data['cpu_usage'] / cpu_limit_millicores) * 100, 2
                                    )
                except Exception as e:
                    logger.debug(f"获取资源限制失败: {str(e)}")
                
                logger.debug(f"从 Metrics Server 获取到 Pod {container_id} 的指标")
                
        except ApiException as e:
            if e.status == 404:
                logger.warning(f"Metrics Server 未部署或 Pod {container_id} 不存在")
                metrics_data['source'] = 'not-available'
            else:
                logger.warning(f"获取 Metrics Server 指标失败: {e.reason}")
                metrics_data['source'] = 'error'
        except Exception as e:
            logger.warning(f"获取 Pod 指标失败: {str(e)}")
            metrics_data['source'] = 'error'
        
        # 2. 尝试获取网络流量（通过 exec 读取 /proc/net/dev）
        if metrics_data['available']:
            try:
                network_stats = self._get_network_stats(container_id)
                if network_stats:
                    metrics_data['network_rx_bytes'] = network_stats.get('rx_bytes', 0)
                    metrics_data['network_tx_bytes'] = network_stats.get('tx_bytes', 0)
            except Exception as e:
                logger.debug(f"获取网络流量失败: {str(e)}")
        
        return metrics_data
        
        return {}
    

    
    def _create_topology_containers(self, challenge, user, flag, memory_limit, cpu_limit, target_node=None):
       
        from container.models import DockerImage
        
        topology_config = challenge.network_topology_config
        created_pods = []
        created_services = []
        web_container = None
        
        try:
            # 1. 从 JSON 中获取所有节点配置
            if not topology_config.topology_data:
                logger.error(f"拓扑配置 {topology_config.name} 的 topology_data 为空")
                raise K8sServiceException("网络拓扑配置为空，请在可视化编辑器中添加节点并保存")
            
            logger.debug(f"拓扑数据结构: {list(topology_config.topology_data.keys())}")
            
            elements = topology_config.topology_data.get('elements', {})
            if not elements:
                logger.error(f"拓扑配置 {topology_config.name} 中没有 'elements' 字段")
                logger.debug(f"完整数据: {topology_config.topology_data}")
                raise K8sServiceException("网络拓扑配置格式错误（缺少 elements 字段），请重新保存拓扑")
            
            nodes_data = elements.get('nodes', [])
            
            if not nodes_data:
                logger.error(f"拓扑配置 {topology_config.name} 中没有节点")
                logger.debug(f"elements 内容: {elements}")
                raise K8sServiceException("网络拓扑配置中没有节点，请在可视化编辑器中添加节点并保存")
            
            logger.info(f"开始创建拓扑场景: {topology_config.name}, 共 {len(nodes_data)} 个节点")

            pod_name_mapping = {} 
            node_info_mapping = {}  
            all_containers = []
            
            for node_element in nodes_data:
                node_data = node_element.get('data', {})
                node_id = node_data.get('id')
                image_id = node_data.get('imageId')
                label = node_data.get('label', '')
                network_area = node_data.get('networkArea', 'INTERNAL')
                is_entry_point = node_data.get('isEntryPoint', False)
                is_target = node_data.get('isTarget', False)
                protocol = node_data.get('protocol', 'http') 
     
                network_policy = node_data.get('networkPolicy', 'ISOLATED')  # 默认隔离外网

                if network_policy == 'INTERNAL_ONLY':
                    network_policy = 'ISOLATED'
                allow_reverse_shell = node_data.get('allowReverseShell', False)
                egress_whitelist = node_data.get('egressWhitelist', '')
                
                if not image_id:
                    logger.warning(f"节点 {node_id} 没有关联镜像，跳过")
                    continue
                
                # 获取镜像对象
                try:
                    docker_image = DockerImage.objects.get(id=image_id)
                except DockerImage.DoesNotExist:
                    logger.warning(f"节点 {node_id} 的镜像 ID {image_id} 不存在，跳过")
                    continue
                
                self._validate_docker_image(docker_image)
                
                # 生成 Pod 名称
                pod_name = self._generate_topology_pod_name(challenge, user, node_id)
                pod_name_mapping[node_id] = pod_name
                
                # 收集节点信息（用于网络策略创建）
                node_info_mapping[node_id] = {
                    'is_entry_point': is_entry_point,
                    'is_target': is_target,
                    'network_area': network_area,
                    'label': label,
                    #  网络策略配置
                    'network_policy': network_policy,
                    'allow_reverse_shell': allow_reverse_shell,
                    'egress_whitelist': egress_whitelist
                }
                
 
                if is_target:
                    env_vars = self._prepare_flag_environment(docker_image, challenge, flag)
                    logger.debug(f"节点 {node_id} 是攻击目标，注入 flag")
                else:
                    env_vars = {}
                    logger.debug(f"节点 {node_id} 不是攻击目标，不注入 flag")
                
                # 添加拓扑相关的环境变量
                env_vars.update({
                    'TOPOLOGY_NODE_ID': node_id,
                    'TOPOLOGY_NODE_LABEL': label,
                    'TOPOLOGY_NETWORK_AREA': network_area,
                    'TOPOLOGY_IS_ENTRY': str(is_entry_point).lower(),
                    'TOPOLOGY_IS_TARGET': str(is_target).lower(),
                })

                node_memory_limit = docker_image.memory_limit or 512
                node_cpu_limit = docker_image.cpu_limit or 1.0

                pod_manifest = self._build_pod_manifest(
                    pod_name=pod_name,
                    docker_image=docker_image,
                    env_vars=env_vars,
                    challenge=challenge,
                    user=user,
                    memory_limit=node_memory_limit, 
                    cpu_limit=node_cpu_limit,       
                    flags=flag if is_target else None,  
                    target_node=target_node
                )
                
                # 添加拓扑相关标签
                pod_manifest.metadata.labels.update({
                    'topology.ctf.node_id': node_id,
                    'topology.ctf.config': str(topology_config.id),
                    'topology.ctf.network_area': network_area,
                })
                
                pod = self.core_api.create_namespaced_pod(
                    namespace=self.namespace,
                    body=pod_manifest
                )
                created_pods.append(pod)

                self._wait_for_pod_ready(pod_name)

                service = self._create_topology_service(
                    pod_name=pod_name,
                    docker_image=docker_image,
                    challenge=challenge,
                    user=user,
                    is_entry_point=is_entry_point,
                    node_id=node_id
                )
                created_services.append(service)

                pod = self.core_api.read_namespaced_pod(
                    name=pod_name,
                    namespace=self.namespace
                )

                container_info = self._extract_container_info(pod, service, pod_name)
                container_info['node_id'] = node_id
                container_info['node_label'] = label or docker_image.name
                container_info['network_area'] = network_area
                container_info['is_entry_point'] = is_entry_point
                container_info['is_target'] = is_target
                container_info['protocol'] = protocol  #  添加协议信息

                container_info['network_policy'] = network_policy
                container_info['allow_reverse_shell'] = allow_reverse_shell
                container_info['egress_whitelist'] = egress_whitelist
                all_containers.append(container_info)
                
                # 如果是入口点，作为 Web 容器
                if is_entry_point and not web_container:
                    web_container = container_info
                    container_info['type'] = 'web'
                
                # 输出创建结果
                node_role = []
                if is_entry_point:
                    node_role.append("入口")
                if is_target:
                    node_role.append("目标")
                if not is_entry_point and not is_target:
                    node_role.append("跳板")
                role_str = "/".join(node_role)

                policy_desc = {
                    'ISOLATED': '隔离外网',
                    'ALLOW_INGRESS': '允许反连',
                    'ALLOW_EGRESS': '允许出网',
                    'ALLOW_BOTH': '双向通信'
                }.get(network_policy, network_policy)
                
                if is_entry_point:
                    logger.info(
                        f" 拓扑节点创建成功【{role_str}】: {node_id} ({label}), "
                        f"Pod={pod_name}, 网络区域={network_area}, "
                        f"外部端口={container_info.get('ports', {})}, "
                        f"网络策略={policy_desc}, "
                        f"Flag={'' if is_target else '✗'}"
                    )
                else:
                    logger.info(
                        f" 拓扑节点创建成功【{role_str}】: {node_id} ({label}), "
                        f"Pod={pod_name}, 网络区域={network_area}, "
                        f"集群内访问={container_info.get('service_name')}, "
                        f"网络策略={policy_desc}, "
                        f"Flag={'' if is_target else '✗'}"
                    )

            if self.enable_network_policy:
                edges_data = elements.get('edges', [])
                if edges_data:
                    self._create_topology_network_policies_from_json(
                        topology_config=topology_config,
                        pod_name_mapping=pod_name_mapping,
                        node_info_mapping=node_info_mapping,
                        edges_data=edges_data
                    )
                else:
                    logger.info(f"拓扑场景没有连线，跳过网络策略创建")
            else:
                logger.warning(
                    f" 引擎已禁用网络策略 (enable_network_policy=False)，"
                    f"拓扑编排中配置的网络隔离策略将不生效。"
                    f"所有容器可以自由访问外网和相互通信。"
                )

            if not web_container and all_containers:
                web_container = all_containers[0]
                web_container['type'] = 'web'
            
            # 统计节点角色
            entry_nodes = [c['node_label'] for c in all_containers if c.get('is_entry_point')]
            target_nodes = [c['node_label'] for c in all_containers if c.get('is_target')]
            
            # 网络策略状态说明
            network_policy_status = "已启用" if self.enable_network_policy else "已禁用（容器可自由访问外网）"
            
            logger.info(
                f" 拓扑场景创建完成: {topology_config.name}, "
                f"共 {len(all_containers)} 个容器, "
                f"入口={', '.join(entry_nodes) if entry_nodes else 'N/A'}, "
                f"目标={', '.join(target_nodes) if target_nodes else 'N/A'}, "
                f"网络策略={network_policy_status}"
            )
            
            return all_containers, web_container
            
        except Exception as e:
           
            logger.error(f"拓扑场景创建失败，开始清理资源: {str(e)}")
            
            # 清理 Service
            for service in created_services:
                try:
                    self.core_api.delete_namespaced_service(
                        name=service.metadata.name,
                        namespace=self.namespace
                    )
                    logger.debug(f" 清理 Service: {service.metadata.name}")
                except Exception as cleanup_err:
                    logger.warning(f"清理 Service 失败: {cleanup_err}")
            
            # 清理 Pod
            for pod in created_pods:
                try:
                    self.core_api.delete_namespaced_pod(
                        name=pod.metadata.name,
                        namespace=self.namespace,
                        grace_period_seconds=0
                    )
                    logger.debug(f" 清理 Pod: {pod.metadata.name}")
                except Exception as cleanup_err:
                    logger.warning(f"清理 Pod 失败: {cleanup_err}")
            
            # 清理 NetworkPolicy（通过标签选择器）
            try:
                policies = self.networking_api.list_namespaced_network_policy(
                    namespace=self.namespace,
                    label_selector=f'topology.ctf.config={topology_config.id}'
                )
                for policy in policies.items:
                    try:
                        self.networking_api.delete_namespaced_network_policy(
                            name=policy.metadata.name,
                            namespace=self.namespace
                        )
                        logger.debug(f" 清理 NetworkPolicy: {policy.metadata.name}")
                    except Exception as cleanup_err:
                        logger.warning(f"清理 NetworkPolicy 失败: {cleanup_err}")
            except Exception as cleanup_err:
                logger.warning(f"查询/清理 NetworkPolicy 失败: {cleanup_err}")
            
            raise
    
    def _validate_docker_image(self, docker_image):
        """验证 DockerImage 配置"""
        if docker_image.review_status != 'APPROVED':
            logger.warning(f"镜像未审核: {docker_image.id}")
            raise K8sServiceException("镜像未通过安全审核，暂时无法使用")
        
        if not docker_image.is_active:
            logger.warning(f"镜像已禁用: {docker_image.id}")
            raise K8sServiceException("镜像已被禁用")
    
    def _cleanup_user_pods(self, user, challenge):

        try:
            # 只清理Failed Pod（立即释放资源配额）
            failed_pods = self.core_api.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=f'ctf.user={user.id},ctf.challenge={challenge.uuid}',
                field_selector='status.phase=Failed',
                _request_timeout=5  # 5秒超时（高并发场景需要更长）
            )
            
            if failed_pods.items:
                logger.info(f"发现{len(failed_pods.items)}个Failed Pod，异步清理")
                
                # 异步清理（不阻塞主流程）
                from threading import Thread
                Thread(target=self._async_cleanup_pods, args=(failed_pods.items,), daemon=True).start()
        
        except Exception as e:
            # 清理失败不影响主流程
            logger.debug(f"清理Failed Pod失败（忽略）: {e}")
        try:
            from datetime import timedelta
            import time
            
            # 1. 清理 Failed Pod
            failed_pods = self.core_api.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=f'ctf.user={user.id}',
                field_selector='status.phase=Failed'
            )
            
            # 2. 清理超时的 Pending Pod（Pending 超过 5 分钟）
            pending_pods = self.core_api.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=f'ctf.user={user.id}',
                field_selector='status.phase=Pending'
            )
            
            #  统一使用 Django timezone，确保带时区信息
            # 性能优化：缩短清理超时时间，快速释放资源配额
            timeout_minutes = 1  # 从5分钟改为1分钟（高并发优化）
            now = timezone.now()
            # 确保 now 有时区信息
            if timezone.is_naive(now):
                now = timezone.make_aware(now, dt_timezone.utc)
            timeout_threshold = now - timedelta(minutes=timeout_minutes)
            
            timed_out_pending = []
            for pod in pending_pods.items:
                creation_time = pod.metadata.creation_timestamp
                if creation_time:
                    # 确保 creation_time 有时区信息（K8s 返回的通常是 UTC 时间）
                    if timezone.is_naive(creation_time):
                        creation_time = creation_time.replace(tzinfo=dt_timezone.utc)
                    
                    if creation_time < timeout_threshold:
                        timed_out_pending.append(pod)
            
            duplicate_pods = []
            try:
          
                running_pods = self.core_api.list_namespaced_pod(
                    namespace=self.namespace,
                    label_selector=f'ctf.user={user.id},ctf.challenge={challenge.uuid}',
                    field_selector='status.phase=Running'
                )
                
                if len(running_pods.items) > 1:
                    logger.warning(
                        f"发现用户 {user.username} 题目 {challenge.uuid} 有 {len(running_pods.items)} 个Running Pod，"
                        f"高并发模式下不自动清理，由定时任务处理"
                    )
                    
            except Exception as e:
                logger.debug(f"检查重复容器失败: {e}")
            
       
            pods_to_clean = list(failed_pods.items) + timed_out_pending + duplicate_pods
            
            if pods_to_clean:
                logger.info(
                    f"发现用户 {user.username} 的问题 Pod: "
                    f"{len(failed_pods.items)} 个 Failed, "
                    f"{len(timed_out_pending)} 个超时 Pending, "
                    f"{len(duplicate_pods)} 个重复容器，准备清理"
                )
                
                for pod in pods_to_clean:
                    try:
                        pod_name = pod.metadata.name
                        pod_status = pod.status.phase
                        
                        # 删除 Pod
                        self.core_api.delete_namespaced_pod(
                            name=pod_name,
                            namespace=self.namespace,
                            grace_period_seconds=0  # 立即删除
                        )
                        logger.info(f" 已清理 {pod_status} Pod: {pod_name}")
                     
                        service_name = f"{pod_name}-svc"
                        try:
                            self.core_api.delete_namespaced_service(
                                name=service_name,
                                namespace=self.namespace
                            )
                            logger.debug(f" 已清理关联 Service: {service_name}")
                        except ApiException as e:
                            if e.status != 404:
                                logger.debug(f"清理 Service 失败: {e.reason}")
                    except ApiException as e:
                        if e.status != 404:
                            logger.warning(f"清理 Pod 失败: {pod_name}, {e.reason}")
                
                time.sleep(1.0)
                logger.info(f" 用户 {user.username} 的问题 Pod 清理完成，已释放资源配额")
        except Exception as e:
            
            logger.warning(f"清理用户 Pod 出错（忽略）: {str(e)}")
    
    def _async_cleanup_pods(self, pods):
        """
        异步清理Pod（后台线程）
        
        Args:
            pods: Pod列表
        """
        for pod in pods:
            try:
                pod_name = pod.metadata.name
                
                # 删除Pod
                self.core_api.delete_namespaced_pod(
                    name=pod_name,
                    namespace=self.namespace,
                    grace_period_seconds=0
                )

                service_name = f"{pod_name}-svc"
                try:
                    self.core_api.delete_namespaced_service(
                        name=service_name,
                        namespace=self.namespace
                    )
                except ApiException as e:
                    if e.status != 404:
                        logger.debug(f"清理Service失败: {e.reason}")
                
                logger.info(f" 异步清理Failed Pod: {pod_name}")
                
            except Exception as e:
                logger.debug(f"清理Pod失败: {e}")
    
    def _cleanup_all_timed_out_pending_pods(self):
        
   
        logger.debug("跳过全局Pending清理（由定时任务负责）")
        try:
            from datetime import timedelta
            import time
            
            # 获取所有 Pending Pod
            pending_pods = self.core_api.list_namespaced_pod(
                namespace=self.namespace,
                field_selector='status.phase=Pending'
            )
            
            if not pending_pods.items:
                return
            

            timeout_minutes = 1 
            now = timezone.now()
            # 确保 now 有时区信息
            if timezone.is_naive(now):
                now = timezone.make_aware(now, dt_timezone.utc)
            timeout_threshold = now - timedelta(minutes=timeout_minutes)
            
            timed_out_pods = []
            for pod in pending_pods.items:
                creation_time = pod.metadata.creation_timestamp
                if creation_time:
                    # 确保 creation_time 有时区信息（K8s 返回的通常是 UTC 时间）
                    if timezone.is_naive(creation_time):
                        creation_time = creation_time.replace(tzinfo=dt_timezone.utc)
                    
                    if creation_time < timeout_threshold:
                        timed_out_pods.append(pod)
            
            if timed_out_pods:
                logger.warning(
                    f"发现 {len(timed_out_pods)} 个超时 Pending Pod "
                    f"(总共 {len(pending_pods.items)} 个 Pending)，自动清理以释放资源配额"
                )
                
                cleaned_count = 0
                for pod in timed_out_pods:
                    try:
                        pod_name = pod.metadata.name
                        
                        # 删除 Pod
                        self.core_api.delete_namespaced_pod(
                            name=pod_name,
                            namespace=self.namespace,
                            grace_period_seconds=0
                        )
                        cleaned_count += 1
                        logger.debug(f" 已清理超时 Pending Pod: {pod_name}")
                        
                        # 同时删除关联的 Service
                        service_name = f"{pod_name}-svc"
                        try:
                            self.core_api.delete_namespaced_service(
                                name=service_name,
                                namespace=self.namespace
                            )
                        except ApiException as e:
                            if e.status != 404:
                                pass  # 忽略
                    except ApiException as e:
                        if e.status != 404:
                            logger.debug(f"清理 Pod 失败: {pod_name}, {e.reason}")
                
                if cleaned_count > 0:
                    logger.info(f" 已清理 {cleaned_count} 个超时 Pending Pod，等待资源配额更新...")
                    # 等待 K8s 更新资源配额
                    time.sleep(1.5)
                    
        except Exception as e:
            # 清理失败不影响后续流程
            logger.warning(f"全局清理超时 Pending Pod 出错（忽略）: {str(e)}")
    
    def check_cluster_capacity_with_limit(self, memory_limit_mb, cpu_limit):
        
        try:
            self._quick_health_check()
        except K8sServiceException:
            raise
        except Exception as e:
            logger.warning(f"健康检查失败（降级跳过）: {e}")
    
    def _quick_health_check(self):
       
      
        cache_key = f'k8s:health_check:{self.namespace}'
        cached_result = cache.get(cache_key)
        
        if cached_result == 'healthy':
            return
        
        try:
            # 获取节点列表（最多等待5秒）
            nodes = self.core_api.list_node(_request_timeout=5)
            
            # 检查是否有Ready节点
            has_ready_node = False
            for node in nodes.items:
                if node.spec.unschedulable:
                    continue
                    
                if node.status.conditions:
                    for condition in node.status.conditions:
                        if condition.type == 'Ready' and condition.status == 'True':
                            has_ready_node = True
                            break
                
                if has_ready_node:
                    break
            
            if not has_ready_node:
                raise K8sServiceException("集群无可用节点，请联系管理员")
            
            # 缓存健康状态（5秒）
            cache.set(cache_key, 'healthy', timeout=5)
            
        except K8sServiceException:
            raise
        except Exception as e:
            logger.warning(f"健康检查失败: {e}")
            # 降级跳过，让K8s调度器决定
    
    def increment_creating_count(self, timeout=120):
        
        logger.debug("跳过创建计数（已使用令牌桶替代）")
    
    def decrement_creating_count(self):
        
        logger.debug("跳过创建计数（已使用令牌桶替代）")
    
    def _get_cluster_resources(self):
        
        try:
            use_max_node = self.config.k8s_use_max_node_capacity
            
            # 1. 获取所有Ready且可调度的节点容量
            nodes = self.core_api.list_node()
            node_resources = []
            
            for node in nodes.items:
                node_name = node.metadata.name
                
                # 跳过不可调度的节点
                if node.spec.unschedulable:
                    continue
                
                # 检查节点是否 Ready
                is_ready = False
                if node.status.conditions:
                    for condition in node.status.conditions:
                        if condition.type == 'Ready' and condition.status == 'True':
                            is_ready = True
                            break
                
                if not is_ready:
                    continue
                
                # 检查污点
                has_no_schedule_taint = False
                if node.spec.taints:
                    for taint in node.spec.taints:
                        if taint.effect in ('NoSchedule', 'NoExecute'):
                            has_no_schedule_taint = True
                            break
                
                if has_no_schedule_taint:
                    continue
                
                # 获取节点可分配资源
                allocatable = node.status.allocatable
                node_total_memory = self._parse_memory_to_mb(allocatable.get('memory', '0'))
                node_total_cpu = float(allocatable.get('cpu', '0'))
                
                node_resources.append({
                    'name': node_name,
                    'total_memory': node_total_memory,
                    'total_cpu': node_total_cpu,
                    'allocated_memory': 0,  # 待计算
                    'allocated_cpu': 0,     # 待计算
                })
            
            if not node_resources:
                raise K8sServiceException("没有可用的Ready节点")
            
            # 2.  关键：统计所有Running/Pending Pod的 requests 总量（按节点分组）
            pods = self.core_api.list_pod_for_all_namespaces(
                field_selector='status.phase!=Succeeded,status.phase!=Failed'
            )
            
            for pod in pods.items:
                # 只统计已调度的Pod
                if not pod.spec.node_name:
                    continue
                
                # 找到对应节点
                node_info = None
                for n in node_resources:
                    if n['name'] == pod.spec.node_name:
                        node_info = n
                        break
                
                if not node_info:
                    continue
                
                # 累加该Pod的 requests
                for container in pod.spec.containers:
                    if container.resources and container.resources.requests:
                        mem_req = container.resources.requests.get('memory', '0')
                        cpu_req = container.resources.requests.get('cpu', '0')
                        
                        node_info['allocated_memory'] += self._parse_memory_to_mb(mem_req)
                        node_info['allocated_cpu'] += self._parse_cpu_to_cores(cpu_req)
            
            # 3. 记录每个节点的详细信息
            for node_info in node_resources:
                available_mem = node_info['total_memory'] - node_info['allocated_memory']
                available_cpu = node_info['total_cpu'] - node_info['allocated_cpu']
                mem_usage_pct = (node_info['allocated_memory'] / node_info['total_memory'] * 100) if node_info['total_memory'] > 0 else 0
                cpu_usage_pct = (node_info['allocated_cpu'] / node_info['total_cpu'] * 100) if node_info['total_cpu'] > 0 else 0
                
                logger.info(
                    f" 节点 {node_info['name']}: "
                    f"内存 {node_info['allocated_memory']:.0f}/{node_info['total_memory']:.0f}MB ({mem_usage_pct:.1f}%), "
                    f"CPU {node_info['allocated_cpu']:.2f}/{node_info['total_cpu']:.2f}核 ({cpu_usage_pct:.1f}%)"
                )
            
            # 4. 根据配置选择策略
            if use_max_node:
                # 保守策略：使用最大单节点容量（适合K3s）
                max_node = max(node_resources, key=lambda x: x['total_memory'])
                
                total_memory = max_node['total_memory']
                total_cpu = max_node['total_cpu']
                allocated_memory = max_node['allocated_memory']
                allocated_cpu = max_node['allocated_cpu']
                
                logger.warning(
                    f" K8s资源策略: 单节点容量（保守）\n"
                    f"   目标节点: {max_node['name']}\n"
                    f"   可用内存: {total_memory - allocated_memory:.0f}MB / {total_memory:.0f}MB\n"
                    f"   可用CPU: {total_cpu - allocated_cpu:.2f}核 / {total_cpu:.2f}核"
                )
            else:
                # 激进策略：使用集群总资源
                total_memory = sum(n['total_memory'] for n in node_resources)
                total_cpu = sum(n['total_cpu'] for n in node_resources)
                allocated_memory = sum(n['allocated_memory'] for n in node_resources)
                allocated_cpu = sum(n['allocated_cpu'] for n in node_resources)
                
                logger.info(
                    f" K8s资源策略: 集群总资源（激进）\n"
                    f"   节点数: {len(node_resources)}个\n"
                    f"   可用内存: {total_memory - allocated_memory:.0f}MB / {total_memory:.0f}MB\n"
                    f"   可用CPU: {total_cpu - allocated_cpu:.2f}核 / {total_cpu:.2f}核"
                )
            
            return total_memory, total_cpu, allocated_memory, allocated_cpu
            
        except Exception as e:
            logger.error(f"获取K8s集群资源失败: {e}", exc_info=True)
            raise K8sServiceException(f"无法获取集群资源信息: {str(e)}")
    
    def _parse_memory_to_mb(self, memory_str):
        
        memory_str = memory_str.strip()
        
        # 处理带单位的情况
        if memory_str.endswith('Ki'):
            return float(memory_str[:-2]) / 1024
        elif memory_str.endswith('Mi'):
            return float(memory_str[:-2])
        elif memory_str.endswith('Gi'):
            return float(memory_str[:-2]) * 1024
        elif memory_str.endswith('Ti'):
            return float(memory_str[:-2]) * 1024 * 1024
        else:
            # 纯数字，按字节处理
            return float(memory_str) / (1024 * 1024)
    
    def _parse_cpu_to_cores(self, cpu_str):
        
        cpu_str = cpu_str.strip()
        
        # 处理nanocore（如 "44351213n"）
        # 1 核 = 1,000,000,000 nanocores
        if cpu_str.endswith('n'):
            return float(cpu_str[:-1]) / 1_000_000_000
        # 处理millicore（如 "500m"）
        # 1 核 = 1,000 millicores
        elif cpu_str.endswith('m'):
            return float(cpu_str[:-1]) / 1000
        else:
            return float(cpu_str)
    
    def _check_cluster_resources(self, memory_limit_mb, cpu_limit):
       
        try:
            from django.conf import settings
            
            #  优先清理：自动清理所有超时的 Pending Pod（释放资源配额）
            self._cleanup_all_timed_out_pending_pods()
            
            # 获取所有节点
            nodes = self.core_api.list_node()
            
            if not nodes.items:
                logger.warning("K8s 集群没有可用节点")
                raise K8sServiceException("集群暂时不可用，请联系管理员")
            
            # 使用与 _build_resource_requirements 相同的 requests 计算策略（从数据库配置读取）
            from container.models import ContainerEngineConfig
            config = ContainerEngineConfig.get_config()
            requests_ratio = config.k8s_requests_ratio
            
            requested_memory_mb = max(int(memory_limit_mb * requests_ratio), 64)
            requested_memory_mb = min(requested_memory_mb, memory_limit_mb)  # 不能超过limits
            requested_cpu_cores = max(cpu_limit * requests_ratio, 0.1)
            requested_cpu_cores = min(requested_cpu_cores, cpu_limit)  # 不能超过limits
            requested_memory_bytes = requested_memory_mb * 1024 * 1024
            requested_cpu_millicores = int(requested_cpu_cores * 1000)
            
            logger.debug(
                f"K8s requests: 内存={requested_memory_mb}MB, "
                f"CPU={requested_cpu_millicores}m "
                f"(比例={requests_ratio*100:.0f}%, limits={memory_limit_mb}MB/{cpu_limit}核)"
            )
            
            # 检查是否有任何节点满足资源需求
            has_sufficient_node = False
            has_ready_node = False  #  新增：标记是否有Ready节点
            resource_info = []
            
            for node in nodes.items:
                # 跳过不可调度的节点
                if node.spec.unschedulable:
                    resource_info.append(
                        f"节点 {node.metadata.name}: 不可调度（Unschedulable）"
                    )
                    continue
                
                # 检查节点是否 Ready
                is_ready = False
                if node.status.conditions:
                    for condition in node.status.conditions:
                        if condition.type == 'Ready':
                            is_ready = (condition.status == 'True')
                            break
                
                if not is_ready:
                    resource_info.append(
                        f"节点 {node.metadata.name}: 不可用（NotReady）"
                    )
                    continue
                
                #  标记至少有一个Ready节点
                has_ready_node = True
                
                # 检查节点是否有 NoSchedule 或 NoExecute 的 taints
                has_blocking_taint = False
                if node.spec.taints:
                    for taint in node.spec.taints:
                        if taint.effect in ('NoSchedule', 'NoExecute'):
                            has_blocking_taint = True
                            resource_info.append(
                                f"节点 {node.metadata.name}: 不可用（Taint: {taint.key}={taint.value}:{taint.effect}）"
                            )
                            break
                
                if has_blocking_taint:
                    continue
                
                # 获取节点容量（物理资源 vs 可调度资源）
                capacity = node.status.capacity
                allocatable = node.status.allocatable
                if not allocatable:
                    continue
                
                # 解析物理总资源
                capacity_memory = self._parse_memory(capacity.get('memory', '0')) if capacity else 0
                capacity_cpu_millicores = int(self._parse_cpu(capacity.get('cpu', '0')) * 1000) if capacity else 0
                
                # 解析可分配资源
                allocatable_memory = self._parse_memory(allocatable.get('memory', '0'))
                allocatable_cpu_millicores = int(self._parse_cpu(allocatable.get('cpu', '0')) * 1000)
                
                #  获取实际资源使用率（通过 Metrics API）
                actual_memory_usage_percent = 0
                actual_cpu_usage_percent = 0
                
                try:
                    # 获取节点的实际资源使用情况
                    node_metrics = self._get_node_metrics(node.metadata.name)
                    if node_metrics:
                        # 解析实际使用量
                        actual_memory_used = self._parse_memory(node_metrics.get('memory', '0'))
                        actual_cpu_used_millicores = int(self._parse_cpu(node_metrics.get('cpu', '0')) * 1000)
                        
                        # 计算实际使用率（基于物理容量）
                        actual_memory_usage_percent = (actual_memory_used / capacity_memory * 100) if capacity_memory > 0 else 0
                        actual_cpu_usage_percent = (actual_cpu_used_millicores / capacity_cpu_millicores * 100) if capacity_cpu_millicores > 0 else 0
                        
                        logger.debug(
                            f"节点 {node.metadata.name} 实际资源使用: "
                            f"内存={actual_memory_usage_percent:.1f}%, CPU={actual_cpu_usage_percent:.1f}%"
                        )
                except Exception as e:
                    logger.warning(f"获取节点 {node.metadata.name} metrics 失败: {e}，将基于物理容量估算")
                
                resource_info.append(
                    f"节点 {node.metadata.name}: "
                    f"物理容量[内存={capacity_memory/(1024**3):.1f}GB, CPU={capacity_cpu_millicores}m], "
                    f"实际使用[内存={actual_memory_usage_percent:.1f}%, CPU={actual_cpu_usage_percent:.1f}%]"
                )
                
    
                max_usage_threshold = self.config.k8s_max_usage_threshold
                
                if actual_memory_usage_percent == 0 and actual_cpu_usage_percent == 0:
                    logger.warning(
                        f"节点 {node.metadata.name} Metrics不可用（使用率=0%），"
                        f"降级跳过实际使用率检查，由K8s调度器决定"
                    )
                    has_sufficient_node = True
                    break
                
                if (actual_memory_usage_percent < max_usage_threshold and 
                    actual_cpu_usage_percent < max_usage_threshold):
                    has_sufficient_node = True
                    logger.info(
                        f" 节点 {node.metadata.name} 资源充足: "
                        f"实际内存使用={actual_memory_usage_percent:.1f}% < {max_usage_threshold}%, "
                        f"实际CPU使用={actual_cpu_usage_percent:.1f}% < {max_usage_threshold}%"
                    )
                    break
                else:
                    logger.debug(
                        f" 节点 {node.metadata.name} 资源紧张: "
                        f"实际内存使用={actual_memory_usage_percent:.1f}% >= {max_usage_threshold}% 或 "
                        f"实际CPU使用={actual_cpu_usage_percent:.1f}% >= {max_usage_threshold}%"
                    )
            
            # 记录集群资源状态
            logger.info(
                f"集群资源检查（基于实际使用率）:\n" + 
                "\n".join(resource_info)
            )
            
            
            if not has_ready_node:
                logger.error("K8s 集群没有可用的 Ready 节点")
                detailed_report = "\n".join(resource_info)
                logger.info(f"详细资源报告:\n{detailed_report}")
                
                raise K8sServiceException(
                    "集群节点不可用，所有节点都处于 NotReady 或 Unschedulable 状态。"
                    "请联系管理员检查 Kubernetes 集群状态。"
                )

            if not has_sufficient_node:
                logger.warning(f"K8s 集群资源不足，所有节点资源使用率超过阈值")
                logger.warning(f" 将触发智能降级机制，尝试切换到其他可用引擎")
                
                # 生成详细的错误报告
                detailed_report = "\n".join(resource_info)
                logger.info(f"详细资源报告:\n{detailed_report}")
                
                #  高并发优化：更友好的错误信息，引导用户稍后重试
                raise K8sServiceException(
                    "系统当前负载较高，暂时无法创建容器环境。"
                )
        
        except K8sServiceException:
           
            raise
        except Exception as e:
            # 资源检查失败不阻止创建（降级处理）
            logger.warning(f"集群资源检查失败（降级跳过）: {str(e)}")
            # 不抛出异常，让 Pod 创建继续，由 K8s 调度器决定
    
    def _get_node_metrics(self, node_name):
        """
        获取单个节点的实际资源使用情况
        
        Args:
            node_name: 节点名称
            
        Returns:
            dict: {'cpu': '100m', 'memory': '1000Mi'} 或 None
        """
        try:
            # 使用 CustomObjectsApi 获取 metrics
            custom_api = client.CustomObjectsApi()
            metrics = custom_api.get_cluster_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                plural="nodes",
                name=node_name
            )
            return metrics.get('usage', {})
            
        except client.exceptions.ApiException as e:
            if e.status == 404:
                logger.warning(f"节点 {node_name} metrics 不可用（Metrics Server 可能未安装）")
            else:
                logger.warning(f"获取节点 {node_name} metrics 失败: {e.status}")
            return None
        except Exception as e:
            logger.warning(f"获取节点 {node_name} metrics 异常: {e}")
            return None
    
    def _build_resource_requirements(self, memory_limit, cpu_limit):
        
        from container.models import ContainerEngineConfig
        config = ContainerEngineConfig.get_config()
        
        #  智能分级策略（从数据库配置读取基准值）
        default_ratio = config.k8s_requests_ratio  # 从数据库配置读取
        

        if memory_limit < 1024 and cpu_limit < 2:
            requests_ratio = min(default_ratio, 0.3)
        elif memory_limit > 4096 or cpu_limit > 4:
            requests_ratio = max(default_ratio, 0.5)
        else:
            requests_ratio = default_ratio
        
        # 计算 requests（确保最小值，但不能超过limits）
        memory_requests = max(int(memory_limit * requests_ratio), 64)  # 最小64MB
        memory_requests = min(memory_requests, memory_limit)  # 不能超过limits
        cpu_requests = max(cpu_limit * requests_ratio, 0.1)  # 最小0.1核
        cpu_requests = min(cpu_requests, cpu_limit)  # 不能超过limits
        
        logger.debug(
            f"资源配置(优化): limits=[{memory_limit}MB, {cpu_limit}核], "
            f"requests=[{memory_requests}MB, {cpu_requests:.2f}核] ({requests_ratio*100:.0f}%) "
            f"[预计可超卖{1/requests_ratio:.1f}x]"
        )
        
        return client.V1ResourceRequirements(
            limits={
                "memory": f"{memory_limit}Mi",
                "cpu": str(cpu_limit)
            },
            requests={
                "memory": f"{memory_requests}Mi",
                "cpu": str(cpu_requests)
            }
        )
    
    def _build_pod_manifest(self, pod_name, docker_image, env_vars, 
                           challenge, user, memory_limit, cpu_limit, flags=None, target_node=None):
        """构建 Pod 配置（支持指定节点调度）"""
        
        # 转换环境变量格式
        env_list = [
            client.V1EnvVar(name=k, value=str(v)) 
            for k, v in env_vars.items()
        ]
        
        # 获取暴露端口
        exposed_ports = docker_image.get_ports_list()
        if not exposed_ports:
            raise K8sServiceException("镜像未配置暴露端口")
        
        # 构建容器端口列表
        container_ports = [
            client.V1ContainerPort(container_port=int(port))
            for port in exposed_ports
        ]
        
        # 使用镜像原始地址
        image_name = docker_image.full_name
        logger.info(f"使用镜像: {image_name}")
        
        # 准备生命周期钩子（用于脚本注入）
        lifecycle = None
        if flags and docker_image.flag_inject_method == 'SCRIPT' and docker_image.flag_script:
            # 只有当 flags 不为空时才准备脚本内容（替换占位符）
            script = self._prepare_flag_script(docker_image, flags)
            
            # 使用 postStart 钩子执行脚本
            lifecycle = client.V1Lifecycle(
                post_start=client.V1LifecycleHandler(
                    _exec=client.V1ExecAction(
                        command=["/bin/sh", "-c", script]
                    )
                )
            )
            logger.info(f"配置 postStart 钩子执行 Flag 注入脚本")
        
        # 构建容器配置
        container = client.V1Container(
            name="challenge",
            image=image_name,
            image_pull_policy="IfNotPresent",  # 优先使用本地镜像，减少拉取失败
            env=env_list,
            ports=container_ports,
            lifecycle=lifecycle,  # 添加生命周期钩子
            resources=self._build_resource_requirements(memory_limit, cpu_limit),
            # 容器安全上下文
            security_context=self._build_container_security_context()
        )
        
        #  节点选择器（防止节点过载）
        node_selector = None
        if target_node:
            node_selector = {
                "kubernetes.io/hostname": target_node
            }
            logger.debug(f"Pod将被调度到节点: {target_node}")
        

        pod_spec = client.V1PodSpec(
            containers=[container],
            restart_policy="Never", 
            

            automount_service_account_token=False,
            

            security_context=self._build_pod_security_context(),
            

            host_network=False,
            host_pid=False,
            host_ipc=False,
            

            node_selector=node_selector,
        )
        

        pod_metadata = client.V1ObjectMeta(
            name=pod_name,
            labels={
                "app": "ctf-challenge",
                "ctf.system": "secsnow",
                "ctf.user": str(user.id),
                "ctf.challenge": str(challenge.uuid),
                "pod": pod_name  # 用于 Service 选择器
            },
            annotations={
                # 中文内容放在 annotations 中（annotations 允许任意字符）
                "ctf.user.name": user.username,
                "ctf.challenge.title": challenge.title,
                "ctf.created_at": timezone.now().isoformat()
            }
        )
        
        return client.V1Pod(
            api_version="v1",
            kind="Pod",
            metadata=pod_metadata,
            spec=pod_spec
        )
    
    def _create_service(self, pod_name, docker_image, challenge, user):
        """创建 Service 暴露 Pod"""
        service_name = f"{pod_name}-svc"
        
        # 获取端口配置
        exposed_ports = docker_image.get_ports_list()
        
        ports = []
        for port_str in exposed_ports:
            port_num = int(port_str)
            ports.append(
                client.V1ServicePort(
                    port=port_num,
                    target_port=port_num,
                    protocol="TCP",
                    name=f"port-{port_num}"
                )
            )
        
        # 创建 Service
        service = client.V1Service(
            metadata=client.V1ObjectMeta(
                name=service_name,
                labels={
                    "app": "ctf-challenge",
                    "ctf.system": "secsnow",
                    "ctf.user": str(user.id),
                    "ctf.challenge": str(challenge.uuid)
                },
                annotations={
                    "ctf.user.name": user.username,
                    "ctf.challenge.title": challenge.title
                }
            ),
            spec=client.V1ServiceSpec(
                type="NodePort",  # 使用 NodePort 暴露
                selector={"pod": pod_name},  # 选择对应的 Pod
                ports=ports
            )
        )
        
        return self.core_api.create_namespaced_service(
            namespace=self.namespace,
            body=service
        )
    
    def _create_topology_service(self, pod_name, docker_image, challenge, user, is_entry_point, node_id):
       
        service_name = f"{pod_name}-svc"
        
        # 获取端口配置
        exposed_ports = docker_image.get_ports_list()
        
        ports = []
        for port_str in exposed_ports:
            port_num = int(port_str)
            ports.append(
                client.V1ServicePort(
                    port=port_num,
                    target_port=port_num,
                    protocol="TCP",
                    name=f"port-{port_num}"
                )
            )
        

        if is_entry_point:
           
            service_type = "NodePort"
            logger.info(f"创建 NodePort Service（入口节点）: {service_name}")
        else:
          
            service_type = "ClusterIP"
            logger.info(f"创建 ClusterIP Service（内部节点）: {service_name}")
        
        # 创建 Service
        service = client.V1Service(
            metadata=client.V1ObjectMeta(
                name=service_name,
                labels={
                    "app": "ctf-topology",
                    "ctf.system": "secsnow",
                    "ctf.user": str(user.id),
                    "ctf.challenge": str(challenge.uuid),
                    "topology.node_id": node_id,
                    "topology.is_entry": str(is_entry_point).lower()
                },
                annotations={
                    "ctf.user.name": user.username,
                    "ctf.challenge.title": challenge.title,
                    "topology.node_id": node_id
                }
            ),
            spec=client.V1ServiceSpec(
                type=service_type,
                selector={"pod": pod_name},
                ports=ports
            )
        )
        
        return self.core_api.create_namespaced_service(
            namespace=self.namespace,
            body=service
        )
    
    def _prepare_flag_environment(self, docker_image, challenge, flags):
        """
        准备 Flag 环境变量（与 DockerService 保持一致）
        """
        environment = {}
        
        # 统一处理 flag 格式：确保是列表
        if isinstance(flags, str):
            flags = [flags]
        elif not flags:
            flags = []
        
        flag_count = len(flags)
        is_multi_flag = flag_count > 1  # 是否启用多flag
        
        if docker_image.flag_inject_method == 'INTERNAL':
            # 主 flag（第一个）
            if flags:
                environment['SNOW_FLAG'] = flags[0]
            
            # 仅在多flag时添加额外的环境变量
            if is_multi_flag:
                # 所有 flag（逗号分隔）
                environment['SNOW_FLAGS'] = ','.join(flags)
                
                # flag 数量
                environment['SNOW_FLAG_COUNT'] = str(flag_count)
                
                # 每个 flag 单独的环境变量
                for i, flag in enumerate(flags, start=1):
                    environment[f'SNOW_FLAG_{i}'] = flag
                
                logger.debug(f"使用标准 SNOW_FLAG 环境变量（多flag模式，共{flag_count}个）")
            else:
                logger.debug(f"使用标准 SNOW_FLAG 环境变量（单flag模式）")
            
        elif docker_image.flag_inject_method == 'CUSTOM_ENV':
            if not docker_image.flag_env_name:
                raise K8sServiceException("自定义环境变量未配置变量名")
            
           
            if flags:
                environment[docker_image.flag_env_name] = flags[0]
            
          
            if flags:
                environment['SNOW_FLAG'] = flags[0]
            
          
            if is_multi_flag:
                environment['SNOW_FLAGS'] = ','.join(flags)
                environment['SNOW_FLAG_COUNT'] = str(flag_count)
                
                # 自定义环境变量（所有 flag）
                custom_flags_name = f"{docker_image.flag_env_name}S"
                environment[custom_flags_name] = ','.join(flags)
                
                logger.debug(f"Flag 映射: SNOW_FLAG -> {docker_image.flag_env_name}（多flag模式，共{flag_count}个）")
            else:
                logger.debug(f"Flag 映射: SNOW_FLAG -> {docker_image.flag_env_name}（单flag模式）")
            
        elif docker_image.flag_inject_method == 'SCRIPT':
          
            if flags:
                environment['SNOW_FLAG'] = flags[0]
            
            # 仅在多flag时添加额外的环境变量
            if is_multi_flag:
                environment['SNOW_FLAGS'] = ','.join(flags)
                environment['SNOW_FLAG_COUNT'] = str(flag_count)
                logger.debug(f"脚本注入模式（多flag，共{flag_count}个）")
            else:
                logger.debug(f"脚本注入模式（单flag）")
            
        elif docker_image.flag_inject_method == 'NONE':
       
            if challenge.flag_type == 'DYNAMIC':
                
                logger.warning(
                    f"配置提示：镜像 {docker_image.name} 设置为'无需注入flag'，"
                    f"但题目 {challenge.title} 配置为'动态flag'。"
                    f"如果镜像没有内置flag生成逻辑，题目可能无法正常获取flag。"
                )
            logger.debug(f"镜像无需注入 flag（flag_inject_method=NONE）")
        
        return environment
    
    def _prepare_flag_script(self, docker_image, flags):
        
        if not docker_image.flag_script:
            return ""
        
        # 统一处理 flag 格式：确保是列表
        if isinstance(flags, str):
            flags = [flags]
        elif not flags:
            flags = []
        
        # 准备替换值
        main_flag = flags[0] if flags else ''  # 主 flag（第一个）
        all_flags = ','.join(flags)  # 所有 flag，逗号分隔
        
        # 替换占位符 - 按照从具体到一般的顺序替换，避免重复替换
        script = docker_image.flag_script
        
        # 1. 先替换特定位置的 flag（SNOW_FLAG_1, SNOW_FLAG_2, ...）
        for i, flag in enumerate(flags, start=1):
            script = script.replace(f'${{SNOW_FLAG_{i}}}', flag)
            script = script.replace(f'$SNOW_FLAG_{i}', flag)
        
        # 2. 替换所有 flags（SNOW_FLAGS）
        script = script.replace('${SNOW_FLAGS}', all_flags)
        script = script.replace('$SNOW_FLAGS', all_flags)
        script = script.replace('{SNOW_FLAGS}', all_flags)
        
        # 3. 替换主 flag（SNOW_FLAG）- 为了向后兼容
        script = script.replace('${SNOW_FLAG}', main_flag)
        script = script.replace('$SNOW_FLAG', main_flag)
        script = script.replace('{SNOW_FLAG}', main_flag)
        script = script.replace('{flag}', main_flag)
        
        logger.debug(f"准备 Flag 注入脚本: {script[:100]}...")
        return script
    
    def _wait_for_pod_ready(self, pod_name, timeout=90):
        """等待 Pod 就绪（增强版：支持自动清理重试机制）
        
        Args:
            pod_name: Pod 名称
            timeout: 超时时间（秒），默认 90 秒（增加以支持自动清理后重新调度）
        """
        start_time = time.time()
        resource_shortage_detected = False
        
        while time.time() - start_time < timeout:
            try:
                pod = self.core_api.read_namespaced_pod(
                    name=pod_name,
                    namespace=self.namespace
                )
                
                phase = pod.status.phase
                
                if phase == 'Running':
                    # 检查容器是否就绪
                    if pod.status.container_statuses:
                        all_ready = all(cs.ready for cs in pod.status.container_statuses)
                        if all_ready:
                            logger.info(f" Pod {pod_name} 已完全就绪")
                            return
                        else:
                            # 容器在运行但未完全就绪，等待超过 30 秒后也认为成功
                            # （有些容器启动慢，不影响使用）
                            if time.time() - start_time > 30:
                                logger.info(f" Pod {pod_name} 已启动（容器运行中，等待就绪）")
                                return
                    else:
                        # 没有 container_statuses，但状态是 Running，可能是刚启动
                        if time.time() - start_time > 20:
                            logger.info(f" Pod {pod_name} 已启动")
                            return
                
                elif phase in ['Failed', 'Unknown']:
                    # 获取失败原因
                    reason = pod.status.reason or '未知错误'
                    message = pod.status.message or ''
                    
                    #  检查容器是否OOMKilled
                    oom_killed = False
                    if pod.status.container_statuses:
                        for cs in pod.status.container_statuses:
                            if cs.state and cs.state.terminated:
                                if cs.state.terminated.reason == 'OOMKilled':
                                    oom_killed = True
                                    logger.error(
                                        f" 容器OOM被杀: {pod_name} - "
                                        f"内存limits可能不足或容器内存泄漏"
                                    )
                    
                    logger.error(f"Pod 启动失败: {pod_name}, 原因: {reason}, 信息: {message}")
                    
                    if oom_killed:
                        raise K8sServiceException(f"容器内存不足被杀(OOMKilled)，请增加内存limits或优化容器")
                    else:
                        raise K8sServiceException(f"容器启动失败，请检查镜像配置或网络连接")
                
                elif phase == 'Pending':
                    # 检查容器状态，提前发现镜像拉取失败（检查等待时间避免误判）
                    elapsed = time.time() - start_time
                    if elapsed > 10:  # 等待10秒后才检查，避免Pod刚创建时误判
                        # 检查init容器
                        if pod.status.init_container_statuses:
                            for cs in pod.status.init_container_statuses:
                                if cs.state and cs.state.waiting:
                                    waiting_reason = cs.state.waiting.reason
                                    waiting_message = cs.state.waiting.message or ''
                                    
                                    if waiting_reason in ['ErrImagePull', 'ImagePullBackOff', 'InvalidImageName']:
                                        logger.error(
                                            f"Init容器镜像拉取失败: {pod_name}, "
                                            f"容器={cs.name}, 原因={waiting_reason}, 信息={waiting_message}"
                                        )
                                        raise K8sServiceException(
                                            f"镜像拉取失败，请联系管理员检查镜像配置或网络连接。"
                                        )
                        
                        # 检查主容器
                        if pod.status.container_statuses:
                            for cs in pod.status.container_statuses:
                                if cs.state and cs.state.waiting:
                                    waiting_reason = cs.state.waiting.reason
                                    waiting_message = cs.state.waiting.message or ''
                                    
                                    if waiting_reason in ['ErrImagePull', 'ImagePullBackOff', 'InvalidImageName']:
                                        logger.error(
                                            f"容器镜像拉取失败: {pod_name}, "
                                            f"容器={cs.name}, 原因={waiting_reason}, 信息={waiting_message}"
                                        )
                                        raise K8sServiceException(
                                            f"镜像拉取失败"
                                            "请联系管理员检查镜像配置或网络连接。"
                                        )
                    
                    # 检测 Pod 是否被调度到 NotReady 节点（快速失败）
                    if pod.spec.node_name:
                        try:
                            node = self.core_api.read_node(name=pod.spec.node_name)
                            is_ready = False
                            if node.status.conditions:
                                for condition in node.status.conditions:
                                    if condition.type == 'Ready' and condition.status == 'True':
                                        is_ready = True
                                        break
                            
                            if not is_ready:
                                logger.error(
                                    f"Pod 被调度到 NotReady 节点: {pod.spec.node_name}，"
                                    f"该节点当前不可用，立即清理该 Pod"
                                )
                                
                                # 关键修复：立即删除这个被调度到 NotReady 节点的 Pod
                                # 避免它占用资源配额，防止重试时继续调度到同一节点
                                try:
                                    self.core_api.delete_namespaced_pod(
                                        name=pod_name,
                                        namespace=self.namespace,
                                        grace_period_seconds=0  # 立即删除
                                    )
                                    logger.info(f" 已清理被错误调度的 Pod: {pod_name}")
                                except Exception as cleanup_err:
                                    logger.warning(f"清理 Pod 失败（忽略）: {cleanup_err}")
                                
                                raise K8sServiceException(
                                    "K8s 集群资源不足或节点不可用，无法调度容器"
                                )
                        except K8sServiceException:
                            raise  # 重新抛出业务异常
                        except Exception as e:
                            logger.debug(f"检查节点状态失败: {e}")
                    
                    #  检测持续的资源不足问题（通过 Events）
                    # 但不立即失败，因为自动清理后 Pod 可能会被调度成功
                    try:
                        from datetime import timedelta
                        events = self.core_api.list_namespaced_event(
                            namespace=self.namespace,
                            field_selector=f"involvedObject.name={pod_name}"
                        )
                        
                        #  统一使用 Django timezone
                        recent_threshold = timezone.now() - timedelta(seconds=30)
                        recent_failed_scheduling = False
                        
                        for event in events.items:
                            if event.reason == 'FailedScheduling':
                                # 检查事件时间
                                event_time = event.last_timestamp or event.first_timestamp
                                if event_time and event_time > recent_threshold:
                                    message = event.message or ''
                                    # 检测资源不足关键词
                                    if 'Insufficient cpu' in message or 'Insufficient memory' in message:
                                        recent_failed_scheduling = True
                                        if not resource_shortage_detected:
                                            logger.warning(f"K8s 资源不足（继续等待自动清理）: {message}")
                                            resource_shortage_detected = True
                        
                        # 如果等待超过 40 秒且仍然有资源不足事件，才失败
                        if recent_failed_scheduling and (time.time() - start_time > 40):
                            logger.error(f"K8s 集群资源持续不足，Pod 无法调度")
                            raise K8sServiceException(
                                "系统资源不足，暂时无法创建容器环境。"
                                "请稍后再试或联系管理员扩容服务器资源。"
                            )
                    except K8sServiceException:
                        raise  
                    except Exception:
                        pass  
                
            except K8sServiceException:
                
                raise
            except ApiException as e:
                if e.status == 404:
                    
                    try:
                       
                        try:
                            final_pod = self.core_api.read_namespaced_pod(
                                name=pod_name,
                                namespace=self.namespace
                            )
                            # 检查容器终止原因
                            if final_pod.status.container_statuses:
                                for cs in final_pod.status.container_statuses:
                                    if cs.state and cs.state.terminated:
                                        logger.error(
                                            f" 容器终止详情: {pod_name} - "
                                            f"原因={cs.state.terminated.reason}, "
                                            f"退出码={cs.state.terminated.exit_code}, "
                                            f"信息={cs.state.terminated.message}"
                                        )
                                    if cs.last_state and cs.last_state.terminated:
                                        logger.error(
                                            f" 容器上次终止: {pod_name} - "
                                            f"原因={cs.last_state.terminated.reason}, "
                                            f"退出码={cs.last_state.terminated.exit_code}"
                                        )
                            # 检查Pod级别的原因
                            if final_pod.status.reason:
                                logger.error(f" Pod终止原因: {pod_name} - {final_pod.status.reason}")
                            if final_pod.status.message:
                                logger.error(f" Pod终止信息: {pod_name} - {final_pod.status.message}")
                        except:
                            pass
                        
                        # 获取事件
                        events = self.core_api.list_namespaced_event(
                            namespace=self.namespace,
                            field_selector=f"involvedObject.name={pod_name}"
                        )
                        if events.items:
                            logger.error(f"Pod {pod_name} 被删除前的事件:")
                            for event in events.items[-5:]:  # 最后5个事件
                                logger.error(f"  - {event.reason}: {event.message}")
                    except Exception:
                        pass
                    
                    
                    elapsed_time = time.time() - start_time
                    if elapsed_time < 30: 
                        logger.warning(f"Pod 暂时不存在（可能正在调度），继续等待: {pod_name}")
                        time.sleep(3)  
                        continue
                    else:
                       
                        logger.error(f"Pod 已不存在，停止等待: {pod_name}")
                        raise K8sServiceException(
                            "K8s 集群资源不足，Pod 被系统清理，请稍后再试"
                        )
                else:
                    logger.warning(f"检查 Pod 状态失败: {e.reason}")
            except Exception as e:
                
                logger.debug(f"检查 Pod 状态时发生异常: {str(e)}")
            
            time.sleep(2)
        
        # 超时 - 获取详细信息
        error_details = []
        user_friendly_message = "容器启动超时，请稍后再试"  # 默认用户友好信息
        
        try:
            pod = self.core_api.read_namespaced_pod(
                name=pod_name,
                namespace=self.namespace
            )
            error_details.append(f"状态: {pod.status.phase}")
            
            # 获取容器状态
            if pod.status.container_statuses:
                for cs in pod.status.container_statuses:
                    if cs.state.waiting:
                        error_details.append(f"容器等待中: {cs.state.waiting.reason} - {cs.state.waiting.message}")
                    elif cs.state.terminated:
                        error_details.append(f"容器已终止: {cs.state.terminated.reason} - {cs.state.terminated.message}")
            
            # 获取 Events（最重要）-  优先检测资源不足
            events = self.core_api.list_namespaced_event(
                namespace=self.namespace,
                field_selector=f"involvedObject.name={pod_name}"
            )
            if events.items:
                for event in events.items[-3:]:  # 最后3个事件
                    error_details.append(f"事件: {event.reason} - {event.message}")
                    
                    #  检测资源不足
                    if event.reason == 'FailedScheduling':
                        message = event.message or ''
                        if 'Insufficient cpu' in message or 'Insufficient memory' in message:
                            user_friendly_message = (
                                "系统资源不足，暂时无法创建容器环境。"
                                "请稍后再试或联系管理员扩容服务器资源。"
                            )
                        elif 'No nodes are available' in message:
                            user_friendly_message = "集群节点不可用，请联系管理员检查服务器状态"
                    
                    #  检测镜像拉取失败
                    elif event.reason in ['Failed', 'FailedPullImage', 'ErrImagePull', 'ImagePullBackOff']:
                        user_friendly_message = "镜像拉取失败，请联系管理员检查镜像配置"
            
            # 尝试获取日志（可能还没有）
            try:
                logs = self.core_api.read_namespaced_pod_log(
                    name=pod_name,
                    namespace=self.namespace,
                    tail_lines=10
                )
                if logs:
                    error_details.append(f"日志: {logs[:200]}")
            except:
                pass
                
        except Exception as e:
            error_details.append(f"获取详情失败: {str(e)}")
        
        # 记录详细日志（给管理员看）
        detailed_error_msg = f"Pod 启动超时: {pod_name}. " + "; ".join(error_details)
        logger.error(detailed_error_msg)
        
        # 抛出用户友好的错误信息
        raise K8sServiceException(user_friendly_message)
    
    def _extract_container_info(self, pod, service, pod_name):
        """提取容器信息（返回与 Docker 一致的格式）"""
        # 获取端口信息
        ports = {}
        service_type = service.spec.type
        
        if service.spec.ports:
            for port in service.spec.ports:
                if service_type == 'NodePort':
                    # NodePort Service：返回 NodePort
                    ports[str(port.port)] = port.node_port
                else:
                    # ClusterIP Service：返回内部端口（集群内访问）
                    ports[str(port.port)] = port.port
        
        # 获取 Pod 所在节点的内网 IP
        node_ip = None
        if pod.spec.node_name:
            try:
                node = self.core_api.read_node(name=pod.spec.node_name)
                if node.status.addresses:
                    for addr in node.status.addresses:
                        if addr.type == 'InternalIP':
                            node_ip = addr.address
                            logger.debug(f"Pod {pod_name} 运行在节点 {pod.spec.node_name} (IP: {node_ip})")
                            break
            except Exception as e:
                logger.warning(f"获取节点 IP 失败: {str(e)}")
        
        # 对于 ClusterIP，添加集群内部访问地址
        cluster_ip = None
        if service_type == 'ClusterIP':
            cluster_ip = service.spec.cluster_ip
        
        return {
            'id': pod.metadata.name,
            'name': pod_name,
            'type': 'web',
            'ports': ports,
            'node_ip': node_ip,
            'service_type': service_type,
            'cluster_ip': cluster_ip, 
            'service_name': service.metadata.name  
        }
    
    def _generate_topology_pod_name(self, challenge, user, node_id):
        """
        生成拓扑节点的 Pod 名称
        
        Args:
            challenge: 题目对象
            user: 用户对象
            node_id: 节点ID
            
        Returns:
            str: 唯一的 Pod 名称
        """
        from pypinyin import lazy_pinyin
        
      
        title_pinyin = ''.join(lazy_pinyin(challenge.title))
        username_pinyin = ''.join(lazy_pinyin(user.username))
        
       
        challenge_name = re.sub(r'[^a-z0-9-]', '-', title_pinyin.lower())[:15]
        user_name = re.sub(r'[^a-z0-9-]', '-', username_pinyin.lower())[:10]
        node_id_safe = re.sub(r'[^a-z0-9-]', '-', node_id.lower())[:10]
        
      
        random_suffix = uuid.uuid4().hex[:6]
        
        name = f"{challenge_name}-{user_name}-{node_id_safe}-{random_suffix}"
        
       
        name = name.strip('-')
        
       
        if len(name) > 63:
            name = name[:63].rstrip('-')
        
        return name
    
    def _cleanup_topology_containers(self, topology_config_id):
        
        try:
            logger.info(f"开始清理拓扑场景: config_id={topology_config_id}")
            
            # 查找所有属于该拓扑的 Pod
            pods = self.core_api.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=f'topology.ctf.config={topology_config_id}'
            )
            
            # 查找所有属于该拓扑的 NetworkPolicy
            policies = self.networking_api.list_namespaced_network_policy(
                namespace=self.namespace,
                label_selector=f'topology.ctf.config={topology_config_id}'
            )
            
            cleaned_pods = 0
            cleaned_services = 0
            cleaned_policies = 0
            
            # 删除所有 Pod 和关联的 Service
            for pod in pods.items:
                pod_name = pod.metadata.name
                service_name = f"{pod_name}-svc"
                
                # 删除 Pod
                try:
                    self.core_api.delete_namespaced_pod(
                        name=pod_name,
                        namespace=self.namespace,
                        grace_period_seconds=10
                    )
                    cleaned_pods += 1
                    logger.debug(f" 删除拓扑 Pod: {pod_name}")
                except ApiException as e:
                    if e.status != 404:
                        logger.warning(f"删除 Pod 失败: {pod_name} - {e.reason}")
                
                # 删除 Service
                try:
                    self.core_api.delete_namespaced_service(
                        name=service_name,
                        namespace=self.namespace
                    )
                    cleaned_services += 1
                    logger.debug(f" 删除拓扑 Service: {service_name}")
                except ApiException as e:
                    if e.status != 404:
                        logger.warning(f"删除 Service 失败: {service_name} - {e.reason}")
            
            # 删除所有 NetworkPolicy
            for policy in policies.items:
                policy_name = policy.metadata.name
                try:
                    self.networking_api.delete_namespaced_network_policy(
                        name=policy_name,
                        namespace=self.namespace
                    )
                    cleaned_policies += 1
                    logger.debug(f" 删除拓扑 NetworkPolicy: {policy_name}")
                except ApiException as e:
                    if e.status != 404:
                        logger.warning(f"删除 NetworkPolicy 失败: {policy_name} - {e.reason}")
            
            logger.info(
                f"拓扑场景清理完成: "
                f"Pod={cleaned_pods}, Service={cleaned_services}, NetworkPolicy={cleaned_policies}"
            )
            
        except Exception as e:
            logger.error(f"清理拓扑场景失败: {str(e)}", exc_info=True)
            raise K8sServiceException(f"清理拓扑场景失败: {str(e)}")
    
    def _get_ports_for_protocol(self, protocol_name):
        
        protocol_ports = {
            'SSH': [client.V1NetworkPolicyPort(protocol='TCP', port=22)],
            'HTTP': [client.V1NetworkPolicyPort(protocol='TCP', port=80)],
            'HTTPS': [client.V1NetworkPolicyPort(protocol='TCP', port=443)],
            'FTP': [
                client.V1NetworkPolicyPort(protocol='TCP', port=21),
                client.V1NetworkPolicyPort(protocol='TCP', port=20)  # FTP 数据端口
            ],
            'SMTP': [client.V1NetworkPolicyPort(protocol='TCP', port=25)],
            'MySQL': [client.V1NetworkPolicyPort(protocol='TCP', port=3306)],
            'Redis': [client.V1NetworkPolicyPort(protocol='TCP', port=6379)],
            'MongoDB': [client.V1NetworkPolicyPort(protocol='TCP', port=27017)],
            'DNS': [
                client.V1NetworkPolicyPort(protocol='UDP', port=53),
                client.V1NetworkPolicyPort(protocol='TCP', port=53)
            ],
            'LDAP': [client.V1NetworkPolicyPort(protocol='TCP', port=389)],
            'SMB': [client.V1NetworkPolicyPort(protocol='TCP', port=445)],
            'RDP': [client.V1NetworkPolicyPort(protocol='TCP', port=3389)],
            'VNC': [client.V1NetworkPolicyPort(protocol='TCP', port=5900)],
        }
        
        # 标准化协议名称（大写）
        protocol_upper = protocol_name.upper()
        
        # 如果是 TCP/UDP，不限制端口（允许所有端口）
        if protocol_upper in ['TCP', 'UDP']:
            return None  # 返回 None 表示不添加端口限制
        
        # 返回对应协议的端口，如果没有匹配则返回 None
        return protocol_ports.get(protocol_upper, None)
    
    def _create_topology_network_policies_from_json(self, topology_config, pod_name_mapping, node_info_mapping, edges_data):
        
        try:
            logger.info(f"开始创建网络策略，共 {len(edges_data)} 个连接")
            
          
            node_egress = {}
            node_ingress = {}  
            
            # 初始化所有节点
            for node_id in pod_name_mapping.keys():
                node_egress[node_id] = []
                node_ingress[node_id] = []
            
            # 解析连接关系
            for edge in edges_data:
                edge_data = edge.get('data', {})
                source_node_id = edge_data.get('source')
                target_node_id = edge_data.get('target')
                protocol = edge_data.get('label', 'TCP')
                
                if not source_node_id or not target_node_id:
                    continue
                
                # 出站：source 可以访问 target
                node_egress[source_node_id].append({
                    'target_node_id': target_node_id,
                    'target_pod_name': pod_name_mapping.get(target_node_id),
                    'protocol': protocol
                })
                
                # 入站：target 允许来自 source 的访问
                node_ingress[target_node_id].append({
                    'source_node_id': source_node_id,
                    'source_pod_name': pod_name_mapping.get(source_node_id),
                    'protocol': protocol
                })
            
            # 为每个节点创建完整的网络策略（Ingress + Egress）
            for node_id, pod_name in pod_name_mapping.items():
                policy_name = f"{pod_name}-netpol"
                
                # 获取节点信息和网络策略配置
                node_info = node_info_mapping.get(node_id, {})
                network_policy = node_info.get('network_policy', 'ISOLATED')
                egress_whitelist_str = node_info.get('egress_whitelist', '')
                
                # 构建出站规则
                egress_rules = []
                
                # 1. 允许到指定目标的出站流量（拓扑内部连接）
                for target in node_egress.get(node_id, []):
                    if not target['target_pod_name']:
                        continue
                    
                    # 🔌 根据协议配置端口规则
                    protocol_config = target.get('protocol', 'TCP')
                    ports = self._get_ports_for_protocol(protocol_config)
                    
                    egress_rule = client.V1NetworkPolicyEgressRule(
                        to=[
                            client.V1NetworkPolicyPeer(
                                pod_selector=client.V1LabelSelector(
                                    match_labels={'pod': target['target_pod_name']}
                                ),
                                namespace_selector=client.V1LabelSelector(
                                    match_labels={'kubernetes.io/metadata.name': self.namespace}
                                )
                            )
                        ]
                    )
                    
                    # 如果协议有明确的端口，添加端口限制
                    if ports:
                        egress_rule.ports = ports
                    
                    egress_rules.append(egress_rule)
                
                # 2. 允许 DNS 查询（必需）
                egress_rules.append(
                    client.V1NetworkPolicyEgressRule(
                        to=[
                            client.V1NetworkPolicyPeer(
                                namespace_selector=client.V1LabelSelector(
                                    match_labels={'kubernetes.io/metadata.name': 'kube-system'}
                                )
                            )
                        ],
                        ports=[
                            client.V1NetworkPolicyPort(protocol='UDP', port=53),
                            client.V1NetworkPolicyPort(protocol='TCP', port=53)
                        ]
                    )
                )
                
                #  3. 根据网络策略配置添加额外规则
                if network_policy in ['ALLOW_EGRESS', 'ALLOW_BOTH']:
                    # 允许出网：可以访问外部资源
                    if egress_whitelist_str.strip():
                        # 有白名单：只允许访问指定地址
                        whitelist = [line.strip() for line in egress_whitelist_str.split('\n') if line.strip()]
                        for addr in whitelist:
                            # 尝试解析为 CIDR
                            try:
                                if '/' in addr:
                                    # CIDR 格式
                                    egress_rules.append(
                                        client.V1NetworkPolicyEgressRule(
                                            to=[
                                                client.V1NetworkPolicyPeer(
                                                    ip_block=client.V1IPBlock(cidr=addr)
                                                )
                                            ]
                                        )
                                    )
                                else:
                                    # 单个IP或域名（K8s不支持域名，只能用IP）
                                    # 如果是IP，添加 /32 后缀
                                    import ipaddress
                                    try:
                                        ipaddress.ip_address(addr)
                                        egress_rules.append(
                                            client.V1NetworkPolicyEgressRule(
                                                to=[
                                                    client.V1NetworkPolicyPeer(
                                                        ip_block=client.V1IPBlock(cidr=f"{addr}/32")
                                                    )
                                                ]
                                            )
                                        )
                                    except ValueError:
                                        logger.warning(f"节点 {node_id} 出网白名单包含域名 {addr}，K8s NetworkPolicy 不支持域名，已跳过")
                            except Exception as e:
                                logger.warning(f"节点 {node_id} 出网白名单地址 {addr} 解析失败: {e}")
                        logger.info(f"节点 {node_id} 配置出网白名单: {len(whitelist)} 个地址")
                    else:
                        # 无白名单：允许所有外网访问
                        egress_rules.append(
                            client.V1NetworkPolicyEgressRule(
                                to=[
                                    client.V1NetworkPolicyPeer(
                                        ip_block=client.V1IPBlock(
                                            cidr='0.0.0.0/0',
                                            _except=[
                                                # 排除集群内部网段（防止绕过拓扑内部连接规则）
                                                '10.0.0.0/8',
                                                '172.16.0.0/12',
                                                '192.168.0.0/16'
                                            ]
                                        )
                                    )
                                ]
                            )
                        )
                        logger.info(f"节点 {node_id} 允许访问所有外网地址（反弹shell、wget等）")
                
             
                ingress_rules = []
                
                is_entry_point = node_info.get('is_entry_point', False)
                allow_reverse_shell = node_info.get('allow_reverse_shell', False)
                
                
                if is_entry_point or network_policy in ['ALLOW_INGRESS', 'ALLOW_BOTH']:
                    
                    ingress_rules.append(
                        client.V1NetworkPolicyIngressRule() 
                    )
                    if is_entry_point:
                        logger.debug(f"入口节点 {node_id} 允许所有入站流量（NodePort访问）")
                    else:
                        logger.info(f"节点 {node_id} 允许反连入站流量（反弹shell支持）")
                else:
                    
                    for source in node_ingress.get(node_id, []):
                        if not source['source_pod_name']:
                            continue
                        
                        # 🔌 根据协议配置端口规则
                        protocol_config = source.get('protocol', 'TCP')
                        ports = self._get_ports_for_protocol(protocol_config)
                        
                        ingress_rule = client.V1NetworkPolicyIngressRule(
                            _from=[
                                client.V1NetworkPolicyPeer(
                                    pod_selector=client.V1LabelSelector(
                                        match_labels={'pod': source['source_pod_name']}
                                    ),
                                    namespace_selector=client.V1LabelSelector(
                                        match_labels={'kubernetes.io/metadata.name': self.namespace}
                                    )
                                )
                            ]
                        )
                        
                        # 如果协议有明确的端口，添加端口限制
                        if ports:
                            ingress_rule.ports = ports
                        
                        ingress_rules.append(ingress_rule)
                    logger.debug(f"节点 {node_id} 仅允许拓扑内部入站流量")
                
                # 创建网络策略（默认拒绝所有，仅允许规则中的流量）
                network_policy_obj = client.V1NetworkPolicy(
                    api_version='networking.k8s.io/v1',
                    kind='NetworkPolicy',
                    metadata=client.V1ObjectMeta(
                        name=policy_name,
                        namespace=self.namespace,
                        labels={
                            'app': 'ctf-topology',
                            'topology.ctf.config': str(topology_config.id),
                            'topology.node_id': node_id,
                            'managed-by': 'secsnow-platform'
                        }
                    ),
                    spec=client.V1NetworkPolicySpec(
                        pod_selector=client.V1LabelSelector(
                            match_labels={'pod': pod_name}
                        ),
                        policy_types=['Ingress', 'Egress'],  # 同时控制入站和出站
                        ingress=ingress_rules if ingress_rules else [],
                        egress=egress_rules
                    )
                )
                
                self.networking_api.create_namespaced_network_policy(
                    namespace=self.namespace,
                    body=network_policy_obj
                )
                
                egress_count = len(node_egress.get(node_id, []))
                ingress_count = len(node_ingress.get(node_id, []))
                
                # 构建描述信息
                policy_desc = {
                    'ISOLATED': '隔离外网',
                    'ALLOW_INGRESS': '允许反连',
                    'ALLOW_EGRESS': '允许出网',
                    'ALLOW_BOTH': '双向通信'
                }.get(network_policy, network_policy)
                
                if is_entry_point:
                    ingress_desc = "所有（入口节点）"
                elif network_policy in ['ALLOW_INGRESS', 'ALLOW_BOTH']:
                    ingress_desc = "所有（允许反连）"
                else:
                    ingress_desc = str(ingress_count)
                
                if network_policy in ['ALLOW_EGRESS', 'ALLOW_BOTH']:
                    egress_desc = f"{egress_count}+外网"
                else:
                    egress_desc = str(egress_count)
                
                logger.info(
                    f" 创建网络策略: {policy_name}, "
                    f"策略={policy_desc}, 出站={egress_desc}, 入站={ingress_desc}"
                )
            
            logger.info(
                f" 网络策略创建完成，共 {len(pod_name_mapping)} 个策略\n"
                f" 重要说明：\n"
                f"  拓扑内连线的节点自动允许通信（无论策略如何）\n"
                f"  网络策略仅控制外网访问和反连能力\n"
                f"  所有节点都允许DNS查询（必需）"
            )
            
        except Exception as e:
            logger.warning(f"创建网络策略失败（不影响容器运行）: {str(e)}")
            
    
    def _generate_pod_name(self, challenge, user, max_retries=3):
       
        from pypinyin import lazy_pinyin
        
        # 转换为拼音
        title_pinyin = ''.join(lazy_pinyin(challenge.title))
        username_pinyin = ''.join(lazy_pinyin(user.username))
        
        challenge_name = re.sub(r'[^a-z0-9-]', '-', title_pinyin.lower())[:20]
        user_name = re.sub(r'[^a-z0-9-]', '-', username_pinyin.lower())[:15]
        
        for attempt in range(max_retries):
            suffix_length = 8 + (attempt * 4)
            random_suffix = uuid.uuid4().hex[:suffix_length]
            
            name = f"{challenge_name}-{user_name}-{random_suffix}"
            
            name = name.strip('-')
            
            if len(name) > 63:
                available_length = 63 - len(random_suffix) - 2  
                challenge_part = max(10, available_length // 2)
                user_part = available_length - challenge_part
                name = f"{challenge_name[:challenge_part]}-{user_name[:user_part]}-{random_suffix}"
                name = name.strip('-')
            
            #  检查名称是否已存在
            try:
                self.core_api.read_namespaced_pod(
                    name=name,
                    namespace=self.namespace
                )
                # Pod 存在，尝试下一个名称
                logger.warning(f"Pod 名称冲突，重试: {name} (尝试 {attempt + 1}/{max_retries})")
                continue
            except ApiException as e:
                if e.status == 404:
                    logger.debug(f" 生成唯一 Pod 名称: {name}")
                    return name
                else:
                    logger.warning(f"检查 Pod 名称时出错: {e.reason}")
                    return name
        
        logger.warning(f"Pod 名称生成重试次数用尽，使用最后名称: {name}")
        return name
    
    def _ensure_namespace(self):
        """
        确保命名空间存在（已迁移到 K8sNamespaceManager）
        
        保留此方法以兼容旧代码，实际逻辑由 K8sNamespaceManager 处理
        """
        K8sNamespaceManager.ensure_namespace(self.core_api, self.namespace)
    
    def cleanup_orphan_network_policies(self):
        """
        清理孤儿 NetworkPolicy（Pod 已删除但策略还在）
        
        Returns:
            dict: 清理结果统计
        """
        result = {
            'success': True,
            'total_policies': 0,
            'orphan_policies': 0,
            'deleted_policies': [],
            'errors': []
        }
        
        try:
            # 获取所有拓扑相关的网络策略
            policies = self.networking_api.list_namespaced_network_policy(
                namespace=self.namespace,
                label_selector='app=ctf-topology'
            )
            
            result['total_policies'] = len(policies.items)
            
            if not policies.items:
                logger.info(f"命名空间 {self.namespace} 中没有拓扑网络策略")
                return result
            
            # 获取所有现存的 Pod
            pods = self.core_api.list_namespaced_pod(namespace=self.namespace)
            existing_pod_names = {pod.metadata.name for pod in pods.items}
            
            # 检查每个策略对应的 Pod 是否存在
            for policy in policies.items:
                policy_name = policy.metadata.name
                
                pod_name = None
                if policy_name.endswith('-netpol'):
                    pod_name = policy_name[:-7]  # 去掉 '-netpol'
                elif policy_name.endswith('-egress'):
                    pod_name = policy_name[:-7]  # 去掉 '-egress'
                
                # 如果 Pod 不存在，删除策略
                if pod_name and pod_name not in existing_pod_names:
                    try:
                        self.networking_api.delete_namespaced_network_policy(
                            name=policy_name,
                            namespace=self.namespace
                        )
                        result['orphan_policies'] += 1
                        result['deleted_policies'].append(policy_name)
                        logger.info(f" 清理孤儿 NetworkPolicy: {policy_name} (Pod {pod_name} 已不存在)")
                    except ApiException as e:
                        error_msg = f"删除孤儿 NetworkPolicy {policy_name} 失败: {e.reason}"
                        result['errors'].append(error_msg)
                        logger.warning(error_msg)
            
            logger.info(
                f" 孤儿 NetworkPolicy 清理完成: "
                f"总计={result['total_policies']}, "
                f"孤儿={result['orphan_policies']}, "
                f"已删除={len(result['deleted_policies'])}"
            )
            
        except Exception as e:
            result['success'] = False
            error_msg = f"清理孤儿 NetworkPolicy 失败: {str(e)}"
            result['errors'].append(error_msg)
            logger.error(error_msg, exc_info=True)
        
        return result
    
    def remove_network_policies(self):
        """
        删除命名空间中的所有网络策略（用于关闭网络限制）
        
        Returns:
            dict: 删除结果统计
        """
        result = {
            'success': True,
            'deleted_policies': [],
            'errors': []
        }
        
        try:
            # 获取所有网络策略（包括 CTF 和 AWD）
            policies = self.networking_api.list_namespaced_network_policy(
                namespace=self.namespace,
                label_selector='app in (ctf-security, awd-security)'
            )
            
            if not policies.items:
                logger.info(f"命名空间 {self.namespace} 中没有网络策略")
                return result
            
            # 删除每个策略
            for policy in policies.items:
                policy_name = policy.metadata.name
                try:
                    self.networking_api.delete_namespaced_network_policy(
                        name=policy_name,
                        namespace=self.namespace
                    )
                    result['deleted_policies'].append(policy_name)
                    logger.info(f"已删除网络策略: {policy_name}")
                except ApiException as e:
                    error_msg = f"删除网络策略 {policy_name} 失败: {e.reason}"
                    result['errors'].append(error_msg)
                    logger.error(error_msg)
            
            if result['errors']:
                result['success'] = False
            
            logger.info(
                f"网络策略清理完成: 成功删除 {len(result['deleted_policies'])} 个策略, "
                f"失败 {len(result['errors'])} 个"
            )
            
            #  清除缓存（策略已删除）
            if result['deleted_policies']:
                self._clear_network_policy_cache()
            
        except Exception as e:
            result['success'] = False
            error_msg = f"清理网络策略失败: {str(e)}"
            result['errors'].append(error_msg)
            logger.error(error_msg, exc_info=True)
        
        return result
    
    def _clear_network_policy_cache(self):
        """
        清除网络策略缓存
        
        在以下情况调用：
        - 手动删除网络策略后
        - 配置变更后
        """
        # 清除所有可能的策略缓存键
        for enable_policy in [True, False]:
            for is_awd in [True, False]:
                cache_key = f'k8s:network_policy:{self.namespace}:{enable_policy}:{is_awd}'
                cache.delete(cache_key)
        
        logger.debug(f"已清除网络策略缓存: {self.namespace}")
    
    @classmethod
    def clear_all_caches(cls, namespace=None):
        """
        清除所有 K8s 相关缓存（类方法，可在配置变更时调用）
        
        Args:
            namespace: 指定命名空间，None 则清除所有
        """
        if namespace:
            # 清除指定命名空间的缓存
            cache.delete(f'k8s:namespace_exists:{namespace}')
            for enable_policy in [True, False]:
                for is_awd in [True, False]:
                    cache.delete(f'k8s:network_policy:{namespace}:{enable_policy}:{is_awd}')
            logger.info(f"已清除命名空间缓存: {namespace}")
        else:
            # 清除所有 K8s 缓存（使用模式匹配）
            # 注意：Django cache 不支持模式删除，需要手动清除
            logger.warning("清除所有 K8s 缓存需要重启服务或等待缓存过期")
    
    def _ensure_network_policies(self):
       
        policies_to_apply = []
        
        # 1. 默认拒绝所有出站流量，但允许所有入站流量
        deny_all_egress = client.V1NetworkPolicy(
            api_version='networking.k8s.io/v1',
            kind='NetworkPolicy',
            metadata=client.V1ObjectMeta(
                name='ctf-deny-all-egress',
                namespace=self.namespace,
                labels={
                    'app': 'ctf-security',
                    'managed-by': 'ctf-platform'
                }
            ),
            spec=client.V1NetworkPolicySpec(
                pod_selector=client.V1LabelSelector(),  # 应用于所有 Pod
                policy_types=['Egress', 'Ingress'],  # 同时定义出站和入站策略
                egress=[],  # 空列表 = 拒绝所有出站
                ingress=[{}]  #  允许所有入站（无法在网络层区分用户）
            )
        )
        policies_to_apply.append(('ctf-deny-all-egress', deny_all_egress))
        
        allow_dns = client.V1NetworkPolicy(
            api_version='networking.k8s.io/v1',
            kind='NetworkPolicy',
            metadata=client.V1ObjectMeta(
                name='ctf-allow-dns',
                namespace=self.namespace,
                labels={
                    'app': 'ctf-security',
                    'managed-by': 'ctf-platform'
                }
            ),
            spec=client.V1NetworkPolicySpec(
                pod_selector=client.V1LabelSelector(),
                policy_types=['Egress'],
                egress=[
                    client.V1NetworkPolicyEgressRule(
                        to=[
                            client.V1NetworkPolicyPeer(
                                namespace_selector=client.V1LabelSelector(
                                    match_labels={'name': 'kube-system'}
                                )
                            )
                        ],
                        ports=[
                            client.V1NetworkPolicyPort(
                                protocol='UDP',
                                port=53
                            )
                        ]
                    )
                ]
            )
        )
        policies_to_apply.append(('ctf-allow-dns', allow_dns))
        
        # 应用所有策略
        applied_count = 0
        for policy_name, policy_body in policies_to_apply:
            try:
                # 检查策略是否已存在
                try:
                    self.networking_api.read_namespaced_network_policy(
                        name=policy_name,
                        namespace=self.namespace
                    )
                    # 策略已存在，跳过
                    logger.debug(f"网络策略已存在: {policy_name}")
                except ApiException as e:
                    if e.status == 404:
                        # 策略不存在，创建
                        self.networking_api.create_namespaced_network_policy(
                            namespace=self.namespace,
                            body=policy_body
                        )
                        applied_count += 1
                    else:
                        raise
            except Exception as e:
                logger.error(f"应用网络策略失败 ({policy_name}): {str(e)}")
    
    def _ensure_awd_network_policies(self):
       
        policies_to_apply = []
        
        # 1. 拒绝所有出站流量（防止攻击外网）
        deny_egress = client.V1NetworkPolicy(
            api_version='networking.k8s.io/v1',
            kind='NetworkPolicy',
            metadata=client.V1ObjectMeta(
                name='awd-deny-egress',
                namespace=self.namespace,
                labels={
                    'app': 'awd-security',
                    'managed-by': 'secsnow-platform'
                }
            ),
            spec=client.V1NetworkPolicySpec(
                pod_selector=client.V1LabelSelector(),  # 应用于所有Pod
                policy_types=['Egress'],
                egress=[
                    # 仅允许DNS查询
                    client.V1NetworkPolicyEgressRule(
                        to=[
                            client.V1NetworkPolicyPeer(
                                namespace_selector=client.V1LabelSelector(
                                    match_labels={'name': 'kube-system'}
                                )
                            )
                        ],
                        ports=[
                            client.V1NetworkPolicyPort(
                                protocol='UDP',
                                port=53
                            )
                        ]
                    ),
                    # 允许访问其他AWD namespace（跨队伍攻击）
                    client.V1NetworkPolicyEgressRule(
                        to=[
                            client.V1NetworkPolicyPeer(
                                namespace_selector=client.V1LabelSelector(
                                    match_expressions=[
                                        {
                                            'key': 'awd-team',
                                            'operator': 'Exists'
                                        }
                                    ]
                                )
                            )
                        ]
                    )
                ]
            )
        )
        policies_to_apply.append(('awd-deny-egress', deny_egress))
        
        # 2. 允许所有入站流量（其他队伍可以访问本队服务）
        allow_ingress = client.V1NetworkPolicy(
            api_version='networking.k8s.io/v1',
            kind='NetworkPolicy',
            metadata=client.V1ObjectMeta(
                name='awd-allow-ingress',
                namespace=self.namespace,
                labels={
                    'app': 'awd-security',
                    'managed-by': 'secsnow-platform'
                }
            ),
            spec=client.V1NetworkPolicySpec(
                pod_selector=client.V1LabelSelector(),
                policy_types=['Ingress'],
                ingress=[{}]  # 空对象 = 允许所有入站
            )
        )
        policies_to_apply.append(('awd-allow-ingress', allow_ingress))
        
        # 应用所有策略
        for policy_name, policy_body in policies_to_apply:
            try:
                try:
                    self.networking_api.read_namespaced_network_policy(
                        name=policy_name,
                        namespace=self.namespace
                    )
                    logger.debug(f"AWD网络策略已存在: {policy_name}")
                except ApiException as e:
                    if e.status == 404:
                        self.networking_api.create_namespaced_network_policy(
                            namespace=self.namespace,
                            body=policy_body
                        )
                        logger.info(f"创建AWD网络策略: {policy_name}")
                    else:
                        raise
            except Exception as e:
                logger.error(f" 应用AWD网络策略失败 ({policy_name}): {str(e)}")
    
    def _get_network_stats(self, container_id: str) -> dict:
       
        try:
            # 在 Pod 中执行命令读取网络统计
            command = ['/bin/sh', '-c', 'cat /proc/net/dev 2>/dev/null || echo "not-available"']
            
            resp = stream(
                self.core_api.connect_get_namespaced_pod_exec,
                container_id,
                self.namespace,
                command=command,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
                _preload_content=False
            )
            
            output = ""
            while resp.is_open():
                resp.update(timeout=1)
                if resp.peek_stdout():
                    output += resp.read_stdout()
                if resp.peek_stderr():
                    break
            resp.close()
            
            if "not-available" in output or not output:
                return None
            
            # 解析 /proc/net/dev 输出
            rx_total = 0
            tx_total = 0
            
            for line in output.split('\n'):
                if ':' in line and 'eth' in line.lower():
                    parts = line.split(':')
                    if len(parts) == 2:
                        stats = parts[1].split()
                        if len(stats) >= 9:
                            rx_total += int(stats[0])  # 接收字节
                            tx_total += int(stats[8])  # 发送字节
            
            return {
                'rx_bytes': rx_total,
                'tx_bytes': tx_total
            }
            
        except Exception as e:
            logger.debug(f"获取网络统计失败: {str(e)}")
            return None
    
    @staticmethod
    def _parse_cpu(cpu_str):
        """解析 CPU 用量字符串 (例如: "45m" -> 0.045 核心或 45 毫核)"""
        if not cpu_str:
            return 0.0
        if cpu_str.endswith('n'):
            return int(cpu_str[:-1]) / 1_000_000_000
        elif cpu_str.endswith('u'):
            return int(cpu_str[:-1]) / 1_000_000
        elif cpu_str.endswith('m'):
            return int(cpu_str[:-1]) / 1000
        else:
            return float(cpu_str)
    
    @staticmethod
    def _parse_memory(mem_str):
        """解析内存用量字符串 (例如: "128Mi" -> 134217728 bytes)"""
        if not mem_str:
            return 0
        units = {
            'Ki': 1024,
            'Mi': 1024**2,
            'Gi': 1024**3,
            'K': 1000,
            'M': 1000**2,
            'G': 1000**3
        }
        
        for unit, multiplier in units.items():
            if mem_str.endswith(unit):
                return int(mem_str[:-len(unit)]) * multiplier
        
        return int(mem_str)


    
    
    def _build_container_security_context(self):
        """
        构建容器安全上下文（兼容性优化）
        
        Returns:
            client.V1SecurityContext: 容器安全配置
        """
        security_config = {
            'run_as_non_root': False,  # 允许 root（某些 CTF 题目需要）
            'read_only_root_filesystem': False,  # 允许写入（题目可能需要）
        }
        
        # 特权模式配置
        if self.allow_privileged:
            security_config['privileged'] = True
            security_config['allow_privilege_escalation'] = True
        else:
            security_config['privileged'] = False
            security_config['allow_privilege_escalation'] = False
        
        # Capabilities 配置
        if self.drop_capabilities:
            security_config['capabilities'] = client.V1Capabilities(
                drop=self.drop_capabilities
            )
        
        return client.V1SecurityContext(**security_config)
    
    def _build_pod_security_context(self):
        """
        构建 Pod 级别安全上下文（兼容性优化）
        
        Returns:
            client.V1PodSecurityContext: Pod 安全配置
        """
        security_config = {
            'run_as_non_root': False,  # CTF 题目可能需要 root
        }
        
        # 设置 fsGroup（确保文件权限一致）
        try:
            security_config['fs_group'] = 1000
        except Exception:
            pass
        
        # 尝试启用 seccomp（某些 K8s 版本可能不支持）
        if self.enable_seccomp:
            try:
                security_config['seccomp_profile'] = client.V1SeccompProfile(
                    type='RuntimeDefault'
                )
                logger.debug("已启用 seccomp profile")
            except Exception as e:
                logger.warning(f"Seccomp profile 不支持，跳过: {e}")
        
        return client.V1PodSecurityContext(**security_config)
    
    def _build_security_context(self):
        """构建容器安全上下文（K8s Manifest 使用）"""
        return self._build_container_security_context()
    
    def _create_service_for_pod(self, pod_name, ports, challenge, user):
      
        service_name = f"{pod_name}-svc"
        
        # 构建Service端口列表
        service_ports = []
        for port_str in ports:
            try:
                port_num = int(port_str)
                service_ports.append(
                    client.V1ServicePort(
                        port=port_num,
                        target_port=port_num,
                        protocol="TCP",
                        name=f"port-{port_num}"
                    )
                )
            except ValueError:
                logger.warning(f"无效的端口号: {port_str}")
                continue
        
        if not service_ports:
            raise K8sServiceException("没有有效的端口配置")
        
        # 创建Service
        service = client.V1Service(
            metadata=client.V1ObjectMeta(
                name=service_name,
                labels={
                    'app': 'ctf-challenge',
                    'ctf.system': 'secsnow',
                    'ctf.user': str(user.id),
                    'ctf.challenge': str(challenge.uuid),
                    'managed-by': 'secsnow-platform'
                },
                annotations={
                    'ctf.user.name': user.username,
                    'ctf.challenge.title': challenge.title
                }
            ),
            spec=client.V1ServiceSpec(
                type="NodePort",
                selector={'pod': pod_name},  # 选择对应的Pod
                ports=service_ports
            )
        )
        
        created_service = self.core_api.create_namespaced_service(
            namespace=self.namespace,
            body=service
        )
        
        logger.info(f" 自动创建Service: {service_name}")
        return created_service
    
    def _get_service_ports(self, service_name):
        """
        获取Service的端口映射
        
        Args:
            service_name: Service名称
            
        Returns:
            dict: {容器端口: NodePort}
        """
        try:
            service = self.core_api.read_namespaced_service(
                name=service_name,
                namespace=self.namespace
            )
            
            ports = {}
            if service.spec.ports:
                for port in service.spec.ports:
                    # port.port 是Service端口（通常等于容器端口）
                    # port.node_port 是NodePort（外部访问端口）
                    ports[str(port.port)] = port.node_port
            
            return ports
            
        except ApiException as e:
            if e.status == 404:
                logger.warning(f"Service不存在: {service_name}")
                return {}
            else:
                logger.error(f"获取Service端口失败: {e.reason}")
                raise K8sServiceException(f"获取Service端口失败: {e.reason}")
    
    def close(self):
        """
        关闭 K8s API 客户端连接
        
        释放底层的 REST 客户端和连接池资源。
        """
        try:
            # 关闭所有 API 客户端的底层连接
            if hasattr(self, 'core_api') and hasattr(self.core_api, 'api_client'):
                if hasattr(self.core_api.api_client, 'close'):
                    self.core_api.api_client.close()
                    logger.debug("已关闭 CoreV1Api 客户端连接")
            
            if hasattr(self, 'apps_api') and hasattr(self.apps_api, 'api_client'):
                if hasattr(self.apps_api.api_client, 'close'):
                    self.apps_api.api_client.close()
                    logger.debug("已关闭 AppsV1Api 客户端连接")
            
            if hasattr(self, 'networking_api') and hasattr(self.networking_api, 'api_client'):
                if hasattr(self.networking_api.api_client, 'close'):
                    self.networking_api.api_client.close()
                    logger.debug("已关闭 NetworkingV1Api 客户端连接")
            
            logger.debug(f"K8sService.close() 完成（命名空间: {self.namespace}）")
        except Exception as e:
            logger.warning(f"关闭 K8s API 客户端时出错: {str(e)}")
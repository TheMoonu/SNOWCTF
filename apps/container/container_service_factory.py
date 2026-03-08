"""
容器服务工厂

根据引擎类型创建对应的容器服务实例（Docker 或 K8s）
"""

import logging

logger = logging.getLogger("apps.container")


class ContainerServiceFactory:
    """容器服务工厂类"""
    
    @staticmethod
    def create_service(engine):
        """
        根据引擎类型创建服务实例
        
        Args:
            engine: DockerEngine 对象
            
        Returns:
            ContainerServiceBase: Docker 或 K8s 服务实例
            
        Raises:
            ValueError: 不支持的引擎类型
        """
        engine_type = getattr(engine, 'engine_type', 'DOCKER')
        
        if engine_type == 'KUBERNETES':
            # 创建 K8s 服务
            from .k8s_service import K8sService
            
            logger.info(
                f"创建 K8s 服务: engine={engine.name}, "
                f"namespace={getattr(engine, 'namespace', 'ctf-challenges')}, "
                f"verify_ssl={getattr(engine, 'verify_ssl', False)}"
            )
            
            # K8s 的安全配置从 engine 对象中读取
            return K8sService(engine=engine)
        
        elif engine_type == 'DOCKER':
            # 创建 Docker 服务
            from .docker_service import DockerService
            
            # 获取 Docker URL
            if engine.host_type == 'LOCAL':
                docker_url = "unix:///var/run/docker.sock"
            else:
                docker_url = f"tcp://{engine.host}:{engine.port}"
            
            # 获取 TLS 配置
            tls_config = None
            if engine.tls_enabled:
                tls_config = engine.get_tls_config()
            
            # 获取安全配置
            security_config = {
                'allow_privileged': getattr(engine, 'allow_privileged', False),
                'drop_capabilities': getattr(engine, 'drop_capabilities', 'NET_RAW,SYS_ADMIN,SYS_MODULE,SYS_PTRACE'),
                'enable_seccomp': getattr(engine, 'enable_seccomp', True),
                'allow_host_network': getattr(engine, 'allow_host_network', False),
                'allow_host_pid': getattr(engine, 'allow_host_pid', False),
                'allow_host_ipc': getattr(engine, 'allow_host_ipc', False),
                'enable_network_policy': getattr(engine, 'enable_network_policy', True),
            }
            
            logger.info(
                f"创建 Docker 服务: engine={engine.name}, "
                f"url={docker_url}, "
                f"连接池=启用, "
                f"安全级别={'高' if security_config['enable_network_policy'] else '低'}"
            )
            
            #  传递 engine 对象以启用连接池
            return DockerService(
                url=docker_url,
                tls_config=tls_config,
                security_config=security_config,
                engine=engine  # 传递 engine 启用连接池
            )
        
        else:
            raise ValueError(f"不支持的引擎类型: {engine_type}")
    
    @staticmethod
    def get_supported_types():
        """
        获取支持的引擎类型列表
        
        Returns:
            list: 支持的引擎类型
        """
        return ['DOCKER', 'KUBERNETES']

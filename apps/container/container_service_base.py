"""
容器服务抽象基类

提供统一的容器服务接口，支持 Docker 和 Kubernetes
"""

from abc import ABC, abstractmethod
from typing import Tuple, List, Dict


class ContainerServiceException(Exception):
    """容器服务统一异常基类"""
    pass


class ContainerServiceBase(ABC):
    """容器服务抽象基类"""
    
    @abstractmethod
    def create_containers(self, challenge, user, flag, memory_limit, cpu_limit) -> Tuple[List[Dict], Dict]:
        """
        创建容器
        
        Args:
            challenge: 题目对象
            user: 用户对象
            flag: Flag 值（可以是字符串或列表）
            memory_limit: 内存限制 (MB)
            cpu_limit: CPU 限制（核心数）
            
        Returns:
            Tuple[List[dict], dict]: (所有容器信息列表, Web容器信息)
                容器信息格式：{
                    'id': str,          # 容器/Pod ID
                    'name': str,        # 容器/Pod 名称
                    'type': str,        # 'web'
                    'ports': dict       # 端口映射 {'80': 30001, '443': 30002}
                }
        
        Raises:
            Exception: 容器创建失败
        """
        pass
    
    @abstractmethod
    def stop_and_remove_container(self, container_id: str):
        """
        停止并删除容器
        
        Args:
            container_id: 容器ID（Docker 容器ID 或 K8s Pod 名称）
        
        Raises:
            Exception: 容器清理失败
        """
        pass
    
    def get_container_status(self, container_id: str) -> str:
        """
        获取容器状态（可选实现）
        
        Args:
            container_id: 容器ID
            
        Returns:
            str: 容器状态
        """
        return 'UNKNOWN'
    
    def get_container_metrics(self, container_id: str) -> Dict:
        """
        获取容器指标（可选实现，用于监控）
        
        Args:
            container_id: 容器ID
            
        Returns:
            dict: 容器指标 {
                'cpu_usage': float,     # CPU 使用率
                'memory_usage': int,    # 内存使用量（字节）
                'rx_bytes': int,        # 接收字节数
                'tx_bytes': int         # 发送字节数
            }
        """
        return {}

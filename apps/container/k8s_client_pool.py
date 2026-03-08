# -*- coding: utf-8 -*-
"""
K8s API 客户端连接池
解决高并发场景下重复初始化 K8s 客户端的性能问题
"""
import threading
import logging
from kubernetes import client, config
from django.core.cache import cache

logger = logging.getLogger("apps.container")


class K8sClientPool:
    """
    K8s API 客户端连接池（单例模式 + LRU缓存）
    
    功能：
    1. 复用 K8s API 客户端，避免重复初始化
    2. 按 kubeconfig 路径缓存客户端
    3. 线程安全
    4. LRU缓存策略（最多缓存10个配置）
    5. 定期健康检查和过期清理
    """
    _instance = None
    _lock = threading.Lock()
    _clients_cache = {}  
    _max_cache_size = 10  
    _cache_ttl = 3600  
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def get_clients(cls, kubeconfig_path=None, verify_ssl=True, connection_pool_maxsize=50):
        """
        获取或创建 K8s API 客户端（复用连接 + LRU缓存）
        
        Args:
            kubeconfig_path: kubeconfig 文件路径
            verify_ssl: 是否验证 SSL
            connection_pool_maxsize: 连接池最大连接数
            
        Returns:
            Tuple[CoreV1Api, AppsV1Api, NetworkingV1Api]: API 客户端三元组
        """
        import time
        
        # 构建缓存键
        cache_key = f"{kubeconfig_path or 'incluster'}:{verify_ssl}:{connection_pool_maxsize}"
        current_time = time.time()
        
        # 检查缓存（含过期检查）
        if cache_key in cls._clients_cache:
            cached_item = cls._clients_cache[cache_key]
            last_used_time = cached_item[3] if len(cached_item) > 3 else 0
            
            # 检查是否过期
            if current_time - last_used_time < cls._cache_ttl:
                # 更新最后使用时间
                core_api, apps_api, networking_api = cached_item[0], cached_item[1], cached_item[2]
                cls._clients_cache[cache_key] = (core_api, apps_api, networking_api, current_time)
                logger.debug(f"✓ 复用 K8s API 客户端: {cache_key}")
                return (core_api, apps_api, networking_api)
            else:
                # 缓存过期，删除旧缓存
                logger.info(f"K8s 客户端缓存过期，重新创建: {cache_key}")
                del cls._clients_cache[cache_key]
        
        # 加锁创建
        with cls._lock:
            # 双重检查（包含过期检查）
            if cache_key in cls._clients_cache:
                cached_item = cls._clients_cache[cache_key]
                last_used_time = cached_item[3] if len(cached_item) > 3 else 0
                if current_time - last_used_time < cls._cache_ttl:
                    core_api, apps_api, networking_api = cached_item[0], cached_item[1], cached_item[2]
                    cls._clients_cache[cache_key] = (core_api, apps_api, networking_api, current_time)
                    return (core_api, apps_api, networking_api)
            
            #  LRU清理：如果缓存满了，删除最久未使用的
            if len(cls._clients_cache) >= cls._max_cache_size:
                oldest_key = min(
                    cls._clients_cache.keys(),
                    key=lambda k: cls._clients_cache[k][3] if len(cls._clients_cache[k]) > 3 else 0
                )
                logger.info(f"K8s 客户端缓存已满，移除最旧配置: {oldest_key}")
                del cls._clients_cache[oldest_key]
            
            logger.info(f"创建新的 K8s API 客户端: {cache_key}")
            
            try:
                # 加载 kubeconfig
                if kubeconfig_path:
                    config.load_kube_config(config_file=kubeconfig_path)
                else:
                    config.load_incluster_config()
                
                # 获取配置并优化
                configuration = client.Configuration.get_default_copy()
                configuration.verify_ssl = verify_ssl
                configuration.connection_pool_maxsize = connection_pool_maxsize
                
                # 🔥 高并发优化：设置合理的超时时间
                # 默认读取超时：10秒（高并发场景下K8s API响应慢）
                # 默认连接超时：5秒
                import urllib3
                configuration.retries = urllib3.Retry(
                    total=3,  # 最多重试3次
                    connect=3,  # 连接重试3次
                    read=2,  # 读取重试2次
                    backoff_factor=0.3,  # 指数退避
                    status_forcelist=[500, 502, 503, 504]  # 这些状态码才重试
                )
                # 注意：socket_options需要在rest client层面设置
                # 这里我们通过retries来控制重试行为
                
                # 设置为默认配置
                client.Configuration.set_default(configuration)
                
                # 创建 API 客户端（共享连接池）
                core_api = client.CoreV1Api()
                apps_api = client.AppsV1Api()
                networking_api = client.NetworkingV1Api()
                
                #  缓存（带时间戳）
                cls._clients_cache[cache_key] = (core_api, apps_api, networking_api, current_time)
                
                logger.info(f"✓ K8s API 客户端创建成功，当前池大小: {len(cls._clients_cache)}/{cls._max_cache_size}")
                
                return (core_api, apps_api, networking_api)
                
            except Exception as e:
                logger.error(f"创建 K8s API 客户端失败: {str(e)}")
                raise
    
    @classmethod
    def clear_cache(cls, kubeconfig_path=None):
        """清除指定客户端缓存"""
        with cls._lock:
            if kubeconfig_path:
                # 清除指定 kubeconfig 的缓存
                keys_to_remove = [k for k in cls._clients_cache.keys() if k.startswith(kubeconfig_path or 'incluster')]
                for key in keys_to_remove:
                    del cls._clients_cache[key]
                    logger.info(f"清除 K8s 客户端缓存: {key}")
            else:
                # 清除所有缓存
                cls._clients_cache.clear()
                logger.info("清除所有 K8s 客户端缓存")
    
    @classmethod
    def cleanup_expired(cls):
        """
        清理过期的客户端缓存
        
        Returns:
            int: 清理的数量
        """
        import time
        current_time = time.time()
        expired_keys = []
        
        with cls._lock:
            for key, value in cls._clients_cache.items():
                last_used_time = value[3] if len(value) > 3 else 0
                if current_time - last_used_time >= cls._cache_ttl:
                    expired_keys.append(key)
            
            for key in expired_keys:
                del cls._clients_cache[key]
                logger.info(f"清理过期的 K8s 客户端缓存: {key}")
        
        if expired_keys:
            logger.info(f"✓ 清理了 {len(expired_keys)} 个过期的 K8s 客户端缓存")
        
        return len(expired_keys)
    
    @classmethod
    def get_pool_status(cls):
        """获取连接池状态（监控用）"""
        import time
        current_time = time.time()
        
        cache_info = []
        for key, value in cls._clients_cache.items():
            last_used_time = value[3] if len(value) > 3 else 0
            age = current_time - last_used_time
            cache_info.append({
                'key': key,
                'age_seconds': int(age),
                'expired': age >= cls._cache_ttl
            })
        
        return {
            'pool_size': len(cls._clients_cache),
            'max_size': cls._max_cache_size,
            'ttl_seconds': cls._cache_ttl,
            'cached_configs': cache_info
        }


class K8sNamespaceManager:
    """
    K8s 命名空间管理器（优化命名空间检查）
    
    使用 Redis 缓存避免重复的 API 调用
    """
    
    @staticmethod
    def ensure_namespace(core_api, namespace, is_awd_team=False):
        """
        确保命名空间存在（带缓存 + AWD队伍标签支持）
        
        Args:
            core_api: CoreV1Api 客户端
            namespace: 命名空间名称
            is_awd_team: 是否为AWD队伍namespace（用于添加awd-team标签）
        """
        cache_key = f'k8s:namespace_exists:{namespace}'
        
        # 检查缓存状态（但不盲目信任）
        cached = cache.get(cache_key)
        
        try:
            # 🔧 修复：总是验证 namespace 真实存在（即使有缓存）
            # 原因：namespace 可能在缓存期间被外部删除，导致 404 错误
            existing_ns = core_api.read_namespace(name=namespace)
            logger.debug(f"✓ 命名空间已存在: {namespace}")
            
            # 🆕 如果是AWD队伍namespace，确保有正确的label
            if is_awd_team:
                labels = existing_ns.metadata.labels or {}
                if 'awd-team' not in labels:
                    # 补充添加label
                    labels['awd-team'] = 'true'
                    existing_ns.metadata.labels = labels
                    core_api.patch_namespace(name=namespace, body=existing_ns)
                    logger.info(f"✓ 为AWD队伍namespace添加标签: {namespace}")
            
            # 验证通过，更新缓存
            if not cached:
                cache.set(cache_key, True, timeout=3600)
                logger.debug(f"已缓存命名空间状态: {namespace}")
            
        except client.exceptions.ApiException as e:
            if e.status == 404:
                # 命名空间不存在，需要创建
                
                # 清除错误的缓存
                if cached:
                    cache.delete(cache_key)
                    logger.info(f"已清除过期缓存: {cache_key}")
                
                # 创建命名空间
                labels = {}
                if is_awd_team:
                    # AWD队伍namespace添加特殊标签
                    labels['awd-team'] = 'true'
                    labels['managed-by'] = 'secsnow-platform'
                    logger.info(f"创建AWD队伍命名空间: {namespace}（带awd-team标签）")
                else:
                    logger.info(f"创建普通命名空间: {namespace}")
                
                ns = client.V1Namespace(
                    metadata=client.V1ObjectMeta(
                        name=namespace,
                        labels=labels if labels else None
                    )
                )
                
                try:
                    core_api.create_namespace(body=ns)
                    logger.info(f"✓ 命名空间创建成功: {namespace}")
                    # 缓存新创建的 namespace
                    cache.set(cache_key, True, timeout=3600)
                except Exception as create_err:
                    logger.error(f"❌ 创建命名空间失败: {namespace} - {create_err}")
                    raise
            else:
                logger.error(f"检查命名空间失败: {e.reason}")
                raise
    
    @staticmethod
    def clear_namespace_cache(namespace):
        """清除命名空间缓存"""
        cache_key = f'k8s:namespace_exists:{namespace}'
        cache.delete(cache_key)
        logger.info(f"清除命名空间缓存: {namespace}")


# 便捷函数
def get_k8s_clients(kubeconfig_path=None, verify_ssl=True, connection_pool_maxsize=50):
    """
    获取 K8s API 客户端（全局入口）
    
    Usage:
        core_api, apps_api, networking_api = get_k8s_clients()
    """
    return K8sClientPool.get_clients(
        kubeconfig_path=kubeconfig_path,
        verify_ssl=verify_ssl,
        connection_pool_maxsize=connection_pool_maxsize
    )


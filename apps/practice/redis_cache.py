import json
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone
from container.models import ContainerEngineConfig

class UserContainerCache:
    """
    用户容器缓存管理器
    
    优化说明：
    - 存储容器访问信息（IP、端口、域名等）
    - 容器销毁时自动清理缓存
    - 缓存过期时间与容器过期时间保持一致
    - Flag 验证使用 HMAC 校验，不依赖缓存
    """
    PREFIX = "user_container:"
    
    @classmethod
    def get_expire_time(cls):
        """动态获取过期时间（秒）- 从数据库配置读取"""
        config = ContainerEngineConfig.get_config()
        return int(3600 * config.container_expiry_hours)

    @classmethod
    def get_key(cls, user_id, challenge_uuid):
        return f"{cls.PREFIX}{user_id}:{challenge_uuid}"

    @classmethod
    def set(cls, user_container, url_prefix=None, container_urls=None):
        """
        设置容器缓存（不包含 flag，flag 验证使用 HMAC）
        
        Args:
            user_container: UserContainer 对象
            url_prefix: URL前缀（用于生成域名URL）
            container_urls: 容器URL列表（支持多协议、多入口）
        """
        key = cls.get_key(user_container.user.id, user_container.challenge_uuid)
        
        data = {
            "id": user_container.id,
            "user_id": user_container.user.id,
            "challenge_id": user_container.challenge_title,
            "challenge_uuid": str(user_container.challenge_uuid),
            "docker_engine_id": user_container.docker_engine.id,
            "container_id": user_container.container_id,
            "ip_address": user_container.ip_address,
            "port": user_container.port,
            "domain": user_container.domain,
            "created_at": user_container.created_at.isoformat(),
            "expires_at": user_container.expires_at.isoformat(),
            "url_prefix": url_prefix,  # 🆕 URL前缀
            "container_urls": container_urls  # 🆕 容器URL列表（支持多协议）
        }
        cache.set(key, json.dumps(data), timeout=cls.get_expire_time())

    @classmethod
    def get(cls, user_id, challenge_uuid):
        """获取容器缓存"""
        key = cls.get_key(user_id, challenge_uuid)
        data = cache.get(key)
        if data:
            return json.loads(data)
        return None

    @classmethod
    def delete(cls, user_id, challenge_uuid):
        """删除容器缓存"""
        key = cls.get_key(user_id, challenge_uuid)
        cache.delete(key)
    
    @classmethod
    def get_user_container(cls, user_id):
        """
        获取用户的活跃容器信息（用户只能有一个容器）
        
        Args:
            user_id: 用户ID
        
        Returns:
            dict: 容器信息，包含所有字段（ip_address, port, domain, expires_at等），如果没有则返回 None
        """
        from django.core.cache import cache
        from datetime import datetime
        from django.utils import timezone
        
        # 使用 Redis keys 模式匹配获取该用户的容器缓存
        pattern = f"{cls.PREFIX}{user_id}:*"
        
        # 获取所有匹配的 key
        if hasattr(cache, 'keys'):
            cache_keys = cache.keys(pattern)
            if cache_keys:
                # 只取第一个（用户只能有一个容器）
                cache_key = cache_keys[0]
                
                # 从 cache_key 中提取 challenge_uuid
                # key 格式: user_container:{user_id}:{challenge_uuid}
                parts = cache_key.split(':')
                if len(parts) >= 3:
                    challenge_uuid = ':'.join(parts[2:])
                    
                    # 获取容器数据
                    container_data = cls.get(user_id, challenge_uuid)
                    
                    if container_data:
                        # 检查是否过期
                        expires_at_str = container_data.get('expires_at', '')
                        if expires_at_str:
                            try:
                                expires_at = datetime.fromisoformat(expires_at_str)
                                if expires_at < timezone.now():
                                    # 已过期，返回 None
                                    return None
                            except:
                                pass
                        
                        return container_data
        
        return None
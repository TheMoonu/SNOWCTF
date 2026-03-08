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
    - 维护用户容器索引，快速查找用户的活跃容器
    """
    PREFIX = "com_container:"
    
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
        同时维护用户容器索引
        
        Args:
            user_container: UserContainer 对象
            url_prefix: 可选的 URL 随机前缀（用于域名方式访问）
            container_urls: 容器URL列表（支持多协议、多入口）
        """
        key = cls.get_key(user_container.user.id, user_container.challenge_uuid)
        
        # 如果没有提供 url_prefix 且有域名，生成一个随机前缀
        if url_prefix is None and user_container.domain:
            import uuid
            url_prefix = uuid.uuid4().hex[:8]
        
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
            "url_prefix": url_prefix,
            "container_urls": container_urls  
        }
        expire_time = cls.get_expire_time()
        cache.set(key, json.dumps(data), timeout=expire_time)
        
        # 维护用户容器索引（用于快速查找用户的活跃容器）
        user_index_key = f"{cls.PREFIX}user:{user_container.user.id}"
        cache.set(user_index_key, str(user_container.challenge_uuid), timeout=expire_time)

    @classmethod
    def get(cls, user_id, challenge_uuid):
        """获取容器缓存"""
        key = cls.get_key(user_id, challenge_uuid)
        data = cache.get(key)
        if data:
            return json.loads(data)
        return None

    @classmethod
    def get_user_container(cls, user_id):
        """
        获取用户当前的活跃容器（不区分题目）
        通过用户索引键快速查找
        
        Args:
            user_id: 用户ID
            
        Returns:
            dict: 容器数据，如果没有则返回 None
        """
        # 从用户索引键获取 challenge_uuid
        user_index_key = f"{cls.PREFIX}user:{user_id}"
        challenge_uuid = cache.get(user_index_key)
        
        if challenge_uuid:
            # 从索引键获取到 challenge_uuid，再获取完整数据
            return cls.get(user_id, challenge_uuid)
        
        return None

    @classmethod
    def delete(cls, user_id, challenge_uuid):
        """删除容器缓存，同时清理用户索引"""
        key = cls.get_key(user_id, challenge_uuid)
        cache.delete(key)
        
        # 清理用户容器索引
        user_index_key = f"{cls.PREFIX}user:{user_id}"
        cache.delete(user_index_key)

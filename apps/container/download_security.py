"""
文件下载安全模块

提供防暴力下载保护功能：
1. 时效性下载令牌（Token）
2. 下载频率限制
"""

import logging
from django.core.signing import BadSignature, TimestampSigner
from django.core.cache import cache
from django.conf import settings

logger = logging.getLogger('apps.container')


class DownloadTokenGenerator:
    """
    下载令牌生成器
    
    生成带时效性的签名令牌，防止URL被滥用
    """
    
    # 令牌有效期（秒）
    TOKEN_MAX_AGE = getattr(settings, 'DOWNLOAD_TOKEN_MAX_AGE', 300)  # 默认5分钟
    
    # 使用独立的 salt 增强安全性
    TOKEN_SALT = 'download-token-salt-v1'
    
    def __init__(self):
        self.signer = TimestampSigner(salt=self.TOKEN_SALT)
    
    def generate_token(self, file_id, user_id):
        """
        生成下载令牌
        
        Args:
            file_id: 文件ID
            user_id: 用户ID
            
        Returns:
            str: 签名后的令牌
        """
        # 使用简洁的格式：file_id:user_id（更适合URL）
        token_string = f"{file_id}:{user_id}"
        
        # 使用 TimestampSigner 签名（会自动添加时间戳）
        signed_token = self.signer.sign(token_string)
        
        logger.debug(
            f"生成下载令牌: 文件ID={file_id}, 用户ID={user_id}, "
            f"有效期={self.TOKEN_MAX_AGE}秒"
        )
        
        return signed_token
    
    def verify_token(self, token, file_id, user_id):
        """
        验证下载令牌
        
        Args:
            token: 签名后的令牌
            file_id: 文件ID
            user_id: 用户ID
            
        Returns:
            tuple: (是否有效, 错误消息)
        """
        try:
            # 验证签名和时效性
            token_string = self.signer.unsign(
                token, 
                max_age=self.TOKEN_MAX_AGE
            )
            
            # 解析令牌数据（格式：file_id:user_id）
            parts = token_string.split(':')
            if len(parts) != 2:
                logger.warning(f"令牌格式无效: {token_string}")
                return False, "无效的下载链接"
            
            token_file_id = int(parts[0])
            token_user_id = int(parts[1])
            
            # 验证文件ID和用户ID
            if token_file_id != file_id:
                logger.warning(
                    f"令牌验证失败: 文件ID不匹配 "
                    f"(期望={file_id}, 实际={token_file_id})"
                )
                return False, "无效的下载链接"
            
            if token_user_id != user_id:
                logger.warning(
                    f"令牌验证失败: 用户ID不匹配 "
                    f"(期望={user_id}, 实际={token_user_id})"
                )
                return False, "无效的下载链接"
            
            logger.debug(f"令牌验证成功: 文件ID={file_id}, 用户ID={user_id}")
            return True, None
            
        except BadSignature:
            logger.warning(f"令牌验证失败: 签名无效或已过期")
            return False, "下载链接无效或已过期"
        
        except (ValueError, IndexError) as e:
            logger.warning(f"令牌解析失败: {str(e)}")
            return False, "无效的下载链接格式"
        
        except Exception as e:
            logger.error(f"令牌验证异常: {str(e)}")
            return False, "下载链接验证失败"


class DownloadRateLimiter:
    """
    下载频率限制器
    
    防止同一用户在短时间内频繁下载
    """
    
    # 时间窗口（秒）
    WINDOW_SIZE = getattr(settings, 'DOWNLOAD_RATE_WINDOW', 60)  # 默认1分钟
    
    # 最大下载次数
    MAX_DOWNLOADS = getattr(settings, 'DOWNLOAD_RATE_LIMIT', 5)  # 默认1分钟5次
    
    # 全局限制（每个IP）
    GLOBAL_WINDOW_SIZE = getattr(settings, 'DOWNLOAD_GLOBAL_RATE_WINDOW', 3600)  # 1小时
    GLOBAL_MAX_DOWNLOADS = getattr(settings, 'DOWNLOAD_GLOBAL_RATE_LIMIT', 100)  # 1小时100次
    
    @classmethod
    def _get_cache_key(cls, user_id, file_id):
        """获取用户+文件的缓存键"""
        return f"download_rate:user_{user_id}:file_{file_id}"
    
    @classmethod
    def _get_user_cache_key(cls, user_id):
        """获取用户的缓存键"""
        return f"download_rate:user_{user_id}:all"
    
    @classmethod
    def _get_ip_cache_key(cls, ip_address):
        """获取IP的缓存键"""
        return f"download_rate:ip_{ip_address}"
    
    @classmethod
    def check_rate_limit(cls, user_id, file_id, ip_address=None):
        """
        检查是否超过频率限制
        
        Args:
            user_id: 用户ID
            file_id: 文件ID
            ip_address: IP地址（可选）
            
        Returns:
            tuple: (是否允许, 错误消息, 剩余时间)
        """
        # 1. 检查用户+文件的频率限制
        cache_key = cls._get_cache_key(user_id, file_id)
        download_count = cache.get(cache_key, 0)
        
        if download_count >= cls.MAX_DOWNLOADS:
            remaining_time = cache.ttl(cache_key)
            logger.warning(
                f"下载频率限制: 用户ID={user_id}, 文件ID={file_id}, "
                f"次数={download_count}/{cls.MAX_DOWNLOADS}, "
                f"剩余时间={remaining_time}秒"
            )
            return False, f"下载过于频繁，请 {remaining_time} 秒后再试", remaining_time
        
        # 2. 检查用户全局频率限制
        user_cache_key = cls._get_user_cache_key(user_id)
        user_download_count = cache.get(user_cache_key, 0)
        
        if user_download_count >= cls.GLOBAL_MAX_DOWNLOADS:
            remaining_time = cache.ttl(user_cache_key)
            logger.warning(
                f"用户全局下载限制: 用户ID={user_id}, "
                f"次数={user_download_count}/{cls.GLOBAL_MAX_DOWNLOADS}"
            )
            return False, f"下载次数已达上限，请 {remaining_time // 60} 分钟后再试", remaining_time
        
        # 3. 检查IP全局频率限制（可选）
        if ip_address:
            ip_cache_key = cls._get_ip_cache_key(ip_address)
            ip_download_count = cache.get(ip_cache_key, 0)
            
            if ip_download_count >= cls.GLOBAL_MAX_DOWNLOADS:
                remaining_time = cache.ttl(ip_cache_key)
                logger.warning(
                    f"IP全局下载限制: IP={ip_address}, "
                    f"次数={ip_download_count}/{cls.GLOBAL_MAX_DOWNLOADS}"
                )
                return False, f"下载次数已达上限，请稍后再试", remaining_time
        
        return True, None, None
    
    @classmethod
    def record_download(cls, user_id, file_id, ip_address=None):
        """
        记录一次下载
        
        Args:
            user_id: 用户ID
            file_id: 文件ID
            ip_address: IP地址（可选）
        """
        # 1. 记录用户+文件的下载次数
        cache_key = cls._get_cache_key(user_id, file_id)
        download_count = cache.get(cache_key, 0)
        cache.set(cache_key, download_count + 1, cls.WINDOW_SIZE)
        
        # 2. 记录用户全局下载次数
        user_cache_key = cls._get_user_cache_key(user_id)
        user_download_count = cache.get(user_cache_key, 0)
        cache.set(user_cache_key, user_download_count + 1, cls.GLOBAL_WINDOW_SIZE)
        
        # 3. 记录IP全局下载次数（可选）
        if ip_address:
            ip_cache_key = cls._get_ip_cache_key(ip_address)
            ip_download_count = cache.get(ip_cache_key, 0)
            cache.set(ip_cache_key, ip_download_count + 1, cls.GLOBAL_WINDOW_SIZE)
        
        logger.debug(
            f"记录下载: 用户ID={user_id}, 文件ID={file_id}, "
            f"IP={ip_address}, 次数={download_count + 1}"
        )
    
    @classmethod
    def get_remaining_quota(cls, user_id, file_id):
        """
        获取剩余下载配额
        
        Returns:
            dict: 包含各项配额信息
        """
        # 用户+文件配额
        cache_key = cls._get_cache_key(user_id, file_id)
        download_count = cache.get(cache_key, 0)
        file_remaining = max(0, cls.MAX_DOWNLOADS - download_count)
        
        # 用户全局配额
        user_cache_key = cls._get_user_cache_key(user_id)
        user_download_count = cache.get(user_cache_key, 0)
        user_remaining = max(0, cls.GLOBAL_MAX_DOWNLOADS - user_download_count)
        
        return {
            'file_remaining': file_remaining,
            'file_total': cls.MAX_DOWNLOADS,
            'user_remaining': user_remaining,
            'user_total': cls.GLOBAL_MAX_DOWNLOADS
        }


def get_client_ip(request):
    """
    获取客户端真实IP地址
    
    Args:
        request: Django request 对象
        
    Returns:
        str: IP地址
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


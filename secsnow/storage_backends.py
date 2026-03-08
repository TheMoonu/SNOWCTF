"""
自定义存储后端
解决对象存储在 Docker 内部访问的 URL 生成问题
"""
from storages.backends.s3boto3 import S3Boto3Storage
from django.conf import settings
import os


class MediaStorage(S3Boto3Storage):
    """
    媒体文件存储后端
    当使用内部地址（如 rustfs）时，生成通过 Nginx 代理访问的相对路径
    """
    
    
    def url(self, name, parameters=None, expire=None, http_method=None):
        """
        重写 url 方法，根据配置返回合适的 URL
        
        - 如果 ENDPOINT_URL 是内部地址（rustfs/localhost/127.0.0.1），返回 /media/ 相对路径
        - 如果配置了 SNOW_STORAGE_PUBLIC_URL，使用公网访问地址
        - 否则返回完整的 S3 URL
        """
        import os
        from urllib.parse import urljoin
        
        endpoint_url = getattr(settings, 'AWS_S3_ENDPOINT_URL', '')
        storage_public_url = os.getenv('SNOW_STORAGE_PUBLIC_URL', '')
        bucket_name = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '')
        
        # 检测是否为内部地址（Docker 内部）
        is_internal = any(keyword in endpoint_url for keyword in ['rustfs', 'localhost', '127.0.0.1'])
        
        if is_internal and not storage_public_url:
            # 方案1：内部地址，返回相对路径，通过 Nginx 代理访问
            location = getattr(settings, 'AWS_LOCATION', '')
            if location and name.startswith(location + '/'):
                name = name[len(location) + 1:]
            elif location and name.startswith(location):
                name = name[len(location):]
            
            # 返回 /media/ 开头的相对路径
            media_url = getattr(settings, 'MEDIA_URL', '/media/')
            if not media_url.endswith('/'):
                media_url += '/'
            
            return f'{media_url}{name}'
        
        elif storage_public_url:
            # 方案2：使用配置的公网访问地址
            # 确保 URL 以 / 结尾
            if not storage_public_url.endswith('/'):
                storage_public_url += '/'
            
            # 构建完整 URL: http://ip:port/bucket/file_path
            if bucket_name not in storage_public_url:
                full_url = f'{storage_public_url}{bucket_name}/{name}'
            else:
                full_url = f'{storage_public_url}{name}'
            
            return full_url
        
        else:
            # 方案3：外部地址，直接使用 endpoint_url 构建完整 URL
            # 格式: http://endpoint/bucket/file_path
            if not endpoint_url.endswith('/'):
                endpoint_url += '/'
            
            return f'{endpoint_url}{bucket_name}/{name}'


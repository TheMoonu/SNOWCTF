import bleach
import html
import re
import time
import logging
import threading
from functools import wraps
from datetime import datetime, timedelta
from django.apps import apps as django_apps
from django.core.exceptions import ImproperlyConfigured
from django.conf import settings
import random
import string
import uuid
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import base64
from django.core.cache import cache

from django.http import JsonResponse
from pygments.formatters.html import HtmlFormatter


# ==================== 本地内存缓存（性能优化） ====================
class _LocalMemoryCache:
    """线程安全的本地内存缓存（用于高频访问的网站配置）"""
    
    def __init__(self):
        self._cache = {}
        self._lock = threading.RLock()
    
    def get(self, key):
        with self._lock:
            item = self._cache.get(key)
            if item is None:
                return None
            
            # 检查是否过期
            if item['expires'] and item['expires'] <= datetime.now():
                del self._cache[key]
                return None
            
            return item['value']
    
    def set(self, key, value, timeout=60):
        with self._lock:
            expires = None
            if timeout is not None:
                expires = datetime.now() + timedelta(seconds=timeout)
            
            self._cache[key] = {
                'value': value,
                'expires': expires
            }
    
    def clear(self):
        with self._lock:
            self._cache.clear()


# 全局本地缓存实例
_local_cache = _LocalMemoryCache()

def html_to_md_link(content):
    """将 HTML 链接转回 markdown 格式"""
    def replace_html_link(match):
        url = match.group(1)
        return f'<{url}>'
    
    return re.sub(r'<a href="([^"]+)">[^<]+</a>', replace_html_link, content)

def unescape_content(content):
    """还原已转义的内容"""
    content = html.unescape(content)
    # 将 HTML 链接转回 markdown 格式
    content = html_to_md_link(content)
    return content

def sanitize_html(html_content):
    # 0. 先还原已转义的内容
    content = unescape_content(html_content)
    
    # 1. 保存 markdown 链接
    links = {}
    def save_link(match):
        placeholder = f"LINK_{len(links)}"
        links[placeholder] = match.group(0)
        return placeholder
    
    content = re.sub(r'<(https?://[^>]+)>', save_link, content)
    
    # 2. 转义 HTML
    content = html.escape(content, quote=False)
    
    # 3. 还原并处理 markdown 链接
    def process_link(match):
        url = match.group(1)
        return f'<a href="{url}">{url}</a>'
    
    for placeholder, link in links.items():
        html_link = re.sub(r'<(https?://[^>]+)>', process_link, link)
        content = content.replace(placeholder, html_link)
    
    # 4. 清理 HTML
    allowed_tags = [
        'p', 'div', 'span', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'ul', 'ol', 'li', 'blockquote', 'pre', 'code',
        'em', 'strong', 'del', 'a', 'img',
        'table', 'thead', 'tbody', 'tr', 'th', 'td'
    ]
    allowed_attributes = {
        'a': ['href', 'title'],
        'img': ['src', 'alt', 'title'],
        'code': ['class'],
        '*': ['class']
    }
    
    cleaned_html = bleach.clean(content, 
                              tags=allowed_tags, 
                              attributes=allowed_attributes, 
                              protocols=['http', 'https', 'mailto'],
                              strip=True)
    return cleaned_html

def escape_xss(content):
    return html.escape(content, quote=False)

import yaml
from typing import Tuple, Union

def validate_docker_compose(yaml_content: str) -> Tuple[bool, Union[str, None]]:
    """
    验证是否是有效的 docker-compose.yml 格式
    
    Args:
        yaml_content (str): 要验证的 YAML 字符串内容
        
    Returns:
        Tuple[bool, Union[str, None]]: 返回一个元组，包含:
            - bool: 是否是有效的 docker-compose 格式
            - Union[str, None]: 如果无效，返回错误信息；如果有效，返回 None
    """
    try:
        compose_data = yaml.safe_load(yaml_content)
        
        # 检查是否是字典类型
        if not isinstance(compose_data, dict):
            return False, "docker-compose 配置必须是一个字典格式"
            
        # 检查版本号
        if 'version' not in compose_data:
            return False, "缺少 version 字段"
            
        # 检查服务定义
        if 'services' not in compose_data:
            return False, "缺少 services 字段"
            
        services = compose_data['services']
        if not isinstance(services, dict):
            return False, "services 必须是一个字典格式"
            
        # 验证每个服务的必要字段
        for service_name, service_config in services.items():
            if not isinstance(service_config, dict):
                return False, f"服务 '{service_name}' 的配置必须是字典格式"
                
            # 检查是否至少包含 image 或 build 中的一个
            if 'image' not in service_config and 'build' not in service_config:
                return False, f"服务 '{service_name}' 必须指定 image 或 build"

        return True, None
        
    except yaml.YAMLError as e:
        return False, f"YAML 格式错误: {str(e)}"
    except Exception as e:
        return False, str(e)


def generate_captcha(length=4):
    """生成指定长度的随机验证码"""
    characters = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'  
    return ''.join(random.choice(characters) for _ in range(length))

def generate_captcha_image(captcha_text):
    """生成验证码图片"""
    width, height = 120, 40  
    image = Image.new('RGB', (width, height), color=(245, 245, 245))
    draw = ImageDraw.Draw(image)
    
    # 尝试多个字体路径（兼容不同操作系统和 Docker 环境）
    font = None
    font_size = 18
    font_paths = [
        # Linux 常见路径
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
        # 相对路径（如果字体在项目中）
        'arial.ttf',
        'DejaVuSans.ttf',
    ]
    
    for font_path in font_paths:
        try:
            font = ImageFont.truetype(font_path, font_size)
            break
        except (IOError, OSError):
            continue
    
    # 如果所有字体都加载失败，使用默认字体但增大图片和字符间距
    if font is None:
        import logging
        logging.warning('无法加载 TrueType 字体，使用默认字体。建议在 Docker 中安装字体: apt-get install fonts-dejavu-core')
        font = ImageFont.load_default()
        # 使用默认字体时，调整布局参数
        char_spacing = 20  # 字符间距
        start_x = 20
        start_y = 12
    else:
        char_spacing = 25
        start_x = 10
        start_y = 6
    
    # 绘制干扰线
    for i in range(2):  
        start_point = (random.randint(0, width // 3), random.randint(0, height))
        end_point = (random.randint(width // 3 * 2, width), random.randint(0, height))
        draw.line([start_point, end_point], fill=(random.randint(100, 200), random.randint(100, 200), random.randint(100, 200)), width=1)
    
    # 绘制干扰点
    for i in range(20): 
        draw.point((random.randint(0, width), random.randint(0, height)), fill=(random.randint(100, 200), random.randint(100, 200), random.randint(100, 200)))
    
    # 绘制验证码文字
    for i, char in enumerate(captcha_text):
        # 随机颜色（深色）
        color = (random.randint(0, 80), random.randint(0, 80), random.randint(0, 80))
        # 位置（带随机偏移）
        position = (start_x + i * char_spacing, start_y + random.randint(-3, 3))  
        draw.text(position, char, font=font, fill=color)
    
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    img_str = base64.b64encode(buffer.getvalue()).decode()
    
    return f"data:image/png;base64,{img_str}"

def create_captcha_for_registration():
    """创建验证码并存储到Redis"""
    captcha_text = generate_captcha()
    captcha_image = generate_captcha_image(captcha_text)
    captcha_key = str(uuid.uuid4())
    
    # 将验证码存储到Redis，设置5分钟过期
    cache.set(f'registration_captcha_{captcha_key}', captcha_text, 300)
    
    return {
        'captcha_key': captcha_key,
        'captcha_image': captcha_image
    }

def create_captcha_for_writeup():
    """创建Writeup上传验证码并存储到Redis"""
    captcha_text = generate_captcha()
    captcha_image = generate_captcha_image(captcha_text)
    captcha_key = str(uuid.uuid4())
    
    # 将验证码存储到Redis，设置5分钟过期
    cache.set(f'writeup_captcha_{captcha_key}', captcha_text, 300)
    
    return {
        'captcha_key': captcha_key,
        'captcha_image': captcha_image
    }

def verify_writeup_captcha(captcha_key, captcha_input):
    """验证Writeup上传的验证码"""
    if not captcha_key or not captcha_input:
        return False
    
    stored_captcha = cache.get(f'writeup_captcha_{captcha_key}')
    if not stored_captcha:
        return False
    
    # 验证成功后删除验证码（防止重复使用）
    cache.delete(f'writeup_captcha_{captcha_key}')
    
    return stored_captcha.lower() == captcha_input.lower()

def clear_ranking_cache(competition_id=None,user_id = None):
    """清除排行榜缓存的辅助函数"""
    cache_keys = [
        f'user_ranking:{"all" if competition_id is None else competition_id}:10',
        f'team_ranking:{"all" if competition_id is None else competition_id}:10',
        f'user_ctf_stats:{user_id}:{competition_id}'
    ]
    for key in cache_keys:
        cache.delete(key)

def site_protocol():
    """
    返回当前使用的协议 http|https，可以给很多需要用到网站完整地址的地方调用
    :return: 当前协议
    """
    protocol = getattr(settings, 'PROTOCOL_HTTPS', 'http')
    return protocol
def site_domain():
    """
    获取当前站点的域名，这个域名实际上是去读数据库的sites表
    settings 配置中需要配置 SITE_ID ，INSTALLED_APPS 中需要添加 django.contrib.sites
    :return: 当前站点域名
    """
    if not django_apps.is_installed('django.contrib.sites'):
        raise ImproperlyConfigured(
            "get site_domain requires django.contrib.sites, which isn't installed.")

    Site = django_apps.get_model('sites.Site')
    current_site = Site.objects.get_current()
    domain = current_site.domain
    return domain

def site_full_url():
    """
    返回当前站点完整地址，协议+域名
    :return:
    """
    protocol = site_protocol()
    domain = site_domain()
    return '{}://{}'.format(protocol, domain)







logger = logging.getLogger('django')


class CustomHtmlFormatter(HtmlFormatter):
    def __init__(self, lang_str='', **options):
        super().__init__(**options)
        # lang_str has the value {lang_prefix}{lang}
        # specified by the CodeHilite's options
        self.lang_str = lang_str

    def _wrap_code(self, source):
        yield 0, f'<code class="{self.lang_str}">'
        yield from source
        yield 0, '</code>'


class DateCalculator:
    @staticmethod
    def calculate_date_diff(start_date, end_date):
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        diff = end - start
        start_year = start.year

        days = diff.days
        years = 0

        if start.year == end.year:
            remaining_days = days
        else:
            if (start.month > end.month) or (start.month == end.month and start.day > end.day):
                years = end.year - start.year - 1
                last_start = start.replace(year=end.year - 1)
                remaining_days = (end - last_start).days
            else:
                years = end.year - start.year
                last_start = start.replace(year=end.year)
                remaining_days = (end - last_start).days
        if years > 0:
            result = f"{years} 年 {remaining_days} 天"
        else:
            result = f"{remaining_days} 天"

        return result, start_year


def get_site_create_day(create_day):
    """
    返回给的时间到当前日期的年天，create_day格式%Y-%m-%d
    """
    now_day = datetime.now().strftime("%Y-%m-%d")
    return DateCalculator.calculate_date_diff(create_day, now_day)




def clear_competition_cache(competition):
    """清除与特定比赛相关的所有缓存"""
    # 清除challenge_types缓存
    challenge_types_key = f"competition_{competition.slug}_challenge_types"
    cache.delete(challenge_types_key)
    
    # 清除difficulties缓存
    difficulties_key = f"competition_{competition.slug}_difficulties"
    cache.delete(difficulties_key)
    cache_keys = f"competition_stats_{competition.id}"
    cache.delete(cache_keys)
    # 清除所有可能的用户队伍缓存
    # 由于用户队伍缓存依赖于用户ID，我们需要使用缓存前缀删除
   


def clear_user_teams_cache(user_id):
  
    """清除与特定比赛相关的所有缓存"""
    # 清除challenge_types缓存
    # 清除所有可能的用户队伍缓存
    # 由于用户队伍缓存依赖于用户ID，我们需要使用缓存前缀删除
    try:
        cache.delete_pattern(f"user_content_{user_id}*")
        cache_keys = [
            f'user_teams_{user_id}_anonymous',  # 匿名查看的缓存
            f'user_teams_{user_id}_{user_id}'   # 用户自己查看的缓存
        ]
        
        # 只清除存在的缓存
        for key in cache_keys:
            if cache.get(key) is not None:
                cache.delete(key)
    except Exception as e:
        print(e)


def check_request_headers(headers_obj):
    """
    校验请求头信息，比如识别User-Agent，从而过滤掉该请求
    @param headers_obj: request.headers对象
    @return:
    use: flag = check_request_headers(request.headers)
    """
    # 常见的搜索引擎爬虫的请求头，还有Python的
    # 无请求头或者请求头里面包含爬虫信息则返回False，否则返回True
    user_agent_black_keys = ['spider', 'bot', 'python']
    if not headers_obj.get('user-agent'):
        return False
    else:
        user_agent = str(headers_obj.get('user-agent')).lower()
        for key in user_agent_black_keys:
            if key in user_agent:
                logger.warning(f'Bot/Spider request user-agent：{user_agent}')
                return False
    return True


class SiteSettingsCache:
    """网站配置缓存管理类"""
    
    CACHE_KEY_PREFIX = 'site_settings:'
    CACHE_TIMEOUT = 3600 * 24  # 24小时
    
    @staticmethod
    def get_settings():
        """获取网站配置（本地内存缓存优化版本）"""
        cache_key = f"{SiteSettingsCache.CACHE_KEY_PREFIX}active"
        
        # 先尝试从本地内存缓存获取（避免Redis压力）
        settings_data = _local_cache.get(cache_key)
        
        if settings_data is None:
            from public.models import SiteSettings
            try:
                settings_obj = SiteSettings.objects.filter(is_active=True).first()
                if settings_obj:
                    # 安全获取文件 URL 的辅助函数
                    def get_file_url(file_field):
                        """安全获取文件字段的 URL"""
                        try:
                            if file_field and hasattr(file_field, 'url'):
                                return file_field.url
                        except (ValueError, AttributeError):
                            pass
                        return None

                    
                    
                    settings_data = {
                        'site_name': settings_obj.site_name,
                        'site_logo': get_file_url(settings_obj.site_logo),
                        'site_favicon': get_file_url(settings_obj.site_favicon),
                        'site_description': settings_obj.site_description,
                        'site_keywords': settings_obj.site_keywords,
                        'site_create_date': settings_obj.site_create_date.strftime('%Y-%m-%d'),
                        'beian': settings_obj.beian,

                        'cnzz_code': settings_obj.cnzz_code,
                        'la51_code': settings_obj.la51_code,
                        'site_verification': settings_obj.site_verification,
                        # 邮箱配置
                        'email_enabled': settings_obj.email_enabled,
                        'email_host': settings_obj.email_host,
                        'email_port': settings_obj.email_port,
                        'email_host_user': settings_obj.email_host_user,
                        'email_host_password': settings_obj.email_host_password,
                        'email_use_ssl': settings_obj.email_use_ssl,
                        'email_from': settings_obj.email_from,
                        # 第三方登录配置
                        'github_login_enabled': settings_obj.github_login_enabled,
                        # 注册配置
                        'registration_enabled': settings_obj.registration_enabled,
                        # 页脚配置
                        'footer_style': settings_obj.footer_style,
                        # 二次元风格图片配置
                        'anime_side_left_image': get_file_url(settings_obj.anime_side_left_image),
                        'anime_side_right_image': get_file_url(settings_obj.anime_side_right_image),
                        'anime_filter_right_image': get_file_url(settings_obj.anime_filter_right_image),
                        'anime_challenge_start_bg': get_file_url(settings_obj.anime_challenge_start_bg),
                    }
                else:
                    # 如果没有配置，返回默认值
                    from datetime import date
                    settings_data = {
                        'site_name': 'SECSNOW',
                        'site_logo': None,
                        'site_favicon': None,
                        'site_description': 'SECSNOW 一个开源、共创、共享网络安全技术学习网站',
                        'site_keywords': 'secsnow,CTF竞赛、漏洞靶场、网络安全',
                        'site_create_date': '2024-01-01',
                        'beian': '',
                        'cnzz_code': '',
                        'la51_code': '',
                        'site_verification': '',
                        # 邮箱配置默认值
                        'email_enabled': False,
                        'email_host': 'smtp.163.com',
                        'email_port': 465,
                        'email_host_user': '',
                        'email_host_password': '',
                        'email_use_ssl': True,
                        'email_from': 'SECSNOW',
                        # 第三方登录配置默认值
                        'github_login_enabled': False,
                        # 注册配置默认值
                        'registration_enabled': True,
                        # 页脚配置默认值
                        'footer_style': 'dark',
                        # 二次元风格图片配置默认值
                        'anime_side_left_image': None,
                        'anime_side_right_image': None,
                        'anime_filter_right_image': None,
                        'anime_challenge_start_bg': None,
                    }
                
                # 缓存到本地内存（300秒=5分钟，配置变化不频繁）
                _local_cache.set(cache_key, settings_data, timeout=300)
            except Exception as e:
                logger.warning(f"从数据库读取网站配置失败，使用settings配置: {e}")
                # 返回默认值
                from datetime import date
                settings_data = {
                    'site_name': 'SECSNOW',
                    'site_logo': None,
                    'site_favicon': None,
                    'site_description': 'SECSNOW 一个开源、共创、共享网络安全技术学习网站',
                    'site_keywords': 'secsnow,CTF竞赛、漏洞靶场、网络安全',
                    'site_create_date': '2024-01-01',
                    'beian': '',
                    'cnzz_code': '',
                    'la51_code': '',
                    'site_verification': '',
                    'email_enabled': False,
                    'email_verification_method': 'none',
                    'email_host': '',
                    'email_port': 465,
                    'email_host_user': '',
                    'email_host_password': '',
                    'email_use_ssl': True,
                    'email_from': '',
                    'github_login_enabled': False,
                    'registration_enabled': True,
                    'footer_style': 'dark',
                    # 二次元风格图片配置默认值
                    'anime_side_left_image': None,
                    'anime_side_right_image': None,
                    'anime_filter_right_image': None,
                    'anime_challenge_start_bg': None,
                }
        
        return settings_data
    
    @staticmethod
    def get_footer_columns():
        """获取页脚栏目（本地内存缓存优化版本）"""
        cache_key = f"{SiteSettingsCache.CACHE_KEY_PREFIX}footer_columns"
        
        # 先尝试从本地内存缓存获取
        columns_data = _local_cache.get(cache_key)
        
        if columns_data is None:
            try:
                from public.models import FooterColumn
                
                columns = FooterColumn.objects.filter(
                    is_active=True
                ).prefetch_related('links')
                
                columns_data = []
                for column in columns:
                    links_data = []
                    # 获取该栏目下的启用的链接
                    for link in column.links.filter(is_active=True):
                        links_data.append({
                            'title': link.title,
                            'url': link.url,
                            'url_type': link.url_type,
                            'target': link.target,
                        })
                    
                    columns_data.append({
                        'title': column.title,
                        'links': links_data,
                    })
                
                # 缓存到本地内存（300秒=5分钟）
                _local_cache.set(cache_key, columns_data, timeout=300)
            except Exception as e:
                logger.warning(f"获取页脚栏目失败: {e}")
                columns_data = []
        
        return columns_data
    
    @staticmethod
    def get_homepage_content():
        """获取首页内容（本地内存缓存优化版本）"""
        cache_key = f"{SiteSettingsCache.CACHE_KEY_PREFIX}homepage_content"
        
        # 先尝试从本地内存缓存获取
        homepage_data = _local_cache.get(cache_key)
        
        if homepage_data is None:
            try:
                from public.models import HomePageConfig, ServiceCard
                
                homepage_obj = HomePageConfig.objects.filter(is_active=True).first()
                
                if homepage_obj:
                    # 获取所有启用的服务卡片
                    service_cards_data = []
                    for card in ServiceCard.objects.filter(is_active=True):
                        service_cards_data.append({
                            'title': card.title,
                            'description': card.description,
                            'image_url': card.image.url if card.image else None,
                        })
                    
                    homepage_data = {
                        'main_title': homepage_obj.main_title,
                        'main_subtitle': homepage_obj.main_subtitle,
                        'main_description': homepage_obj.main_description,
                        'main_image_url': homepage_obj.main_image.url if homepage_obj.main_image else None,
                        'service_badge': homepage_obj.service_badge,
                        'service_title': homepage_obj.service_title,
                        'service_description': homepage_obj.service_description,
                        'service_cards': service_cards_data,
                    }
                else:
                    # 返回默认值
                    homepage_data = {
                        'main_title': '',
                        'main_subtitle': '',
                        'main_description': '',
                        'main_image_url': None,
                        'service_badge': '',
                        'service_title': '',
                        'service_description': '',
                        'service_cards': [],
                    }
                
                # 缓存到本地内存（300秒=5分钟）
                _local_cache.set(cache_key, homepage_data, timeout=300)
            except Exception as e:
                logger.warning(f"获取首页内容失败: {e}")
                # 返回默认值
                homepage_data = {
                    'main_title': '',
                    'main_subtitle': '',
                    'main_description': '',
                    'main_image_url': None,
                    'service_badge': '',
                    'service_title': '',
                    'service_description': '',
                    'service_cards': [],
                }
        
        return homepage_data
    
    @staticmethod
    def clear_cache():
        """清除所有网站配置缓存（包括本地内存缓存和Redis缓存）"""
        # 1. 清除本地内存缓存（最重要）
        _local_cache.clear()
        logger.info('[缓存] 已清除网站配置本地内存缓存')
        
        # 2. 清除Redis缓存（如果使用）
        # 直接删除已知的缓存键（最可靠的方式）
        known_keys = [
            f"{SiteSettingsCache.CACHE_KEY_PREFIX}active",
            f"{SiteSettingsCache.CACHE_KEY_PREFIX}footer_columns",
            f"{SiteSettingsCache.CACHE_KEY_PREFIX}homepage_content",
        ]
        
        deleted_count = 0
        for key in known_keys:
            try:
                result = cache.delete(key)
                if result:  # 如果删除成功（key存在）
                    deleted_count += 1
            except Exception as e:
                logger.warning(f"删除缓存键 {key} 失败: {e}")
        
        logger.info(f"清除了 {deleted_count}/{len(known_keys)} 个网站配置缓存键")
        
        # 尝试使用 keys 方法清除其他可能的缓存（作为补充）
        try:
            keys = cache.keys(f"{SiteSettingsCache.CACHE_KEY_PREFIX}*")
            if keys:
                extra_keys = [k for k in keys if k not in known_keys]
                if extra_keys:
                    cache.delete_many(extra_keys)
                    logger.info(f"额外清除了 {len(extra_keys)} 个缓存键")
        except Exception as e:
            # keys 方法可能不被支持，忽略错误
            pass
    
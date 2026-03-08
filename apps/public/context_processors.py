# -*- coding: utf-8 -*-
import datetime
import json
from django.conf import settings
import logging
logger = logging.getLogger("apps.public")




# 静态文件版本（只收集常改的，不常改的直接在页面改），每次更新了静态文件就更新一下这个版本
# todo 可以做成自动化，每次拉git代码的时候检查是否更新了某个静态文件，自动更新版本
STATIC_VERSION = {
    'css_blog_base': '20240305.02',
    'css_blog_detail': '20240131.01',
    'css_blog_night': '20240115.01',

    'js_blog_base': '20240305.01',
    'js_blog_article': '20240115.01',
    'js_blog_code': '20240129.02',

    'css_tool_tool': '20240115.01',
    'js_tool_tool': '20240115.01',
}

from public.utils import (site_full_url, get_site_create_day, SiteSettingsCache)

# 自定义上下文管理器
def settings_info(request):
    """网站配置上下文处理器 - 优先从数据库读取，失败时使用settings配置"""

    
    # 尝试从数据库获取配置（带缓存）
    try:
        site_settings = SiteSettingsCache.get_settings()
        footer_columns = SiteSettingsCache.get_footer_columns()
        homepage_content = SiteSettingsCache.get_homepage_content()
        
        # 计算网站运行天数
        site_create_day = get_site_create_day(site_settings['site_create_date'])
        
        return {
            'this_year': datetime.datetime.now().year,
            
            # 从数据库读取的配置
            'site_name': site_settings['site_name'],
            'site_logo': site_settings['site_logo'],
            'site_favicon': site_settings['site_favicon'],
            'site_description': site_settings['site_description'],
            'site_keywords': site_settings['site_keywords'],
            'site_create_date': site_create_day[0],
            'site_create_year': site_create_day[1],
            'beian': site_settings['beian'],
            'cnzz_protocol': site_settings['cnzz_code'],
            '51la': site_settings['la51_code'],
            'site_verification': site_settings['site_verification'],
            
            # 邮箱配置
            'email_settings': {
                'enabled': site_settings['email_enabled'],
                'host': site_settings['email_host'],
                'port': site_settings['email_port'],
                'user': site_settings['email_host_user'],
                'password': site_settings['email_host_password'],
                'use_ssl': site_settings['email_use_ssl'],
                'from': site_settings['email_from'],
            },
            
            # 第三方登录配置
            'github_login_enabled': site_settings['github_login_enabled'],
            
            # 注册配置
            'registration_enabled': site_settings['registration_enabled'],
            
            # 页脚配置
            'footer_style': site_settings.get('footer_style', 'dark'),
            
            # 二次元风格图片配置
            'anime_side_left_image': site_settings.get('anime_side_left_image'),
            'anime_side_right_image': site_settings.get('anime_side_right_image'),
            'anime_filter_right_image': site_settings.get('anime_filter_right_image'),
            'anime_challenge_start_bg': site_settings.get('anime_challenge_start_bg'),
            
            # 页脚栏目数据
            'footer_columns': footer_columns,
            
            # 首页内容数据
            'homepage_content': homepage_content,
            
            # 其他配置（仍从settings读取）
            'site_url': site_full_url(),
            'tool_flag': getattr(settings, 'TOOL_FLAG', True),
            'api_flag': getattr(settings, 'API_FLAG', True),
            'seo_flag': getattr(settings, 'SEO_FLAG', False),
            'version': getattr(settings, 'SECSNOW_VERSION', ''),
            'private_links': json.loads(getattr(settings, 'PRIVATE_LINKS', '[]')),
            'static_version': STATIC_VERSION,
        }
    except Exception as e:
        # 如果数据库读取失败，降级到settings配置
        
        logger.warning(f'从数据库读取网站配置失败，使用settings配置: {e}')
        
        site_create_day = get_site_create_day('2024-01-01')
        
        return {
            'this_year': datetime.datetime.now().year,
            'site_name': 'SECSNOW',
            'site_logo': None,
            'site_description': 'SECSNOW 一个开源、共创、共享网络安全技术学习网站',
            'site_keywords': 'secsnow,CTF竞赛、漏洞靶场、网络安全',
            'site_url': site_full_url(),
            'tool_flag': getattr(settings, 'TOOL_FLAG', True),
            'api_flag': getattr(settings, 'API_FLAG', True),
            'site_create_date': site_create_day[0],
            'site_create_year': site_create_day[1],
            'cnzz_protocol': getattr(settings, 'CNZZ_PROTOCOL', ''),
            '51la': getattr(settings, 'LA51_PROTOCOL', ''),
            'beian': '',
            'my_github': '',
            'site_verification':  '',
            'private_links': json.loads('[]'),
            'version': getattr(settings, 'SECSNOW_VERSION', ''),
            'static_version': STATIC_VERSION,
            'footer_columns': [],  # 降级时没有页脚数据
            'homepage_content': None,  # 降级时没有首页内容
            'email_settings': {'enabled': False},  # 降级时邮箱不启用
            'github_login_enabled': False,  # 降级时GitHub登录不启用
            'registration_enabled': True,  # 降级时默认允许注册
            'footer_style': 'dark',  # 降级时使用默认深色页脚
            'anime_side_left_image': None,  # 降级时无二次元图片
            'anime_side_right_image': None,
            'anime_filter_right_image': None,
            'anime_challenge_start_bg': None,
        }

# 自定义上下文管理器



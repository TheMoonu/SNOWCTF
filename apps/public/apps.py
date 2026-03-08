# -*- coding: utf-8 -*-
import sys
from django.apps import AppConfig
from django.db import connection
from django.db.utils import OperationalError


class PublicConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'public'
    verbose_name = '网站管理'
    
    def ready(self):
        """应用就绪时的初始化操作"""
        # 导入信号处理器（确保信号被注册）
        import public.models  # noqa: F401
        
        # 检查是否正在运行数据库迁移或其他管理命令
        # 这些情况下不应该访问数据库
        if self._should_skip_db_access():
            return
        
        # 注意：邮箱配置现在通过自定义邮件后端 (public.email_backend.DynamicEmailBackend) 动态加载
        # 在发送邮件时会自动从数据库读取配置，无需在此处手动更新 settings
    
    def _should_skip_db_access(self):
        """检查是否应该跳过数据库访问"""
        # 检查命令行参数，如果正在运行迁移相关命令则跳过
        if len(sys.argv) > 1:
            skip_commands = [
                'migrate',
                'makemigrations',
                'showmigrations',
                'sqlmigrate',
                'squashmigrations',
                'inspectdb',
                'flush',
                'createsuperuser',
            ]
            if any(cmd in sys.argv[1] for cmd in skip_commands):
                return True
        
        # 检查数据库连接和表是否存在
        try:
            # 尝试检查关键表是否存在
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT EXISTS (SELECT FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = 'public_sitesettings')"
                )
                table_exists = cursor.fetchone()[0]
                if not table_exists:
                    return True
        except (OperationalError, Exception):
            # 数据库连接失败或表不存在
            return True
        
        return False
    
    def update_email_settings(self):
        """
        【已废弃】此方法已被自定义邮件后端取代
        
        邮箱配置现在通过 public.email_backend.DynamicEmailBackend 动态加载
        在发送邮件时会自动从数据库读取配置，优先使用数据库配置，
        如果数据库未配置则回退到 settings.py 中的环境变量配置
        
        保留此方法仅为兼容性，实际上不再需要调用
        """
        pass

# -*- coding: utf-8 -*-
"""
自定义邮件后端 - 从数据库读取SMTP配置
"""
from django.core.mail.backends.smtp import EmailBackend
from django.conf import settings
import logging

logger = logging.getLogger("apps.public")


class DynamicEmailBackend(EmailBackend):
    """动态邮件后端"""
    
    def __init__(self, *args, **kwargs):
        # 检查邮箱功能
        self._email_enabled = self._check_email_enabled()
        
        if not self._email_enabled:
            logger.info('邮箱功能未启用')
            self.fail_silently = kwargs.get('fail_silently', False)
            return
        
        super().__init__(*args, **kwargs)
        self._load_db_config()
    
    def _check_email_enabled(self):
        """检查邮箱是否启用"""
        try:
            from public.utils import SiteSettingsCache
            return SiteSettingsCache.get_settings().get('email_enabled', False)
        except:
            return False
    
    def _load_db_config(self):
        """加载数据库SMTP配置"""
        try:
            from public.utils import SiteSettingsCache
            s = SiteSettingsCache.get_settings()
            
            if s.get('email_host') and s.get('email_host_user') and s.get('email_host_password'):
                self.host = s['email_host']
                self.port = s['email_port']
                
                # 提取纯邮箱地址用于 SMTP 认证
                email_host_user = s['email_host_user']
                if '<' in email_host_user and '>' in email_host_user:
                    # 从 "SECSNOW <sec_snow@163.com>" 提取 "sec_snow@163.com"
                    pure_email = email_host_user.split('<')[1].split('>')[0].strip()
                    logger.info(f'从格式化地址中提取纯邮箱: {email_host_user} -> {pure_email}')
                else:
                    pure_email = email_host_user.strip()
                
                self.username = pure_email  # SMTP 认证使用纯邮箱地址
                self.password = s['email_host_password']
                self.use_ssl = s['email_use_ssl']
                self.use_tls = not s['email_use_ssl']
                
                # 设置发件人显示格式
                # 统一使用 email_from（显示名称）+ 纯邮箱地址的方式构建
                email_from = s.get('email_from', '').strip()
                
                if email_from:
                    # 使用 email_from（显示名称）+ 纯邮箱地址
                    settings.DEFAULT_FROM_EMAIL = f"{email_from} <{pure_email}>"
                else:
                    # email_from 为空，直接使用纯邮箱地址
                    settings.DEFAULT_FROM_EMAIL = pure_email
                
                logger.info(f'SMTP: {self.host}:{self.port}, 认证: {self.username}, 发件人: {settings.DEFAULT_FROM_EMAIL}')
        except Exception as e:
            logger.warning(f'加载SMTP配置失败: {e}')
    
    def send_messages(self, email_messages):
        """发送邮件"""
        if not self._email_enabled:
            logger.info(f'跳过 {len(email_messages)} 封邮件')
            return len(email_messages)
        
        try:
            return super().send_messages(email_messages)
        except Exception as e:
            logger.error(f'发送失败: {e}')
            if not self.fail_silently:
                raise
            return 0
    
    def open(self):
        if not self._email_enabled:
            return False
        return super().open()
    
    def close(self):
        if not self._email_enabled:
            return
        super().close()

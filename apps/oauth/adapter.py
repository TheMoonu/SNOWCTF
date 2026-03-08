"""
Django-allauth 自定义适配器
"""
from django.contrib import messages
from django.conf import settings
from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from public.utils import SiteSettingsCache
import logging

logger = logging.getLogger('apps.oauth')


class CustomAccountAdapter(DefaultAccountAdapter):
    """自定义账号适配器"""
    
    def is_open_for_signup(self, request):
        """检查是否允许注册"""
        try:
            site_settings = SiteSettingsCache.get_settings()
            return site_settings.get('registration_enabled', True)
        except:
            return True
    
    
    def send_confirmation_mail(self, request, emailconfirmation, signup):
        """
        控制邮件发送：只有启用邮箱功能才发送
        """
        try:
            site_settings = SiteSettingsCache.get_settings()
            email_enabled = site_settings.get('email_enabled', False)
            
            if not email_enabled:
                # 标记为已验证
                from allauth.account.models import EmailAddress
                try:
                    email_obj = EmailAddress.objects.get(
                        user=emailconfirmation.email_address.user,
                        email=emailconfirmation.email_address.email
                    )
                    email_obj.verified = True
                    email_obj.save()
                except Exception as mark_error:
                    logger.warning(f'⚠️ 标记邮箱已验证失败: {mark_error}')
                return
            
            # 验证收件人邮箱地址
            recipient_email = emailconfirmation.email_address.email
            if not recipient_email or not recipient_email.strip():
                logger.error(f'❌ 收件人邮箱地址为空，无法发送邮件')
                return
            
            
            
            # 发送邮件
            super().send_confirmation_mail(request, emailconfirmation, signup)
            
            
        except Exception as e:
            logger.error(f'❌ 邮件发送失败: {e}', exc_info=True)
    
    def save_user(self, request, user, form, commit=True):
        """保存用户"""
        user = super().save_user(request, user, form, commit=False)
        
        if hasattr(form, 'inviter'):
            user.invited_by = form.inviter
        
        if commit:
            user.save()
        
        return user
    
    def send_mail(self, template_prefix, email, context):
        """
        重写 send_mail 方法，确保发件人地址正确
        """
        try:
            # 从数据库加载邮箱配置
            site_settings = SiteSettingsCache.get_settings()
            email_enabled = site_settings.get('email_enabled', False)
            
            if not email_enabled:
                
                return
            
            # 验证收件人邮箱
            if not email or not email.strip():
                logger.error(f'❌ 收件人邮箱地址为空')
                return
            
            # 确保发件人地址正确
            email_host_user = site_settings.get('email_host_user', '').strip()
            email_from = site_settings.get('email_from', '').strip()
            
            if not email_host_user:
                logger.error(f'❌ 发件人邮箱未配置')
                return
            
            # 提取纯邮箱地址
            if '<' in email_host_user and '>' in email_host_user:
                pure_email = email_host_user.split('<')[1].split('>')[0].strip()
            else:
                pure_email = email_host_user
            
            # 设置正确的发件人
            if '<' in email_host_user and '>' in email_host_user:
                # 已经是格式化的，直接使用
                settings.DEFAULT_FROM_EMAIL = email_host_user
            elif email_from:
                # 使用显示名称 + 纯邮箱
                settings.DEFAULT_FROM_EMAIL = f"{email_from} <{pure_email}>"
            else:
                # 只使用纯邮箱
                settings.DEFAULT_FROM_EMAIL = pure_email
            
        
            
            # 调用父类方法发送邮件
            super().send_mail(template_prefix, email, context)
            
        except Exception as e:
            logger.error(f'❌ 邮件发送失败: {e}', exc_info=True)


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """自定义社交账号适配器"""
    
    def is_open_for_signup(self, request, sociallogin):
        """检查是否允许社交账号注册"""
        try:
            site_settings = SiteSettingsCache.get_settings()
            return site_settings.get('registration_enabled', True)
        except:
            return True
    
    def pre_social_login(self, request, sociallogin):
        """社交账号登录前检查"""
        if sociallogin.is_existing:
            return
        
        if not self.is_open_for_signup(request, sociallogin):
            messages.error(request, '网站暂时关闭注册功能')

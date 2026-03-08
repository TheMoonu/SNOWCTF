from django.apps import AppConfig


class OauthConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'oauth'
    verbose_name = '用户管理'

    def ready(self):
        from . import signals  # 导入信号处理程序模块

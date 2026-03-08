from django.apps import AppConfig


class BlogConfig(AppConfig):
    name = 'blog'
    verbose_name = 'WIKI管理'

    default_auto_field = 'django.db.models.AutoField'

    def ready(self):
        from . import signals  # 导入信号处理程序模块

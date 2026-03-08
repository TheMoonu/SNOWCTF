from django.apps import AppConfig


class CommentConfig(AppConfig):
    name = 'comment'
    verbose_name = '系统管理'
    default_auto_field = 'django.db.models.AutoField'

    def ready(self):
        from . import signals  # 导入信号处理程序模块

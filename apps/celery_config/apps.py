# -*- coding: utf-8 -*-
"""
自定义 Celery 相关应用的显示名称和后台管理
"""
from django_celery_results.apps import CeleryResultConfig
from django_celery_beat.apps import BeatConfig


class CustomCeleryResultConfig(CeleryResultConfig):
    """自定义 Celery Results 配置"""
    # 不要修改 name，使用继承的 'django_celery_results'
    verbose_name = '任务管理'
    
    def ready(self):
        super().ready()
        # 延迟导入自定义的 admin 配置
        import celery_config.admin


class CustomCeleryBeatConfig(BeatConfig):
    """自定义 Celery Beat 显示名称"""
    verbose_name = '定时任务'
    
    def ready(self):
        super().ready()
        # Beat 的 admin 配置也在 celery_config.admin 中



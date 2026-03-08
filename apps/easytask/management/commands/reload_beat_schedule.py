# -*- coding: utf-8 -*-
"""
强制 Celery Beat 重新加载调度配置

使用方法:
    python manage.py reload_beat_schedule
"""
from django.core.management.base import BaseCommand
from django.core.cache import cache


class Command(BaseCommand):
    help = '强制 Celery Beat 重新加载调度配置（立即生效）'

    def handle(self, *args, **options):
        # 设置重新加载标志
        cache.set('scheduled_tasks_updated', True, timeout=120)
        
        self.stdout.write(self.style.SUCCESS('✅ 已设置重新加载标志'))
        self.stdout.write('Celery Beat 将在10秒内检测到更新并重新加载配置')
        
        # 显示当前启用的任务
        try:
            from easytask.models import ScheduledTaskSwitch
            
            enabled_tasks = ScheduledTaskSwitch.objects.filter(enabled=True)
            disabled_tasks = ScheduledTaskSwitch.objects.filter(enabled=False)
            
            self.stdout.write('\n📋 当前配置：')
            self.stdout.write(f'  ✓ 启用的任务: {enabled_tasks.count()} 个')
            for task in enabled_tasks:
                self.stdout.write(f'    - {task.display_name} ({task.task_name})')
            
            self.stdout.write(f'\n  ✗ 禁用的任务: {disabled_tasks.count()} 个')
            for task in disabled_tasks:
                self.stdout.write(f'    - {task.display_name} ({task.task_name})')
            
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'⚠️ 无法读取任务配置: {e}'))


# -*- coding: utf-8 -*-
"""
Celery 任务结果和定时任务的简约美化 Admin 配置
"""
from django.contrib import admin
from django.utils.html import format_html
from django.utils import timezone
from django_celery_results.models import TaskResult, GroupResult
from django_celery_beat.models import (
    PeriodicTask, IntervalSchedule, CrontabSchedule,
    SolarSchedule, ClockedSchedule
)
from django.db.models import Q
from datetime import timedelta
from django import forms


# 先取消注册默认的 admin 配置
for model in [TaskResult, GroupResult, PeriodicTask, IntervalSchedule, 
              CrontabSchedule, SolarSchedule, ClockedSchedule]:
    try:
        admin.site.unregister(model)
    except admin.sites.NotRegistered:
        pass


# ============================================
# Celery 任务结果管理
# ============================================

@admin.register(TaskResult)
class TaskResultAdmin(admin.ModelAdmin):
    """任务结果管理 - 简约风格"""
    
    # 设置中文显示名称
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model._meta.verbose_name = '任务结果'
        self.model._meta.verbose_name_plural = '任务结果'
    
    list_display = [
        'task_id_short',
        'task_name_short',
        'status_badge', 
        'date_done_relative',
        'result_preview'
    ]
    list_filter = [
        'status',
        ('date_done', admin.DateFieldListFilter),
    ]
    search_fields = ['task_name', 'task_id']
    readonly_fields = [
        'task_id', 'task_name', 'status', 'date_done', 'result', 'traceback'
    ]
    date_hierarchy = 'date_done'
    list_per_page = 50
    ordering = ['-date_done']
    
    fieldsets = (
        ('任务信息', {
            'fields': ('task_id', 'task_name', 'status', 'date_done'),
        }),
        ('执行结果', {
            'fields': ('result',),
        }),
        ('错误信息', {
            'fields': ('traceback',),
            'classes': ('collapse',),
        }),
    )
    
    def has_add_permission(self, request):
        """禁止添加"""
        return False
    
    def has_change_permission(self, request, obj=None):
        """禁止修改"""
        return False
    
    def task_id_short(self, obj):
        """简化任务ID显示"""
        if obj.task_id:
            return format_html(
                '<code style="background: #f5f5f5; padding: 2px 6px; '
                'border-radius: 3px; font-size: 11px;" title="{}">{}</code>',
                obj.task_id,
                obj.task_id[:8] + '...' if len(obj.task_id) > 8 else obj.task_id
            )
        return '-'
    task_id_short.short_description = '任务ID'
    
    def task_name_short(self, obj):
        """任务名称显示（中文）"""
        if obj.task_name:
            # 获取中文名称
            chinese_name = TASK_NAME_MAP.get(obj.task_name)
            if chinese_name:
                display_name = chinese_name
            else:
                # 自动生成显示名称
                parts = obj.task_name.split('.')
                display_name = parts[-1].replace('_', ' ').title() if parts else obj.task_name
            
            return format_html(
                '<span title="{}">{}</span>',
                obj.task_name,  # 鼠标悬停显示完整技术路径
                display_name  # 显示中文名称
            )
        return '-'
    task_name_short.short_description = '任务名称'
    
    def status_badge(self, obj):
        """状态徽章"""
        status_config = {
            'SUCCESS': ('成功', '#28a745'),
            'FAILURE': ('失败', '#dc3545'),
            'PENDING': ('等待', '#ffc107'),
            'STARTED': ('执行中', '#17a2b8'),
            'RETRY': ('重试', '#fd7e14'),
            'REVOKED': ('已撤销', '#6c757d'),
        }
        text, color = status_config.get(obj.status, (obj.status, '#6c757d'))
        return format_html(
            '<span style="background: {}; color: white; padding: 2px 8px; '
            'border-radius: 10px; font-size: 12px;">{}</span>',
            color, text
        )
    status_badge.short_description = '状态'
    
    def date_done_relative(self, obj):
        """相对完成时间"""
        if obj.date_done:
            return format_html(
                '<span title="{}">{}</span>',
                obj.date_done.strftime('%Y-%m-%d %H:%M:%S'),
                self._format_relative_time(obj.date_done)
            )
        return '-'
    date_done_relative.short_description = '完成时间'
    
    def result_preview(self, obj):
        """结果预览"""
        if obj.status == 'SUCCESS':
            result_str = str(obj.result)[:30]
            return format_html(
                '<span style="color: #6c757d; font-size: 12px;" title="{}">{}</span>',
                obj.result, result_str + '...' if len(str(obj.result)) > 30 else result_str
            )
        elif obj.status == 'FAILURE':
            return format_html(
                '<span style="color: #dc3545; font-size: 12px;">查看错误详情</span>'
            )
        return '-'
    result_preview.short_description = '结果'
    
    @staticmethod
    def _format_relative_time(dt):
        """格式化相对时间"""
        now = timezone.now()
        delta = now - dt
        
        if delta.days > 30:
            return dt.strftime('%Y-%m-%d')
        elif delta.days > 0:
            return f'{delta.days} 天前'
        elif delta.seconds > 3600:
            return f'{delta.seconds // 3600} 小时前'
        elif delta.seconds > 60:
            return f'{delta.seconds // 60} 分钟前'
        else:
            return '刚刚'


@admin.register(GroupResult)
class GroupResultAdmin(admin.ModelAdmin):
    """任务组结果管理 - 简约风格"""
    
    # 设置中文显示名称
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model._meta.verbose_name = '任务组结果'
        self.model._meta.verbose_name_plural = '任务组结果'
    
    list_display = ['group_id_short', 'date_done_relative']
    search_fields = ['group_id']
    readonly_fields = ['group_id', 'date_done', 'result']
    date_hierarchy = 'date_done'
    list_per_page = 50
    ordering = ['-date_done']
    
    fieldsets = (
        ('任务组信息', {
            'fields': ('group_id', 'date_done', 'result'),
        }),
    )
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def group_id_short(self, obj):
        """简化组ID显示"""
        return format_html(
            '<code style="background: #f5f5f5; padding: 2px 6px; '
            'border-radius: 4px; font-size: 11px;">{}</code>',
            obj.group_id[:16] + '...' if len(obj.group_id) > 16 else obj.group_id
        )
    group_id_short.short_description = '任务组ID'
    
    def date_done_relative(self, obj):
        if obj.date_done:
            return TaskResultAdmin._format_relative_time(obj.date_done)
        return '-'
    date_done_relative.short_description = '完成时间'


# ============================================
# Celery Beat 定时任务管理
# ============================================

# 任务名称到中文的映射表（根据 easytask/tasks.py 中的实际任务）
TASK_NAME_MAP = {
    # 容器管理任务
    'easytask.tasks.cleanup_expired_containers': '定期清理过期容器',
    
    # 系统维护任务
    'easytask.tasks.clear_notification': '清理过期通知',
    'easytask.tasks.cleanup_task_result': '清理任务结果',
    'easytask.tasks.clear_expired_sessions': '清理过期Session',
    
    # Docker/K8s 任务
    'easytask.tasks.check_docker_engines_health': '检查引擎健康',

    'easytask.tasks.cleanup_k8s_stale_pods': '清理K8s僵尸Pod',
    'easytask.tasks.set_views_to_redis': '文章访问量写入Redis',
    'easytask.tasks.set_feed_data': '采集Feed数据',
    # 通知任务
    'easytask.tasks.send_unread_notifications_email': '发送未读通知邮件',
    
    # AI知识库同步任务
    'easytask.tasks.sync_wiki_knowledge_base': '同步Wiki知识库',
    'easytask.tasks.sync_external_knowledge_sources': '同步外部知识源',
    'easytask.tasks.sync_ctf_writeups': '同步CTF题解',
}


class PeriodicTaskForm(forms.ModelForm):
    """定时任务表单 - 任务名称下拉选择"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # 获取所有已注册的任务
        try:
            # 只显示 TASK_NAME_MAP 中定义的任务
            choices = []
            for task_path, chinese_name in TASK_NAME_MAP.items():
                choices.append((task_path, chinese_name))
            
            # 按中文名称排序
            choices.sort(key=lambda x: x[1])
            
            # 设置 task 字段为下拉选择（缩小宽度）
            if choices:
                self.fields['task'] = forms.ChoiceField(
                    choices=choices,
                    label='任务',
                    help_text='选择要执行的任务',
                    widget=forms.Select(attrs={'style': 'width: 100%; max-width: 17%;'})
                )
        except Exception as e:
            # 如果获取失败，保持原有的文本输入框
            pass
    
    class Meta:
        model = PeriodicTask
        fields = '__all__'


@admin.register(PeriodicTask)
class PeriodicTaskAdmin(admin.ModelAdmin):
    """定时任务管理 - 简约风格"""
    
    # 设置中文显示名称
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model._meta.verbose_name = '定时任务'
        self.model._meta.verbose_name_plural = '定时任务'
    
    form = PeriodicTaskForm
    list_display = [
        'name',
        'enabled_badge',
        'task_display',
        'schedule_display',
        'last_run_display',
    ]
    list_filter = ['enabled', ('last_run_at', admin.DateFieldListFilter)]
    search_fields = ['name', 'task']
    readonly_fields = ['last_run_at', 'total_run_count']
    list_per_page = 50
    ordering = ['name']
    
    fieldsets = (
        ('基本信息', {
            'fields': ('name', 'task', 'enabled'),
            'description': '任务名称是唯一标识，从下拉列表选择要执行的任务',
        }),
        ('调度配置', {
            'fields': ('interval', 'crontab', 'clocked'),
            'description': '选择一种调度方式',
        }),
        ('运行统计', {
            'fields': ('last_run_at', 'total_run_count'),
            'classes': ('collapse',),
        }),
    )
    
    actions = ['enable_tasks', 'disable_tasks', 'run_tasks_now']
    
    
    def enabled_badge(self, obj):
        """启用状态徽章"""
        if obj.enabled:
            return format_html(
                '<span style="background: #28a745; color: white; padding: 2px 8px; '
                'border-radius: 10px; font-size: 12px;">已启用</span>'
            )
        return format_html(
            '<span style="background: #6c757d; color: white; padding: 2px 8px; '
            'border-radius: 10px; font-size: 12px;">已禁用</span>'
        )
    enabled_badge.short_description = '状态'
    
    def task_display(self, obj):
        """任务名称显示（中文）"""
        # 获取中文名称
        chinese_name = TASK_NAME_MAP.get(obj.task)
        if chinese_name:
            display_name = chinese_name
        else:
            # 自动生成显示名称
            parts = obj.task.split('.')
            display_name = parts[-1].replace('_', ' ').title() if parts else obj.task
        
        return format_html(
            '<span title="{}">{}</span>',
            obj.task,  # 鼠标悬停显示完整技术路径
            display_name  # 显示中文名称
        )
    task_display.short_description = '任务'
    
    def schedule_display(self, obj):
        """调度信息显示"""
        if obj.interval:
            return format_html(
                '<span style="color: #1976d2;">间隔: {}</span>',
                obj.interval
            )
        elif obj.crontab:
            return format_html(
                '<span style="color: #7b1fa2;">Crontab: {}</span>',
                obj.crontab
            )
        elif obj.clocked:
            return format_html(
                '<span style="color: #2e7d32;">定时: {}</span>',
                obj.clocked.clocked_time.strftime('%m-%d %H:%M')
            )
        return '-'
    schedule_display.short_description = '调度方式'
    
    def last_run_display(self, obj):
        """最后运行时间"""
        if obj.last_run_at:
            return format_html(
                '<span title="{}">{}</span>',
                obj.last_run_at.strftime('%Y-%m-%d %H:%M:%S'),
                TaskResultAdmin._format_relative_time(obj.last_run_at)
            )
        return format_html('<span style="color: #6c757d;">从未运行</span>')
    last_run_display.short_description = '最后运行'
    
    def enable_tasks(self, request, queryset):
        """批量启用任务"""
        count = queryset.update(enabled=True)
        self.message_user(request, f'已启用 {count} 个定时任务')
    enable_tasks.short_description = '启用选中的任务'
    
    def disable_tasks(self, request, queryset):
        """批量禁用任务"""
        count = queryset.update(enabled=False)
        self.message_user(request, f'已禁用 {count} 个定时任务')
    disable_tasks.short_description = '禁用选中的任务'
    
    def run_tasks_now(self, request, queryset):
        """立即运行选中的任务"""
        from celery import current_app
        
        success_count = 0
        failed_count = 0
        
        for task in queryset:
            try:
                # 解析 args 和 kwargs
                import json
                args = json.loads(task.args) if task.args else []
                kwargs = json.loads(task.kwargs) if task.kwargs else {}
                
                # 触发任务立即执行
                current_app.send_task(
                    task.task,
                    args=args,
                    kwargs=kwargs
                )
                success_count += 1
            except Exception as e:
                failed_count += 1
                self.message_user(
                    request,
                    f'任务 {task.name} 执行失败: {str(e)}',
                    level='ERROR'
                )
        
        if success_count > 0:
            self.message_user(request, f'已触发 {success_count} 个任务执行')
        if failed_count > 0:
            self.message_user(request, f'{failed_count} 个任务触发失败', level='WARNING')
    run_tasks_now.short_description = '立即运行选中的任务'


@admin.register(IntervalSchedule)
class IntervalScheduleAdmin(admin.ModelAdmin):
    """时间间隔调度 - 简约风格"""
    
    # 设置中文显示名称
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model._meta.verbose_name = '时间间隔'
        self.model._meta.verbose_name_plural = '时间间隔'
    
    list_display = ['interval_display', 'task_count']
    list_per_page = 50
    
    fields = ('every', 'period')
    
    def interval_display(self, obj):
        """间隔显示"""
        return f'每 {obj.every} {obj.get_period_display()}'
    interval_display.short_description = '时间间隔'
    
    def task_count(self, obj):
        """使用该间隔的任务数"""
        count = obj.periodictask_set.count()
        return format_html(
            '<span style="color: #1976d2;">{} 个任务</span>',
            count
        ) if count > 0 else '-'
    task_count.short_description = '使用情况'


@admin.register(CrontabSchedule)
class CrontabScheduleAdmin(admin.ModelAdmin):
    """Crontab 调度 - 简约风格"""
    
    # 设置中文显示名称
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model._meta.verbose_name = 'Crontab 定时'
        self.model._meta.verbose_name_plural = 'Crontab 定时'
    
    list_display = ['crontab_display', 'task_count']
    list_per_page = 50
    
    fields = ('minute', 'hour', 'day_of_week', 'day_of_month', 'month_of_year', 'timezone')
    
    def crontab_display(self, obj):
        """Crontab 表达式显示"""
        cron_str = f'{obj.minute} {obj.hour} {obj.day_of_month} {obj.month_of_year} {obj.day_of_week}'
        return format_html(
            '<code style="background: #f5f5f5; padding: 3px 6px; '
            'border-radius: 3px; font-size: 12px;">{}</code>',
            cron_str
        )
    crontab_display.short_description = 'Crontab 表达式'
    
    def task_count(self, obj):
        """使用该 Crontab 的任务数"""
        count = obj.periodictask_set.count()
        return format_html(
            '<span style="color: #7b1fa2;">{} 个任务</span>',
            count
        ) if count > 0 else '-'
    task_count.short_description = '使用情况'


@admin.register(SolarSchedule)
class SolarScheduleAdmin(admin.ModelAdmin):
    """太阳事件调度 - 简约风格"""
    
    # 设置中文显示名称
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model._meta.verbose_name = '日程'
        self.model._meta.verbose_name_plural = '日程'
    
    list_display = ['event', 'latitude', 'longitude']
    list_per_page = 50
    
    fields = ('event', 'latitude', 'longitude')


@admin.register(ClockedSchedule)
class ClockedScheduleAdmin(admin.ModelAdmin):
    """指定时间调度 - 简约风格"""
    
    # 设置中文显示名称
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model._meta.verbose_name = '指定时间'
        self.model._meta.verbose_name_plural = '指定时间'
    
    list_display = ['clocked_time']
    list_filter = [('clocked_time', admin.DateFieldListFilter)]
    date_hierarchy = 'clocked_time'
    list_per_page = 50
    ordering = ['-clocked_time']
    
    fields = ('clocked_time',)


# Admin 配置完成

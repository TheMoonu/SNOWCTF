from django.contrib import admin
from logs.models import SystemLog

@admin.register(SystemLog)
class SystemLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'level', 'user', 'ip_address', 'message')
    list_filter = ('level', 'timestamp')
    search_fields = ('message', 'user', 'ip_address', 'request_path')
    readonly_fields = ('timestamp', 'level', 'logger_name', 'message', 'user', 
                      'ip_address', 'request_path', 'stack_trace')
    date_hierarchy = 'timestamp'
    list_per_page = 50
    
    fieldsets = (
        ('基本信息', {
            'fields': ('timestamp', 'level', 'logger_name', 'message')
        }),
        ('用户信息', {
            'fields': ('user', 'ip_address', 'request_path')
        }),
        ('错误详情', {
            'fields': ('stack_trace',),
            'classes': ('collapse',)
        }),
    )
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        # 可以允许删除日志
        return True
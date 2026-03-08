from django.contrib import admin
from tool.models import ToolCategory, ToolLink, Tool
from tool.forms import ToolAdminForm
from django.conf import settings
from django.utils.html import format_html

# Register your models here.
if settings.TOOL_FLAG:
    @admin.register(ToolCategory)
    class ToolCategoryAdmin(admin.ModelAdmin):
        list_display = ('name', 'key', 'order_num', 'icon_preview', 'is_active', 'tool_count', 'created_time')
        list_filter = ('is_active',)
        search_fields = ('name', 'key')
        list_editable = ('order_num', 'is_active')
        
        fieldsets = (
            ('基本信息', {
                'fields': ('name', 'key', 'icon', 'order_num', 'is_active')
            }),
            ('说明', {
                'fields': (),
                'description': format_html(
                    '<div style="background: #f0f8ff; padding: 15px; border-left: 4px solid #3b82f6; margin: 10px 0;">'
                    '<p><strong>图标说明：</strong></p>'
                    '<ul style="margin: 5px 0;">'
                    '<li>使用 Font Awesome 图标类名，如：<code>fa fa-briefcase</code></li>'
                    '<li>常用图标：<code>fa fa-wrench</code> (扳手)、<code>fa fa-code</code> (代码)、<code>fa fa-globe</code> (地球)、<code>fa fa-tools</code> (工具)</li>'
                    '<li>更多图标请访问：<a href="https://fontawesome.com/v4/icons/" target="_blank">Font Awesome 4</a></li>'
                    '</ul>'
                    '</div>'
                )
            }),
        )
        
        def icon_preview(self, obj):
            if obj.icon:
                return format_html(
                    '<i class="{}" style="font-size: 18px; color: #3b82f6;"></i> <code>{}</code>',
                    obj.icon, obj.icon
                )
            return '-'
        icon_preview.short_description = '图标预览'
        
        def tool_count(self, obj):
            count = obj.tools.filter(is_published=True).count()
            return format_html('<span style="color: #28a745; font-weight: bold;">{}</span>', count)
        tool_count.short_description = '已发布工具数'
        
  
    
    
    @admin.register(Tool)
    class ToolAdmin(admin.ModelAdmin):
        form = ToolAdminForm
        list_display = ( 'name', 'category', 'tool_type', 'url_preview', 'is_published', 'is_admin_only', 'order_num', 'views', 'created_time')
        list_filter = ('category', 'tool_type', 'is_published', 'is_admin_only', 'created_time')
        search_fields = ('name', 'description', 'url_name', 'external_url')
        list_editable = ('order_num', 'is_published', 'is_admin_only')
        readonly_fields = ('views', 'created_time', 'updated_time', 'preview_link')
        date_hierarchy = 'created_time'
        
        fieldsets = (
            ('基本信息', {
                'fields': ('name', 'description', 'category', 'tool_type'),
                'description': '填写工具的基本信息，工具类型决定了下方需要填写的链接配置'
            }),
            ('链接配置', {
                'fields': ('url_name', 'external_url', 'preview_link'),
            }),
            ('显示设置', {
                'fields': ('icon', 'order_num', 'is_published', 'is_admin_only'),
                'description': '设置工具的显示图标、排序和发布状态。勾选"仅管理员可见"后，只有管理员能在前端看到此工具'
            }),
            ('统计信息', {
                'fields': ('views', 'created_time', 'updated_time'),
                'classes': ('collapse',),
                'description': '工具的访问统计和时间记录'
            }),
        )
        
        actions = ['publish_tools', 'unpublish_tools', 'duplicate_tool']
        
        def icon_preview(self, obj):
            """图标预览"""
            if obj.icon:
                return format_html(
                    '<i class="{}" style="font-size: 20px; color: #3b82f6;"></i>',
                    obj.icon
                )
            return format_html('<i class="fa fa-question-circle" style="font-size: 20px; color: #ccc;"></i>')
        icon_preview.short_description = '图标'
        
        def url_preview(self, obj):
            """URL预览"""
            if obj.tool_type == 'internal':
                return format_html(
                    '<span style="color: #28a745;"><i class="fa fa-home"></i> {}</span>',
                    obj.url_name or '-'
                )
            else:
                return format_html(
                    '<span style="color: #17a2b8;"><i class="fa fa-external-link"></i> {}</span>',
                    obj.external_url[:30] + '...' if obj.external_url and len(obj.external_url) > 30 else (obj.external_url or '-')
                )
        url_preview.short_description = 'URL'
        
        def preview_link(self, obj):
            """预览链接"""
            if obj.pk:
                url = obj.get_absolute_url()
                target = obj.get_target()
                return format_html(
                    '<a href="{}" target="{}" style="display: inline-block; padding: 8px 16px; color: white; '
                    'text-decoration: none; border-radius: 4px; font-weight: 500;">'
                    '<i class="fa fa-eye"></i> 预览工具</a>',
                    url, target
                )
            return '-'
        preview_link.short_description = '预览'
        
        def publish_tools(self, request, queryset):
            count = queryset.update(is_published=True)
            self.message_user(request, f'已发布 {count} 个工具')
        publish_tools.short_description = '发布选中的工具'
        
        def unpublish_tools(self, request, queryset):
            count = queryset.update(is_published=False)
            self.message_user(request, f'已取消发布 {count} 个工具')
        unpublish_tools.short_description = '取消发布选中的工具'
        
        def duplicate_tool(self, request, queryset):
            """复制工具"""
            count = 0
            for tool in queryset:
                tool.pk = None
                tool.name = f'{tool.name} (副本)'
                tool.is_published = False
                tool.views = 0
                tool.save()
                count += 1
            self.message_user(request, f'已复制 {count} 个工具')
        duplicate_tool.short_description = '复制选中的工具'
        
     

    @admin.register(ToolLink)
    class ToolLinkAdmin(admin.ModelAdmin):
        list_display = ('name', 'description', 'link', 'order_num', 'is_admin_only', 'category')
        list_filter = ('category', 'is_admin_only')
        search_fields = ('name', 'link')
        list_editable = ('order_num', 'is_admin_only')

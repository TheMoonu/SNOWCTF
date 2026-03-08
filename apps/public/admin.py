from django.contrib import admin
from django.conf import settings
from django.contrib.sites.models import Site

# Register your models here.
admin.site.site_header = f'{settings.SNOW_HOME_TITLE}后台管理'  # 设置header
admin.site.site_title = f'{settings.SNOW_HOME_TITLE}后台管理'   # 设置title
admin.site.index_title = f'{settings.SNOW_HOME_TITLE}后台管理'

# 取消注册默认的 Site Admin（将使用 Proxy Model）
try:
    admin.site.unregister(Site)
except admin.sites.NotRegistered:
    pass


from django.contrib import admin
from public.models import (
    RewardItem, ExchangeRecord, MotivationalQuote, CTFUser,
    SiteSettings, FooterColumn, FooterLink, HomePageConfig, ServiceCard,
    PublicSite  # 导入 Site Proxy Model
)




# ============================================
# 站点管理（使用 Proxy Model）
# ============================================

@admin.register(PublicSite)
class PublicSiteAdmin(admin.ModelAdmin):
    """站点管理 - 只允许修改，不允许新增和删除"""
    list_display = ('domain', 'name')
    search_fields = ('domain', 'name')
    
    fieldsets = (
        ('站点信息', {
            'fields': ('domain', 'name'),
            'description': '修改当前站点的域名和名称'
        }),
    )
    
    def has_add_permission(self, request):
        """禁止添加新站点"""
        return False
    
    def has_delete_permission(self, request, obj=None):
        """禁止删除站点"""
        return False
    
    def get_actions(self, request):
        """移除批量删除操作"""
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions


# ============================================
# 奖品和兑换管理
# ============================================

@admin.register(RewardItem)
class RewardItemAdmin(admin.ModelAdmin):
    list_display = ['name', 'coins', 'stock', 'is_active', 'created_at']
    list_editable = ['coins', 'stock', 'is_active']
    search_fields = ['name']
    readonly_fields = ['created_at']

@admin.register(ExchangeRecord)
class ExchangeRecordAdmin(admin.ModelAdmin):
    list_display = ['user', 'reward', 'contact', 'is_processed', 'created_at']
    list_filter = ['is_processed']
    search_fields = ['user__username', 'contact']
    list_editable = ['is_processed']
    readonly_fields = ['user', 'reward', 'created_at']



@admin.register(MotivationalQuote)
class MotivationalQuoteAdmin(admin.ModelAdmin):
    """励志语录管理配置"""
    list_display = ('content', 'author', 'is_active', 'created_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('content', 'author')
    list_editable = ('is_active',)
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('基本信息', {
            'fields': ('content', 'author')
        }),
        ('状态设置', {
            'fields': ('is_active',)
        }),
    )
    
    def get_ordering(self, request):
        """默认按创建时间倒序排列"""
        return ('-created_at',)
    
    def get_readonly_fields(self, request, obj=None):
        """如果是编辑已有对象，created_at设为只读"""
        if obj:
            return ['created_at']
        return []


# CTFUser 已移至 oauth 模块管理
# @admin.register(CTFUser) - 已在 oauth/admin.py 中注册


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    """网站配置管理"""
    list_display = ('site_name', 'beian', 'registration_enabled', 'is_active', 'updated_at')
    list_filter = ('is_active', 'registration_enabled')
    search_fields = ('site_name', 'beian')
    readonly_fields = ('updated_at',)
    
    fieldsets = (
        ('基本信息', {
            'fields': ('site_name', 'site_logo', 'site_favicon',
                      'site_description', 'site_keywords', 'site_create_date','footer_style'),
            'description': 'Logo和Favicon留空将使用默认图片'
        }),
        ('备案信息', {
            'fields': ('beian',)
        }),
        ('邮箱配置', {
            'fields': ('email_enabled',
                      'email_host', 'email_port', 'email_host_user', 
                      'email_host_password', 'email_use_ssl', 'email_from'),
            'classes': ('collapse',),
            'description': '配置邮箱功能。启用后可发送邮件。验证方式在环境变量 SNOW_ACCOUNT_EMAIL_VERIFICATION 中配置（none/optional/mandatory）'
        }),
        ('第三方登录', {
            'fields': ('github_login_enabled',),
            'classes': ('collapse',),
            'description': '配置是否启用GitHub等第三方登录方式'
        }),
        ('注册配置', {
            'fields': ('registration_enabled',),
            'classes': ('collapse',),
            'description': '控制是否允许新用户注册。关闭后，现有用户仍可登录，但无法注册新账号'
        }),
        ('比赛页面装饰图片配置', {
            'fields': ('anime_side_left_image', 'anime_side_right_image', 
                      'anime_filter_right_image', 'anime_challenge_start_bg'),
            'classes': ('collapse',),
            'description': '上传的装饰图片，用于简洁版比赛页面美化。建议使用透明PNG格式，支持JPG、PNG、GIF、WEBP格式'
        }),
        ('状态设置', {
            'fields': ('is_active', 'updated_at')
        }),
    )
    
    def save_model(self, request, obj, form, change):
        """保存时提示"""
        super().save_model(request, obj, form, change)
        


# ============================================
# 页脚管理（优化版：使用新模型）
# ============================================

class FooterLinkInline(admin.TabularInline):
    """页脚链接内联编辑"""
    model = FooterLink
    extra = 1
    fields = ('title', 'url', 'url_type', 'target', 'order', 'is_active')
    ordering = ['order', 'id']


@admin.register(FooterColumn)
class FooterColumnAdmin(admin.ModelAdmin):
    """页脚栏目管理"""
    list_display = ('title', 'order', 'is_active', 'get_links_count', 'created_at')
    list_filter = ('is_active',)
    list_editable = ('order', 'is_active')
    search_fields = ('title',)
    inlines = [FooterLinkInline]
    
    fieldsets = (
        ('基本信息', {
            'fields': ('title',)
        }),
        ('显示设置', {
            'fields': ('order', 'is_active', 'created_at')
        }),
    )
    
    readonly_fields = ('created_at',)
    
    def get_links_count(self, obj):
        """显示链接数量"""
        count = obj.links.count()
        active_count = obj.links.filter(is_active=True).count()
        return f"{active_count}/{count}"
    get_links_count.short_description = '链接数（启用/总数）'
    
    def get_queryset(self, request):
        """优化查询，预加载链接"""
        qs = super().get_queryset(request)
        return qs.prefetch_related('links')


@admin.register(FooterLink)
class FooterLinkAdmin(admin.ModelAdmin):
    """页脚链接管理"""
    list_display = ('title', 'column', 'get_url_display', 'target', 'order', 'is_active', 'created_at')
    list_filter = ('column', 'url_type', 'target', 'is_active')
    list_editable = ('order', 'is_active')
    search_fields = ('title', 'url')
    
    fieldsets = (
        ('基本信息', {
            'fields': ('column', 'title')
        }),
        ('链接设置', {
            'fields': ('url', 'url_type', 'target'),
            'description': 'URL类型选择"Django URL Name"时，请填写Django的URL名称（如：blog:index）'
        }),
        ('显示设置', {
            'fields': ('order', 'is_active', 'created_at')
        }),
    )
    
    readonly_fields = ('created_at',)
    
    def get_url_display(self, obj):
        """显示URL信息"""
        return f"{obj.url} ({obj.get_url_type_display()})"
    get_url_display.short_description = '链接地址'
    
    def get_queryset(self, request):
        """优化查询，预加载栏目"""
        qs = super().get_queryset(request)
        return qs.select_related('column')




# ============================================
# 首页内容管理（优化版：使用新模型）
# ============================================

@admin.register(HomePageConfig)
class HomePageConfigAdmin(admin.ModelAdmin):
    """首页配置管理
    
    注意：ServiceCard 是全局管理的，不使用内联编辑。
    启用的 HomePageConfig 会自动显示所有启用的 ServiceCard。
    """
    list_display = ('main_title', 'is_active', 'get_service_cards_count', 'updated_at')
    list_filter = ('is_active',)
    list_editable = ('is_active',)
    search_fields = ('main_title', 'main_subtitle')
    
    fieldsets = (
        ('主要内容', {
            'fields': ('main_title', 'main_subtitle', 'main_description', 'main_image'),
            'description': '首页顶部展示的主要内容'
        }),
        ('服务区域', {
            'fields': ('service_badge', 'service_title', 'service_description'),
            'description': '服务区域的标题和描述'
        }),
        ('状态设置', {
            'fields': ('is_active', 'updated_at', 'created_at')
        }),
    )
    
    readonly_fields = ('updated_at', 'created_at')
    
    def get_service_cards_count(self, obj):
        """显示服务卡片数量"""
        count = ServiceCard.objects.count()
        active_count = ServiceCard.objects.filter(is_active=True).count()
        return f"{active_count}/{count}"
    get_service_cards_count.short_description = '服务卡片（启用/总数）'


@admin.register(ServiceCard)
class ServiceCardAdmin(admin.ModelAdmin):
    """服务卡片管理"""
    list_display = ('title', 'order', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    list_editable = ('order', 'is_active')
    search_fields = ('title', 'description')
    
    fieldsets = (
        ('卡片内容', {
            'fields': ('title', 'description', 'image')
        }),
        ('显示设置', {
            'fields': ('order', 'is_active', 'updated_at', 'created_at')
        }),
    )
    
    readonly_fields = ('updated_at', 'created_at')

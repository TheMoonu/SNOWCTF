from django.contrib import admin
from django.contrib.auth.admin import UserAdmin, GroupAdmin
from django.contrib.auth.models import Group, Permission
from django.urls import reverse
from django.utils.html import format_html
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.admin.views.decorators import staff_member_required
from oauth.models import Ouser, UserGroup
from public.models import CTFUser

# 尝试导入 allauth 的代理模型
try:
    from oauth.models import UserEmailAddress
    from allauth.account.models import EmailAddress
    HAS_ALLAUTH = True
except ImportError:
    HAS_ALLAUTH = False
    EmailAddress = None


# 在 admin.py 中定义 CTFUser Proxy Model（避免循环导入）
class UserCTFData(CTFUser):
    """
    用户CTF数据代理模型
    - 不创建新表，使用 practice_ctfuser 表
    - 在 Admin 中显示为"用户数据管理"
    """
    class Meta:
        proxy = True
        verbose_name = '用户CTF数据'
        verbose_name_plural = '用户数据管理'


# 取消注册默认的 Admin（使用 Proxy Model 重新注册）
try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass

try:
    admin.site.unregister(CTFUser)
except admin.sites.NotRegistered:
    pass

# 如果安装了 allauth，取消注册默认的 EmailAddress
if HAS_ALLAUTH:
    try:
        admin.site.unregister(EmailAddress)
    except admin.sites.NotRegistered:
        pass



@admin.register(Ouser)
class OuserAdmin(UserAdmin):
    list_display = (
        'username', 
        'email',
        'is_member', 
        'member_until',
        'display_phones',  # 使用自定义方法显示手机号
        'is_staff', 
        'is_active', 
        'date_joined',
        'password_link',  # 添加密码重置链接
    )
    
    # 添加用户时显示的字段（包含密码）
    add_fieldsets = (
        ('账户信息', {
            'classes': ('wide',),
            'fields': ('username', 'email', 'password1', 'password2'),
            'description': '创建新用户账户，邮箱为必填项'
        }),
        ('基础信息', {
            'fields': (
                ('profile', 'invite_code'), 
                ('link',), 
                ('avatar',),
            )
        }),
        ('会员信息', {
            'fields': (
                ('member_since', 'member_until'),
            )
        }),
        ('权限信息', {
            'fields': (
                ('is_active', 'is_staff', 'is_superuser'),
                'groups', 
                'user_permissions'
            )
        }),
    )
    
    # 编辑用户时显示的字段
    fieldsets = (
        ('基础信息', {
            'fields': (
                ('username', 'email', 'profile', 'invite_code'), 
                ('link',), 
                ('avatar',),
                'uuid',
            )
        }),
        ('加密信息', {
            'fields': (
                'display_phones_full',
                'display_real_name',
                'display_department',
                'display_student_id',
            ),
            'description': '以下是加密存储的敏感信息，仅显示不可编辑'
        }),
        ('会员信息', {
            'fields': (
                ('member_since', 'member_until'),
            )
        }),
        ('权限信息', {
            'fields': (
                ('is_active', 'is_staff', 'is_superuser'),
                'groups', 
                'user_permissions'
            )
        }),
        ('重要日期', {
            'fields': (
                ('last_login', 'date_joined'),
            )
        }),
        
    )
    
    readonly_fields = (
        'uuid', 
        'last_login', 
        'date_joined',
        'display_phones_full',
        'display_real_name',
        'display_department',
        'display_student_id',
        # 密码字段只读（显示哈希值）
    )
    
    filter_horizontal = ('groups', 'user_permissions')
    list_filter = ('is_staff', 'is_superuser', 'is_active','is_member', 'groups')
    search_fields = ('username', 'email', '_encrypted_phones')
    ordering = ('-date_joined',)

    def display_phones(self, obj):
        """列表显示：显示脱敏后的手机号"""
        return obj.phones_masked if obj else '-'
    display_phones.short_description = '手机号'
    
    def display_phones_full(self, obj):
        """详情显示：显示完整手机号（仅管理员可见）"""
        return obj.phones if obj else '-'
    display_phones_full.short_description = '手机号（完整）'
    
    def display_real_name(self, obj):
        """显示真实姓名"""
        return obj.real_name if obj else '-'
    display_real_name.short_description = '真实姓名'
    
    def display_department(self, obj):
        """显示学院/部门"""
        return obj.department if obj else '-'
    display_department.short_description = '学院/部门'
    
    def display_student_id(self, obj):
        """显示学号/工号"""
        return obj.student_id if obj else '-'
    display_student_id.short_description = '学号/工号'
    
    def password_link(self, obj):
        """显示修改密码按钮（弹框）"""
        if obj:
            return format_html(
                '<a href="javascript:void(0)"  onclick="openResetPasswordModal({}, \'{}\')">🔑 修改密码</a>',
                obj.pk, obj.username
            )
        return '-'
    password_link.short_description = '密码管理'
    
    class Media:
        js = ('oauth/js/admin_password_reset.js',)
        css = {
            'all': ('oauth/css/admin_password_reset.css',)
        }

    def get_fieldsets(self, request, obj=None):
        """根据是添加还是编辑用户返回不同的字段集"""
        if not obj:
            # 添加新用户时使用 add_fieldsets
            return self.add_fieldsets
        # 编辑现有用户时使用 fieldsets
        return super().get_fieldsets(request, obj)
    
    def get_form(self, request, obj=None, **kwargs):
        """重写表单以支持密码字段"""
        if not obj:
            # 添加新用户时，导入 UserCreationForm
            from django.contrib.auth.forms import UserCreationForm
            
            class CustomUserCreationForm(UserCreationForm):
                class Meta:
                    model = Ouser
                    fields = ('username', 'email')
            
            kwargs['form'] = CustomUserCreationForm
        return super().get_form(request, obj, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        """设置只读字段"""
        if not obj:
            # 添加新用户时，不需要只读字段
            return []
            # 编辑用户时的只读字段
        if not request.user.is_superuser:
            return self.readonly_fields + ('is_staff', 'is_superuser', 'groups', 'user_permissions')
        return self.readonly_fields
    
    def changelist_view(self, request, extra_context=None):
        """添加密码重置弹框到列表页"""
        extra_context = extra_context or {}
        extra_context['show_password_modal'] = True
        return super().changelist_view(request, extra_context=extra_context)
    
    def formfield_for_manytomany(self, db_field, request, **kwargs):
        """
        修复 PostgreSQL 游标失效问题
        强制立即评估查询集，避免游标在表单渲染时失效
        """
        formfield = super().formfield_for_manytomany(db_field, request, **kwargs)
        if formfield and hasattr(formfield, 'queryset'):
            # 强制评估查询集，将结果缓存到内存中
            formfield.queryset = formfield.queryset.all()
            # 使用 list() 强制立即执行查询
            formfield.queryset._result_cache = list(formfield.queryset)
        return formfield


# ============================================
# 使用 Proxy Model 管理其他模型
# ============================================

@admin.register(UserGroup)
class UserGroupAdmin(GroupAdmin):
    """用户组管理（使用代理模型）"""
    pass


if HAS_ALLAUTH:
    @admin.register(UserEmailAddress)
    class UserEmailAddressAdmin(admin.ModelAdmin):
        """用户邮箱管理（使用代理模型）"""
        
        list_display = ('email', 'user', 'verified', 'primary')
        list_filter = ('verified', 'primary')
        search_fields = ('email', 'user__username', 'user__email')
        readonly_fields = ('user',)
        
        def has_add_permission(self, request):
            """禁止手动添加（用户通过前端添加）"""
            return False


@admin.register(UserCTFData)
class UserCTFDataAdmin(admin.ModelAdmin):
    """用户CTF数据管理（使用代理模型）"""
    list_display = ('user', 'score', 'coins', 'rank', 'solves')
    list_filter = ('rank',)
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('rank',)
    filter_horizontal = ('solved_challenges', 'collect_challenges', 'collect_jobs')
    
    fieldsets = (
        ('用户信息', {
            'fields': ('user',)
        }),
        ('积分数据', {
            'fields': ('score', 'coins', 'rank', 'solves')
        }),
        ('题目关联', {
            'fields': ('solved_challenges', 'collect_challenges'),
            'classes': ('collapse',),
        }),
        ('岗位收藏', {
            'fields': ('collect_jobs',),
            'classes': ('collapse',),
        }),
    )
    
    def get_ordering(self, request):
        """默认按分数倒序排列"""
        return ('-score',)
    
    def formfield_for_manytomany(self, db_field, request, **kwargs):
        """
        修复 PostgreSQL 游标失效问题
        强制立即评估查询集，避免游标在表单渲染时失效
        """
        formfield = super().formfield_for_manytomany(db_field, request, **kwargs)
        if formfield and hasattr(formfield, 'queryset'):
            # 强制评估查询集，将结果缓存到内存中
            formfield.queryset = formfield.queryset.all()
            # 使用 list() 强制立即执行查询
            formfield.queryset._result_cache = list(formfield.queryset)
        return formfield

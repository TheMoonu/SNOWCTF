from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.conf import settings
from django.utils import timezone
import os



User = get_user_model()

def validate_image_size(image):
    """验证图片大小"""
    file_size = image.size
    limit_mb = 2
    if file_size > limit_mb * 1024 * 1024:
        raise ValidationError(f"图片大小不能超过 {limit_mb}MB")

def service_card_image_upload_path(instance, filename):
    """服务卡片图片上传路径（使用时间戳）"""
    # 获取文件扩展名
    ext = filename.split('.')[-1].lower()
    # 生成新文件名：时间戳_原文件名
    timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    new_filename = f"{timestamp}_{filename}"
    # 按年月组织目录：homepage/services/2024/01/时间戳_文件名.ext
    date_path = timezone.now().strftime('%Y/%m')
    return os.path.join('homepage', 'services', date_path, new_filename)

def reward_image_upload_path(instance, filename):
    """奖品图片上传路径（使用时间戳）"""
    timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    new_filename = f"{timestamp}_{filename}"
    date_path = timezone.now().strftime('%Y/%m')
    return os.path.join('rewards', date_path, new_filename)

def site_logo_upload_path(instance, filename):
    """网站Logo上传路径（使用时间戳）"""
    timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    new_filename = f"{timestamp}_{filename}"
    return os.path.join('site', 'logo', new_filename)

def site_favicon_upload_path(instance, filename):
    """网站Favicon上传路径（使用时间戳）"""
    timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    new_filename = f"{timestamp}_{filename}"
    return os.path.join('site', 'favicon', new_filename)

def homepage_main_image_upload_path(instance, filename):
    """主页主图片上传路径（使用时间戳）"""
    timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    new_filename = f"{timestamp}_{filename}"
    date_path = timezone.now().strftime('%Y/%m')
    return os.path.join('homepage', 'main', date_path, new_filename)

class RewardItem(models.Model):
    """奖品项目"""
    name = models.CharField('奖品名称', max_length=100)
    description = models.TextField('奖品描述')
    coins = models.PositiveIntegerField(
        '所需金币',
        validators=[MinValueValidator(1)],
        default=100
    )
    image = models.FileField(
        '奖品图片', 
        upload_to=reward_image_upload_path,
        validators=[
            FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'webp', 'svg']),
            validate_image_size
        ],
        help_text='支持格式：JPG、PNG、WEBP、SVG，大小不超过2MB'
    )
    stock = models.PositiveIntegerField(
        '剩余数量',
        default=0,
        validators=[MinValueValidator(0)]
    )
    is_active = models.BooleanField('是否可兑换', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    
    class Meta:
        verbose_name = '奖品'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    def can_exchange(self):
        """检查是否可以兑换"""
        return self.is_active and self.stock > 0

class ExchangeRecord(models.Model):
    """兑换记录"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name='用户')
    reward = models.ForeignKey(RewardItem, on_delete=models.CASCADE, verbose_name='奖品')
    contact = models.CharField('联系方式', max_length=50)
    is_processed = models.BooleanField('是否处理', default=False)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    
    class Meta:
        verbose_name = '兑换记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return f'{self.user.username} - {self.reward.name}'
    
    def save(self, *args, **kwargs):
        if not self.pk:  # 新记录
            reward = self.reward
            # 检查库存
            if reward.stock <= 0:
                raise ValidationError('奖品库存不足')
            # 扣减库存
            reward.stock -= 1
            reward.save()
        super().save(*args, **kwargs)

class MotivationalQuote(models.Model):
    """存储CTF励志语录的模型"""
    content = models.CharField(max_length=200, verbose_name="语录内容")
    author = models.CharField(max_length=50, verbose_name="作者", blank=True, null=True)
    is_active = models.BooleanField(default=True, verbose_name="是否启用")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    
    class Meta:
        verbose_name = "励志语录"
        verbose_name_plural = verbose_name
        ordering = ["?"]  # 随机排序
    
    def __str__(self):
        return self.content[:30]


class CTFUser(models.Model):
    """用户CTF数据（从practice迁移过来，保持表名兼容）"""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name="用户"
    )
    score = models.IntegerField(default=0, verbose_name="用户分数")
    coins = models.IntegerField(default=10, verbose_name="用户金币")
    rank = models.IntegerField(default=0, verbose_name="用户排名")
    solves = models.IntegerField(default=0, verbose_name="用户解题数")
    solved_challenges = models.ManyToManyField(
        'practice.PC_Challenge',
        related_name="solved_by_users",
        default=None,
        blank=True,
        verbose_name="已解决的题目"
    )
    collect_challenges = models.ManyToManyField(
        'practice.PC_Challenge',
        related_name="collected_by_users",
        default=None,
        blank=True,
        verbose_name="用户收藏的题目"
    )
    collect_jobs = models.ManyToManyField(
        'recruit.Job',
        related_name="collected_by_users",
        default=None,
        blank=True,
        verbose_name="用户收藏的岗位"
    )

    class Meta:
        verbose_name = "用户数据"
        verbose_name_plural = "用户数据"
        # 保持原来的数据库表名，避免数据迁移
        db_table = 'practice_ctfuser'

    def __str__(self):
        return f"{self.user.username}"

    def update_rank(self):
        """更新当前用户排名（优化版：只更新单个用户）"""
        try:
            # 刷新自己的数据，因为可能使用了F()表达式
            self.refresh_from_db()
            
            # 统计比当前用户分数高的用户数量+1就是当前排名
            # 使用聚合查询，不遍历所有用户，效率高
            rank = CTFUser.objects.filter(score__gt=self.score).count() + 1
            
            # 只更新当前用户的排名
            CTFUser.objects.filter(pk=self.pk).update(rank=rank)
            
            # 刷新实例以获取最新rank
            self.refresh_from_db()
            
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(
                f"更新用户排名失败: user={self.user.username if self.user else 'None'}, "
                f"ctf_user_pk={self.pk}, score={self.score}, 错误={str(e)}"
            )
    
    def deduct_coins(self, amount):
        """扣除用户金币
        
        Args:
            amount: 要扣除的金币数量
            
        Returns:
            bool: 扣除是否成功
            str: 错误信息（如果有）
        """
        try:
            with transaction.atomic():
                # 检查金币是否足够
                if self.coins < amount:
                    return False, "金币不足"
                
                # 使用 F() 来确保并发安全
                self.coins = F('coins') - amount
                self.save()
                
                # 刷新实例以获取最新值
                self.refresh_from_db()
                
                return True, None
                
        except Exception as e:
            return False, f"扣除金币失败: {str(e)}"


class SiteSettings(models.Model):
    """网站基础配置（单例模式）"""
    
    # 基本信息
    site_name = models.CharField('网站名称', max_length=100, default='SECSNOW')
    site_logo = models.FileField(
        '网站Logo', 
        upload_to=site_logo_upload_path, 
        blank=True, 
        null=True,
        validators=[FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg'])],
        help_text='网站Logo图片，支持格式：JPG、PNG、SVG等，留空使用默认 blog/img/logo.svg'
    )
    site_favicon = models.FileField(
        '网站Favicon', 
        upload_to=site_favicon_upload_path, 
        blank=True, 
        null=True,
        validators=[FileExtensionValidator(allowed_extensions=['ico', 'png', 'jpg', 'jpeg', 'svg'])],
        help_text='网站图标（显示在浏览器标签栏），支持格式：ICO、PNG、SVG等，留空使用默认 public/img/favicon.ico'
    )
    site_description = models.CharField('网站描述', max_length=200, 
                                       default='SECSNOW 一个开源、共创、共享网络安全技术学习网站')
    site_keywords = models.CharField('网站关键词', max_length=200, 
                                     default='secsnow,CTF竞赛、漏洞靶场、网络安全')
    site_create_date = models.DateField('网站创建日期', 
                                       help_text='用于计算网站运行天数')
    
    # 备案信息
    beian = models.CharField('ICP备案号', max_length=50, 
                            default='', blank=True)
    
    
    # 统计代码
    cnzz_code = models.TextField('站长统计代码（友盟）', blank=True, 
                                 help_text='直接粘贴完整的统计代码')
    la51_code = models.TextField('站长统计代码（51.la）', blank=True,
                                help_text='直接粘贴完整的统计代码')
    site_verification = models.TextField('站长验证代码', blank=True,
                                        help_text='用于站长平台推送验证')
    
    # 邮箱配置
    email_enabled = models.BooleanField('启用邮箱功能', default=False,
                                       help_text='是否启用邮箱功能。验证方式请在请在.env 中配置 SNOW_ACCOUNT_EMAIL_VERIFICATION （none/optional/mandatory）')
    email_host = models.CharField('SMTP服务器', max_length=100, 
                                  default='smtp.163.com', blank=True,
                                  help_text='SMTP服务器地址，如: smtp.qq.com, smtp.163.com, smtp.gmail.com')
    email_port = models.IntegerField('SMTP端口', default=465, blank=True,
                                    help_text='SMTP端口号：SSL使用465，TLS使用587')
    email_host_user = models.EmailField('发件人邮箱', max_length=100, blank=True,
                                       help_text='用于SMTP认证的邮箱地址，只填写纯邮箱地址即可，如: admin@example.com')
    email_host_password = models.CharField('授权码', max_length=100, blank=True,
                                          help_text='邮箱SMTP授权码（注意：不是邮箱登录密码，需要在邮箱设置中生成）')
    email_use_ssl = models.BooleanField('使用SSL', default=True,
                                       help_text='勾选使用SSL（端口465），不勾选使用TLS（端口587）')
    email_from = models.CharField('发件人名称', max_length=100, 
                                  default='SECSNOW', blank=True,
                                  help_text='发件人显示名称（只填写名称，不要包含邮箱地址），如: SECSNOW、网站管理员等')
    
    # 第三方登录配置
    github_login_enabled = models.BooleanField('启用GitHub登录', default=False,
                                              help_text='是否显示GitHub快速登录按钮')
    
    # 注册配置
    registration_enabled = models.BooleanField('允许用户注册', default=True,
                                              help_text='关闭后，新用户将无法注册账号（管理员后台添加不受影响）')
    
    # 页脚配置
    FOOTER_STYLE_CHOICES = [
        ('dark', '深色页脚（默认）'),
        ('light', '浅色页脚（白色）'),
    ]
    footer_style = models.CharField('页脚风格', max_length=10, 
                                   choices=FOOTER_STYLE_CHOICES,
                                   default='dark',
                                   help_text='选择页脚的颜色风格')
    
    # 二次元风格图片配置
    anime_side_left_image = models.FileField(
        '左侧装饰图片', 
        upload_to='site_images/anime/', 
        blank=True, 
        null=True,
        validators=[FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'gif', 'webp'])],
        help_text='简洁版比赛页面主背景左侧装饰图片，建议尺寸：高度与页面等高，宽度200-400px，支持透明PNG'
    )
    anime_side_right_image = models.FileField(
        '右侧装饰图片', 
        upload_to='site_images/anime/', 
        blank=True, 
        null=True,
        validators=[FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'gif', 'webp'])],
        help_text='简洁版比赛页面主背景右侧装饰图片，建议尺寸：高度与页面等高，宽度200-400px，支持透明PNG'
    )
    anime_filter_right_image = models.FileField(
        '筛选框右侧图片', 
        upload_to='site_images/anime/', 
        blank=True, 
        null=True,
        validators=[FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'gif', 'webp'])],
        help_text='题目筛选框右侧装饰图片，建议尺寸：300x300px，支持透明PNG'
    )
    anime_challenge_start_bg = models.FileField(
        '答题启动背景图', 
        upload_to='site_images/anime/', 
        blank=True, 
        null=True,
        validators=[FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'gif', 'webp'])],
        help_text='答题启动按钮区域背景图，建议尺寸：1200x600px或更大，支持透明PNG'
    )
    
    # 其他配置
    is_active = models.BooleanField('是否启用', default=True,
                                   help_text='只能有一个启用的配置')
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        verbose_name = '网站配置'
        verbose_name_plural = verbose_name
        ordering = ['-is_active', '-updated_at']
    
    def __str__(self):
        return f"{self.site_name} ({'启用' if self.is_active else '停用'})"
    
    def save(self, *args, **kwargs):
        """保存时确保只有一个启用的配置"""
        if self.is_active:
            # 停用其他所有配置
            SiteSettings.objects.filter(is_active=True).exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)
        from public.utils import SiteSettingsCache
        # 清除缓存
        SiteSettingsCache.clear_cache()
    
    @classmethod
    def get_active(cls):
        """获取当前启用的配置（带缓存）"""
        from public.utils import SiteSettingsCache

        return SiteSettingsCache.get_settings()


# ============================================
# 页脚管理模型（优化版：拆分为独立模型）
# ============================================

class FooterColumn(models.Model):
    """页脚栏目（一级）"""
    title = models.CharField('栏目标题', max_length=50)
    order = models.IntegerField('排序', default=0, help_text='数字越小越靠前')
    is_active = models.BooleanField('是否显示', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    
    class Meta:
        verbose_name = '页脚栏目'
        verbose_name_plural = verbose_name
        ordering = ['order', 'id']
    
    def __str__(self):
        return f"{self.title}"
    


class FooterLink(models.Model):
    """页脚链接（二级）"""
    column = models.ForeignKey(FooterColumn, on_delete=models.CASCADE,
                              related_name='links', verbose_name='所属栏目')
    title = models.CharField('链接文本', max_length=50)
    url = models.CharField('链接地址', max_length=200,
                          help_text='可以是URL或Django URL name')
    url_type = models.CharField('链接类型', max_length=10,
                               choices=[
                                   ('url', '直接URL'),
                                   ('name', 'Django URL Name')
                               ], default='url')
    target = models.CharField('打开方式', max_length=10,
                             choices=[
                                 ('_self', '当前窗口'),
                                 ('_blank', '新窗口')
                             ], default='_self')
    order = models.IntegerField('排序', default=0, help_text='数字越小越靠前')
    is_active = models.BooleanField('是否显示', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    
    class Meta:
        verbose_name = '页脚链接'
        verbose_name_plural = verbose_name
        ordering = ['order', 'id']
    
    def __str__(self):
        return f"{self.column.title} - {self.title}"





# ============================================
# 首页内容管理模型（优化版：拆分为独立模型）
# ============================================

class HomePageConfig(models.Model):
    """首页配置（全局唯一）"""
    main_title = models.CharField('主标题', max_length=100,
                                  default='小雪花安全实验室')
    main_subtitle = models.CharField('副标题', max_length=100,
                                     default='SECSNOW')
    main_description = models.TextField('主描述',
                                       default='一个致力于开源、共享、共创的网络安全、数据安全、人工智能安全研究的实验室， 我们的使命：坚决保护国家网络安全。')
    main_image = models.FileField(
        '主图片', 
        upload_to=homepage_main_image_upload_path, 
        blank=True, 
        null=True,
        validators=[FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg'])],
        help_text='支持格式：JPG、PNG、GIF、WEBP、SVG，建议尺寸：800x600'
    )
    service_badge = models.CharField('服务标签', max_length=50,
                                    default='Get started')
    service_title = models.CharField('服务标题', max_length=100,
                                    default='我们的服务与价值')
    service_description = models.TextField('服务描述',
                                          default='伟大的服务源于对他人需求的深刻理解。')
    is_active = models.BooleanField('是否启用', default=True,
                                   help_text='只能有一个启用的配置')
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    
    class Meta:
        verbose_name = '首页配置'
        verbose_name_plural = verbose_name
        ordering = ['-is_active', '-updated_at']
    
    def __str__(self):
        return f"{self.main_title} ({'启用' if self.is_active else '停用'})"
    
    def save(self, *args, **kwargs):
        """保存时确保只有一个启用的配置"""
        if self.is_active:
            # 停用其他所有配置
            HomePageConfig.objects.filter(
                is_active=True
            ).exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)
    
    @classmethod
    def get_active(cls):
        """获取当前启用的配置（带缓存）"""
        from public.utils import SiteSettingsCache
        return SiteSettingsCache.get_homepage_content()


class ServiceCard(models.Model):
    """服务卡片"""
    title = models.CharField('卡片标题', max_length=100)
    description = models.TextField('卡片描述')
    image = models.FileField(
        '卡片图片', 
        upload_to=service_card_image_upload_path, 
        blank=True, 
        null=True,
        validators=[FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg'])],
        help_text='支持格式：JPG、PNG、GIF、WEBP、SVG，建议尺寸：400x300'
    )
    order = models.IntegerField('排序', default=0, help_text='数字越小越靠前')
    is_active = models.BooleanField('是否显示', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        verbose_name = '服务卡片'
        verbose_name_plural = verbose_name
        ordering = ['order', 'id']
    
    def __str__(self):
        return f"{self.title}"




# ============================================
# 信号处理：确保批量删除也能清除缓存
# ============================================
from django.db.models.signals import post_save, post_delete, pre_delete
from django.dispatch import receiver
from public.utils import SiteSettingsCache

# 新模型的信号
@receiver(post_save, sender=FooterColumn)
def clear_footer_column_cache_on_save(sender, instance, **kwargs):
    """FooterColumn 保存后清除缓存"""
    SiteSettingsCache.clear_cache()

@receiver(post_delete, sender=FooterColumn)
def clear_footer_column_cache_on_delete(sender, instance, **kwargs):
    """FooterColumn 删除后清除缓存"""
    SiteSettingsCache.clear_cache()

@receiver(post_save, sender=FooterLink)
def clear_footer_link_cache_on_save(sender, instance, **kwargs):
    """FooterLink 保存后清除缓存"""
    SiteSettingsCache.clear_cache()

@receiver(post_delete, sender=FooterLink)
def clear_footer_link_cache_on_delete(sender, instance, **kwargs):
    """FooterLink 删除后清除缓存"""
    SiteSettingsCache.clear_cache()

@receiver(post_save, sender=HomePageConfig)
def clear_homepage_config_cache_on_save(sender, instance, **kwargs):
    """HomePageConfig 保存后清除缓存"""
    SiteSettingsCache.clear_cache()

@receiver(post_delete, sender=HomePageConfig)
def clear_homepage_config_cache_on_delete(sender, instance, **kwargs):
    """HomePageConfig 删除后清除缓存"""
    SiteSettingsCache.clear_cache()

@receiver(post_save, sender=ServiceCard)
def clear_service_card_cache_on_save(sender, instance, **kwargs):
    """ServiceCard 保存后清除缓存"""
    SiteSettingsCache.clear_cache()

@receiver(post_delete, sender=ServiceCard)
def clear_service_card_cache_on_delete(sender, instance, **kwargs):
    """ServiceCard 删除后清除缓存"""
    SiteSettingsCache.clear_cache()

@receiver(post_save, sender=SiteSettings)
def clear_settings_cache_on_save(sender, instance, **kwargs):
    """SiteSettings 保存后清除缓存"""
    SiteSettingsCache.clear_cache()


# ============================================
# Proxy Models - 用于 Admin 分组显示
# ============================================

from django.contrib.sites.models import Site as DjangoSite


class PublicSite(DjangoSite):
    """
    站点代理模型
    - 不创建新表，使用 django_site 表
    - 在 Public 模块中显示为"站点管理"
    """
    class Meta:
        proxy = True
        verbose_name = '站点'
        verbose_name_plural = '站点管理'


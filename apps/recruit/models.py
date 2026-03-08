# jobs/models.py
from datetime import timedelta
from django.db import models
from django.utils import timezone
from django.urls import reverse
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator









class ActiveManager(models.Manager):
    """自定义管理器：只显示已发布且在有效期内的职位"""
    def get_queryset(self):
        return super().get_queryset().filter(
            is_published=True,
            expire_at__gte=timezone.now()
        )

def _default_expire():
    return timezone.now() + timedelta(days=60)

class Job(models.Model):
    # 安全方向枚举（可扩展）
    SECURITY_TRACK_CHOICES = [
        ('redteam', '红队'),
        ('blueteam', '蓝队'),
        ('secdev', '安全开发'),
        ('reversing', '逆向/恶意分析'),
        ('iot', '物联网安全'),
        ('cloud', '云安全'),
        ('compliance', '合规/等保'),
        ('carrisk', '车联网安全'),
        ('audit', '安全审计'),
        ('onsite','驻场服务'),
        ('operations','安全运营'),
        ('Penetration','渗透测试'),
        ('safetest','安全测试'),
        ('analysis','安全分析'),
        ('other', '其它'),
    ]

    Recruitment_Type = [
        ('school','校招'),
        ('society','社招'),
        ('internship','实习')
    ]

    # 基础字段
    title = models.CharField('职位名称', max_length=120)
    track = models.CharField(
        '安全方向', max_length=20,
        choices=SECURITY_TRACK_CHOICES, default='other'
    )
    RecruitmentType = models.CharField(
        '招聘类型', max_length=20,
        choices=Recruitment_Type, default='school')
    description = models.TextField('职位描述', help_text='支持 Markdown')
    requirements = models.TextField('任职要求', help_text='支持 Markdown')

    # 技能标签（逗号分隔，后续可拆多对多表）
    apply_link = models.URLField('在线投递链接', blank=True, help_text='可填 Boss/拉勾/官网链接')
    Internal_push = models.CharField('内推码', max_length=30, blank=True)

    # 薪资范围（单位：千/年，可空）
    salary_min = models.PositiveSmallIntegerField(
        '最低年薪(k)', null=True, blank=True,
        validators=[MinValueValidator(1)]
    )
    salary_max = models.PositiveSmallIntegerField(
        '最高年薪(k)', null=True, blank=True,
        validators=[MinValueValidator(1)]
    )

    # 工作地点
    companyname = models.ManyToManyField('Company', blank=True, verbose_name='公司')
    cityname = models.ManyToManyField('City', blank=True, verbose_name='城市')
    address = models.CharField('来源', max_length=200, blank=True)

    # 状态与有效期
    is_published = models.BooleanField('立即发布', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    expire_at = models.DateTimeField('过期时间', default=_default_expire)
    send_author = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='send_authors', verbose_name="投递人员", blank=True) 

    # SEO / 运营
    slug = models.SlugField('URL', max_length=140, unique=True, help_text='留空将自动生成')
    views = models.IntegerField('阅览量', default=0)
    page_keywords = models.ManyToManyField('Tag',blank=True,verbose_name='标签')
    #page_description = models.CharField('SEO 描述', max_length=160, blank=True)

    objects = models.Manager()
    active = ActiveManager()  # Job.active.all()

    class Meta:
        verbose_name = '岗位配置'
        verbose_name_plural = '岗位配置'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['track', '-created_at']),
        ]

    def __str__(self):
        return f'{self.title} '

    def get_absolute_url(self):
        return reverse('recruit:jobdetail', kwargs={'slug': self.slug})

    def save(self, *args, **kwargs):
        # 自动生成 slug
        if not self.slug:
            from django.utils.text import slugify
            import uuid
            base = slugify(self.title, allow_unicode=True) or 'job'
            # 使用UUID确保首次保存时slug的唯一性
            unique_suffix = str(uuid.uuid4())[:8]
            self.slug = f'{base}-{unique_suffix}'
        
        super().save(*args, **kwargs)
        
        # 清除相关城市和公司的缓存
        self._clear_related_cache()
    
    def update_views(self):
        """增加浏览量（不更新updated_at字段）"""
        self.views += 1
        # 浏览量更新不触发缓存清除
        super(Job, self).save(update_fields=['views'])
    
    def delete(self, *args, **kwargs):
        """删除职位时清除相关缓存"""
        # 先清除缓存，再删除对象
        self._clear_related_cache()
        super().delete(*args, **kwargs)
    
    def _clear_related_cache(self):
        """清除相关城市和公司的职位列表缓存"""
        from django.core.cache import cache
        
        # 清除所有相关城市的缓存（所有页码）
        for city in self.cityname.all():
            # 清除该城市的所有页面缓存（假设最多100页）
            for page in range(1, 101):
                cache_key = f'city_jobs:{city.slug}:page:{page}'
                cache.delete(cache_key)
        
        # 清除所有相关公司的缓存（所有页码）
        for company in self.companyname.all():
            # 清除该公司的所有页面缓存（假设最多100页）
            for page in range(1, 101):
                cache_key = f'company_jobs:{company.slug}:page:{page}'
                cache.delete(cache_key)

    @property
    def salary_desc(self):
        """前端友好薪资描述"""
        if self.salary_min and self.salary_max:
            return f'{self.salary_min}k-{self.salary_max}k/年'
        if self.salary_min:
            return f'{self.salary_min}k/年 起'
        if self.salary_max:
            return f'最高 {self.salary_max}k/年'
        return '面议'

    

    
class Tag(models.Model):
    name = models.CharField('岗位关键字', max_length=20, unique=True)
    slug = models.SlugField(unique=True, null=True, blank=True)
    description = models.TextField('描述', default='暂无描述',
                                   help_text='用来作为SEO中description,长度参考SEO标准')

    class Meta:
        verbose_name = '标签配置'
        verbose_name_plural = verbose_name
        ordering = ['id']

    def __str__(self):
        return self.name


class City(models.Model):
    name = models.CharField('城市名称', max_length=20, unique=True)
    slug = models.SlugField(unique=True, null=True, blank=True)
    description = models.TextField('描述', default='暂无描述',
                                   help_text='用来作为SEO中description,长度参考SEO标准')

    class Meta:
        verbose_name = '城市配置'
        verbose_name_plural = verbose_name
        ordering = ['id']

    def __str__(self):
        return self.name
    
    def get_absolute_url(self):
        if self.slug:
            return reverse('recruit:city_detail', kwargs={'slug': self.slug})
        return reverse('recruit:city_detail', kwargs={'slug': self.id})
    
    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            self.slug = slugify(self.name, allow_unicode=True)
        super().save(*args, **kwargs)


class Company(models.Model):
    company_name = models.CharField('公司名称', max_length=100)
    slug = models.SlugField(unique=True, null=True, blank=True)
    description = models.TextField('描述', default='暂无描述',
                                   help_text='用来作为SEO中description,长度参考SEO标准')
    company_size = models.CharField(
        '公司规模', max_length=20,
        choices=[
            ('<15', '15人以下'),
            ('15-50', '15-50人'),
            ('50-150', '50-150人'),
            ('150-500', '150-500人'),
            ('500-2000', '500-2000人'),
            ('>2000', '2000人以上'),
        ], blank=True
    )
    company_homepage = models.URLField('公司官网', blank=True)

    # 招聘联系
    contact_email = models.EmailField('HR 邮箱', blank=True)
    website = models.URLField('招聘官网', blank=True, help_text='招聘官网')

    class Meta:
        verbose_name = '公司配置'
        verbose_name_plural = verbose_name
        ordering = ['id']

    def __str__(self):
        return self.company_name
    
    def get_absolute_url(self):
        if self.slug:
            return reverse('recruit:company_detail', kwargs={'slug': self.slug})
        return reverse('recruit:company_detail', kwargs={'slug': self.id})
    
    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            self.slug = slugify(self.company_name, allow_unicode=True)
        super().save(*args, **kwargs)
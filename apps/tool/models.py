from django.db import models
from django.urls import reverse


# Create your models here.

class ToolCategory(models.Model):
    name = models.CharField('工具分类名称', max_length=20)
    key = models.CharField('分类标识', max_length=50, unique=True, help_text='英文标识，如：office, auxiliary, develop, web')
    order_num = models.IntegerField('序号', default=99, help_text='序号可以用来调整顺序，越小越靠前')
    icon = models.CharField('图标', max_length=50, blank=True, null=True, default='fa fa-link')
    is_active = models.BooleanField('是否启用', default=True)
    created_time = models.DateTimeField('创建时间', auto_now_add=True)
    updated_time = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '工具分类'
        verbose_name_plural = verbose_name
        ordering = ['order_num', 'id']

    def __str__(self):
        return self.name


class Tool(models.Model):
    """在线工具模型"""
    TOOL_TYPE_CHOICES = (
        ('internal', '内部工具'),
        ('external', '外部链接'),
    )
    
    name = models.CharField('工具名称', max_length=50)
    description = models.CharField('工具描述', max_length=200)
    tool_type = models.CharField('工具类型', max_length=20, choices=TOOL_TYPE_CHOICES, default='internal')
    
    # 内部工具使用URL名称
    url_name = models.CharField('URL路由名称', max_length=100, blank=True, null=True, 
                                 help_text='Django URL名称，如：tool:markdown_editor')
    
    # 外部工具使用完整URL
    external_url = models.URLField('外部链接', blank=True, null=True, 
                                    help_text='外部工具的完整URL地址')
    
    icon = models.CharField('工具图标', max_length=200, 
                            help_text='Font Awesome图标类名，如：fa fa-link, fa fa-code, fa fa-wrench 等。参考：https://fontawesome.com/icons')
    category = models.ForeignKey(ToolCategory, verbose_name='工具分类', 
                                  on_delete=models.CASCADE, related_name='tools')
    
    order_num = models.IntegerField('序号', default=99, help_text='序号可以用来调整顺序，越小越靠前')
    is_published = models.BooleanField('是否发布', default=True, help_text='控制工具是否在前台显示')
    is_admin_only = models.BooleanField('仅管理员可见', default=False, help_text='勾选后只有管理员能在前端看到此工具')
    views = models.PositiveIntegerField('浏览次数', default=0)
    
    created_time = models.DateTimeField('创建时间', auto_now_add=True)
    updated_time = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '在线工具'
        verbose_name_plural = verbose_name
        ordering = ['category__order_num', 'order_num', 'id']

    def __str__(self):
        return self.name
    
    def get_absolute_url(self):
        """获取工具的访问URL"""
        if self.tool_type == 'internal' and self.url_name:
            try:
                return reverse(self.url_name)
            except:
                return '#'
        elif self.tool_type == 'external' and self.external_url:
            return self.external_url
        return '#'
    
    def get_target(self):
        """获取链接打开方式"""
        return '_blank' if self.tool_type == 'external' else '_self'


class ToolLink(models.Model):
    """推荐工具链接（保留原有的外部链接功能）"""
    name = models.CharField('网站名称', max_length=20)
    description = models.CharField('网站描述', max_length=100)
    link = models.URLField('网站链接')
    order_num = models.IntegerField('序号', default=99, help_text='序号可以用来调整顺序，越小越靠前')
    is_admin_only = models.BooleanField('仅管理员可见', default=False, help_text='勾选后只有管理员能在前端看到此工具链接')
    category = models.ForeignKey(ToolCategory, verbose_name='网站分类', blank=True, null=True,
                                 on_delete=models.SET_NULL)

    class Meta:
        verbose_name = '工具链接'
        verbose_name_plural = verbose_name
        ordering = ['order_num', 'id']

    def __str__(self):
        return self.name

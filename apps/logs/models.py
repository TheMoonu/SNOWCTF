from django.db import models







class SystemLog(models.Model):
    """系统日志模型"""
    LEVEL_CHOICES = (
        ('DEBUG', 'DEBUG'),
        ('INFO', 'INFO'),
        ('WARNING', 'WARNING'),
        ('ERROR', 'ERROR'),
        ('CRITICAL', 'CRITICAL'),
    )
    
    timestamp = models.DateTimeField('记录时间', auto_now_add=True)
    level = models.CharField('日志级别', max_length=10, choices=LEVEL_CHOICES)
    logger_name = models.CharField('日志器名称', max_length=100)
    message = models.TextField('日志消息')
    user = models.CharField('用户', max_length=100, blank=True, null=True)
    ip_address = models.GenericIPAddressField('IP地址', blank=True, null=True)
    request_path = models.CharField('请求路径', max_length=255, blank=True, null=True)
    module = models.CharField('模块', max_length=100, blank=True, null=True)
    function = models.CharField('函数', max_length=100, blank=True, null=True)
    stack_trace = models.TextField('堆栈跟踪', blank=True, null=True)
    
    class Meta:
        verbose_name = '系统日志'
        verbose_name_plural = verbose_name
        ordering = ['-timestamp']
        
    def __str__(self):
        return f"{self.timestamp} [{self.level}] {self.message[:50]}"
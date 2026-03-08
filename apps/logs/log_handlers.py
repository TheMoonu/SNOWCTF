import logging
import traceback
from django.conf import settings
from django.db import transaction

class DatabaseLogHandler(logging.Handler):
    """数据库日志处理器"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 延迟导入模型，避免循环导入
        self.model = None
    
    def _get_model(self):
        """延迟加载模型"""
        if self.model is None:
            # 修改导入路径
            from logs.models import SystemLog  
            self.model = SystemLog
        return self.model
    
    def emit(self, record):
        """将日志记录到数据库"""
        # 避免在处理日志时出现的异常导致无限递归
        try:
            # 使用事务确保日志记录的原子性
            with transaction.atomic():
                model = self._get_model()
                
                # 提取请求信息（如果可用）
                request = getattr(record, 'request', None)
                user = None
                ip_address = None
                request_path = None
                
                if request:
                    # 获取用户信息
                    if hasattr(request, 'user') and request.user.is_authenticated:
                        user = request.user.username
                    
                    # 获取IP地址
                    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
                    if x_forwarded_for:
                        ip_address = x_forwarded_for.split(',')[0].strip()
                    else:
                        ip_address = request.META.get('REMOTE_ADDR')
                    
                    # 获取请求路径
                    request_path = request.path
                
                # 提取堆栈信息（如果有异常）
                stack_trace = None
                if record.exc_info:
                    stack_trace = ''.join(traceback.format_exception(*record.exc_info))
                
                # 创建日志记录
                model.objects.create(
                    level=record.levelname,
                    logger_name=record.name,
                    message=self.format(record),
                    user=user,
                    ip_address=ip_address,
                    request_path=request_path,
                    module=getattr(record, 'module', ''),
                    function=getattr(record, 'funcName', ''),
                    stack_trace=stack_trace
                )
        except Exception as e:
            # 如果记录日志时出错，打印错误信息并使用标准错误输出
            print(f"数据库日志记录失败: {str(e)}")
            self.handleError(record)
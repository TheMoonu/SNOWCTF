"""
知识竞赛工具函数
提供缓存、锁、安全验证等功能
"""
import hashlib
import time
from functools import wraps
from django.core.cache import cache
from django.conf import settings
from django.http import JsonResponse
import logging

logger = logging.getLogger(__name__)


class RedisLock:
    """Redis分布式锁"""
    
    def __init__(self, key, timeout=10, retry_times=3, retry_delay=0.1):
        self.key = f"lock:{key}"
        self.timeout = timeout
        self.retry_times = retry_times
        self.retry_delay = retry_delay
        self.lock_value = None
    
    def __enter__(self):
        """获取锁"""
        for i in range(self.retry_times):
            # 生成唯一锁值
            self.lock_value = hashlib.md5(
                f"{self.key}:{time.time()}".encode()
            ).hexdigest()
            
            # 尝试获取锁（NX表示不存在时才设置）
            if cache.add(self.key, self.lock_value, self.timeout):
                return self
            
            # 获取失败，等待后重试
            if i < self.retry_times - 1:
                time.sleep(self.retry_delay)
        
        raise Exception(f"获取锁失败: {self.key}")
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """释放锁"""
        try:
            # 只有持有锁的进程才能释放
            if cache.get(self.key) == self.lock_value:
                cache.delete(self.key)
        except Exception as e:
            logger.error(f"释放锁失败: {e}")


def cache_result(key_prefix, timeout=300):
    """
    缓存函数结果装饰器
    
    Args:
        key_prefix: 缓存键前缀
        timeout: 过期时间（秒）
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 生成缓存键
            cache_key = f"{key_prefix}:{hash(str(args) + str(sorted(kwargs.items())))}"
            
            # 尝试从缓存获取
            result = cache.get(cache_key)
            if result is not None:
                return result
            
            # 执行函数
            result = func(*args, **kwargs)
            
            # 缓存结果
            cache.set(cache_key, result, timeout)
            
            return result
        return wrapper
    return decorator


def rate_limit(key_prefix, max_requests=10, window=60):
    """
    API频率限制装饰器
    
    Args:
        key_prefix: 限制键前缀
        max_requests: 时间窗口内最大请求数
        window: 时间窗口（秒）
    """
    def decorator(func):
        @wraps(func)
        def wrapper(request, *args, **kwargs):
            # 获取用户标识（IP或用户ID）
            if request.user.is_authenticated:
                identifier = f"user_{request.user.id}"
            else:
                identifier = get_client_ip(request)
            
            # 生成限流键
            rate_key = f"rate_limit:{key_prefix}:{identifier}"
            
            # 获取当前请求次数
            current = cache.get(rate_key, 0)
            
            if current >= max_requests:
                return JsonResponse({
                    'error': '请求过于频繁，请稍后再试',
                    'retry_after': cache.ttl(rate_key)
                }, status=429)
            
            # 增加计数
            if current == 0:
                cache.set(rate_key, 1, window)
            else:
                cache.incr(rate_key)
            
            return func(request, *args, **kwargs)
        return wrapper
    return decorator


def get_client_ip(request):
    """获取客户端IP地址"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def verify_quiz_access(user, quiz):
    """
    验证用户是否有权限访问竞赛
    
    Returns:
        (bool, str): (是否有权限, 错误信息)
    """
    from django.utils import timezone
    
    # 检查竞赛是否启用
    if not quiz.is_active:
        return False, "竞赛未启用"
    
    # 检查时间范围
    now = timezone.now()
    if quiz.start_time and now < quiz.start_time:
        return False, "竞赛还未开始"
    
    if quiz.end_time and now > quiz.end_time:
        return False, "竞赛已结束"
    
    # 检查答题次数
    can_attempt, message = quiz.can_user_attempt(user)
    if not can_attempt:
        return False, message
    
    return True, ""


def verify_answer_integrity(answer_data, question):
    """
    验证答案数据完整性
    
    Args:
        answer_data: 答案数据（选项ID列表）
        question: 题目对象
    
    Returns:
        (bool, str): (是否有效, 错误信息)
    """
    # 验证选项数量
    if question.question_type == 'single' and len(answer_data) > 1:
        return False, "单选题只能选择一个选项"
    
    if question.question_type == 'judge' and len(answer_data) > 1:
        return False, "判断题只能选择一个选项"
    
    if question.question_type == 'multiple' and len(answer_data) < 2:
        return False, "多选题至少选择两个选项"
    
    # 验证选项是否属于该题目
    valid_option_ids = set(question.options.values_list('id', flat=True))
    answer_ids = set(answer_data)
    
    if not answer_ids.issubset(valid_option_ids):
        return False, "选项不属于该题目"
    
    return True, ""


def generate_anti_csrf_token(record_id, user_id):
    """
    生成防CSRF令牌
    
    Args:
        record_id: 答题记录ID
        user_id: 用户ID
    
    Returns:
        str: CSRF令牌
    """
    secret = getattr(settings, 'SECRET_KEY', 'default-secret')
    timestamp = str(int(time.time()))
    
    token = hashlib.sha256(
        f"{record_id}:{user_id}:{timestamp}:{secret}".encode()
    ).hexdigest()
    
    # 缓存令牌（30分钟有效）
    cache_key = f"csrf_token:{record_id}:{user_id}"
    cache.set(cache_key, token, 1800)
    
    return token


def verify_anti_csrf_token(record_id, user_id, token):
    """
    验证防CSRF令牌
    
    Args:
        record_id: 答题记录ID
        user_id: 用户ID
        token: 待验证的令牌
    
    Returns:
        bool: 是否有效
    """
    cache_key = f"csrf_token:{record_id}:{user_id}"
    cached_token = cache.get(cache_key)
    
    return cached_token == token


class QueryOptimizer:
    """查询优化器"""
    
    @staticmethod
    def optimize_quiz_detail(quiz):
        """优化竞赛详情查询"""
        from django.db.models import Prefetch
        from quiz.models import QuizQuestion, Question, Option
        
        return quiz.prefetch_related(
            Prefetch(
                'quiz_questions',
                queryset=QuizQuestion.objects.select_related('question').prefetch_related(
                    Prefetch(
                        'question__options',
                        queryset=Option.objects.order_by('order')
                    )
                ).order_by('order')
            )
        )
    
    @staticmethod
    def optimize_record_list(queryset):
        """优化答题记录列表查询"""
        return queryset.select_related('quiz', 'user').only(
            'uuid', 'quiz__title', 'user__username',
            'status', 'score', 'start_time', 'submit_time',
            'violation_count'
        )
    
    @staticmethod
    def optimize_answer_detail(queryset):
        """优化答案详情查询"""
        from django.db.models import Prefetch
        from quiz.models import Option
        
        return queryset.select_related('question').prefetch_related(
            Prefetch(
                'selected_options',
                queryset=Option.objects.order_by('order')
            ),
            Prefetch(
                'question__options',
                queryset=Option.objects.order_by('order')
            )
        )


def log_security_event(event_type, user, details):
    """
    记录安全事件
    
    Args:
        event_type: 事件类型
        user: 用户对象
        details: 详细信息
    """
    logger.warning(
        f"Security Event: {event_type} | "
        f"User: {user.username if user else 'Anonymous'} | "
        f"Details: {details}"
    )


def sanitize_input(data):
    """
    清理输入数据，防止XSS
    
    Args:
        data: 输入数据
    
    Returns:
        清理后的数据
    """
    import html
    
    if isinstance(data, str):
        return html.escape(data)
    elif isinstance(data, dict):
        return {k: sanitize_input(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitize_input(item) for item in data]
    
    return data


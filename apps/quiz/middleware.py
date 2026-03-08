"""
Quiz应用中间件 - 用于性能优化和安全防护
"""
from django.core.cache import cache
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin
import time
import json


class QuizRequestBodyCacheMiddleware(MiddlewareMixin):
    """
    缓存POST请求体中间件
    解决 request.body 只能读取一次的问题
    """
    
    def process_request(self, request):
        # 只处理答题相关的 POST 请求
        if request.method == 'POST' and '/quiz/' in request.path:
            try:
                # 提前读取并缓存请求体
                if not hasattr(request, '_body') or request._body is None:
                    # 读取原始字节
                    body = request.body  # 这会触发读取并自动缓存到 _body
                    
                # 如果是 JSON 请求，预解析并缓存
                if request.content_type == 'application/json' and not hasattr(request, '_cached_json_body'):
                    try:
                        request._cached_json_body = json.loads(request._body.decode('utf-8'))
                    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                        # 解析失败，不缓存
                        pass
            except Exception:
                # 如果读取失败，不影响后续处理
                pass
        
        return None


class QuizRateLimitMiddleware(MiddlewareMixin):
    """
    答题系统速率限制中间件
    防止恶意刷题和频繁提交
    """
    
    def process_request(self, request):
        # 只对答题相关的POST请求进行限制
        if request.method == 'POST' and '/quiz/' in request.path:
            user_id = request.user.id if request.user.is_authenticated else request.META.get('REMOTE_ADDR')
            
            # 保存答案的限流：每秒最多2次
            if '/save/' in request.path:
                cache_key = f'quiz_save_rate_{user_id}'
                current_time = time.time()
                
                last_request_time = cache.get(cache_key)
                if last_request_time:
                    time_diff = current_time - last_request_time
                    if time_diff < 0.5:  # 0.5秒内不能重复请求
                        return JsonResponse({
                            'success': False,
                            'message': '操作过于频繁，请稍后再试'
                        }, status=429)
                
                cache.set(cache_key, current_time, 60)  # 缓存60秒
            
            # 提交试卷的限流：每分钟最多1次
            elif '/submit/' in request.path:
                cache_key = f'quiz_submit_rate_{user_id}'
                submit_count = cache.get(cache_key, 0)
                
                if submit_count >= 1:
                    return JsonResponse({
                        'success': False,
                        'message': '提交过于频繁，请稍后再试'
                    }, status=429)
                
                cache.set(cache_key, submit_count + 1, 60)  # 缓存60秒
        
        return None


class QuizSecurityMiddleware(MiddlewareMixin):
    """
    答题系统安全中间件
    防止各种攻击
    """
    
    def process_request(self, request):
        # 检查可疑的请求头
        user_agent = request.META.get('HTTP_USER_AGENT', '')
        
        # 阻止已知的爬虫
        blocked_agents = ['bot', 'spider', 'crawler', 'scraper']
        if any(agent in user_agent.lower() for agent in blocked_agents):
            if '/quiz/' in request.path and request.method == 'POST':
                return JsonResponse({
                    'success': False,
                    'message': '访问被拒绝'
                }, status=403)
        
        return None


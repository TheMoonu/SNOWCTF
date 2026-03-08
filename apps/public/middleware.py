from django.http import HttpResponseForbidden
from django.utils.deprecation import MiddlewareMixin

class APIPermissionMiddleware(MiddlewareMixin):
    def process_request(self, request):
        # 检查请求路径是否以 /api/v1 开头
        if request.path.startswith('/api/v1'):
            # 检查用户是否已登录
            if not request.user.is_authenticated:
                return HttpResponseForbidden('无权限访问')
        return None

from django.core.exceptions import PermissionDenied

class MemberCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hasattr(request, 'user'):
            if not request.user.is_authenticated:
                request.user.is_member = False
        return self.get_response(request)
# -*- coding: utf-8 -*-
from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from django.urls import reverse


def check_tool_permission(url_name):
    """
    检查工具访问权限的装饰器
    @param url_name: URL名称，如 'tool:ip'
    """
    def decorator(func):
        @wraps(func)
        def wrapper(request, *args, **kwargs):
            from .models import Tool
            
            # 查找对应的Tool对象
            try:
                tool = Tool.objects.get(url_name=url_name, is_published=True)
                
                # 检查是否仅管理员可见
                if tool.is_admin_only:
                    # 检查用户是否为管理员
                    if not (request.user.is_authenticated and request.user.is_superuser):
                        messages.warning(request, '此工具仅限管理员访问')
                        return redirect('tool:total')
            except Tool.DoesNotExist:
                # 如果Tool不存在，说明可能是老工具或未配置，允许访问
                pass
            except Tool.MultipleObjectsReturned:
                # 如果有多个Tool，取第一个
                tool = Tool.objects.filter(url_name=url_name, is_published=True).first()
                if tool and tool.is_admin_only:
                    if not (request.user.is_authenticated and request.user.is_superuser):
                        messages.warning(request, '此工具仅限管理员访问')
                        return redirect('tool:total')
            
            return func(request, *args, **kwargs)
        
        return wrapper
    
    return decorator

from django import template
from django.http import QueryDict

register = template.Library()


@register.simple_tag
def url_replace(request, **kwargs):
    """
    替换URL中的查询参数，同时保留其他参数
    用法: <a href="?{% url_replace request sort='views' %}">
    返回: 如果有参数返回 'param=value'，如果没有参数返回空字符串
    """
    query = request.GET.copy()
    
    # 更新或添加新参数
    for key, value in kwargs.items():
        if value is None or value == '':
            # 如果值为空，删除该参数
            query.pop(key, None)
        else:
            query[key] = value
    
    # 移除所有空值参数和空key参数
    keys_to_remove = []
    for key, value in query.items():
        if not key or not value or (isinstance(value, str) and not value.strip()):
            keys_to_remove.append(key)
    
    for key in keys_to_remove:
        query.pop(key, None)
    
    # 如果没有参数，返回空字符串；否则返回编码后的查询字符串
    encoded = query.urlencode()
    return encoded if encoded else ''


@register.simple_tag
def build_query_url(request, **kwargs):
    """
    构建完整的查询URL（包括问号）
    用法: <a href="{% build_query_url request sort='views' %}">
    返回: 如果有参数返回 '?param=value'，如果没有参数返回当前路径
    """
    from django.urls import resolve
    
    query = request.GET.copy()
    
    # 更新或添加新参数
    for key, value in kwargs.items():
        if value is None or value == '':
            # 如果值为空，删除该参数
            query.pop(key, None)
        else:
            query[key] = value
    
    # 移除所有空值参数和空key参数
    keys_to_remove = []
    for key, value in query.items():
        if not key or not value or (isinstance(value, str) and not value.strip()):
            keys_to_remove.append(key)
    
    for key in keys_to_remove:
        query.pop(key, None)
    
    # 构建URL
    encoded = query.urlencode()
    if encoded:
        return f"?{encoded}"
    else:
        # 没有参数时返回当前路径（不带问号）
        return request.path


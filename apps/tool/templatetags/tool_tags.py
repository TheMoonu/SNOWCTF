# -*- coding: utf-8 -*-
from django import template
from django.db.models.aggregates import Count
from django.templatetags.static import static
from django.urls import reverse

from ..models import ToolCategory
from ..utils import IZONE_TOOLS

register = template.Library()


@register.simple_tag
def get_toolcates():
    """获取所有工具分类，只显示有工具的分类"""
    return ToolCategory.objects.annotate(total_num=Count('toollink')).filter(
        total_num__gt=0)


@register.simple_tag(takes_context=True)
def get_toollinks(context, cate):
    """获取单个分类下所有工具"""
    request = context.get('request')
    is_admin = request and request.user.is_authenticated and request.user.is_superuser
    
    toollinks = cate.toollink_set.all()
    
    # 如果不是管理员，过滤掉仅管理员可见的工具链接
    if not is_admin:
        toollinks = toollinks.exclude(is_admin_only=True)
    
    return toollinks


@register.simple_tag(takes_context=True)
def get_toollist_by_key(context, key=None):
    """返回工具列表（从数据库读取）"""
    from ..models import Tool, ToolCategory
    
    tools = []
    request = context.get('request')
    is_admin = request and request.user.is_authenticated and request.user.is_superuser
    
    # 获取查询集
    if key:
        # 按分类key筛选
        queryset = Tool.objects.filter(
            category__key=key,
            category__is_active=True,
            is_published=True
        ).select_related('category')
    else:
        # 获取所有已发布的工具
        queryset = Tool.objects.filter(
            category__is_active=True,
            is_published=True
        ).select_related('category')
    
    # 转换为前端需要的格式
    for tool in queryset:
        # 如果是仅管理员可见的工具，且当前用户不是管理员，则跳过
        if tool.is_admin_only and not is_admin:
            continue
            
        item = {
            'tag': tool.category.name,
            'name': tool.name,
            'url': tool.get_absolute_url(),
            'icon': tool.icon,  # 直接使用Font Awesome类名
            'desc': tool.description,
            'target': tool.get_target(),
        }
        tools.append(item)
    
    return tools


@register.inclusion_tag('tool/tags/tool_item.html')
def load_tool_item(item):
    """返回单个工具显示栏"""
    return {'tool_item': item}


@register.inclusion_tag('tool/tags/github_corners.html')
def load_github_corners(position, color, url):
    """
    加载github项目跳转，根据颜色返回
    """
    return {'position': position, 'color': color, 'url': url}

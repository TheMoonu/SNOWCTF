# -*- coding: utf-8 -*-
from django import template

register = template.Library()


@register.simple_tag
def get_search_type_options():
    """获取搜索类型选项"""
    return [
        {'value': 'article', 'label': '文章', 'icon': ''},
        {'value': 'challenge', 'label': '靶场', 'icon': ''},
        {'value': 'job', 'label': '岗位', 'icon': ''},
    ]


@register.filter
def default_search_options(value):
    """如果没有传入选项，使用默认选项"""
    if not value:
        return get_search_type_options()
    return value


@register.filter
def default_navbar_search_options(value):
    """导航栏搜索类型默认选项"""
    if not value:
        return [
            {'value': 'article', 'label': '文章', 'icon': ''},
            {'value': 'challenge', 'label': '靶场', 'icon': ''},
            {'value': 'job', 'label': '岗位', 'icon': ''},
        ]
    return value


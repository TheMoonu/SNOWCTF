from django import template
from oauth.models import Ouser

register = template.Library()

@register.filter
def is_following(user, target_user):
    """检查用户是否关注了目标用户"""
    if not user.is_authenticated:  # 检查用户是否登录
        return False
    return user.is_following(target_user)  # 使用用户实例调用方法

@register.filter
def format_count(value):
    """
    将数字格式化为K单位
    例如：
    1000 -> 1K
    1500 -> 1.5K
    999 -> 999
    """
    if value >= 1000:
        k_value = value / 1000
        if k_value.is_integer():
            return f"{int(k_value)}K"
        return f"{k_value:.1f}K"
    return str(value)

@register.filter
def format_k(value):
    try:
        value = int(value)
        if value >= 1000:
            return f"{value / 1000:.1f}k"
        return str(value)
    except (ValueError, TypeError):
        return value
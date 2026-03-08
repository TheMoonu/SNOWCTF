import json
import re
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from django.contrib.auth import get_user_model
from django import template
from django.core.cache import cache
from django.db.models.aggregates import Count
from django.utils.html import mark_safe
from django.db.models import Q
from ..models import (
    PC_Challenge,
    CTFUser,
    Tag,
    SolveRecord
)
from practice.redis_cache import UserContainerCache
from container.models import UserContainer
import markdown as md_lib  # 重命名以避免与过滤器函数冲突
from markdown.extensions.codehilite import CodeHiliteExtension
from markdown.extensions.toc import TocExtension
from pygments.formatters.html import HtmlFormatter
from django.utils.text import slugify
from django.utils import timezone

# 导入自定义 Markdown 扩展
from utils.markdown_ext import (
    DelExtension,
    IconExtension,
    AlertExtension,
    CodeItemExtension,
    CodeGroupExtension
)

register = template.Library()


# 自定义代码高亮格式化器
class CustomHtmlFormatter(HtmlFormatter):
    def __init__(self, lang_str='', **options):
        super().__init__(**options)
        self.lang_str = lang_str

    def _wrap_code(self, source):
        yield 0, f'<code class="{self.lang_str}">'
        yield from source
        yield 0, '</code>'

@register.simple_tag
def get_user_ctf_stats(user):
    """
    获取用户的 CTF 统计信息,包括挑战总数,用户解题数,用户总分数和用户金币数
    """
    # 检查是否是匿名用户
    if not user.is_authenticated:
        return {
            'user_solves': 0,
            'user_score': 0,
            'user_coins': 0
        }
    
    # 生成唯一的缓存键
    cache_key = f'user_ctf_stats_{user.id}'
    
    # 尝试从缓存中获取数据
    cached_data = cache.get(cache_key)
    if cached_data:
        return json.loads(cached_data)
    
    # 如果缓存中没有数据,则从数据库中获取
    
   
    try:
        ctf_user, created = CTFUser.objects.get_or_create(user=user)
        user_score = ctf_user.score
        user_coins = ctf_user.coins
        user_solves = ctf_user.solves
    except CTFUser.DoesNotExist:
        user_score = 0
        user_coins = 0
        user_solves = 0
        
    # 创建包含所有信息的字典
    stats = {
        'user_solves': user_solves,
        'user_score': user_score,
        'user_coins': user_coins
    }
    
    # 将数据存储到缓存中,设置过期时间为 1 小时
    cache.set(cache_key, json.dumps(stats), 3600)
    
    return stats

@register.simple_tag
def get_challenge_categories():
    """
    返回题目类型及数量，使用 Redis 缓存结果
    """
    cache_key = 'pcchallenge_categories'
    
    # 尝试从缓存中获取数据
    cached_data = cache.get(cache_key)
    if cached_data:
        return json.loads(cached_data)
    
    # 如果缓存中没有数据，则从数据库中获取
    categories = PC_Challenge.objects.values('category').annotate(count=Count('category')).order_by('category')
    result = {category['category']: category['count'] for category in categories}
    
    # 将数据存储到缓存中，设置过期时间为 5 分钟
    cache.set(cache_key, json.dumps(result), 3600)
    
    return result

@register.simple_tag
def get_all_challenge_tags(user=None):
    """
    返回所有被使用的挑战标签及其使用次数和用户完成进度
    
    Args:
        user: 当前用户（可选）
        
    Returns:
        list: 包含标签信息的字典列表，按完成进度排序，每个字典包含：
            - tag: Tag 对象
            - total: 该学习岛总题目数
            - solved: 用户已完成题目数（如果提供了 user）
            - progress: 完成进度百分比（如果提供了 user）
    """
    # 如果提供了用户，尝试从缓存获取
    if user and user.is_authenticated:
        cache_key = f'user_island_progress:{user.id}'
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result
    
    # 获取所有有题目的标签
    tags_with_count = Tag.objects.annotate(
        total_count=Count('pc_challenge')
    ).filter(total_count__gt=0).order_by('-total_count', 'name')
    
    result = []
    
    # 如果提供了用户，计算完成进度
    if user and user.is_authenticated:
        try:
            from public.models import CTFUser
            ctf_user = CTFUser.objects.get(user=user)
            solved_challenges = set(ctf_user.solved_challenges.values_list('id', flat=True))
            
            for tag in tags_with_count:
                # 获取该标签下的所有题目 ID
                tag_challenge_ids = set(tag.pc_challenge_set.values_list('id', flat=True))
                
                # 计算已完成的题目数
                solved_count = len(tag_challenge_ids & solved_challenges)
                total_count = tag.total_count
                
                # 计算进度百分比
                progress = int((solved_count / total_count * 100)) if total_count > 0 else 0
                
                result.append({
                    'tag': tag,
                    'total': total_count,
                    'solved': solved_count,
                    'progress': progress
                })
            
            # 按进度排序：已完成的(100%)在前，然后按进度从高到低，最后按题目数量
            result.sort(key=lambda x: (-x['progress'], -x['total']))
            
            # 缓存结果（5分钟）
            cache.set(cache_key, result, 300)
            
        except Exception:
            # 如果获取用户数据失败，返回不带进度的数据
            for tag in tags_with_count:
                result.append({
                    'tag': tag,
                    'total': tag.total_count,
                    'solved': 0,
                    'progress': 0
                })
    else:
        # 没有用户时，只返回总数（按题目数量排序）
        for tag in tags_with_count:
            result.append({
                'tag': tag,
                'total': tag.total_count,
                'solved': 0,
                'progress': 0
            })
    
    return result


@register.simple_tag
def get_users_ranked_by_solves(limit=None):
    User = get_user_model()
    # 缓存键
    cache_key = 'users_ranked_by_solves_nonzero'
    if limit:
        cache_key += f'_limit_{limit}'

    # 尝试从缓存中获取结果
    ranked_users = cache.get(cache_key)

    if ranked_users is None:
        # 获取排名用户
        users = CTFUser.objects.filter(score__gt=0)\
                              .order_by('-score')
        if limit:
            users = users[:limit]

        ranked_users = list(users)
        for index, user in enumerate(ranked_users):
            user.rank = index + 1
            try:
                # 尝试查找对应的 User 对象
                django_user = User.objects.get(username=user.user)
                user.uuid = django_user.uuid
                # 添加用户头像
                user.avatar = django_user.avatar.url if hasattr(django_user, 'avatar') and django_user.avatar else None
            except User.DoesNotExist:
                user.uuid = None
                user.avatar = None

        # 将结果存入缓存，设置过期时间为 5 分钟
        cache.set(cache_key, ranked_users, 3600)

    return ranked_users

@register.simple_tag
def get_challenge_solve_records(challenge, limit=10):
    cache_key = f'challenge_{challenge.uuid}_limit_10'
    
    # 尝试从缓存中获取结果
    solve_records = cache.get(cache_key)
    
    if solve_records is None:
        # 如果缓存中没有，从数据库获取
        solve_records = SolveRecord.objects.filter(challenge__uuid=challenge.uuid)\
                                   .select_related('user')\
                                   .order_by('-solved_at')[:limit]
        
        # 将结果存入缓存，设置过期时间为 1 分钟（可以根据需求调整）
        cache.set(cache_key, list(solve_records), 3600)
    
    return solve_records

@register.simple_tag
def get_challenge_tags(challenge_uuid):
    cache_key = f'challenge_tags_{challenge_uuid}'
    tags = cache.get(cache_key)
    
    if tags is None:
        try:
            challenge = PC_Challenge.objects.get(uuid=challenge_uuid)
            # 获取完整的Tag对象，而不仅仅是名称
            tags = list(challenge.tags.all())
            cache.set(cache_key, tags, 3600)  # 缓存1小时
        except PC_Challenge.DoesNotExist:
            tags = []
    
    return tags


@register.filter
def format_k(value):
    try:
        value = int(value)
        if value >= 1000:
            return f"{value / 1000:.1f}k"
        return str(value)
    except (ValueError, TypeError):
        return value

def make_practice_markdown():
    """创建统一的 Markdown 解析器（与博客模块一致）"""
    md = md_lib.Markdown(extensions=[
        'markdown.extensions.extra',
        'markdown_checklist.extension',
        CodeHiliteExtension(pygments_formatter=CustomHtmlFormatter),
        TocExtension(slugify=slugify),
        DelExtension(),
        IconExtension(),
        AlertExtension(),
        CodeItemExtension(),
        CodeGroupExtension()
    ])
    return md


@register.filter
def markdown(value):
    """Markdown 过滤器 - 渲染 Markdown 文本为 HTML（防XSS）"""
    if not value:
        return ''
    
    import bleach
    from bleach.css_sanitizer import CSSSanitizer
    
    # 1. 进行 Markdown 渲染
    md = make_practice_markdown()
    html_output = md.convert(value)
    
    # 2. 使用 bleach 清理 HTML，只允许安全的标签和属性
    # 允许的 HTML 标签
    allowed_tags = [
        'p', 'br', 'strong', 'em', 'u', 'del', 's', 'i',  # 添加 i 标签支持图标
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'blockquote', 'code', 'pre',
        'ul', 'ol', 'li',
        'a', 'img',
        'table', 'thead', 'tbody', 'tr', 'th', 'td',
        'div', 'span',
        'hr',
        'sup', 'sub',
        'dl', 'dt', 'dd'
    ]
    
    # 允许的 HTML 属性
    allowed_attributes = {
        '*': ['class', 'id'],
        'a': ['href', 'title', 'target', 'rel'],
        'img': ['src', 'alt', 'title', 'width', 'height'],
        'code': ['class'],
        'pre': ['class'],
        'div': ['class', 'style'],
        'span': ['class', 'style'],
        'td': ['colspan', 'rowspan'],
        'th': ['colspan', 'rowspan'],
    }
    
    # 允许的 CSS 属性（用于代码高亮等）
    css_sanitizer = CSSSanitizer(allowed_css_properties=[
        'color', 'background-color', 'font-weight', 'text-decoration',
        'padding', 'margin', 'border', 'border-left'
    ])
    
    # 使用 bleach 清理 HTML
    # strip=False: 转义不允许的标签而不是删除，这样可以显示 <script> 等代码示例但不会执行
    safe_html = bleach.clean(
        html_output,
        tags=allowed_tags,
        attributes=allowed_attributes,
        css_sanitizer=css_sanitizer,
        strip=False  # 转义不允许的标签，显示但不执行
    )
    
    return mark_safe(safe_html)


@register.filter
def compact_time(value):
    """
    将datetime对象转换为简洁的相对时间字符串
    例如：刚刚、5分钟前、2小时前、3天前、2周前
    """
    now = timezone.now()
    diff = now - value
    
    seconds = diff.total_seconds()
    
    if seconds < 60:
        return "刚刚"
    
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}分钟前"
    
    hours = int(minutes // 60)
    if hours < 24:
        return f"{hours}小时前"
    
    days = int(hours // 24)
    if days < 7:
        return f"{days}天前"
    
    weeks = int(days // 7)
    if weeks < 4:
        return f"{weeks}周前"
    
    months = int(days // 30)
    if months < 12:
        return f"{months}月前"
    
    years = int(days // 365)
    return f"{years}年前"


@register.simple_tag
def latest_challenges(count=5):
    """
    获取最新上线的题目
    :param count: 显示的题目数量，默认 5
    """
    cache_key = f'latest_challenges'
    challenges = cache.get(cache_key)
    
    if challenges is None:
        try:
            challenges = PC_Challenge.objects.order_by('-created_at')[:count]
            cache.set(cache_key, challenges, 3600)
        except PC_Challenge.DoesNotExist:
            return {}
    return challenges



def _units_len(text):
    """
    返回“视觉宽度”：中文汉字/全角符号=2，ASCII=1
    """
    # 所有 CJK 统一表意符号 + 全角标点
    return sum(2 if re.match(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]', ch) else 1
               for ch in text)


def _slice_by_visual(text, max_units, slice_obj: slice):
    """
    按视觉宽度截断，并返回实际下标切片
    """
    start, stop, step = slice_obj.indices(len(text))
    if step != 1:
        # 步长不为 1 的场景很少，先不处理
        return text[slice_obj]

    units = 0
    real_start, real_stop = None, None

    # 找 start 对应的真实字符下标
    i = 0
    for idx, ch in enumerate(text):
        if idx < start:
            continue
        if real_start is None:
            real_start = idx
        units += _units_len(ch)
        if units >= max_units:
            real_stop = idx + 1
            break
    else:  # 循环完都没超
        real_stop = len(text)

    return text[real_start:real_stop]


@register.filter(is_safe=True)
def my_slice(value, arg):
    """
    中英混排截断过滤器
    用法同内置 slice，但按「中文=2、英文=1」视觉宽度截断，
    超长在尾部补 ...，非头开始则在头部补 ...
    例：{{ desc|my_slice:":23" }}
    """
    if not isinstance(value, str):
        return value

    try:
        # 解析 slice 参数
        bits = []
        for x in str(arg).split(':'):
            if not x:
                bits.append(None)
            else:
                bits.append(int(x))
        slice_obj = slice(*bits)
        # 默认按「最后数字」当最大视觉宽度
        max_units = bits[-1] if bits[-1] else 9999

        # 真正截断
        result = _slice_by_visual(value, max_units, slice_obj)

        # 补 ... 逻辑
        if len(value) > len(result):
            result += '…'
        if slice_obj.start:
            result = '…' + result

        return result

    except (ValueError, TypeError):
        return value


@register.simple_tag(takes_context=True)
def get_user_active_container(context, user):
    """
    获取用户当前活跃的容器（用户只能有一个容器）
    
    Args:
        context: 模板上下文
        user: 当前用户
    
    Returns:
        dict: 容器信息，包含:
            - challenge_title: 题目标题
            - challenge_url: 题目 URL
            - expires_at: 过期时间
            - is_current: 是否是当前题目
        如果没有活跃容器则返回 None
    """
    if not user or not user.is_authenticated:
        return None
    
    # 从上下文中获取当前题目的 UUID（如果存在）
    current_challenge_uuid = None
    if 'challenge' in context and hasattr(context['challenge'], 'uuid'):
        current_challenge_uuid = str(context['challenge'].uuid)
    
    try:
        # 直接从缓存获取用户的容器信息
        cached_data = UserContainerCache.get_user_container(user.id)
        
        if not cached_data:
            return None
        
        # 从缓存读取题目信息
        challenge_uuid = cached_data.get('challenge_uuid')
        challenge_title = cached_data.get('challenge_id', '未知题目')
        
        # 拼接题目 URL
        challenge_url = f"/snowlab/{challenge_uuid}/"
        
        # 判断是否是当前题目
        is_current = challenge_uuid == str(current_challenge_uuid) if current_challenge_uuid else False
        
        # 将 expires_at 字符串转换为 datetime 对象
        from datetime import datetime
        expires_at_str = cached_data.get('expires_at', '')
        expires_at = None
        if expires_at_str:
            try:
                expires_at = datetime.fromisoformat(expires_at_str)
            except:
                pass
        
        return {
            'challenge_title': challenge_title,
            'challenge_url': challenge_url,
            'expires_at': expires_at,
            'is_current': is_current
        }
    
    except Exception as e:
        # 静默失败，不影响页面其他部分
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"获取用户活跃容器失败: {e}")
        return None


@register.simple_tag
def deal_with_full_path(full_path, key, value):
    """
    处理当前路径，包含参数的
    @param value: 参数值
    @param key: 要修改的参数，也可以新增
    @param full_path: /search/?q=python&page=2
    @return: 得到新的路径
    """
    parsed_url = urlparse(full_path)
    query_params = parse_qs(parsed_url.query)
    # 去除参数key
    query_params[key] = [value]
    # 重新生成URL
    updated_query_string = urlencode(query_params, doseq=True)
    new_full_path = urlunparse((
        parsed_url.scheme,
        parsed_url.netloc,
        parsed_url.path,
        parsed_url.params,
        updated_query_string,
        parsed_url.fragment
    ))
    return new_full_path


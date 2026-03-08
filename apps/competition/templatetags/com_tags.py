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
from django.conf import settings
from django.utils.timezone import localtime
from django.utils import timezone
import markdown as md
from django.utils.html import mark_safe
from ..models import Competition, ScoreUser, ScoreTeam,Challenge,Tag,Submission

from django.core.serializers.json import DjangoJSONEncoder

# 导入自定义 Markdown 扩展
from utils.markdown_ext import IconExtension, AlertExtension, DelExtension
import markdown as md_lib
from markdown.extensions.codehilite import CodeHiliteExtension
from markdown.extensions.toc import TocExtension
from pygments.formatters.html import HtmlFormatter
from django.utils.text import slugify

import logging
logger = logging.getLogger('app.competition')
# 导入自定义 Markdown 扩展
from utils.markdown_ext import (
    DelExtension,
    IconExtension,
    AlertExtension,
    CodeItemExtension,
    CodeGroupExtension
)


# 自定义代码高亮格式化器
class CustomHtmlFormatter(HtmlFormatter):
    def __init__(self, lang_str='', **options):
        super().__init__(**options)
        self.lang_str = lang_str

    def _wrap_code(self, source):
        yield 0, f'<code class="{self.lang_str}">'
        yield from source
        yield 0, '</code>'
register = template.Library()


@register.simple_tag
def get_challenge_tags(challenge_uuid):
    cache_key = f'challenge_tags_{challenge_uuid}'
    tags = cache.get(cache_key)
    
    if tags is None:
        try:
            challenge = Challenge.objects.get(uuid=challenge_uuid)
            tags = list(challenge.tags.values_list('name', flat=True))
            cache.set(cache_key, tags, 3600)  # 缓存1小时
        except Challenge.DoesNotExist:
            tags = []
    
    return tags

@register.simple_tag
def get_user_ctf_stats(user, competition=None):
    """获取用户在当前比赛中的CTF统计数据（带缓存）
    
    Args:
        user: 用户对象
        competition: 比赛对象
    
    Returns:
        dict: 包含用户统计数据的字典
    """
    if not user or user.is_anonymous:
        return {
            'solved_count': 0,
            'user_points': 0,
            'team_score': 0,
            'team_rank': '-',
            'user_rank': '-',
            'is_team_competition': False
        }

    # 获取当前比赛
    if not competition:
        competition = Competition.objects.filter(
            start_time__lte=timezone.now(),
            end_time__gte=timezone.now()
        ).first()
        
    if not competition:
        return {
            'solved_count': 0,
            'user_points': 0,
            'team_score': 0,
            'team_rank': '-',
            'user_rank': '-',
            'is_team_competition': False
        }
        
    # 判断比赛类型
    is_team_competition = competition.competition_type == 'team'

    # 获取用户得分记录
    user_score = ScoreUser.objects.filter(
        user=user,
        competition=competition
    ).first()
    
    # 获取团队信息（如果是团队赛）
    team = None
    team_score = None
    if is_team_competition:
        # 仅在团队比赛中获取团队数据
        if user_score and user_score.team:
            team = user_score.team
        else:
            team = user.teams.filter(competition=competition).first()
    
    # 构建统计数据
    stats = {
        'is_team_competition': is_team_competition
    }
    
    if is_team_competition and team:
        # 团队赛：个人数据和团队数据都使用缓存
        
        # 1. 个人数据缓存
        user_cache_key = f'user_stats:{user.id}:{competition.id}'
        user_cached = cache.get(user_cache_key)
        
        if user_cached:
            user_data = json.loads(user_cached)
            stats['solved_count'] = user_data.get('solved_count', 0)
            stats['user_points'] = user_data.get('user_points', 0)
            stats['user_rank'] = user_data.get('user_rank', '-')
        else:
            # 查询个人数据并缓存
            stats['solved_count'] = user_score.solved_challenges.count() if user_score else 0
            stats['user_points'] = user_score.points if user_score else 0
            stats['user_rank'] = '-'
            
            user_data = {
                'solved_count': stats['solved_count'],
                'user_points': stats['user_points'],
                'user_rank': stats['user_rank']
            }
            # 缓存个人数据（60秒）
            cache.set(user_cache_key, json.dumps(user_data, cls=DjangoJSONEncoder), 60)
        
        # 2. 团队数据缓存（使用队伍级缓存，全队共享）
        team_cache_key = f'team_score_data:{team.id}:{competition.id}'
        team_cached = cache.get(team_cache_key)
        
        if team_cached:
            team_data = json.loads(team_cached)
            stats['team_score'] = team_data.get('team_score', 0)
            stats['team_rank'] = team_data.get('team_rank', '-')
        else:
            # 查询团队数据并缓存（全队共享）
            team_score = ScoreTeam.objects.filter(
                team=team,
                competition=competition
            ).first()
            
            team_data = {
                'team_score': team_score.score if team_score else 0,
                'team_rank': team_score.rank if team_score else '-'
            }
            stats['team_score'] = team_data['team_score']
            stats['team_rank'] = team_data['team_rank']
            
            # 缓存团队数据（60秒）
            cache.set(team_cache_key, json.dumps(team_data, cls=DjangoJSONEncoder), 60)
    else:
        # 个人赛：使用用户级缓存
        user_cache_key = f'user_stats:{user.id}:{competition.id}'
        user_cached = cache.get(user_cache_key)
        
        if user_cached:
            return json.loads(user_cached)
        
        stats.update({
            'solved_count': user_score.solved_challenges.count() if user_score else 0,
            'user_points': user_score.points if user_score else 0,
            'user_rank': user_score.rank if user_score else '-',
            'team_score': 0,
            'team_rank': '-'
        })
        
        # 缓存个人赛数据（60秒）
        cache.set(user_cache_key, json.dumps(stats, cls=DjangoJSONEncoder), 60)

    return stats

@register.simple_tag
def get_challenge_categories():
    """
    返回题目类型及数量，使用 Redis 缓存结果
    """
    cache_key = 'challenge_categories'
    
    # 尝试从缓存中获取数据
    cached_data = cache.get(cache_key)
    if cached_data:
        return json.loads(cached_data)
    
    # 如果缓存中没有数据，则从数据库中获取
    categories = Challenge.objects.values('category').annotate(count=Count('category')).order_by('category')
    result = {category['category']: category['count'] for category in categories}
    
    # 将数据存储到缓存中，设置过期时间为 5 分钟
    cache.set(cache_key, json.dumps(result), 3600)
    
    return result

@register.simple_tag
def get_all_challenge_tags():
    """
    返回所有被使用的挑战标签及其使用次数，使用 Redis 缓存结果
    不返回挑战数为 0 的标签
    """
    cache_key = 'all_challenge_tags'
    
    cached_data = cache.get(cache_key)
    if cached_data:
        return json.loads(cached_data)
    
    tags = Tag.objects.annotate(count=Count('challenge')).filter(count__gt=0).order_by('-count', 'name')
    result = {tag.name: tag.count for tag in tags}
    
    cache.set(cache_key, json.dumps(result), 3600)
    
    return result




@register.simple_tag
def get_challenge_solve_records(challenge, competition=None, limit=10):
    """获取题目解题记录
    
    Args:
        challenge: 题目对象
        competition: 当前比赛对象
        limit: 显示记录数量限制
        
    Returns:
        list: 解题记录列表，包含解题时间和解题者信息（团队或个人）
    """
    cache_key = f'challenge_{challenge.uuid}_competition_{competition.id if competition else "none"}_limit_{limit}'
    
    # 尝试从缓存中获取结果
    solve_records = cache.get(cache_key)
    
    if solve_records is None:
        if competition and competition.competition_type == 'team':
            # 团队赛 - 从ScoreTeam中获取解题记录
            solve_records = ScoreTeam.objects.filter(
                solved_challenges=challenge,
                competition=competition  # 使用当前比赛
            ).select_related('team').order_by('-time')[:limit]
            
            # 转换为统一格式
            solve_records = [{
                'user': {'user': record.team.name},
                'solved_at': record.time,
                'is_team': True
            } for record in solve_records]
            
        else:
            # 个人赛或非比赛题目
            solve_records = ScoreUser.objects.filter(
                solved_challenges=challenge,
                competition=competition  # 使用当前比赛
            ).select_related('user').order_by('-created_at')[:limit]
            
            # 转换为统一格式
            solve_records = [{
                'user': {'user': record.user.username},
                'solved_at': record.created_at,
                'is_team': False
            } for record in solve_records]
        
        # 将结果存入缓存
        cache.set(cache_key, solve_records, 60)  # 缓存1分钟
    
    return solve_records
    




@register.filter
def format_k(value):
    try:
        value = int(value)
        if value >= 1000:
            return f"{value / 1000:.1f}k"
        return str(value)
    except (ValueError, TypeError):
        return value
def make_markdown():
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
    md = make_markdown()
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



@register.inclusion_tag('public/tags/competition_countdown.html')
def show_competition_countdown(competition):
    """显示指定比赛的倒计时信息（使用Redis长期缓存）"""
    now = timezone.now()
    
    # 如果没有比赛，直接返回
    if not competition:
        return {
            'competition': None,
            'competition_status': None,
            'now': now,
        }
    
    # 缓存键：使用比赛ID
    cache_key = f'competition_time_data_{competition.id}'
    
    # 尝试从缓存获取比赛时间数据
    cached_data = cache.get(cache_key)
    
    if cached_data:
        # 如果有缓存数据，使用缓存的比赛信息
        competition_data = cached_data
    else:
        # 如果没有缓存，创建新的缓存数据
        competition_data = {
            'id': competition.id,
            'name': competition.title,
            'slug': competition.slug,
            'start_time': competition.start_time,
            'end_time': competition.end_time,
        }
        
        # 计算缓存时间：比赛结束时间 + 一些额外时间（例如1天）
        cache_timeout = None  # None表示永不过期
        if competition.end_time > now:
            # 如果比赛还没结束，设置缓存到比赛结束后24小时
            cache_timeout = int((competition.end_time - now).total_seconds()) + 86400
        
        # 将数据缓存到Redis
        cache.set(cache_key, competition_data, timeout=cache_timeout)
    
    # 计算比赛状态
    competition_status = None
    if now < competition_data['start_time']:
        competition_status = 'upcoming'
    elif competition_data['start_time'] <= now <= competition_data['end_time']:
        competition_status = 'ongoing'
    else:
        competition_status = 'ended'
    
    # 返回与模板兼容的数据结构
    return {
        'competition': competition,  # 保留原始比赛对象，模板中使用了它的start_time和end_time
        'competition_status': competition_status,
        'now': now,
    }








def serialize_user_data(user_data):
    """序列化用户数据，确保可以被JSON序列化"""
    return {
        'rank': user_data['rank'],
        'user': user_data['user'],
        'team': user_data['team'],
        'score': user_data['score'],
        'avatar': user_data['avatar'],
        'solved_count': user_data['solved_count']
    }



@register.simple_tag(takes_context=True)
def get_users_ranked_by_solves(context, competition=None, limit=10):
    """获取个人排行榜（带缓存，高亮当前用户和队友）"""
    if not competition:
        return []
    
    # 获取当前用户
    request = context.get('request')
    current_user = request.user if request and request.user.is_authenticated else None
    current_user_id = current_user.id if current_user else None
    
    # 获取当前用户的队伍和队友ID列表
    teammate_ids = set()
    if current_user:
        from ..models import Team
        try:
            # 找到当前用户的队伍
            team = Team.objects.filter(
                leader=current_user,
                competition=competition
            ).first()
            
            if not team:
                team = Team.objects.filter(
                    members=current_user,
                    competition=competition
                ).first()
            
            if team:
                # 获取所有队友的ID（包括队长和成员）
                teammate_ids.add(team.leader.id)
                teammate_ids.update(team.members.values_list('id', flat=True))
        except Exception:
            pass
    
    # 更新缓存键版本
    cache_key = f'user_ranking:{competition.id}:{limit}'
    
    cached_data = cache.get(cache_key)
    if cached_data:
        result = json.loads(cached_data)
    else:
        query = ScoreUser.objects.select_related('user').filter(
            competition=competition
        )
        
        users = query.order_by('-points', 'created_at')[:limit]
        
        result = []
        for index, score in enumerate(users, 1):
            # 安全获取队伍名称（可能是外键或字符串字段）
            team_name = None
            if hasattr(score, 'team') and score.team:
                team_name = score.team.name if hasattr(score.team, 'name') else str(score.team)
            elif hasattr(score, 'team_name'):
                team_name = score.team_name
            
            result.append({
                'rank': index,
                'user': score.user.username,
                'user_id': score.user.id,
                'team': team_name,
                'score': score.points,
                'avatar': score.user.avatar.url if hasattr(score.user, 'avatar') and score.user.avatar else None,
                'solved_count': score.solved_challenges.count()
            })
        
        cache.set(cache_key, json.dumps(result, cls=DjangoJSONEncoder), 3600)
    
    # 标记当前用户和队友
    for item in result:
        user_id = item.get('user_id')
        item['is_current_user'] = (current_user_id and user_id == current_user_id)
        item['is_teammate'] = (user_id in teammate_ids and user_id != current_user_id)
    
    return result

@register.simple_tag(takes_context=True)
def get_teams_ranked_by_solves(context, competition=None, limit=10):
    """获取队伍排行榜（带缓存，高亮当前队伍）"""
    if not competition:
        return []
    
    # 获取当前用户的队伍
    request = context.get('request')
    current_user = request.user if request and request.user.is_authenticated else None
    current_team_id = None
    
    if current_user:
        # 查询当前用户在该比赛中的队伍
        from ..models import Team
        try:
            # 方法1: 用户是队长的队伍
            team = Team.objects.filter(
                leader=current_user,
                competition=competition
            ).first()
            
            if not team:
                # 方法2: 用户是成员的队伍
                team = Team.objects.filter(
                    members=current_user,
                    competition=competition
                ).first()
            
            if team:
                current_team_id = team.id
        except Exception as e:
            # 记录错误日志
            import logging
            # 使用apps.competition作为logger名称，匹配settings.py中的配置
            logger = logging.getLogger('apps.competition')
            logger.error(f"获取用户队伍失败: {e}")
    
    # 更新缓存键版本
    cache_key = f'team_ranking:{competition.id}:{limit}'
    
    cached_data = cache.get(cache_key)
    if cached_data:
        result = json.loads(cached_data)
    else:
        query = ScoreTeam.objects.select_related('team').filter(
            competition=competition
        )
        
        teams = query.order_by('-score', 'time')[:limit]
        
        result = []
        for index, score in enumerate(teams, 1):
            result.append({
                'rank': index,
                'team_name': score.team.name,
                'team_id': score.team.id,
                'score': score.score,
                'solved_count': score.solved_challenges.count()
            })
        
        cache.set(cache_key, json.dumps(result, cls=DjangoJSONEncoder), 3600)
    
    # 标记当前用户的队伍（安全访问）
    for item in result:
        item['is_my_team'] = (current_team_id and item.get('team_id') == current_team_id)
    
    return result




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


@register.inclusion_tag('competition/tags/pagination.html')
def competition_pagination(page_obj, param_name, active_tab):
    """自定义分页标签，自动传递active_tab参数"""
    return {
        'page_obj': page_obj,
        'param_name': param_name,
        'active_tab': active_tab
    }


from public.models import MotivationalQuote



@register.inclusion_tag('competition/tags/motivational_quotes.html')
def show_motivational_quotes(num=10, cache_time=3600):
    """
    获取并显示随机励志语录，使用缓存优化性能
    
    :param num: 要获取的语录数量
    :param cache_time: 缓存时间（秒），默认1小时
    :return: 包含语录的字典
    """
    cache_key = 'motivational_quotes_all'
    quotes_list = cache.get(cache_key)
    
    if quotes_list is None:
        # 缓存未命中，从数据库获取所有激活的语录
        quotes_list = list(MotivationalQuote.objects.filter(is_active=True).values('content', 'author'))
        # 存入缓存
        cache.set(cache_key, quotes_list, cache_time)
    
    # 如果没有语录或语录数量少于请求数量，调整num值
    if not quotes_list:
        return {'quotes': []}
    
    # 随机选择num个语录
    if len(quotes_list) > num:
        selected_quotes = random.sample(quotes_list, num)
    else:
        selected_quotes = quotes_list
    
    return {'quotes': selected_quotes}



  # 修改为你的app名称






@register.simple_tag # 指定模板文件
def get_first_blood(challenge,competition):
    # 获取一血、二血、三血的用户或队伍和时间
    first_blood = Submission.objects.filter(
        challenge=challenge,
        status='correct',
        competition=competition,
    ).order_by('created_at').first()

   

    # 根据比赛类型，返回队伍名还是用户名
    if first_blood and first_blood.competition and first_blood.competition.competition_type == 'team':
        first_blood_info = first_blood.team.name if first_blood.team else '无队伍'
    else:
        first_blood_info = first_blood.user.username if first_blood else '暂无'

    

    return first_blood_info
        

@register.simple_tag # 指定模板文件
def get_second_blood(challenge,competition):

    second_blood = Submission.objects.filter(
        challenge=challenge,
        competition=competition,
        status='correct'
    ).order_by('created_at')[1] if Submission.objects.filter(
        challenge=challenge,
        competition=competition,
        status='correct'
    ).count() >= 2 else None

    # 根据比赛类型，返回队伍名还是用户名


    if second_blood and second_blood.competition and second_blood.competition.competition_type == 'team':
        second_blood_info = second_blood.team.name if second_blood.team else '无队伍'
    else:
        second_blood_info = second_blood.user.username if second_blood else '暂无'



    return second_blood_info

@register.simple_tag # 指定模板文件
def get_third_blood(challenge,competition):
    # 获取一血、二血、三血的用户或队伍和时间


    third_blood = Submission.objects.filter(
        challenge=challenge,
        competition=competition,
        status='correct'
    ).order_by('created_at')[2] if Submission.objects.filter(
        challenge=challenge,
        competition=competition,
        status='correct'
    ).count() >= 3 else None



    if third_blood and third_blood.competition and third_blood.competition.competition_type == 'team':
        third_blood_info = third_blood.team.name if third_blood.team else '无队伍'
    else:
        third_blood_info = third_blood.user.username if third_blood else '暂无'

    return third_blood_info


@register.simple_tag(takes_context=True)
def check_user_writeup_exists(context, competition):
    """
    检查用户是否已提交 Writeup
    个人赛：检查用户
    团队赛：检查队伍
    """
    from competition.models import Writeup
    
    request = context.get('request')
    if not request or not request.user.is_authenticated:
        return False
    
    user = request.user
    
    # 个人赛
    if competition.competition_type == 'individual':
        return Writeup.objects.filter(competition=competition, user=user).exists()
    
    # 团队赛
    else:
        # 获取用户的队伍
        registration = competition.registrations.filter(user=user, audit=True).first()
        if not registration or not registration.team_name:
            return False
        
        return Writeup.objects.filter(competition=competition, team=registration.team_name).exists()


@register.simple_tag(takes_context=True)
def get_user_competition_container(context, user, competition):
    """
    获取用户在当前比赛中的活跃容器信息
    
    Args:
        context: 模板上下文
        user: 当前用户
        competition: 当前比赛对象
    
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
        # 导入缓存管理器
        from competition.redis_cache import UserContainerCache
        
        # 直接从缓存获取用户的容器信息
        cached_data = UserContainerCache.get_user_container(user.id)
        
        
        if not cached_data:
            return None
        
        # 从缓存读取题目信息
        challenge_uuid = cached_data.get('challenge_uuid')
        challenge_title = cached_data.get('challenge_id', '未知题目')
        
        
        
        # 验证题目是否属于当前比赛
        try:
            challenge = Challenge.objects.get(uuid=challenge_uuid)
            # Challenge通过Competition的challenges多对多字段关联
            # 检查当前比赛是否包含这个题目
            if not competition.challenges.filter(uuid=challenge_uuid).exists():
                
                return None
        except Challenge.DoesNotExist:
            
            return None
        
        # 拼接题目 URL
        challenge_url = f"/ctf/{competition.slug}/{challenge_uuid}/"
        
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
        
        result = {
            'challenge_title': challenge_title,
            'challenge_url': challenge_url,
            'expires_at': expires_at,
            'is_current': is_current
        }
        
        return result
    
    except Exception as e:
        # 静默失败，不影响页面其他部分
        logger.error(f"获取用户比赛容器失败: {e}", exc_info=True)
        return None


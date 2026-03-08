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
from practice.models import (
    PC_Challenge,
    Tag,
)

from django.utils import timezone
import markdown as md
from competition.models import Competition, ScoreUser, ScoreTeam

from django.core.serializers.json import DjangoJSONEncoder
import markdown as md_lib
# 导入自定义 Markdown 扩展
from utils.markdown_ext import IconExtension, AlertExtension, DelExtension

register = template.Library()


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
        if user_score and user_score.team:
            team = user_score.team
        else:
            team = user.teams.filter(competition=competition).first()
        
        if team:
            team_score = ScoreTeam.objects.filter(
                team=team,
                competition=competition
            ).first()
    
    # 构建缓存键（团队赛使用队伍ID，个人赛使用用户ID）
    if is_team_competition and team:
        # 团队赛：用队伍ID作为主键，用户ID作为次键（因为个人数据不同）
        cache_key = f'user_ctf_stats:team:{team.id}:user:{user.id}:{competition.id}'
    else:
        # 个人赛：直接用用户ID
        cache_key = f'user_ctf_stats:individual:{user.id}:{competition.id}'
    
    cached_data = cache.get(cache_key)
    if cached_data:
        return json.loads(cached_data)

    # 构建统计数据
    stats = {
        'solved_count': user_score.solved_challenges.count() if user_score else 0,
        'user_points': user_score.points if user_score else 0,
        'is_team_competition': is_team_competition
    }
    
    if is_team_competition:
        # 团队赛：返回团队分数和排名
        stats.update({
            'team_score': team_score.score if team_score else 0,
            'team_rank': team_score.rank if team_score else '-',
            'user_rank': '-'
        })
    else:
        # 个人赛：返回个人排名
        stats.update({
            'team_score': 0,
            'team_rank': '-',
            'user_rank': user_score.rank if user_score else '-'
        })

    # 缓存数据（5分钟）
    cache.set(cache_key, json.dumps(stats, cls=DjangoJSONEncoder), 300)

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
def get_challenge_solve_records(challenge, limit=5):
    """获取题目解题记录
    
    Args:
        challenge: 题目对象
        limit: 显示记录数量限制
        
    Returns:
        list: 解题记录列表，包含解题时间和解题者信息（团队或个人）
    """
    cache_key = f'challenge_{challenge.uuid}_limit_{limit}'
    
    # 尝试从缓存中获取结果
    solve_records = cache.get(cache_key)
    
    if solve_records is None:
        # 获取题目关联的比赛
        competition = challenge.competition_set.first()
        
        if competition and competition.competition_type == 'team':
            # 团队赛 - 从ScoreTeam中获取解题记录
            solve_records = ScoreTeam.objects.filter(
                solved_challenges=challenge,
                competition=competition
            ).select_related('team').order_by('-time')[:limit]
            
            # 转换为统一格式
            solve_records = [{
                'user': {'user': record.team.name},  # 使用队伍名称
                'solved_at': record.time,
                'is_team': True
            } for record in solve_records]
            
        else:
            # 个人赛或非比赛题目 - 从ScoreUser中获取解题记录
            solve_records = ScoreUser.objects.filter(
                solved_challenges=challenge,
                competition=competition if competition else None
            ).select_related('user').order_by('-created_at')[:limit]
            
            # 转换为统一格式
            solve_records = [{
                'user': {'user': record.user.username},  # 使用用户名
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



@register.simple_tag
def get_users_ranked_by_solves(competition=None, limit=10):
    """获取个人排行榜（带缓存）"""
    if not competition:
        return []
    
    cache_key = f'user_ranking:{competition.id}:{limit}'
    
    cached_data = cache.get(cache_key)
    if cached_data:
        return json.loads(cached_data)
    
    query = ScoreUser.objects.select_related('user', 'team').filter(
        competition=competition
    )
    
    users = query.order_by('-points', 'created_at')[:limit]
    
    result = []
    for index, score in enumerate(users, 1):
        result.append({
            'rank': index,
            'user': score.user.username,
            'team': score.team.name if score.team else None,
            'score': score.points,
            'avatar': score.user.avatar.url if hasattr(score.user, 'avatar') and score.user.avatar else None,
            'solved_count': score.solved_challenges.count()
        })
    
    cache.set(cache_key, json.dumps(result, cls=DjangoJSONEncoder), 3600)
    return result

@register.simple_tag
def get_teams_ranked_by_solves(competition=None, limit=10):
    """获取队伍排行榜（带缓存）"""
    if not competition:
        return []
    
    cache_key = f'team_ranking:{competition.id}:{limit}'
    
    cached_data = cache.get(cache_key)
    if cached_data:
        return json.loads(cached_data)
    
    query = ScoreTeam.objects.select_related('team').filter(
        competition=competition
    )
    
    teams = query.order_by('-score', 'time')[:limit]
    
    result = []
    for index, score in enumerate(teams, 1):
        result.append({
            'rank': index,
            'team_name': score.team.name,
            'score': score.score,
            'solved_count': score.solved_challenges.count()
        })
    
    cache.set(cache_key, json.dumps(result, cls=DjangoJSONEncoder), 3600)
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

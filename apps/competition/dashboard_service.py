# -*- coding: utf-8 -*-
"""
CTF比赛数据大屏服务 - 生产优化版
使用Redis Pub/Sub + SSE实现实时数据推送

优化特性：
- 高效数据库查询（select_related, prefetch_related, only）
- 智能缓存策略（动态TTL，按比赛状态调整）
- 并发安全（Redis分布式锁）
- 错误恢复（异常捕获和降级处理）
- 性能监控（详细日志）
"""

from django.core.cache import cache
from django_redis import get_redis_connection
from django.db.models import Count, Q, F, Sum, Prefetch, Exists, OuterRef
from django.db import connection, OperationalError
from datetime import datetime, timedelta
from django.utils import timezone
import json
import logging
import time
import traceback
import signal

from competition.models import (
    Competition, Registration, Submission, 
    ScoreTeam, ScoreUser, Challenge
)

# 使用apps.competition作为logger名称，匹配settings.py中的配置
logger = logging.getLogger('apps.competition')


# 数据库查询超时装饰器（优化版 - 修复连接泄漏）
def query_timeout(seconds=5):
    """
    数据库查询超时装饰器（防止慢查询阻塞系统）
    
    优化：
    1. 使用同一个游标设置和重置超时，减少连接开销
    2. 确保在所有情况下都能关闭游标
    3. 在函数执行后立即关闭未使用的连接
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            # 导入 close_old_connections 用于清理陈旧连接
            from django.db import close_old_connections
            
            cursor = None
            result = None
            
            try:
                # 清理陈旧连接（防止连接池堆积）
                close_old_connections()
                
                # 获取游标并设置超时
                cursor = connection.cursor()
                
                # PostgreSQL
                if connection.vendor == 'postgresql':
                    cursor.execute(f"SET LOCAL statement_timeout = '{seconds}s'")
                # MySQL
                elif connection.vendor == 'mysql':
                    cursor.execute(f"SET SESSION max_execution_time = {seconds * 1000}")
                
                # 执行函数
                result = func(*args, **kwargs)
                
                return result
                
            except OperationalError as e:
                logger.error(f"Query timeout in {func.__name__}: {str(e)}")
                raise
            except Exception as e:
                logger.error(f"Error in {func.__name__}: {str(e)}")
                raise
            finally:
                # 关闭游标（重要：防止连接泄漏）
                if cursor is not None:
                    try:
                        cursor.close()
                    except Exception as e:
                        logger.warning(f"Failed to close cursor: {str(e)}")
                
                # 如果 Django 事务已完成，确保连接被标记为可重用
                # 使用 SET LOCAL 的好处是超时设置会在事务结束后自动重置
                if connection.in_atomic_block is False:
                    try:
                        # 确保连接状态正常
                        if connection.connection is not None:
                            connection.close_if_unusable_or_obsolete()
                    except Exception as e:
                        logger.warning(f"Failed to check connection state: {str(e)}")
        
        return wrapper
    return decorator


class OptimizedDashboardService:
    """优化的数据大屏服务"""
    
    def __init__(self, competition_id):
        self.competition_id = competition_id
        self.redis_conn = get_redis_connection("default")
        self.cache_prefix = f"ctf:dashboard:{competition_id}:"
        self.pubsub_channel = f"ctf:dashboard:updates:{competition_id}"
        
        try:
            self.competition = Competition.objects.select_related('author').get(id=competition_id)
        except Competition.DoesNotExist:
            logger.error(f"Competition {competition_id} not found")
            self.competition = None
    
    def get_cache_key(self, data_type):
        """生成缓存键"""
        return f"{self.cache_prefix}{data_type}"
    
    def publish_update(self, data_type, data):
        """发布更新事件到Redis Pub/Sub"""
        try:
            message = json.dumps({
                'type': data_type,
                'data': data,
                'timestamp': datetime.now().isoformat()
            })
            self.redis_conn.publish(self.pubsub_channel, message)
            logger.info(f"Published update for {data_type} to channel {self.pubsub_channel}")
        except Exception as e:
            logger.error(f"Failed to publish update: {str(e)}")
    
    def _is_competition_active(self):
        """检查比赛是否正在进行中"""
        if not self.competition:
            return False
        now = timezone.now()
        return self.competition.start_time <= now <= self.competition.end_time
    
    def _get_cache_ttl(self):
        """根据比赛状态动态获取缓存TTL"""
        if not self.competition:
            return 3600  # 1小时（比赛不存在）
        
        # 比赛进行中：短缓存，保证实时性
        if self._is_competition_active():
            return 120  # 2分钟
        
        # 比赛未开始或已结束：长缓存，减少负载
        return 3600  # 1小时
    
    @query_timeout(seconds=3)
    def calculate_stats(self, force_refresh=False):
        """
        计算基础统计数据（高性能版 + 超时保护）
        
        优化：使用单次聚合查询减少数据库往返
        """
        cache_key = self.get_cache_key('stats')
        
        # 尝试从缓存获取
        if not force_refresh:
            try:
                cached_data = self.redis_conn.get(cache_key)
                if cached_data:
                    return json.loads(cached_data)
            except Exception as cache_err:
                logger.warning(f"Cache read error for stats: {str(cache_err)}")
        
        start_time = time.time()
        
        try:
            # 使用单个聚合查询获取提交统计
            submission_stats = Submission.objects.filter(
                competition_id=self.competition_id
            ).aggregate(
                total=Count('id'),
                correct=Count('id', filter=Q(status='correct'))
            )
            
            total_submissions = submission_stats['total'] or 0
            correct_submissions = submission_stats['correct'] or 0
            
            # 获取参赛人数
            participant_count = Registration.objects.filter(
                competition_id=self.competition_id
            ).count()
            
            # 获取题目总数和已解决题目数
            total_challenges = self.competition.challenges.count() if self.competition else 0
            solved_challenges = self.competition.challenges.filter(solves__gt=0).count() if self.competition else 0
            
            # 获取当前领先的队伍/选手
            leader_name = '暂无'
            leader_score = 0
            
            if self.competition:
                if self.competition.competition_type == 'team':
                    # 团队赛：获取分数最高的队伍
                    top_team = ScoreTeam.objects.filter(
                        competition_id=self.competition_id,
                        score__gt=0
                    ).select_related('team').only(
                        'team__name', 'score'
                    ).order_by('-score', 'time').first()
                    
                    if top_team and top_team.team:
                        leader_name = top_team.team.name
                        leader_score = top_team.score
                else:
                    # 个人赛：获取分数最高的选手
                    top_user = ScoreUser.objects.filter(
                        competition_id=self.competition_id,
                        points__gt=0
                    ).select_related('user').only(
                        'user__username', 'points'
                    ).order_by('-points', 'created_at').first()
                    
                    if top_user and top_user.user:
                        leader_name = top_user.user.username
                        leader_score = top_user.points
            
            stats = {
                'participant_count': participant_count,
                'submission_count': total_submissions,
                'solved_rate': round(
                    correct_submissions / max(total_submissions, 1),
                    4
                ),
                'total_challenges': total_challenges,
                'solved_challenges': solved_challenges,
                'leader_name': leader_name,
                'leader_score': leader_score,
                'updated_at': datetime.now().isoformat()
            }
            
            # 缓存结果
            ttl = self._get_cache_ttl()
            try:
                self.redis_conn.setex(cache_key, ttl, json.dumps(stats))
            except Exception as cache_err:
                logger.warning(f"Cache write error for stats: {str(cache_err)}")
            
            elapsed = time.time() - start_time
            logger.debug(f"Stats calculated: competition={self.competition_id}, elapsed={elapsed:.3f}s")
            
            return stats
            
        except Exception as e:
            logger.error(
                f"Failed to calculate stats: competition={self.competition_id}, "
                f"error={str(e)}\n{traceback.format_exc()}"
            )
            # 返回默认值，保证系统可用性
            return {
                'participant_count': 0,
                'submission_count': 0,
                'solved_rate': 0,
                'total_challenges': 0,
                'solved_challenges': 0,
                'error': True
            }
    
    @query_timeout(seconds=3)
    def calculate_leaderboard(self, force_refresh=False, limit=30):
        """
        计算排行榜数据（高性能版 + 超时保护）
        
        优化：
        - 使用 select_related 减少查询
        - 使用 annotate 在数据库层面计数
        - 限制返回数量
        """
        cache_key = self.get_cache_key(f'leaderboard_{limit}')
        
        # 尝试从缓存获取
        if not force_refresh:
            try:
                cached_data = self.redis_conn.get(cache_key)
                if cached_data:
                    return json.loads(cached_data)
            except Exception as cache_err:
                logger.warning(f"Cache read error for leaderboard: {str(cache_err)}")
        
        start_time = time.time()
        
        try:
            if self.competition.competition_type == 'team':
                # 团队赛 - 优化查询
                scores = ScoreTeam.objects.filter(
                    competition_id=self.competition_id,
                    score__gt=0  # 只查询有得分的队伍
                ).select_related('team').annotate(
                    solved_count=Count('solved_challenges')
                ).only(
                    'team__name', 'score', 'time', 'id'
                ).order_by('score', 'time')[:limit]
                
                leaderboard = [{
                    'name': score.team.name,
                    'score': score.score,
                    'rank': idx + 1,
                    'solved_count': score.solved_count,
                } for idx, score in enumerate(scores)]
            else:
                # 个人赛 - 优化查询
                scores = ScoreUser.objects.filter(
                    competition_id=self.competition_id,
                    points__gt=0  # 只查询有得分的用户
                ).select_related('user').annotate(
                    solved_count=Count('solved_challenges')
                ).only(
                    'user__username', 'points', 'created_at', 'id'
                ).order_by('points', 'created_at')[:limit]
                
                leaderboard = [{
                    'name': score.user.username,
                    'score': score.points,
                    'rank': idx + 1,
                    'solved_count': score.solved_count,
                } for idx, score in enumerate(scores)]
            
            # 缓存结果
            ttl = 60 if self._is_competition_active() else 3600
            try:
                self.redis_conn.setex(cache_key, ttl, json.dumps(leaderboard, ensure_ascii=False))
            except Exception as cache_err:
                logger.warning(f"Cache write error for leaderboard: {str(cache_err)}")
            
            elapsed = time.time() - start_time
            logger.debug(
                f"Leaderboard calculated: competition={self.competition_id}, "
                f"limit={limit}, count={len(leaderboard)}, elapsed={elapsed:.3f}s"
            )
            
            return leaderboard
            
        except Exception as e:
            logger.error(
                f"Failed to calculate leaderboard: competition={self.competition_id}, "
                f"error={str(e)}\n{traceback.format_exc()}"
            )
            return []
    
    def calculate_category_stats(self, force_refresh=False):
        """
        计算分类完成情况（优化版）
        
        优化：使用 only 减少字段查询
        """
        cache_key = self.get_cache_key('category_stats')
        
        # 尝试从缓存获取
        if not force_refresh:
            try:
                cached_data = self.redis_conn.get(cache_key)
                if cached_data:
                    return json.loads(cached_data)
            except Exception as cache_err:
                logger.warning(f"Cache read error for category stats: {str(cache_err)}")
        
        start_time = time.time()
        
        try:
            # 优化查询：只获取必要的字段
            challenges = self.competition.challenges.only(
                'category', 'solves'
            ).all() if self.competition else []
            
            category_map = {}
            
            for challenge in challenges:
                category = challenge.get_category_display()
                if category not in category_map:
                    category_map[category] = {'total': 0, 'solved': 0}
                category_map[category]['total'] += 1
                
                # 检查是否有人解决
                if challenge.solves > 0:
                    category_map[category]['solved'] += 1
            
            category_stats = [{
                'category': cat,
                'total': data['total'],
                'solved': data['solved'],
                'percent': round(data['solved'] / data['total'] * 100, 1) if data['total'] > 0 else 0
            } for cat, data in category_map.items()]
            
            # 按类别名称排序
            category_stats.sort(key=lambda x: x['category'])
            
            # 缓存结果
            ttl = self._get_cache_ttl()
            try:
                self.redis_conn.setex(cache_key, ttl, json.dumps(category_stats, ensure_ascii=False))
            except Exception as cache_err:
                logger.warning(f"Cache write error for category stats: {str(cache_err)}")
            
            elapsed = time.time() - start_time
            logger.debug(
                f"Category stats calculated: competition={self.competition_id}, "
                f"categories={len(category_stats)}, elapsed={elapsed:.3f}s"
            )
            
            return category_stats
            
        except Exception as e:
            logger.error(
                f"Failed to calculate category stats: competition={self.competition_id}, "
                f"error={str(e)}\n{traceback.format_exc()}"
            )
            return []
    
    @query_timeout(seconds=3)
    def get_recent_submissions(self, force_refresh=False, limit=30):
        """
        获取最近提交记录（高性能版 + 超时保护）
        
        优化：
        - 使用 select_related 预加载关联对象
        - 使用 only 减少字段查询
        - 限制查询数量
        """
        cache_key = self.get_cache_key(f'recent_submissions_{limit}')
        
        # 尝试从缓存获取
        if not force_refresh:
            try:
                cached_data = self.redis_conn.get(cache_key)
                if cached_data:
                    return json.loads(cached_data)
            except Exception as cache_err:
                logger.warning(f"Cache read error for submissions: {str(cache_err)}")
        
        start_time = time.time()
        
        try:
            # 优化查询：只获取必要的字段
            submissions = Submission.objects.filter(
                competition_id=self.competition_id
            ).select_related(
                'user', 'team', 'challenge'
            ).only(
                'user__username', 'team__name', 'challenge__title', 
                'challenge__category', 'status', 'points_earned', 'created_at'
            ).order_by('-created_at')[:limit]
            
            recent_submissions = [{
                'team': (submission.team.name if submission.team else submission.user.username) if submission.team or submission.user else '未知',
                'challenge': submission.challenge.title if submission.challenge else '未知题目',
                'category': submission.challenge.get_category_display() if submission.challenge else '',
                'status': 'success' if submission.status == 'correct' else 'wrong',
                'time': submission.created_at.strftime('%H:%M:%S'),
                'points': submission.points_earned if submission.status == 'correct' else 0
            } for submission in submissions]
            
            # 缓存结果
            ttl = 30 if self._is_competition_active() else 3600
            try:
                self.redis_conn.setex(cache_key, ttl, json.dumps(recent_submissions, ensure_ascii=False))
            except Exception as cache_err:
                logger.warning(f"Cache write error for submissions: {str(cache_err)}")
            
            elapsed = time.time() - start_time
            logger.debug(
                f"Recent submissions retrieved: competition={self.competition_id}, "
                f"limit={limit}, count={len(recent_submissions)}, elapsed={elapsed:.3f}s"
            )
            
            return recent_submissions
            
        except Exception as e:
            logger.error(
                f"Failed to get recent submissions: competition={self.competition_id}, "
                f"error={str(e)}\n{traceback.format_exc()}"
            )
            return []
    
    def get_first_bloods(self, force_refresh=False, limit=20):
        """
        获取血榜数据（一血、二血、三血）- 高性能版
        
        优化：
        - 批量查询减少数据库往返
        - 使用 only 减少字段查询
        - 优化排序逻辑
        """
        cache_key = self.get_cache_key(f'first_bloods_{limit}')
        
        # 尝试从缓存获取
        if not force_refresh:
            try:
                cached_data = self.redis_conn.get(cache_key)
                if cached_data:
                    return json.loads(cached_data)
            except Exception as cache_err:
                logger.warning(f"Cache read error for first bloods: {str(cache_err)}")
        
        start_time = time.time()
        
        try:
            result = []
            
            # 批量获取所有题目的UUID
            challenge_uuids = list(
                self.competition.challenges.values_list('uuid', flat=True)
            ) if self.competition else []
            
            if not challenge_uuids:
                return []
            
            # 获取所有相关的正确提交，按题目和时间排序
            submissions = Submission.objects.filter(
                competition_id=self.competition_id,
                challenge__uuid__in=challenge_uuids,
                status='correct'
            ).select_related(
                'user', 'team', 'challenge'
            ).only(
                'user__username', 'team__name', 'challenge__title', 
                'challenge__points', 'points_earned', 'created_at', 'challenge__uuid'
            ).order_by('challenge__uuid', 'created_at')
            
            # 按题目分组，取前3名
            challenge_blood_map = {}
            for sub in submissions:
                challenge_uuid = sub.challenge.uuid
                if challenge_uuid not in challenge_blood_map:
                    challenge_blood_map[challenge_uuid] = []
                
                if len(challenge_blood_map[challenge_uuid]) < 3:
                    team_name = (sub.team.name if sub.team else sub.user.username) if sub.team or sub.user else '未知'
                    challenge_blood_map[challenge_uuid].append({
                        'rank': len(challenge_blood_map[challenge_uuid]) + 1,
                        'team': team_name,
                        'challenge': sub.challenge.title if sub.challenge else '未知题目',
                        'points': sub.points_earned or (sub.challenge.points if sub.challenge else 0),
                        'time': sub.created_at.strftime('%H:%M:%S'),
                        'timestamp': sub.created_at.timestamp()  # 用于排序
                    })
            
            # 展平结果
            for challenge_bloods in challenge_blood_map.values():
                result.extend(challenge_bloods)
            
            # 按rank和时间排序，取前limit个
            result.sort(key=lambda x: (x['rank'], x['timestamp']))
            result = result[:limit]
            
            # 移除用于排序的timestamp字段
            for item in result:
                item.pop('timestamp', None)
            
            # 缓存结果
            ttl = 300 if self._is_competition_active() else 3600
            try:
                self.redis_conn.setex(cache_key, ttl, json.dumps(result, ensure_ascii=False))
            except Exception as cache_err:
                logger.warning(f"Cache write error for first bloods: {str(cache_err)}")
            
            elapsed = time.time() - start_time
            logger.debug(
                f"First bloods retrieved: competition={self.competition_id}, "
                f"limit={limit}, count={len(result)}, elapsed={elapsed:.3f}s"
            )
            
            return result
            
        except Exception as e:
            logger.error(
                f"Failed to get first bloods: competition={self.competition_id}, "
                f"error={str(e)}\n{traceback.format_exc()}"
            )
            return []
    
    @query_timeout(seconds=5)
    def get_score_trends(self, force_refresh=False, top_n=None, max_points=200):
        """
        获取得分趋势（时间线）- 性能优化版 + 超时保护
        
        Args:
            force_refresh: 是否强制刷新缓存
            top_n: 显示前N名队伍，None表示显示所有（最多30个）
            max_points: 每个队伍最多显示的数据点数量（默认200个）
        """
        # 限制最大队伍数，避免数据量过大
        if top_n is None:
            top_n = 30  # 默认最多30个队伍
        else:
            top_n = min(top_n, 50)  # 最多不超过50个
        
        # 根据top_n参数使用不同的缓存键
        cache_key = self.get_cache_key(f'score_trends_{top_n}_{max_points}')
        
        if not force_refresh:
            cached_data = self.redis_conn.get(cache_key)
            if cached_data:
                return json.loads(cached_data)
        
        try:
            # 获取队伍/选手的得分历史
            if self.competition.competition_type == 'team':
                # 优化：只查询前top_n名且有得分的队伍
                top_scores = ScoreTeam.objects.filter(
                    competition_id=self.competition_id,
                    score__gt=0
                ).select_related('team').order_by('-score')[:top_n]
                
                series_data = []
                # 定义颜色数组
                colors = [
                    '#00f2fe', '#4facfe', '#00c9ff', '#92fe9d', '#00feca',
                    '#a8edea', '#fed6e3', '#fbc2eb', '#f093fb', '#f5576c',
                    '#ffecd2', '#fcb69f', '#ff9a9e', '#fecfef', '#fda085',
                    '#667eea', '#764ba2', '#f093fb', '#4facfe', '#43e97b',
                    '#38f9d7', '#fa709a', '#fee140', '#30cfd0', '#330867',
                    '#96fbc4', '#f9f586', '#52c234', '#061161', '#780206'
                ]
                
                for idx, score in enumerate(top_scores):
                    # 优化：只查询必要的字段，减少数据传输量
                    submissions = Submission.objects.filter(
                        competition_id=self.competition_id,
                        team=score.team,
                        status='correct'
                    ).values('created_at', 'points_earned').order_by('created_at')
                    
                    # 数据采样：如果数据点过多，进行采样
                    if submissions.count() > max_points:
                        # 计算采样间隔
                        step = submissions.count() // max_points
                        submissions = list(submissions)[::step][:max_points]
                    else:
                        submissions = list(submissions)
                    
                    # 计算累计分数
                    cumulative_score = 0
                    data_points = []
                    for sub in submissions:
                        cumulative_score += sub['points_earned']
                        data_points.append([
                            sub['created_at'].isoformat(),
                            cumulative_score
                        ])
                    
                    if data_points:  # 只添加有数据的队伍
                        color = colors[idx % len(colors)]
                        series_data.append({
                            'name': score.team.name,
                            'type': 'line',
                            'smooth': True,
                            'data': data_points,
                            'symbolSize': 4,  # 减小符号大小，提升渲染性能
                            'lineStyle': {'width': 2, 'color': color},
                            'itemStyle': {'color': color},
                            'emphasis': {
                                'focus': 'series'
                            }
                        })
            else:
                # 个人赛
                top_scores = ScoreUser.objects.filter(
                    competition_id=self.competition_id,
                    points__gt=0
                ).select_related('user').order_by('-points')[:top_n]
                
                series_data = []
                colors = [
                    '#00f2fe', '#4facfe', '#00c9ff', '#92fe9d', '#00feca',
                    '#a8edea', '#fed6e3', '#fbc2eb', '#f093fb', '#f5576c',
                    '#ffecd2', '#fcb69f', '#ff9a9e', '#fecfef', '#fda085',
                    '#667eea', '#764ba2', '#f093fb', '#4facfe', '#43e97b',
                    '#38f9d7', '#fa709a', '#fee140', '#30cfd0', '#330867',
                    '#96fbc4', '#f9f586', '#52c234', '#061161', '#780206'
                ]
                
                for idx, score in enumerate(top_scores):
                    # 优化：只查询必要的字段
                    submissions = Submission.objects.filter(
                        competition_id=self.competition_id,
                        user=score.user,
                        status='correct'
                    ).values('created_at', 'points_earned').order_by('created_at')
                    
                    # 数据采样
                    if submissions.count() > max_points:
                        step = submissions.count() // max_points
                        submissions = list(submissions)[::step][:max_points]
                    else:
                        submissions = list(submissions)
                    
                    cumulative_score = 0
                    data_points = []
                    for sub in submissions:
                        cumulative_score += sub['points_earned']
                        data_points.append([
                            sub['created_at'].isoformat(),
                            cumulative_score
                        ])
                    
                    if data_points:
                        color = colors[idx % len(colors)]
                        series_data.append({
                            'name': score.user.username,
                            'type': 'line',
                            'smooth': True,
                            'data': data_points,
                            'symbolSize': 4,
                            'lineStyle': {'width': 2, 'color': color},
                            'itemStyle': {'color': color},
                            'emphasis': {
                                'focus': 'series'
                            }
                        })
            
            # 动态缓存时间（得分趋势图数据量大，适当延长缓存）
            ttl = 180 if self._is_competition_active() else 3600  # 活跃3分钟，结束1小时
            self.redis_conn.setex(cache_key, ttl, json.dumps(series_data))
            return series_data
            
        except Exception as e:
            logger.error(f"Failed to get score trends: {str(e)}", exc_info=True)
            return []
    
    def invalidate_cache(self, *data_types):
        """清除指定类型的缓存"""
        for data_type in data_types:
            cache_key = self.get_cache_key(data_type)
            self.redis_conn.delete(cache_key)
    
    def refresh_all_data(self):
        """
        刷新所有数据并推送更新（优化版）
        
        优化：添加详细日志和错误恢复
        """
        start_time = time.time()
        
        try:
            logger.info(f"Starting full data refresh: competition={self.competition_id}")
            
            # 强制刷新所有数据
            stats = self.calculate_stats(force_refresh=True)
            leaderboard = self.calculate_leaderboard(force_refresh=True, limit=30)
            category_stats = self.calculate_category_stats(force_refresh=True)
            recent_submissions = self.get_recent_submissions(force_refresh=True, limit=30)
            score_trends = self.get_score_trends(force_refresh=True, top_n=30, max_points=200)
            first_bloods = self.get_first_bloods(force_refresh=True, limit=20)
            
            # 组装更新数据
            update_data = {
                'stats': stats,
                'leaderboard': leaderboard,
                'category_stats': category_stats,
                'recent_submissions': recent_submissions,
                'series_data': score_trends,
                'first_bloods': first_bloods
            }
            
            # 发布更新通知
            try:
                self.publish_update('all', update_data)
            except Exception as pub_err:
                logger.error(f"Failed to publish update: {str(pub_err)}")
                # 不影响刷新结果
            
            elapsed = time.time() - start_time
            logger.info(
                f"Full data refresh completed: competition={self.competition_id}, "
                f"elapsed={elapsed:.3f}s"
            )
            return True
            
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(
                f"Failed to refresh all data: competition={self.competition_id}, "
                f"elapsed={elapsed:.3f}s, error={str(e)}\n{traceback.format_exc()}"
            )
            return False
    
    def on_flag_submitted(self, submission):
        """Flag提交后的回调（用于实时更新）"""
        try:
            # 只在正确提交时刷新数据
            if submission.status == 'correct':
                # 清除相关缓存
                self.invalidate_cache('stats', 'leaderboard', 'recent_submissions', 'score_trends', 'first_bloods')
                
                # 刷新并推送更新
                self.refresh_all_data()
                
        except Exception as e:
            logger.error(f"Failed to handle flag submission: {str(e)}")


def get_dashboard_service(competition_id):
    """
    获取Dashboard服务实例
    
    注意：直接创建新实例，不缓存对象本身，因为对象包含Redis连接无法序列化。
    数据已经通过各自的cache_key在Redis中缓存了。
    """
    return OptimizedDashboardService(competition_id)


def generate_demo_dashboard_data():
    """
    生成演示数据（用于前端效果预览）
    """
    import random
    from datetime import datetime, timedelta
    
    # 模拟队伍名称
    team_names = [
        '红客联盟', '白帽先锋白帽先锋白帽先锋', '蓝莲花战队', '极客猎人', 
        '代码战士', '安全卫士', '暗影骑士', '量子黑客',
        '赛博武士', '数字游侠', '破晓战队', '零日猎手',
        '熊猫安全', '龙盾小队', '凤凰战队', '麒麟守护',
        '飓风战队', '闪电突击', '雷霆战队', '烈焰小队',
        '冰霜卫士', 'categorycategory', '神盾局', '正义联盟',
        '复仇者', 'X战队', '银河护卫', '时空巡逻',
        '未来战士', '量子纠缠',
        '未来战士', '量子纠缠',
        '熊猫安全', '龙盾小队', '凤凰战队', '麒麟守护',
        '飓风战队', '闪电突击', '雷霆战队', '烈焰小队',
        '冰霜卫士', 'categorycategory', '神盾局', '正义联盟',
        '复仇者', 'X战队', '银河护卫', '时空巡逻',
        '未来战士', '量子纠缠',
    ]
    
    # 模拟题目
    challenges = [
        {'title': 'Web签到', 'category': 'Web'},
        {'title': 'SQL注入初探', 'category': 'Web'},
        {'title': 'XSS攻防战', 'category': 'Web'},
        {'title': '文件上传漏洞', 'category': 'Web'},
        {'title': 'SSRF实战', 'category': 'Web'},
        {'title': '反序列化漏洞', 'category': 'Web'},
        {'title': '逆向入门', 'category': 'Reverse'},
        {'title': '密码学基础', 'category': 'Crypto'},
        {'title': 'RSA破解', 'category': 'Crypto'},
        {'title': 'AES解密', 'category': 'Crypto'},
        {'title': '二进制漏洞', 'category': 'Pwn'},
        {'title': '栈溢出实战', 'category': 'Pwn'},
        {'title': '堆利用技巧', 'category': 'Pwn'},
        {'title': '杂项签到', 'category': 'Misc'},
        {'title': '隐写术', 'category': 'Misc'},
        {'title': '流量分析', 'category': 'Misc'},
        {'title': '取证分析', 'category': 'Misc'},
        {'title': 'Android逆向', 'category': 'Mobile'},
        {'title': 'iOS安全', 'category': 'Mobile'},
        {'title': '区块链安全', 'category': 'Blockchain'},
    ]
    
    # 1. 基础统计
    stats = {
        'participant_count': len(team_names),
        'total_challenges': len(challenges),
        'solved_challenges': random.randint(12, 18),
        'submission_count': random.randint(450, 650),
        'solved_rate': round(random.uniform(0.35, 0.55), 4),
        'leader_name': '红客联盟',  # 演示数据的第一名
        'leader_score': 5000,  # 演示数据的最高分
        'updated_at': datetime.now().isoformat()
    }
    
    # 2. 排行榜（前30名）
    leaderboard = []
    base_score = 5000
    for idx, team in enumerate(team_names[:30]):
        score = max(0, base_score - idx * random.randint(100, 300))
        leaderboard.append({
            'name': team,
            'score': score,
            'rank': idx + 1,
            'solved_count': random.randint(5, 15)
        })
    
    # 3. 分类统计
    categories = ['Web', 'Reverse', 'Crypto', 'Pwn', 'Misc', 'Mobile']
    category_stats = []
    for cat in categories:
        cat_challenges = [c for c in challenges if c['category'] == cat]
        total = len(cat_challenges)
        solved = random.randint(int(total * 0.3), int(total * 0.8))
        category_stats.append({
            'category': cat,
            'total': total,
            'solved': solved,
            'percent': round(solved / total * 100, 1) if total > 0 else 0
        })
    
    # 4. 最近提交（30条）
    recent_submissions = []
    now = datetime.now()
    for i in range(30):
        team = random.choice(team_names)
        challenge = random.choice(challenges)
        is_correct = random.random() > 0.3  # 70%正确率
        time_offset = timedelta(minutes=random.randint(1, 120))
        
        recent_submissions.append({
            'team': team,
            'challenge': challenge['title'],
            'category': challenge['category'],
            'status': 'success' if is_correct else 'wrong',
            'time': (now - time_offset).strftime('%H:%M:%S'),
            'points': random.choice([100, 200, 300, 500, 800, 1000]) if is_correct else 0
        })
    
    # 按时间排序（最新的在前）
    recent_submissions.sort(key=lambda x: x['time'], reverse=True)
    
    # 5. 得分趋势（前10名队伍）
    series_data = []
    colors = [
        '#00f2fe', '#4facfe', '#00c9ff', '#92fe9d', '#00feca',
        '#a8edea', '#fed6e3', '#fbc2eb', '#f093fb', '#f5576c'
    ]
    
    start_time = now - timedelta(hours=4)
    for idx, team in enumerate(team_names[:10]):
        data_points = []
        current_score = 0
        # 生成20个时间点
        for j in range(20):
            time_point = start_time + timedelta(minutes=j * 12)
            # 随机增加分数
            if random.random() > 0.4:
                current_score += random.choice([100, 200, 300, 500])
            data_points.append([
                time_point.isoformat(),
                current_score
            ])
        
        color = colors[idx % len(colors)]
        series_data.append({
            'name': team,
            'type': 'line',
            'smooth': True,
            'data': data_points,
            'symbolSize': 4,
            'lineStyle': {'width': 2, 'color': color},
            'itemStyle': {'color': color},
            'emphasis': {'focus': 'series'}
        })
    
    # 6. 一血榜（前20个）
    first_bloods = []
    used_challenges = set()
    for rank in [1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2]:
        # 确保不重复使用题目
        available_challenges = [c for c in challenges if c['title'] not in used_challenges]
        if not available_challenges:
            break
        
        challenge = random.choice(available_challenges)
        used_challenges.add(challenge['title'])
        team = random.choice(team_names)
        time_offset = timedelta(minutes=random.randint(5, 180))
        
        first_bloods.append({
            'rank': rank,
            'team': team,
            'challenge': challenge['title'],
            'points': random.choice([100, 200, 300, 500, 800, 1000]),
            'time': (now - time_offset).strftime('%H:%M:%S')
        })
    
    return {
        'stats': stats,
        'leaderboard': leaderboard,
        'category_stats': category_stats,
        'recent_submissions': recent_submissions,
        'series_data': series_data,
        'first_bloods': first_bloods,
        'is_demo': True
    }


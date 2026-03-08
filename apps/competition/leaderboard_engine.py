"""
综合排行榜数据分析引擎
用于比赛结束后的数据分析、缓存和持久化

架构特点：
1. 多层缓存：完整数据 + 分页数据 + 统计数据
2. 数据库持久化：防止缓存失效
3. 异步计算：不阻塞主线程
4. 高并发优化：支持大量用户同时访问
"""

import logging
import json
from decimal import Decimal
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.core.paginator import Paginator

logger = logging.getLogger('apps.competition')


class CombinedLeaderboardEngine:
    """综合排行榜数据分析引擎"""
    
    # 缓存键前缀
    CACHE_PREFIX = 'combined_lb_engine'
    
    # 缓存时间配置（秒）
    CACHE_TIMEOUT_RUNNING = 120      # 比赛进行中：2分钟
    CACHE_TIMEOUT_ENDED = 86400      # 比赛结束后：24小时
    CACHE_TIMEOUT_STATS = 3600       # 统计数据：1小时
    
    def __init__(self, competition):
        """
        初始化分析引擎
        
        Args:
            competition: Competition对象
        """
        self.competition = competition
        self.competition_id = competition.id
        self.quiz = competition.related_quiz
        
        if not self.quiz:
            raise ValueError(f'比赛 {competition.slug} 未关联知识竞赛')
    
    def analyze_and_cache_all(self, force=False):
        """
        分析并缓存所有数据（主入口）
        
        Args:
            force: 是否强制重新计算
            
        Returns:
            dict: 分析结果
        """
        logger.info(f'[分析引擎] 开始分析比赛数据: competition_id={self.competition_id}, force={force}')
        
        try:
            # 1. 检查缓存（非强制模式下）
            if not force:
                cached = self._get_cached_leaderboard()
                if cached:
                    logger.info(f'[分析引擎] 使用缓存数据: competition_id={self.competition_id}')
                    return {
                        'success': True,
                        'source': 'cache',
                        'message': '使用缓存数据',
                        'data': cached
                    }
            
            # 2. 计算综合排行榜（使用优化版）
            from competition.utils_optimized import CombinedLeaderboardCalculator
            calculator = CombinedLeaderboardCalculator(self.competition, self.quiz)
            
            # 使用带分布式锁的安全方法
            result = calculator.calculate_leaderboard_with_lock(limit=None)
            
            if not result.get('success'):
                logger.error(f'[分析引擎] 计算失败: {result.get("message")}')
                return result
            
            leaderboard_data = result['leaderboard']
            total_count = result['total_count']
            
            logger.info(f'[分析引擎] 计算完成: {total_count} 条记录')
            
            # 3. 生成统计数据
            stats = self._generate_statistics(leaderboard_data)
            
            # 4. 缓存完整排行榜
            self._cache_full_leaderboard(leaderboard_data, stats)
            
            # 5. 缓存分页数据（预生成常用分页）
            self._cache_paginated_data(leaderboard_data)
            
            # 6. 缓存用户/团队快速查询
            self._cache_quick_lookup(leaderboard_data)
            
            logger.info(f'[分析引擎] 缓存完成: competition_id={self.competition_id}')
            
            return {
                'success': True,
                'source': 'calculated',
                'message': '数据分析完成',
                'total_count': total_count,
                'stats': stats
            }
            
        except Exception as e:
            logger.error(f'[分析引擎] 分析失败: competition_id={self.competition_id}, error={e}', exc_info=True)
            return {
                'success': False,
                'message': f'数据分析失败: {str(e)}'
            }
    
    def get_leaderboard_page(self, page=1, page_size=20):
        """
        获取分页排行榜数据（优先从缓存读取）
        
        Args:
            page: 页码
            page_size: 每页数量
            
        Returns:
            dict: 分页数据
        """
        # 1. 尝试从缓存获取该页数据
        cache_key = self._get_page_cache_key(page, page_size)
        cached_page = cache.get(cache_key)
        
        if cached_page:
            logger.debug(f'[分析引擎] 命中分页缓存: page={page}, size={page_size}')
            return {
                'success': True,
                'source': 'cache',
                'data': cached_page
            }
        
        # 2. 从数据库读取
        from competition.models import CombinedLeaderboard
        
        if self.competition.competition_type == 'individual':
            queryset = CombinedLeaderboard.objects.filter(
                competition=self.competition,
                user__isnull=False
            ).select_related('user').order_by('rank')
        else:
            queryset = CombinedLeaderboard.objects.filter(
                competition=self.competition,
                team__isnull=False
            ).select_related('team', 'team__leader').order_by('rank')
        
        paginator = Paginator(queryset, page_size)
        
        try:
            page_obj = paginator.page(page)
        except:
            page_obj = paginator.page(1)
        
        # 3. 转换为字典格式
        leaderboard_data = []
        for item in page_obj:
            leaderboard_data.append(self._convert_record_to_dict(item))
        
        result = {
            'leaderboard': leaderboard_data,
            'pagination': {
                'page': page_obj.number,
                'page_size': page_size,
                'total_count': paginator.count,
                'total_pages': paginator.num_pages,
                'has_next': page_obj.has_next(),
                'has_previous': page_obj.has_previous()
            }
        }
        
        # 4. 回写缓存
        cache_timeout = self._get_cache_timeout()
        cache.set(cache_key, result, cache_timeout)
        
        logger.debug(f'[分析引擎] 数据库读取并缓存: page={page}, size={page_size}')
        
        return {
            'success': True,
            'source': 'database',
            'data': result
        }
    
    def get_user_or_team_score(self, user=None, team=None):
        """
        获取用户或团队的成绩（优先从缓存读取）
        
        Args:
            user: User对象
            team: Team对象
            
        Returns:
            dict: 成绩数据
        """
        if user:
            cache_key = f'{self.CACHE_PREFIX}:user_score:{self.competition_id}:{user.id}'
        elif team:
            cache_key = f'{self.CACHE_PREFIX}:team_score:{self.competition_id}:{team.id}'
        else:
            return None
        
        # 1. 尝试从缓存获取
        cached_score = cache.get(cache_key)
        if cached_score:
            return cached_score
        
        # 2. 从数据库读取
        from competition.models import CombinedLeaderboard
        
        if user:
            record = CombinedLeaderboard.objects.filter(
                competition=self.competition,
                user=user
            ).first()
        else:
            record = CombinedLeaderboard.objects.filter(
                competition=self.competition,
                team=team
            ).first()
        
        if not record:
            return None
        
        score_data = self._convert_record_to_dict(record)
        
        # 3. 回写缓存
        cache_timeout = self._get_cache_timeout()
        cache.set(cache_key, score_data, cache_timeout)
        
        return score_data
    
    def clear_all_cache(self):
        """清除所有缓存"""
        logger.info(f'[分析引擎] 清除所有缓存: competition_id={self.competition_id}')
        
        # 清除完整排行榜缓存
        cache.delete(self._get_full_cache_key())
        
        # 清除统计数据缓存
        cache.delete(self._get_stats_cache_key())
        
        # 清除旧的工具类缓存
        from competition.utils_optimized import CombinedLeaderboardCalculator
        CombinedLeaderboardCalculator.clear_cache(self.competition_id)
        
        # 清除分页缓存（常用分页）
        for page_size in [10, 20, 50, 100]:
            for page in range(1, 11):  # 清除前10页
                cache.delete(self._get_page_cache_key(page, page_size))
    
    # ==================== 私有方法 ====================
    
    def _get_cached_leaderboard(self):
        """从缓存获取完整排行榜"""
        cache_key = self._get_full_cache_key()
        return cache.get(cache_key)
    
    def _cache_full_leaderboard(self, leaderboard_data, stats):
        """缓存完整排行榜"""
        cache_key = self._get_full_cache_key()
        cache_timeout = self._get_cache_timeout()
        
        cache_data = {
            'leaderboard': leaderboard_data,
            'stats': stats,
            'total_count': len(leaderboard_data),
            'cached_at': timezone.now().isoformat()
        }
        
        cache.set(cache_key, cache_data, cache_timeout)
        logger.info(f'[分析引擎] 缓存完整排行榜: {len(leaderboard_data)} 条, 过期时间: {cache_timeout}秒')
    
    def _cache_paginated_data(self, leaderboard_data):
        """缓存分页数据（预生成常用分页）"""
        cache_timeout = self._get_cache_timeout()
        
        # 常用分页配置
        page_sizes = [10, 20, 50, 100]
        
        for page_size in page_sizes:
            total_pages = (len(leaderboard_data) + page_size - 1) // page_size
            
            # 只缓存前5页（最常访问）
            for page in range(1, min(6, total_pages + 1)):
                start_idx = (page - 1) * page_size
                end_idx = start_idx + page_size
                page_data = leaderboard_data[start_idx:end_idx]
                
                cache_key = self._get_page_cache_key(page, page_size)
                cache_value = {
                    'leaderboard': page_data,
                    'pagination': {
                        'page': page,
                        'page_size': page_size,
                        'total_count': len(leaderboard_data),
                        'total_pages': total_pages,
                        'has_next': page < total_pages,
                        'has_previous': page > 1
                    }
                }
                
                cache.set(cache_key, cache_value, cache_timeout)
        
        logger.info(f'[分析引擎] 缓存分页数据完成: {len(page_sizes)} 种分页大小')
    
    def _cache_quick_lookup(self, leaderboard_data):
        """缓存用户/团队快速查询"""
        cache_timeout = self._get_cache_timeout()
        
        for item in leaderboard_data:
            if self.competition.competition_type == 'individual':
                cache_key = f'{self.CACHE_PREFIX}:user_score:{self.competition_id}:{item["user_id"]}'
            else:
                cache_key = f'{self.CACHE_PREFIX}:team_score:{self.competition_id}:{item["team_id"]}'
            
            cache.set(cache_key, item, cache_timeout)
        
        logger.info(f'[分析引擎] 缓存快速查询完成: {len(leaderboard_data)} 条')
    
    def _generate_statistics(self, leaderboard_data):
        """生成统计数据"""
        if not leaderboard_data:
            return {}
        
        ctf_scores = [item['ctf_score'] for item in leaderboard_data]
        quiz_scores = [item['quiz_score'] for item in leaderboard_data]
        combined_scores = [item['combined_score'] for item in leaderboard_data]
        
        stats = {
            'total_count': len(leaderboard_data),
            'ctf': {
                'max': max(ctf_scores),
                'min': min(ctf_scores),
                'avg': sum(ctf_scores) / len(ctf_scores),
            },
            'quiz': {
                'max': max(quiz_scores),
                'min': min(quiz_scores),
                'avg': sum(quiz_scores) / len(quiz_scores),
            },
            'combined': {
                'max': max(combined_scores),
                'min': min(combined_scores),
                'avg': sum(combined_scores) / len(combined_scores),
            },
            'generated_at': timezone.now().isoformat()
        }
        
        # 缓存统计数据
        cache_key = self._get_stats_cache_key()
        cache.set(cache_key, stats, self.CACHE_TIMEOUT_STATS)
        
        return stats
    
    def _convert_record_to_dict(self, record):
        """将数据库记录转换为字典"""
        if self.competition.competition_type == 'individual':
            return {
                'rank': record.rank,
                'user_id': record.user.id,
                'username': record.user.username,
                'real_name': getattr(record.user, 'real_name', record.user.username),
                'ctf_score': float(record.ctf_score),
                'ctf_rank': record.ctf_rank,
                'quiz_score': float(record.quiz_score),
                'combined_score': float(record.combined_score),
            }
        else:
            return {
                'rank': record.rank,
                'team_id': record.team.id,
                'team_name': record.team.name,
                'team_code': record.team.team_code,
                'leader': record.team.leader.username,
                'member_count': record.team.members.count(),
                'ctf_score': float(record.ctf_score),
                'ctf_rank': record.ctf_rank,
                'quiz_score': float(record.quiz_score),
                'combined_score': float(record.combined_score),
            }
    
    def _get_cache_timeout(self):
        """根据比赛状态获取缓存超时时间"""
        if timezone.now() > self.competition.end_time:
            return self.CACHE_TIMEOUT_ENDED  # 比赛结束：24小时
        else:
            return self.CACHE_TIMEOUT_RUNNING  # 比赛进行中：2分钟
    
    def _get_full_cache_key(self):
        """获取完整排行榜缓存键"""
        return f'{self.CACHE_PREFIX}:full:{self.competition_id}'
    
    def _get_page_cache_key(self, page, page_size):
        """获取分页缓存键"""
        return f'{self.CACHE_PREFIX}:page:{self.competition_id}:{page}:{page_size}'
    
    def _get_stats_cache_key(self):
        """获取统计数据缓存键"""
        return f'{self.CACHE_PREFIX}:stats:{self.competition_id}'


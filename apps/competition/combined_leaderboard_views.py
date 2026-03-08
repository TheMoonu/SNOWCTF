"""
综合排行榜API视图 - 高并发优化版本
改进了并发控制、错误处理和性能
"""

from django.http import JsonResponse
from django.views import View
from django.shortcuts import get_object_or_404
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
from django.core.cache import cache
from django.utils import timezone
from competition.models import Competition, CombinedLeaderboard, LeaderboardCalculationTask
from competition.utils_optimized import CombinedLeaderboardCalculator
from competition.distributed_lock import get_leaderboard_lock
import logging
import time

logger = logging.getLogger('apps.competition')


class CombinedLeaderboardView(View):
    """综合排行榜API视图 - 高并发优化版"""
    
    MAX_PAGE_SIZE = 500
    DEFAULT_PAGE_SIZE = 100
    RETRY_WAIT_TIME = 1.0  # 获取锁失败时的等待时间（秒）
    MAX_RETRIES = 3  # 最大重试次数
    
    def get(self, request, competition_slug):
        """
        获取综合排行榜
        
        URL参数:
            competition_slug: 竞赛slug
        
        查询参数:
            limit: 限制返回数量（已弃用，使用分页）
            page: 页码，默认1
            page_size: 每页数量，默认100，最大500
            force_refresh: 是否强制刷新（仅管理员），1为是
            include_my_rank: 是否包含我的排名，1为是（默认）
        
        返回:
            JSON格式的排行榜数据
        """
        try:
            # 获取竞赛
            competition = get_object_or_404(Competition, slug=competition_slug)
            
            # 检查是否关联了知识竞赛
            if not competition.related_quiz:
                return JsonResponse({
                    'success': False,
                    'message': '该竞赛未关联知识竞赛，无法生成综合排行榜'
                }, status=400)
            
            # 权限检查
            can_view, can_force_refresh = self._check_permissions(request, competition)
            
            if not can_view:
                return JsonResponse({
                    'success': False,
                    'message': f'综合排行榜将在比赛结束后开放查看（结束时间：{competition.end_time.strftime("%Y-%m-%d %H:%M")}）'
                }, status=403)
            
            # 获取查询参数
            page = int(request.GET.get('page', 1))
            page_size = min(
                int(request.GET.get('page_size', self.DEFAULT_PAGE_SIZE)),
                self.MAX_PAGE_SIZE
            )
            force_refresh = request.GET.get('force_refresh', '0') == '1'
            include_my_rank = request.GET.get('include_my_rank', '1') == '1'
            
            # 检查是否允许强制刷新
            if force_refresh and not can_force_refresh:
                return JsonResponse({
                    'success': False,
                    'message': '没有权限强制刷新排行榜'
                }, status=403)
            
            # 确保排行榜数据存在
            result = self._ensure_leaderboard_exists(
                competition,
                force_refresh=force_refresh and can_force_refresh
            )
            
            if not result['success']:
                return JsonResponse(result, status=result.get('status_code', 500))
            
            # 从数据库获取分页数据
            leaderboard_data = self._get_paginated_leaderboard(
                competition,
                page,
                page_size
            )
            
            # 获取当前用户/队伍的排名
            my_rank_data = None
            if include_my_rank and request.user.is_authenticated:
                my_rank_data = self._get_my_rank(request.user, competition)
            
            # 构建响应
            response_data = {
                'success': True,
                'competition_id': competition.id,
                'competition_slug': competition.slug,
                'competition_type': competition.competition_type,
                'is_ended': timezone.now() > competition.end_time,
                'leaderboard': leaderboard_data['results'],
                'pagination': leaderboard_data['pagination'],
            }
            
            if my_rank_data:
                response_data['my_rank'] = my_rank_data
            
            # 添加计算任务信息（可选）
            if can_force_refresh:
                latest_task = LeaderboardCalculationTask.objects.filter(
                    competition=competition
                ).order_by('-created_at').first()
                
                if latest_task:
                    response_data['calculation_info'] = {
                        'status': latest_task.status,
                        'progress': latest_task.progress_percentage,
                        'last_updated': latest_task.updated_at.isoformat()
                    }
            
            return JsonResponse(response_data)
            
        except ValueError as e:
            logger.warning(f'参数错误: {e}')
            return JsonResponse({
                'success': False,
                'message': '参数格式错误'
            }, status=400)
        except Exception as e:
            logger.error(f'获取综合排行榜失败: {e}', exc_info=True)
            return JsonResponse({
                'success': False,
                'message': '服务器内部错误，请稍后重试'
            }, status=500)
    
    def _check_permissions(self, request, competition):
        """
        检查用户权限
        
        策略：
        1. 比赛结束后，所有登录用户都可以查看
        2. 第一个访问的用户会自动触发计算
        3. 其他并发访问的用户等待计算完成
        4. 管理员可以强制重新计算
        
        Returns:
            tuple: (can_view, can_force_refresh)
        """
        can_view = False
        can_force_refresh = False
        
        # 只有比赛结束后才能查看综合排行榜
        if timezone.now() > competition.end_time:
            if request.user.is_authenticated:
                can_view = True
                
                # 管理员或比赛创建者可以强制刷新
                if request.user.is_staff or request.user.is_superuser or competition.author == request.user:
                    can_force_refresh = True
        
        return can_view, can_force_refresh
    
    def _ensure_leaderboard_exists(self, competition, force_refresh=False):
        """
        确保排行榜数据存在（如果不存在则自动触发计算）
        
        工作流程：
        1. 检查数据库是否有数据
        2. 如果没有数据或需要强制刷新，触发计算
        3. 使用分布式锁确保只有一个进程在计算
        4. 其他并发请求等待计算完成
        5. 计算结果先存 Redis，再写数据库
        
        Args:
            competition: 竞赛对象
            force_refresh: 是否强制刷新
        
        Returns:
            dict: 操作结果
        """
        # 1. 先检查 Redis 缓存
        cache_key = f'combined_leaderboard_{competition.competition_type}_{competition.id}_all'
        cached_data = cache.get(cache_key)
        
        if cached_data and not force_refresh:
            logger.info(f'命中 Redis 缓存: competition_id={competition.id}')
            return {'success': True, 'from_cache': True}
        
        # 2. 检查数据库
        existing_count = CombinedLeaderboard.objects.filter(competition=competition).count()
        
        # 如果数据库有数据且不强制刷新，直接返回
        if existing_count > 0 and not force_refresh:
            return {'success': True, 'from_database': True}
        
        # 3. 需要计算：检查是否有正在运行的任务
        running_task = LeaderboardCalculationTask.objects.filter(
            competition=competition,
            status='running'
        ).first()
        
        if running_task:
            # 有任务正在运行，等待完成
            logger.info(f'发现正在运行的计算任务: task_id={running_task.id}，等待完成...')
            
            for retry in range(self.MAX_RETRIES):
                time.sleep(self.RETRY_WAIT_TIME)
                
                # 检查 Redis 缓存是否已生成
                cached_data = cache.get(cache_key)
                if cached_data:
                    logger.info(f'等待期间计算完成，Redis 缓存已就绪')
                    return {'success': True, 'from_cache': True}
                
                # 检查任务状态
                running_task.refresh_from_db()
                if running_task.status == 'completed':
                    return {'success': True}
                elif running_task.status == 'failed':
                    logger.warning(f'计算任务失败: {running_task.error_message}')
                    break
            
            # 等待超时，返回提示
            return {
                'success': False,
                'message': '排行榜正在计算中（预计 10-30 秒），请稍后刷新页面',
                'status_code': 503
            }
        
        # 4. 没有运行的任务，触发新计算
        logger.info(f'首次访问或需要刷新，触发计算: competition_id={competition.id}, force={force_refresh}')
        
        try:
            if force_refresh:
                # 强制刷新时清除缓存
                CombinedLeaderboardCalculator.clear_cache(competition.id)
            
            # 使用计算器的带锁计算方法（内部会处理并发）
            logger.info(f'[DEBUG] 创建计算器: competition_id={competition.id}')
            calculator = CombinedLeaderboardCalculator(competition, competition.related_quiz)
            
            logger.info(f'[DEBUG] 开始调用计算方法: competition_id={competition.id}')
            result = calculator.calculate_leaderboard_with_lock(force=force_refresh)
            
            logger.info(f'[DEBUG] 计算方法返回: success={result.get("success")}, message={result.get("message")}')
            
            if not result.get('success'):
                logger.error(f'[DEBUG] 计算失败: {result.get("message")}')
                return {
                    'success': False,
                    'message': result.get('message', '计算排行榜失败'),
                    'status_code': 500
                }
            
            logger.info(f'[DEBUG] 计算成功: competition_id={competition.id}, 记录数={result.get("total_count")}')
            logger.info(f'[DEBUG] 数据已缓存到 Redis，数据库写入由异步任务处理')
            
            # 注意：数据库写入是异步的，不需要立即验证
            # 用户优先从 Redis 缓存读取数据，体验更快
            
            return {'success': True, 'calculated': True}
            
        except Exception as e:
            logger.error(f'[DEBUG] 触发计算时发生异常: {e}', exc_info=True)
            return {
                'success': False,
                'message': f'计算异常: {str(e)}',
                'status_code': 500
            }
    
    def _get_paginated_leaderboard(self, competition, page, page_size):
        """
        获取分页的排行榜数据
        
        Args:
            competition: 竞赛对象
            page: 页码
            page_size: 每页数量
        
        Returns:
            dict: 分页数据
        """
        if competition.competition_type == 'individual':
            queryset = CombinedLeaderboard.objects.filter(
                competition=competition,
                user__isnull=False
            ).select_related('user').order_by('rank')
        else:
            queryset = CombinedLeaderboard.objects.filter(
                competition=competition,
                team__isnull=False
            ).select_related('team', 'team__leader').order_by('rank')
        
        paginator = Paginator(queryset, page_size)
        
        try:
            page_obj = paginator.page(page)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)
        
        # 构建结果列表
        results = []
        for item in page_obj:
            if competition.competition_type == 'individual':
                results.append({
                    'rank': item.rank,
                    'user_id': item.user.id,
                    'user_uuid': str(item.user.uuid) if hasattr(item.user, 'uuid') else None,
                    'username': item.user.username,
                    'real_name': getattr(item.user, 'real_name', item.user.username),
                    'ctf_score': float(item.ctf_score),
                    'ctf_rank': item.ctf_rank,
                    'quiz_score': float(item.quiz_score),
                    'combined_score': float(item.combined_score),
                })
            else:
                results.append({
                    'rank': item.rank,
                    'team_id': item.team.id,
                    'team_name': item.team.name,
                    'team_code': item.team.team_code,
                    'leader': item.team.leader.username,
                    'member_count': item.team.members.count(),
                    'ctf_score': float(item.ctf_score),
                    'ctf_rank': item.ctf_rank,
                    'quiz_score': float(item.quiz_score),
                    'combined_score': float(item.combined_score),
                })
        
        return {
            'results': results,
            'pagination': {
                'page': page_obj.number,
                'page_size': page_size,
                'total_count': paginator.count,
                'total_pages': paginator.num_pages,
                'has_next': page_obj.has_next(),
                'has_previous': page_obj.has_previous(),
                'next_page': page_obj.next_page_number() if page_obj.has_next() else None,
                'previous_page': page_obj.previous_page_number() if page_obj.has_previous() else None,
            }
        }
    
    def _get_my_rank(self, user, competition):
        """
        获取当前用户/队伍的排名
        
        Args:
            user: 用户对象
            competition: 竞赛对象
        
        Returns:
            dict or None: 排名数据
        """
        try:
            if competition.competition_type == 'individual':
                my_record = CombinedLeaderboard.objects.filter(
                    competition=competition,
                    user=user
                ).first()
                
                if my_record:
                    return {
                        'rank': my_record.rank,
                        'user_id': user.id,
                        'username': user.username,
                        'real_name': getattr(user, 'real_name', user.username),
                        'ctf_score': float(my_record.ctf_score),
                        'ctf_rank': my_record.ctf_rank,
                        'quiz_score': float(my_record.quiz_score),
                        'combined_score': float(my_record.combined_score),
                    }
            else:
                # 团队赛：查找用户所在的队伍
                from competition.models import Team
                my_team = Team.objects.filter(
                    competition=competition,
                    members=user
                ).first()
                
                if my_team:
                    my_record = CombinedLeaderboard.objects.filter(
                        competition=competition,
                        team=my_team
                    ).first()
                    
                    if my_record:
                        return {
                            'rank': my_record.rank,
                            'team_id': my_team.id,
                            'team_name': my_team.name,
                            'team_code': my_team.team_code,
                            'leader': my_team.leader.username,
                            'member_count': my_team.members.count(),
                            'ctf_score': float(my_record.ctf_score),
                            'ctf_rank': my_record.ctf_rank,
                            'quiz_score': float(my_record.quiz_score),
                            'combined_score': float(my_record.combined_score),
                        }
        except Exception as e:
            logger.error(f'获取我的排名失败: {e}', exc_info=True)
        
        return None


class SyncQuizRegistrationsView(View):
    """同步知识竞赛报名数据的视图"""
    
    def post(self, request, competition_slug):
        """
        手动触发同步CTF报名数据到知识竞赛
        
        需要管理员权限
        """
        try:
            # 检查权限
            if not request.user.is_staff:
                return JsonResponse({
                    'success': False,
                    'message': '需要管理员权限'
                }, status=403)
            
            # 获取竞赛
            competition = get_object_or_404(Competition, slug=competition_slug)
            
            # 执行同步
            result = competition.sync_registrations_to_quiz()
            
            return JsonResponse(result)
            
        except Exception as e:
            logger.error(f'同步报名数据失败: {e}', exc_info=True)
            return JsonResponse({
                'success': False,
                'message': '同步失败，请稍后重试'
            }, status=500)


class UpdateCombinedLeaderboardView(View):
    """更新综合排行榜的视图"""
    
    def post(self, request, competition_slug):
        """
        手动触发更新综合排行榜（仅比赛结束后可用）
        
        需要管理员权限
        """
        try:
            # 检查权限
            if not request.user.is_staff:
                return JsonResponse({
                    'success': False,
                    'message': '需要管理员权限'
                }, status=403)
            
            # 获取竞赛
            competition = get_object_or_404(Competition, slug=competition_slug)
            
            # 检查比赛是否结束
            if timezone.now() <= competition.end_time:
                return JsonResponse({
                    'success': False,
                    'message': f'比赛尚未结束，请等待比赛结束后再计算（结束时间：{competition.end_time.strftime("%Y-%m-%d %H:%M")}）'
                }, status=400)
            
            # 检查是否关联了知识竞赛
            if not competition.related_quiz:
                return JsonResponse({
                    'success': False,
                    'message': '该竞赛未关联知识竞赛'
                }, status=400)
            
            # 清除缓存并重新计算
            CombinedLeaderboardCalculator.clear_cache(competition.id)
            
            calculator = CombinedLeaderboardCalculator(competition, competition.related_quiz)
            result = calculator.calculate_leaderboard_with_lock(force=True)
            
            if result.get('success'):
                return JsonResponse({
                    'success': True,
                    'message': '综合排行榜已更新',
                    'total_count': result.get('total_count', 0),
                    'is_final': result.get('is_final', False)
                })
            else:
                return JsonResponse(result, status=500)
            
        except Exception as e:
            logger.error(f'更新排行榜失败: {e}', exc_info=True)
            return JsonResponse({
                'success': False,
                'message': '更新失败，请稍后重试'
            }, status=500)


class LeaderboardCalculationStatusView(View):
    """排行榜计算状态查询视图"""
    
    def get(self, request, competition_slug):
        """
        查询排行榜计算状态
        
        需要管理员权限
        """
        try:
            # 检查权限
            if not request.user.is_staff:
                return JsonResponse({
                    'success': False,
                    'message': '需要管理员权限'
                }, status=403)
            
            # 获取竞赛
            competition = get_object_or_404(Competition, slug=competition_slug)
            
            # 获取最近的计算任务
            tasks = LeaderboardCalculationTask.objects.filter(
                competition=competition
            ).order_by('-created_at')[:10]
            
            tasks_data = []
            for task in tasks:
                tasks_data.append({
                    'id': task.id,
                    'status': task.status,
                    'competition_type': task.competition_type,
                    'progress': task.progress_percentage,
                    'total_participants': task.total_participants,
                    'processed_count': task.processed_count,
                    'result_count': task.result_count,
                    'duration_seconds': task.duration_seconds,
                    'error_message': task.error_message,
                    'created_at': task.created_at.isoformat(),
                    'started_at': task.started_at.isoformat() if task.started_at else None,
                    'completed_at': task.completed_at.isoformat() if task.completed_at else None,
                })
            
            return JsonResponse({
                'success': True,
                'tasks': tasks_data
            })
            
        except Exception as e:
            logger.error(f'查询计算状态失败: {e}', exc_info=True)
            return JsonResponse({
                'success': False,
                'message': '查询失败'
            }, status=500)


class VerifyLeaderboardView(View):
    """验证并修复排行榜数据"""
    
    def post(self, request, competition_slug):
        """
        验证并修复排行榜数据
        
        需要管理员权限
        """
        try:
            # 检查权限
            if not request.user.is_staff:
                return JsonResponse({
                    'success': False,
                    'message': '需要管理员权限'
                }, status=403)
            
            # 获取竞赛
            competition = get_object_or_404(Competition, slug=competition_slug)
            
            # 执行验证和修复
            result = CombinedLeaderboardCalculator.verify_and_repair_leaderboard(competition.id)
            
            return JsonResponse(result)
            
        except Exception as e:
            logger.error(f'验证排行榜失败: {e}', exc_info=True)
            return JsonResponse({
                'success': False,
                'message': '验证失败'
            }, status=500)

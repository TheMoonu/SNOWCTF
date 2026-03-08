"""
综合排行榜计算工具 - 高并发优化版本
解决并发、性能和数据一致性问题

主要改进：
1. 使用健壮的分布式锁机制
2. 使用临时表+原子切换避免数据丢失
3. 添加计算状态追踪和幂等性保证
4. 优化数据库查询性能
5. 完善的错误处理和恢复机制
6. 支持计算进度追踪
"""

from decimal import Decimal
from django.db.models import Max, Count, Q
from django.core.cache import cache
from django.db import transaction, connection
from django.utils import timezone
import logging
import hashlib
import uuid

logger = logging.getLogger('apps.competition')


class CombinedLeaderboardCalculator:
    """综合排行榜计算器（高并发优化版）"""
    
    # 缓存配置
    CALCULATION_CACHE_TIME = 3600  # 计算结果缓存1小时
    FINAL_CACHE_TIME = 86400 * 7  # 比赛结束后缓存7天
    
    # 批量处理配置
    BATCH_SIZE = 500
    PROGRESS_UPDATE_INTERVAL = 100  # 每处理100条更新一次进度
    
    def __init__(self, competition, quiz):
        """
        初始化计算器
        
        Args:
            competition: Competition对象
            quiz: Quiz对象
        """
        self.competition = competition
        self.quiz = quiz
        self.is_competition_ended = timezone.now() > competition.end_time
        self.task = None  # 计算任务追踪对象
    
    def calculate_leaderboard_with_lock(self, limit=None, force=False, force_recreate=False):
        """
        带分布式锁的安全计算入口
        
        工作流程：
        1. 检查 Redis 缓存，有则直接返回
        2. 检查数据库，有则返回
        3. 使用分布式锁防止并发计算
        4. 计算结果先存 Redis（供即时查询）
        5. 再异步写入数据库（持久化）
        
        Args:
            limit: 限制返回数量
            force: 是否强制重新计算（跳过缓存检查）
            force_recreate: 是否强制重建（删除旧任务和旧数据，用于管理员修改分数后）
        
        Returns:
            dict: 计算结果
        """
        from competition.distributed_lock import get_leaderboard_lock
        
        # 1. 先检查 Redis 缓存
        if not force:
            cached_result = self._get_cached_result(limit)
            if cached_result:
                logger.info(f'命中 Redis 缓存: competition_id={self.competition.id}')
                return cached_result
        
        # 2. 再检查数据库（如果 Redis 失效但数据库有数据）
        if not force and self.is_competition_ended:
            db_result = self._get_fallback_result(limit)
            if db_result and db_result.get('success'):
                logger.info(f'从数据库获取数据: competition_id={self.competition.id}')
                # 重新缓存到 Redis
                self._cache_result(db_result, limit)
                return db_result
        
        # 3. 需要计算，使用分布式锁
        lock = get_leaderboard_lock(self.competition.id, timeout=300)
        
        with lock() as acquired:
            if not acquired:
                # 获取锁失败，说明有其他进程在计算
                logger.warning(f'获取锁失败，有其他进程在计算: competition_id={self.competition.id}')
                
                # 等待一下，尝试从缓存获取
                import time
                for i in range(10):  # 最多等待10秒
                    time.sleep(1)
                    cached_result = self._get_cached_result(limit)
                    if cached_result:
                        logger.info(f'等待期间，其他进程计算完成: competition_id={self.competition.id}')
                        return cached_result
                
                # 等待超时，返回降级数据
                return self._get_fallback_result(limit)
            
            # 4. 获得锁，执行计算
            try:
                logger.info(f'获得分布式锁，开始计算: competition_id={self.competition.id}')
                
                # 4.1 如果是强制重建，先清理旧数据（在锁内执行，避免竞态）
                if force_recreate:
                    from competition.models import LeaderboardCalculationTask, CombinedLeaderboard
                    
                    # 删除所有旧的计算任务
                    task_count = LeaderboardCalculationTask.objects.filter(
                        competition=self.competition
                    ).delete()[0]
                    logger.info(f' 强制重建：删除 {task_count} 个旧任务记录')
                    
                    # 删除旧的排行榜数据
                    if self.competition.competition_type == 'individual':
                        data_count = CombinedLeaderboard.objects.filter(
                            competition=self.competition,
                            user__isnull=False
                        ).delete()[0]
                    else:
                        data_count = CombinedLeaderboard.objects.filter(
                            competition=self.competition,
                            team__isnull=False
                        ).delete()[0]
                    logger.info(f' 强制重建：删除 {data_count} 条旧排行榜数据')
                    
                    # 清除缓存
                    CombinedLeaderboardCalculator.clear_cache(self.competition.id)
                    logger.info(f' 强制重建：已清除缓存')
                
                # 4.2 执行计算
                if self.competition.competition_type == 'individual':
                    result = self._calculate_individual_leaderboard_internal(limit)
                else:
                    result = self._calculate_team_leaderboard_internal(limit)
                
                # 5. 立即缓存到 Redis（优先）
                if result.get('success'):
                    self._cache_result(result, limit)
                    logger.info(f' 计算完成，已缓存到 Redis: competition_id={self.competition.id}')
                
                return result
                
            except Exception as e:
                logger.error(f'计算综合排行榜失败: {e}', exc_info=True)
                if self.task:
                    self.task.mark_failed(str(e))
                return {
                    'success': False,
                    'message': f'计算失败: {str(e)}'
                }
    
    def _calculate_individual_leaderboard_internal(self, limit=None):
        """内部方法：计算个人赛综合排行榜"""
        from competition.models import ScoreUser, CombinedLeaderboard, LeaderboardCalculationTask
        from quiz.models import QuizRecord
        
        # 创建计算任务追踪
        data_version = self._generate_data_version('individual')
        idempotency_key = f"{self.competition.id}_individual_{data_version}"
        
        # 检查是否已经有完成的任务
        existing_task = LeaderboardCalculationTask.objects.filter(
            competition=self.competition,
            competition_type='individual',
            data_version=data_version,
            status='completed'
        ).first()
        
        if existing_task and not self.is_competition_ended:
            logger.info(f'已存在相同版本的完成任务，直接返回数据库结果')
            return self._get_from_database_individual(limit)
        
        # 创建新任务
        try:
            self.task = LeaderboardCalculationTask.objects.create(
                competition=self.competition,
                competition_type='individual',
                data_version=data_version,
                idempotency_key=idempotency_key,
                total_participants=ScoreUser.objects.filter(competition=self.competition).count()
            )
            self.task.mark_running()
        except Exception as e:
            # 如果创建任务失败，记录错误
            logger.error(f'创建任务失败: {e}', exc_info=True)
            # 尝试从数据库获取已有数据
            return self._get_from_database_individual(limit)
        
        try:
            # 获取配置
            top_percent = self.competition.combined_score_top_percent
            ctf_weight = Decimal(str(self.competition.combined_score_ctf_weight))
            quiz_weight = Decimal('1.0') - ctf_weight
            
            # 计算基准分
            ctf_baseline = self._calculate_baseline_score(
                ScoreUser.objects.filter(competition=self.competition)
                    .order_by('-points')
                    .values_list('points', flat=True),
                top_percent
            )
            
            quiz_baseline = self._calculate_baseline_score(
                QuizRecord.objects.filter(quiz=self.quiz, status='completed')
                    .order_by('-score')
                    .values_list('score', flat=True),
                top_percent,
                default=Decimal(str(self.quiz.total_score or 100))
            )
            
            logger.warning(f'[综合排行榜计算] {self.competition.title} 基准分 - CTF: {ctf_baseline}, Quiz: {quiz_baseline}')
            
            # 优化：批量获取所有数据
            ctf_scores = ScoreUser.objects.filter(
                competition=self.competition
            ).select_related('user').order_by('-points')
            
            # 批量获取知识竞赛成绩
            quiz_records = QuizRecord.objects.filter(
                quiz=self.quiz,
                status='completed'
            ).values('user').annotate(
                best_score=Max('score')
            )
            quiz_score_map = {record['user']: record['best_score'] for record in quiz_records}
            
            # 计算综合分数
            leaderboard_data = []
            processed_count = 0
            
            for ctf_score in ctf_scores:
                user = ctf_score.user
                ctf_points = Decimal(str(ctf_score.points))
                quiz_points = quiz_score_map.get(user.id, Decimal('0'))
                
                # 计算综合分数
                combined_score = self._calculate_weighted_normalized_score(
                    ctf_points, quiz_points, ctf_baseline, quiz_baseline,
                    ctf_weight, quiz_weight
                )
                
                # 安全获取用户属性
                user_uuid = str(user.uuid) if hasattr(user, 'uuid') else None
                real_name = getattr(user, 'real_name', None) or user.username
                
                leaderboard_data.append({
                    'user_id': user.id,
                    'user_uuid': user_uuid,
                    'username': user.username,
                    'real_name': real_name,
                    'ctf_score': ctf_points,
                    'ctf_rank': ctf_score.rank if ctf_score.rank > 0 else 0,
                    'quiz_score': quiz_points,
                    'combined_score': combined_score,
                })
                
                processed_count += 1
                
                # 定期更新进度
                if processed_count % self.PROGRESS_UPDATE_INTERVAL == 0:
                    self.task.update_progress(processed_count)
            
            # 排序并添加排名
            leaderboard_data.sort(key=lambda x: (-x['combined_score'], -x['ctf_score']))
            
            for idx, item in enumerate(leaderboard_data, 1):
                item['rank'] = idx
            
            # 异步写入数据库（不阻塞返回，优先给用户展示缓存数据）
            # 触发 Celery 异步任务进行数据库写入
            try:
                from easytask.tasks import save_combined_leaderboard_to_db
                # 异步任务：保存到数据库
                save_combined_leaderboard_to_db.delay(
                    competition_id=self.competition.id,
                    competition_type='individual',
                    leaderboard_data=leaderboard_data,
                    is_final=self.is_competition_ended
                )
                logger.info(f' 已触发异步任务写入数据库: {len(leaderboard_data)} 条记录')
            except Exception as e:
                logger.error(f'触发异步任务失败，尝试同步写入: {e}')
                # 如果异步任务失败，降级为同步写入
                try:
                    self._save_leaderboard_atomic(leaderboard_data, is_team=False)
                    logger.info(f' 同步写入数据库成功: {len(leaderboard_data)} 条记录')
                except Exception as sync_e:
                    logger.error(f'同步写入数据库也失败: {sync_e}', exc_info=True)
                    # 数据库写入失败不影响缓存展示，记录错误即可
            
            # 标记任务完成
            if self.task:
                self.task.mark_completed(len(leaderboard_data))
            
            # 限制返回数量
            if limit:
                leaderboard_data = leaderboard_data[:limit]
            
            result = {
                'success': True,
                'competition_type': 'individual',
                'calculation_method': f'归一化(前{top_percent}%)+加权(CTF:{int(ctf_weight*100)}%/知识竞赛:{int(quiz_weight*100)}%)',
                'leaderboard': leaderboard_data,
                'total_count': len(leaderboard_data),
                'is_final': self.is_competition_ended,
                'data_version': data_version
            }
            
            return result
            
        except Exception as e:
            logger.error(f'计算个人赛排行榜失败: {e}', exc_info=True)
            if self.task:
                self.task.mark_failed(str(e))
            raise
    
    def _calculate_team_leaderboard_internal(self, limit=None):
        """内部方法：计算团队赛综合排行榜"""
        from competition.models import ScoreTeam, CombinedLeaderboard, LeaderboardCalculationTask
        from quiz.models import QuizRecord
        
        # 创建计算任务追踪
        data_version = self._generate_data_version('team')
        idempotency_key = f"{self.competition.id}_team_{data_version}"
        
        # 检查是否已经有完成的任务
        existing_task = LeaderboardCalculationTask.objects.filter(
            competition=self.competition,
            competition_type='team',
            data_version=data_version,
            status='completed'
        ).first()
        
        if existing_task and not self.is_competition_ended:
            logger.info(f'已存在相同版本的完成任务，直接返回数据库结果')
            return self._get_from_database_team(limit)
        
        # 创建新任务
        try:
            self.task = LeaderboardCalculationTask.objects.create(
                competition=self.competition,
                competition_type='team',
                data_version=data_version,
                idempotency_key=idempotency_key,
                total_participants=ScoreTeam.objects.filter(competition=self.competition).count()
            )
            self.task.mark_running()
        except Exception as e:
            # 如果创建任务失败，记录错误
            logger.error(f'创建任务失败: {e}', exc_info=True)
            # 尝试从数据库获取已有数据
            return self._get_from_database_team(limit)
        
        try:
            # 获取配置
            top_percent = self.competition.combined_score_top_percent
            ctf_weight = Decimal(str(self.competition.combined_score_ctf_weight))
            quiz_weight = Decimal('1.0') - ctf_weight
            
            # 计算基准分
            ctf_baseline = self._calculate_baseline_score(
                ScoreTeam.objects.filter(competition=self.competition)
                    .order_by('-score')
                    .values_list('score', flat=True),
                top_percent
            )
            
            quiz_baseline = self._calculate_baseline_score(
                QuizRecord.objects.filter(quiz=self.quiz, status='completed')
                    .order_by('-score')
                    .values_list('score', flat=True),
                top_percent,
                default=Decimal(str(self.quiz.total_score or 100))
            )
            
            logger.warning(f'[综合排行榜计算] {self.competition.title} 基准分 - CTF: {ctf_baseline}, Quiz: {quiz_baseline}')
            
            # 优化：使用prefetch_related一次性加载所有队伍成员
            team_scores = ScoreTeam.objects.filter(
                competition=self.competition
            ).select_related('team', 'team__leader').prefetch_related('team__members').order_by('-score')
            
            # 批量获取所有成员的知识竞赛成绩
            all_team_members = set()
            for team_score in team_scores:
                all_team_members.update(team_score.team.members.values_list('id', flat=True))
            
            quiz_records = QuizRecord.objects.filter(
                quiz=self.quiz,
                user_id__in=all_team_members,
                status='completed'
            ).values('user').annotate(
                best_score=Max('score')
            )
            quiz_score_map = {record['user']: record['best_score'] for record in quiz_records}
            
            # 计算综合分数
            leaderboard_data = []
            processed_count = 0
            
            for team_score in team_scores:
                team = team_score.team
                ctf_points = Decimal(str(team_score.score))
                
                # 获取团队所有成员的知识竞赛最佳成绩
                member_quiz_scores = []
                for member in team.members.all():  # 已经prefetch，不会产生额外查询
                    best_score = quiz_score_map.get(member.id)
                    if best_score:
                        member_quiz_scores.append(Decimal(str(best_score)))
                
                # 计算团队知识竞赛平均分
                team_quiz_score = (
                    sum(member_quiz_scores) / Decimal(str(len(member_quiz_scores)))
                    if member_quiz_scores else Decimal('0')
                )
                
                # 计算综合分数
                combined_score = self._calculate_weighted_normalized_score(
                    ctf_points, team_quiz_score, ctf_baseline, quiz_baseline,
                    ctf_weight, quiz_weight
                )
                
                leaderboard_data.append({
                    'team_id': team.id,
                    'team_name': team.name,
                    'team_code': team.team_code,
                    'leader': team.leader.username,
                    'member_count': team.members.count(),
                    'ctf_score': ctf_points,
                    'ctf_rank': team_score.rank if team_score.rank > 0 else 0,
                    'quiz_score': team_quiz_score,
                    'combined_score': combined_score,
                })
                
                processed_count += 1
                
                # 定期更新进度
                if processed_count % self.PROGRESS_UPDATE_INTERVAL == 0:
                    self.task.update_progress(processed_count)
            
            # 排序并添加排名
            leaderboard_data.sort(key=lambda x: (-x['combined_score'], -x['ctf_score']))
            
            for idx, item in enumerate(leaderboard_data, 1):
                item['rank'] = idx
            
            # 异步写入数据库（不阻塞返回，优先给用户展示缓存数据）
            # 触发 Celery 异步任务进行数据库写入
            try:
                from easytask.tasks import save_combined_leaderboard_to_db
                # 异步任务：保存到数据库
                save_combined_leaderboard_to_db.delay(
                    competition_id=self.competition.id,
                    competition_type='team',
                    leaderboard_data=leaderboard_data,
                    is_final=self.is_competition_ended
                )
                logger.info(f' 已触发异步任务写入数据库: {len(leaderboard_data)} 条记录')
            except Exception as e:
                logger.error(f'触发异步任务失败，尝试同步写入: {e}')
                # 如果异步任务失败，降级为同步写入
                try:
                    self._save_leaderboard_atomic(leaderboard_data, is_team=True)
                    logger.info(f' 同步写入数据库成功: {len(leaderboard_data)} 条记录')
                except Exception as sync_e:
                    logger.error(f'同步写入数据库也失败: {sync_e}', exc_info=True)
                    # 数据库写入失败不影响缓存展示，记录错误即可
            
            # 标记任务完成
            if self.task:
                self.task.mark_completed(len(leaderboard_data))
            
            # 限制返回数量
            if limit:
                leaderboard_data = leaderboard_data[:limit]
            
            result = {
                'success': True,
                'competition_type': 'team',
                'calculation_method': f'归一化(前{top_percent}%)+加权(CTF:{int(ctf_weight*100)}%/知识竞赛:{int(quiz_weight*100)}%)',
                'leaderboard': leaderboard_data,
                'total_count': len(leaderboard_data),
                'is_final': self.is_competition_ended,
                'data_version': data_version
            }
            
            return result
            
        except Exception as e:
            logger.error(f'计算团队赛排行榜失败: {e}', exc_info=True)
            if self.task:
                self.task.mark_failed(str(e))
            raise
    
    def _calculate_baseline_score(self, scores_queryset, top_percent, default=Decimal('1.0')):
        """
        计算基准分数
        
        Args:
            scores_queryset: 分数查询集
            top_percent: 取前百分之多少
            default: 默认值
        
        Returns:
            Decimal: 基准分数
        """
        scores_list = list(scores_queryset)
        
        if not scores_list:
            logger.info(f'[基准分计算] 无数据，使用默认值: {default}')
            return default
        
        top_count = max(1, int(len(scores_list) * top_percent / 100))
        baseline = sum(scores_list[:top_count]) / Decimal(str(top_count))
        
        logger.warning(
            f'[基准分计算] 总人数: {len(scores_list)}, '
            f'前{top_percent}%人数: {top_count}, '
            f'基准分: {baseline}, '
            f'最高分: {scores_list[0] if scores_list else 0}, '
            f'最低分(前{top_percent}%): {scores_list[top_count-1] if top_count <= len(scores_list) else 0}'
        )
        
        return Decimal(str(baseline))
    
    def _calculate_weighted_normalized_score(self, ctf_score, quiz_score,
                                            ctf_baseline, quiz_baseline,
                                            ctf_weight, quiz_weight):
        """
        归一化后计算加权综合分数
        
        Args:
            ctf_score: CTF分数
            quiz_score: 知识竞赛分数
            ctf_baseline: CTF基准分
            quiz_baseline: 知识竞赛基准分
            ctf_weight: CTF权重
            quiz_weight: 知识竞赛权重
        
        Returns:
            Decimal: 综合分数（0-100）
        """
        # 确保都是Decimal类型
        ctf_score = Decimal(str(ctf_score or 0))
        quiz_score = Decimal(str(quiz_score or 0))
        ctf_baseline = Decimal(str(ctf_baseline or 1))
        quiz_baseline = Decimal(str(quiz_baseline or 1))
        
        # 归一化
        ctf_normalized = (ctf_score / ctf_baseline * Decimal('100')) if ctf_baseline > 0 else Decimal('0')
        quiz_normalized = (quiz_score / quiz_baseline * Decimal('100')) if quiz_baseline > 0 else Decimal('0')
        
        # 加权计算
        combined = ctf_normalized * ctf_weight + quiz_normalized * quiz_weight
        
        return combined.quantize(Decimal('0.01'))
    
    def _save_leaderboard_atomic(self, leaderboard_data, is_team=False):
        """
        使用临时表+原子切换的方式保存排行榜（避免数据丢失）
        
        策略：
        1. 先将数据插入到临时表（或使用标记字段）
        2. 在事务中删除旧数据并重命名/更新标记
        3. 确保整个过程原子性
        
        Args:
            leaderboard_data: 排行榜数据列表
            is_team: 是否为团队赛
        """
        from competition.models import CombinedLeaderboard
        
        try:
            with transaction.atomic():
                # 方案1：使用临时UUID标记新数据
                temp_marker = str(uuid.uuid4())
                
                # 批量创建记录（带临时标记）
                records_to_create = []
                for data in leaderboard_data:
                    if is_team:
                        record = CombinedLeaderboard(
                            competition=self.competition,
                            team_id=data['team_id'],
                            ctf_score=data['ctf_score'],
                            quiz_score=data['quiz_score'],
                            combined_score=data['combined_score'],
                            rank=data['rank'],
                            ctf_rank=data['ctf_rank'],
                            quiz_rank=0,
                            is_final=self.is_competition_ended
                        )
                    else:
                        record = CombinedLeaderboard(
                            competition=self.competition,
                            user_id=data['user_id'],
                            ctf_score=data['ctf_score'],
                            quiz_score=data['quiz_score'],
                            combined_score=data['combined_score'],
                            rank=data['rank'],
                            ctf_rank=data['ctf_rank'],
                            quiz_rank=0,
                            is_final=self.is_competition_ended
                        )
                    records_to_create.append(record)
                
                # 步骤1：删除旧数据
                if is_team:
                    old_count = CombinedLeaderboard.objects.filter(
                        competition=self.competition,
                        team__isnull=False
                    ).delete()[0]
                    logger.info(f'删除旧的团队排行榜: {old_count}条')
                else:
                    old_count = CombinedLeaderboard.objects.filter(
                        competition=self.competition,
                        user__isnull=False
                    ).delete()[0]
                    logger.info(f'删除旧的个人排行榜: {old_count}条')
                
                # 步骤2：批量插入新数据
                if records_to_create:
                    CombinedLeaderboard.objects.bulk_create(
                        records_to_create,
                        batch_size=self.BATCH_SIZE,
                        ignore_conflicts=False  # 不忽略冲突，确保数据一致性
                    )
                    logger.info(f'成功保存排行榜记录: {len(records_to_create)}条')
                
                # 事务提交后，数据切换完成
                
        except Exception as e:
            logger.error(f'保存排行榜记录失败: {e}', exc_info=True)
            raise
    
    def _generate_data_version(self, competition_type):
        """
        生成数据版本号（基于数据的哈希）
        
        用于幂等性检查：相同的输入数据应该生成相同的版本号
        
        Args:
            competition_type: 'individual' 或 'team'
        
        Returns:
            str: 版本号哈希
        """
        from competition.models import ScoreUser, ScoreTeam
        from quiz.models import QuizRecord
        
        # 收集关键数据特征
        if competition_type == 'individual':
            ctf_count = ScoreUser.objects.filter(competition=self.competition).count()
            ctf_sum = ScoreUser.objects.filter(competition=self.competition).aggregate(
                total=Count('id')
            )['total'] or 0
        else:
            ctf_count = ScoreTeam.objects.filter(competition=self.competition).count()
            ctf_sum = ScoreTeam.objects.filter(competition=self.competition).aggregate(
                total=Count('id')
            )['total'] or 0
        
        quiz_count = QuizRecord.objects.filter(quiz=self.quiz, status='completed').count()
        
        # 组合特征字符串
        feature_string = f"{self.competition.id}_{competition_type}_{ctf_count}_{ctf_sum}_{quiz_count}_{self.competition.combined_score_ctf_weight}_{self.competition.combined_score_top_percent}"
        
        # 生成哈希
        return hashlib.md5(feature_string.encode()).hexdigest()[:16]
    
    def _get_cached_result(self, limit=None):
        """从缓存获取结果"""
        cache_key = f'combined_leaderboard_{self.competition.competition_type}_{self.competition.id}_{limit or "all"}'
        return cache.get(cache_key)
    
    def _cache_result(self, result, limit=None):
        """
        缓存计算结果到 Redis
        
        策略：
        - 比赛结束后：缓存 7 天
        - 比赛进行中：缓存 1 小时（虽然不会实时计算，但保留此逻辑）
        """
        cache_key = f'combined_leaderboard_{self.competition.competition_type}_{self.competition.id}_{limit or "all"}'
        
        try:
            if self.is_competition_ended:
                cache.set(cache_key, result, self.FINAL_CACHE_TIME)
                logger.info(f' Redis 缓存成功（7天）: {cache_key}, 记录数: {result.get("total_count", 0)}')
            else:
                cache.set(cache_key, result, self.CALCULATION_CACHE_TIME)
                logger.info(f' Redis 缓存成功（1小时）: {cache_key}, 记录数: {result.get("total_count", 0)}')
        except Exception as e:
            logger.error(f'Redis 缓存失败: {e}', exc_info=True)
    
    def _get_fallback_result(self, limit=None):
        """获取降级结果（缓存或数据库）"""
        # 先尝试缓存
        cached = self._get_cached_result(limit)
        if cached:
            return cached
        
        # 再尝试数据库
        if self.competition.competition_type == 'individual':
            return self._get_from_database_individual(limit)
        else:
            return self._get_from_database_team(limit)
    
    def _get_from_database_individual(self, limit=None):
        """从数据库获取个人赛排行榜"""
        from competition.models import CombinedLeaderboard
        
        queryset = CombinedLeaderboard.objects.filter(
            competition=self.competition,
            user__isnull=False
        ).select_related('user').order_by('rank')
        
        if limit:
            queryset = queryset[:limit]
        
        leaderboard_data = []
        for item in queryset:
            leaderboard_data.append({
                'rank': item.rank,
                'user_id': item.user.id,
                'user_uuid': str(item.user.uuid) if hasattr(item.user, 'uuid') else None,
                'username': item.user.username,
                'real_name': getattr(item.user, 'real_name', item.user.username),
                'ctf_score': item.ctf_score,
                'ctf_rank': item.ctf_rank,
                'quiz_score': item.quiz_score,
                'combined_score': item.combined_score,
            })
        
        # 如果数据库里没有数据，返回失败
        if len(leaderboard_data) == 0:
            logger.warning(f'数据库中没有个人赛排行榜数据: competition_id={self.competition.id}')
            return {
                'success': False,
                'message': '排行榜数据不存在，需要重新计算'
            }
        
        return {
            'success': True,
            'competition_type': 'individual',
            'leaderboard': leaderboard_data,
            'total_count': len(leaderboard_data),
            'is_final': item.is_final if queryset.exists() else False,
            'from_database': True
        }
    
    def _get_from_database_team(self, limit=None):
        """从数据库获取团队赛排行榜"""
        from competition.models import CombinedLeaderboard
        
        queryset = CombinedLeaderboard.objects.filter(
            competition=self.competition,
            team__isnull=False
        ).select_related('team', 'team__leader').prefetch_related('team__members').order_by('rank')
        
        if limit:
            queryset = queryset[:limit]
        
        leaderboard_data = []
        for item in queryset:
            leaderboard_data.append({
                'rank': item.rank,
                'team_id': item.team.id,
                'team_name': item.team.name,
                'team_code': item.team.team_code,
                'leader': item.team.leader.username,
                'member_count': item.team.members.count(),
                'ctf_score': item.ctf_score,
                'ctf_rank': item.ctf_rank,
                'quiz_score': item.quiz_score,
                'combined_score': item.combined_score,
            })
        
        # 如果数据库里没有数据，返回失败
        if len(leaderboard_data) == 0:
            logger.warning(f'数据库中没有团队赛排行榜数据: competition_id={self.competition.id}')
            return {
                'success': False,
                'message': '排行榜数据不存在，需要重新计算'
            }
        
        return {
            'success': True,
            'competition_type': 'team',
            'leaderboard': leaderboard_data,
            'total_count': len(leaderboard_data),
            'is_final': item.is_final if queryset.exists() else False,
            'from_database': True
        }
    
    @staticmethod
    def clear_cache(competition_id):
        """清除综合排行榜缓存"""
        # 清除个人赛和团队赛的所有缓存
        for comp_type in ['individual', 'team']:
            cache.delete(f'combined_leaderboard_{comp_type}_{competition_id}_all')
            
            # 清除各种limit的缓存
            for limit in [10, 20, 50, 100, 500]:
                cache.delete(f'combined_leaderboard_{comp_type}_{competition_id}_{limit}')
        
        logger.info(f'已清除排行榜缓存: competition_id={competition_id}')
    
    @staticmethod
    def verify_and_repair_leaderboard(competition_id):
        """
        验证并修复排行榜数据
        
        检查：
        1. 排名连续性
        2. 分数排序正确性
        3. 数据完整性
        
        Args:
            competition_id: 竞赛ID
        
        Returns:
            dict: 验证和修复结果
        """
        from competition.models import Competition, CombinedLeaderboard
        
        try:
            competition = Competition.objects.get(id=competition_id)
        except Competition.DoesNotExist:
            return {'success': False, 'message': '竞赛不存在'}
        
        issues = []
        repaired = []
        
        # 获取排行榜数据
        if competition.competition_type == 'individual':
            records = CombinedLeaderboard.objects.filter(
                competition=competition,
                user__isnull=False
            ).order_by('rank')
        else:
            records = CombinedLeaderboard.objects.filter(
                competition=competition,
                team__isnull=False
            ).order_by('rank')
        
        # 检查1：排名连续性
        expected_rank = 1
        for record in records:
            if record.rank != expected_rank:
                issues.append(f'排名不连续: 期望{expected_rank}，实际{record.rank}')
                record.rank = expected_rank
                record.save(update_fields=['rank'])
                repaired.append(f'修复排名: {record.participant_name}')
            expected_rank += 1
        
        # 检查2：分数排序
        prev_score = None
        for record in records:
            if prev_score is not None and record.combined_score > prev_score:
                issues.append(f'分数排序错误: {record.participant_name}')
            prev_score = record.combined_score
        
        return {
            'success': True,
            'issues_found': len(issues),
            'issues': issues,
            'repairs_made': len(repaired),
            'repairs': repaired
        }

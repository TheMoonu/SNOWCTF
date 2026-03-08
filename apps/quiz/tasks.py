# -*- coding: utf-8 -*-
"""
知识竞赛模块异步任务
包括试卷提交、成绩计算等
"""
import logging
from celery import shared_task
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from django.db import transaction

logger = logging.getLogger('apps.quiz')
User = get_user_model()


class TaskStatus:
    """任务状态常量"""
    PENDING = 'pending'      # 等待处理
    PROCESSING = 'processing'  # 处理中
    SUCCESS = 'success'      # 成功
    FAILED = 'failed'        # 失败


@shared_task(bind=True, max_retries=0)
def submit_quiz_async(self, record_uuid, user_id):
    """
    异步提交试卷任务
    
    Args:
        self: Celery task实例
        record_uuid: 答题记录UUID
        user_id: 用户ID
        
    Returns:
        dict: 任务结果
        {
            'status': 'success' | 'failed',
            'data': {成绩信息} 或 None,
            'error': 错误信息 或 None,
            'task_id': 任务ID
        }
    """
    from django.db import close_old_connections
    from quiz.models import QuizRecord
    from quiz.utils import RedisLock
    
    task_id = self.request.id
    cache_key = f"submit_quiz_task:{task_id}"
    lock_key = f"submit_quiz:{record_uuid}"
    
    # 任务开始时，确保数据库连接是新鲜的
    close_old_connections()
    
    # 初始化任务状态
    task_info = {
        'status': TaskStatus.PROCESSING,
        'progress': 10,
        'message': '正在初始化提交任务...',
        'task_id': task_id,
        'started_at': timezone.now().isoformat()
    }
    cache.set(cache_key, task_info, timeout=300)  # 5分钟过期
    
    try:
        logger.info(f"[Task {task_id}] 开始提交试卷: user={user_id}, record={record_uuid}")
        
        # ==================== 安全校验 ====================
        # 1. 验证用户存在且有效
        try:
            user = User.objects.get(id=user_id, is_active=True)
        except User.DoesNotExist:
            error_msg = f"用户不存在或已禁用: user_id={user_id}"
            logger.error(f"[Task {task_id}] {error_msg}")
            task_info.update({
                'status': TaskStatus.FAILED,
                'error': '用户验证失败',  # 不暴露内部信息
                'progress': 0
            })
            cache.set(cache_key, task_info, timeout=300)
            return task_info
        
        # 2. 使用分布式锁防止重复提交
        task_info.update({
            'progress': 20,
            'message': '正在验证提交权限...'
        })
        cache.set(cache_key, task_info, timeout=300)
        
        try:
            with RedisLock(lock_key, timeout=30):
                with transaction.atomic():
                    # 3. 获取并锁定记录
                    task_info.update({
                        'progress': 40,
                        'message': '正在加载答题记录...'
                    })
                    cache.set(cache_key, task_info, timeout=300)
                    
                    try:
                        record = QuizRecord.objects.select_for_update().select_related(
                            'quiz', 'user'
                        ).get(
                            uuid=record_uuid,
                            user=user,  # 核心：确保记录属于该用户
                            status='in_progress'  # 只能提交进行中的记录
                        )
                        
                        # 额外安全检查：验证竞赛是否有效
                        if not record.quiz.is_active:
                            error_msg = '竞赛已关闭，无法提交'
                            logger.warning(
                                f"[Task {task_id}] {error_msg}: quiz={record.quiz.slug}, "
                                f"user={user.username}"
                            )
                            task_info.update({
                                'status': TaskStatus.FAILED,
                                'error': error_msg,
                                'progress': 0
                            })
                            cache.set(cache_key, task_info, timeout=300)
                            return task_info
                        
                        # 额外安全检查：防止用户篡改记录（双重验证）
                        if record.user_id != user.id:
                            error_msg = '权限验证失败'
                            logger.error(
                                f"[Task {task_id}] ⚠️ 严重安全问题 - 用户ID不匹配: "
                                f"record.user_id={record.user_id}, user.id={user.id}, "
                                f"record={record_uuid}, quiz={record.quiz.slug}"
                            )
                            task_info.update({
                                'status': TaskStatus.FAILED,
                                'error': error_msg,
                                'progress': 0
                            })
                            cache.set(cache_key, task_info, timeout=300)
                            return task_info
                        
                    except QuizRecord.DoesNotExist:
                        # 检查是否是跨用户提交尝试（安全审计）
                        other_user_record = QuizRecord.objects.filter(
                            uuid=record_uuid,
                            status='in_progress'
                        ).select_related('user', 'quiz').first()
                        
                        if other_user_record:
                            # 严重安全事件：尝试提交他人的答题记录
                            error_msg = '权限验证失败'
                            logger.error(
                                f"[Task {task_id}] ⚠️ [安全警告] 跨用户提交尝试: "
                                f"攻击者={user.username}(id={user.id}), "
                                f"目标记录={record_uuid}, "
                                f"记录所属={other_user_record.user.username}(id={other_user_record.user_id}), "
                                f"竞赛={other_user_record.quiz.slug}"
                            )
                        else:
                            # 普通的记录不存在
                            error_msg = '答题记录不存在或已提交'
                            logger.warning(
                                f"[Task {task_id}] {error_msg}: record={record_uuid}, "
                                f"user={user.username}"
                            )
                        
                        task_info.update({
                            'status': TaskStatus.FAILED,
                            'error': error_msg,
                            'progress': 0
                        })
                        cache.set(cache_key, task_info, timeout=300)
                        return task_info
                    
                    # 4. 检查是否超时
                    task_info.update({
                        'progress': 60,
                        'message': '正在检查答题时间...'
                    })
                    cache.set(cache_key, task_info, timeout=300)
                    
                    elapsed_time = (timezone.now() - record.start_time).total_seconds()
                    is_timeout = elapsed_time > record.quiz.duration * 60
                    
                    # 5. 检查所有答案的正确性
                    task_info.update({
                        'progress': 70,
                        'message': '正在评分...'
                    })
                    cache.set(cache_key, task_info, timeout=300)
                    
                    # 预取所有答案及其选项，减少数据库查询
                    answers = record.answers.prefetch_related('selected_options', 'question__options').all()
                    
                    for answer in answers:
                        answer.check_and_save()
                    
                    # 6. 计算总分
                    task_info.update({
                        'progress': 85,
                        'message': '正在计算成绩...'
                    })
                    cache.set(cache_key, task_info, timeout=300)
                    
                    record.calculate_score()
                    
                    # 7. 更新记录状态
                    record.status = 'timeout' if is_timeout else 'completed'
                    record.submit_time = timezone.now()
                    record.save()
                    
                    # 8. 清除排行榜缓存
                    try:
                        # 清除Quiz模型内置的排行榜缓存
                        cache.delete(f'quiz_leaderboard_{record.quiz.id}_all')
                        
                        # 清除Quiz排行榜API的分页缓存
                        try:
                            cache.delete_pattern(f'quiz_rankings_{record.quiz.id}_page_*')
                        except AttributeError:
                            # 如果不支持 delete_pattern，手动删除前20页
                            for page in range(1, 21):
                                cache.delete(f'quiz_rankings_{record.quiz.id}_page_{page}')
                    except Exception as e:
                        logger.error(f"[Task {task_id}] 清除排行榜缓存失败: {e}")
                    
                    # 9. 准备返回数据
                    result_data = {
                        'record_uuid': str(record.uuid),
                        'score': float(record.score),
                        'status': record.status,
                        'submit_time': record.submit_time.isoformat(),
                        'is_timeout': is_timeout
                    }
                    
                    task_info.update({
                        'status': TaskStatus.SUCCESS,
                        'progress': 100,
                        'message': '提交成功',
                        'data': result_data,
                        'completed_at': timezone.now().isoformat()
                    })
                    cache.set(cache_key, task_info, timeout=300)
                    
                    # 记录详细的提交日志（用于审计）
                    logger.info(
                        f"[Task {task_id}] 试卷提交成功: "
                        f"user={user.username}(id={user.id}), "
                        f"quiz={record.quiz.slug}, "
                        f"score={record.score}, "
                        f"is_timeout={is_timeout}, "
                        f"submit_time={record.submit_time.isoformat()}"
                    )
                    
                    return task_info
        
        except Exception as lock_error:
            # 获取锁失败或事务执行失败
            error_msg = f"提交失败: {str(lock_error)}"
            logger.error(f"[Task {task_id}] {error_msg}", exc_info=True)
            task_info.update({
                'status': TaskStatus.FAILED,
                'error': error_msg,
                'progress': 0
            })
            cache.set(cache_key, task_info, timeout=300)
            return task_info
    
    except Exception as e:
        error_msg = str(e)
        error_type = type(e).__name__
        
        logger.error(
            f"[Task {task_id}] 试卷提交失败: "
            f"错误类型={error_type}, 错误信息={error_msg}, "
            f"user={user_id}, record={record_uuid}",
            exc_info=True
        )
        
        task_info.update({
            'status': TaskStatus.FAILED,
            'progress': 0,
            'error': error_msg,
            'completed_at': timezone.now().isoformat()
        })
        cache.set(cache_key, task_info, timeout=300)
        
        return task_info
    
    finally:
        # 确保清理数据库连接
        try:
            close_old_connections()
        except Exception as e:
            logger.debug(f"[Task {task_id}] 清理数据库连接失败: {str(e)}")


from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.db import transaction
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import cache_page
from django.core.cache import cache
from django.contrib import messages
from comment.models import SystemNotification
from quiz.models import Quiz, Question, QuizRecord, Answer, Option, QuizRegistration
from quiz.utils import (
    RedisLock, rate_limit, verify_quiz_access,
    verify_answer_integrity, QueryOptimizer,
    log_security_event, get_client_ip
)
import json
import re
import logging


logger = logging.getLogger('apps.quiz')


def quiz_list(request):
    """竞赛列表页面（优化：添加分页和缓存）"""
    from django.core.paginator import Paginator
    from django.db.models import Q
    
    # 获取筛选参数
    status_filter = request.GET.get('status', '')
    keyword = request.GET.get('q', '').strip()
    
    # 构建缓存键
    cache_key = f'quiz_list_{status_filter}_{keyword}'
    cached_data = cache.get(cache_key)
    
    if cached_data and not keyword:  # 搜索时不使用缓存
        all_quizzes = cached_data
    else:
        # 排除已被CTF竞赛关联的知识竞赛
        quizzes = Quiz.objects.filter(
            is_active=True,
            related_competition__isnull=True  # 没有被任何竞赛关联
        ).select_related('creator')
        
        # 关键词搜索
        if keyword:
            quizzes = quizzes.filter(
                Q(title__icontains=keyword) | Q(description__icontains=keyword)
            )
        
        # 为所有竞赛添加状态标识
        now = timezone.now()
        all_quizzes = []
        
        for quiz in quizzes:
            # 检查竞赛时间并添加状态标识
            if quiz.start_time and quiz.end_time:
                if now < quiz.start_time:
                    quiz.display_status = 'upcoming'  # 未开始
                    all_quizzes.append(quiz)
                elif now <= quiz.end_time:
                    quiz.display_status = 'ongoing'  # 进行中
                    all_quizzes.append(quiz)
                else:
                    quiz.display_status = 'ended'  # 已结束
                    # 默认不显示已结束的，除非明确筛选
                    if status_filter == 'ended':
                        all_quizzes.append(quiz)
            elif not quiz.start_time and not quiz.end_time:
                # 没有时间限制的竞赛
                quiz.display_status = 'ongoing'  # 无限制
                all_quizzes.append(quiz)
        
        # 按状态筛选
        if status_filter:
            all_quizzes = [q for q in all_quizzes if q.display_status == status_filter]
        
        # 缓存 2 分钟（不包含搜索结果）
        if not keyword:
            cache.set(cache_key, all_quizzes, 120)
    
    # 分页（每页 12 个）
    paginator = Paginator(all_quizzes, 9)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    context = {
        'quizzes': page_obj,
        'page_obj': page_obj,
        'status_filter': status_filter,
        'keyword': keyword,
        'hide_footer': True,
    }
    return render(request, 'quiz/quiz_list.html', context)


@login_required
def quiz_detail(request, quiz_slug):
    """竞赛详情页面"""
    # 优化查询
    quiz = get_object_or_404(
        Quiz.objects.prefetch_related('quiz_questions'),
        slug=quiz_slug,
        is_active=True
    )
    
    # 检查竞赛状态
    now = timezone.now()
    quiz_status = 'unlimited'  # 默认无限制
    if quiz.start_time and quiz.end_time:
        if now < quiz.start_time:
            quiz_status = 'upcoming'  # 未开始
        elif now > quiz.end_time:
            quiz_status = 'ended'  # 已结束
    else:
        quiz_status = 'ongoing'  # 进行中
    
    # 使用索引优化的查询
    existing_record = QuizRecord.objects.filter(
        user=request.user,
        quiz=quiz,
        status='in_progress'
    ).select_related('quiz').first()
    
    # 获取用户的历史记录（限制数量）
    history_records = QuizRecord.objects.filter(
        user=request.user,
        quiz=quiz,
        status='completed'
    ).select_related('quiz').order_by('-submit_time')[:10]
    
    # 检查报名状态（使用缓存优化）
    registration = None
    if quiz.require_registration:
        cache_key = f'user_registration_{quiz.id}_{request.user.id}'
        registration = cache.get(cache_key)
        
        if registration is None:
            registration = QuizRegistration.objects.filter(
                quiz=quiz,
                user=request.user
            ).first()
            # 缓存 5 分钟
            cache.set(cache_key, registration if registration else False, 300)
        elif registration is False:
            registration = None
    
    is_registered = quiz.is_user_registered(request.user)
    
    # 检查答题次数限制
    can_attempt, attempt_message = quiz.can_user_attempt(request.user)
    
    # 检查是否可以开始答题（需要满足时间条件和次数限制）
    can_start = can_attempt and (quiz_status == 'ongoing' or quiz_status == 'unlimited')
    
    # 获取排行榜（使用缓存）
    leaderboard = quiz.get_leaderboard(limit=10) if quiz.show_leaderboard else []
    
    # 检查是否被CTF竞赛关联（只查询激活的、未结束的比赛）
    from competition.models import Competition
    related_competition = Competition.objects.filter(
        related_quiz=quiz,
    ).first()  # 按开始时间倒序，取最新的
    
    context = {
        'quiz': quiz,
        'quiz_status': quiz_status,
        'existing_record': existing_record,
        'history_records': history_records,
        'can_attempt': can_attempt,
        'can_start': can_start,
        'attempt_message': attempt_message,
        'leaderboard': leaderboard,
        'is_registered': is_registered,
        'registration': registration,
        'related_competition': related_competition,  # 关联的CTF竞赛
        'enable_anti_cheat': quiz.enable_anti_cheat,  # 是否启用防作弊
    }
    return render(request, 'quiz/quiz_detail.html', context)


@login_required
@rate_limit('start_quiz', max_requests=3, window=60)
def start_quiz(request, quiz_slug):
    """开始答题 - 使用分布式锁防止并发创建"""
    # 优化查询
    quiz = get_object_or_404(
        Quiz.objects.prefetch_related('quiz_questions__question'),
        slug=quiz_slug,
        is_active=True
    )
    
    # 检查是否被CTF竞赛关联（只查询激活的、未结束的比赛）
    from competition.models import Competition, Registration
    from django.utils import timezone
    now = timezone.now()
    related_competition = Competition.objects.filter(
        related_quiz=quiz
    ).first()  # 按开始时间倒序，取最新的
    
    if related_competition:
        # 管理员和比赛创建者可以直接访问
        is_admin = request.user.is_staff or request.user.is_superuser
        is_creator = related_competition.author == request.user
        
        if not is_admin and not is_creator:
            # 如果被关联，检查用户是否已报名CTF竞赛
            ctf_registration = Registration.objects.filter(
                competition=related_competition,
                user=request.user
            ).first()
            
            if not ctf_registration:
                messages.warning(
                    request,
                    f'"您没有权限访问该知识竞赛，请先报名'
                )
                return redirect('competition:competition_detail', slug=related_competition.slug)
    
    # 使用分布式锁防止并发创建多个记录
    lock_key = f"start_quiz:{quiz.id}:{request.user.id}"
    
    try:
        with RedisLock(lock_key, timeout=10):
            # 验证访问权限
            has_access, error_msg = verify_quiz_access(request.user, quiz)
            if not has_access:
                
                messages.error(request, error_msg)
                log_security_event('quiz_access_denied', request.user, {
                    'quiz': quiz.slug,
                    'reason': error_msg
                })
                return redirect('quiz:quiz_detail', quiz_slug=quiz.slug)
            
            # 检查是否已有进行中的记录（双重检查）
            existing_record = QuizRecord.objects.filter(
                user=request.user,
                quiz=quiz,
                status='in_progress'
            ).first()
            
            if existing_record:
                return redirect('quiz:quiz_answer', record_uuid=existing_record.uuid)
            
            # 使用事务创建记录
            with transaction.atomic():
                # 创建答题记录
                record = QuizRecord.objects.create(
                    user=request.user,
                    quiz=quiz,
                    status='in_progress'
                )
                
                # 批量创建答案记录
                questions = quiz.quiz_questions.select_related('question').all()
                answer_objects = [
                    Answer(record=record, question=qq.question)
                    for qq in questions
                ]
                Answer.objects.bulk_create(answer_objects)
                
                # 记录日志
                logger.info(f"User {request.user.username} started quiz {quiz.slug}")
                
                return redirect('quiz:quiz_answer', record_uuid=record.uuid)
    
    except Exception as e:
        logger.error(f"Error starting quiz: {e}")
    
        messages.error(request, '开始答题失败，请重试')
        return redirect('quiz:quiz_detail', quiz_slug=quiz.slug)


@login_required
def quiz_answer(request, record_uuid):
    """答题页面"""
    # 优化查询：一次性获取所有相关数据
    record = get_object_or_404(
        QuizRecord.objects.select_related('quiz', 'user'),
        uuid=record_uuid,
        user=request.user,
        status='in_progress'
    )
    
    # 检测设备类型（防作弊：只允许PC端答题）
    user_agent = request.META.get('HTTP_USER_AGENT', '')
    is_mobile = is_mobile_device(user_agent)
    
    if is_mobile:
        
        messages.error(request, '为保证考试公平性，仅支持使用电脑进行答题，请使用PC浏览器访问')
        return redirect('quiz:quiz_detail', quiz_slug=record.quiz.slug)
    
    # 保存设备信息（首次访问时）
    if not record.user_agent:
        record.user_agent = user_agent
        record.device_type = get_device_type(user_agent)
        record.save(update_fields=['user_agent', 'device_type'])
    
    # 检查是否超时
    elapsed_time = (timezone.now() - record.start_time).total_seconds()
    if elapsed_time > record.quiz.duration * 60:
        # 自动提交
        with transaction.atomic():
            for answer in record.answers.all():
                answer.check_and_save()
            record.calculate_score()
            record.status = 'timeout'
            record.submit_time = timezone.now()
            record.save()
            # 清除排行榜缓存
            record.quiz.clear_leaderboard_cache()
        return redirect('quiz:quiz_result', record_uuid=record.uuid)
    
    # 使用新的获取题目方法（支持随机顺序）
    quiz_questions = record.quiz.get_questions_for_user(request.user)
    
    answers = record.answers.prefetch_related('selected_options').all()
    
    # 构建题目和答案的映射
    answer_dict = {answer.question_id: answer for answer in answers}
    
    questions_data = []
    for qq in quiz_questions:
        question = qq.question
        answer = answer_dict.get(question.id)
        
        questions_data.append({
            'question': question,
            'options': question.options.all(),
            'answer': answer,
            'order': qq.order
        })
    
    remaining_time = max(0, record.quiz.duration * 60 - elapsed_time)
    
    # 调试日志
    logger.debug(
        f"[答题页面] user={request.user.username}, "
        f"quiz={record.quiz.slug}, "
        f"duration={record.quiz.duration}分钟, "
        f"elapsed={int(elapsed_time)}秒, "
        f"remaining={int(remaining_time)}秒"
    )
    
    context = {
        'record': record,
        'questions_data': questions_data,
        'remaining_time': int(remaining_time),
        'enable_anti_cheat': record.quiz.enable_anti_cheat
    }
    return render(request, 'quiz/quiz_answer.html', context)


@login_required
@require_http_methods(["GET"])
def get_answers(request, record_uuid):
    """获取已保存的答案（用于恢复）"""
    try:
        record = get_object_or_404(
            QuizRecord.objects.select_related('quiz'),
            uuid=record_uuid,
            user=request.user
        )
        
        # 获取所有答案
        answers = record.answers.prefetch_related('selected_options').select_related('question').all()
        
        # 构建答案字典
        answers_dict = {}
        for answer in answers:
            # 选择题和判断题：保存选项ID
            if answer.question.question_type in ['single', 'multiple', 'judge']:
                option_ids = list(answer.selected_options.values_list('id', flat=True))
                if option_ids:
                    answers_dict[str(answer.question_id)] = {
                        'optionIds': option_ids,
                        'timestamp': int(answer.created_at.timestamp() * 1000)
                    }
            # 填空题和简答题：保存文本答案
            elif answer.question.question_type in ['fill_blank', 'essay']:
                if answer.text_answer:
                    answers_dict[str(answer.question_id)] = {
                        'optionIds': [],
                        'textAnswer': answer.text_answer,
                        'timestamp': int(answer.created_at.timestamp() * 1000)
                    }
        
        return JsonResponse({
            'success': True,
            'answers': answers_dict,
            'count': len(answers_dict)
        })
        
    except Exception as e:
        logger.error(f"获取答案失败: {e}")
        return JsonResponse({
            'success': False,
            'message': '获取答案失败'
        }, status=500)


@login_required
@rate_limit('submit_quiz', max_requests=3, window=10)
@require_http_methods(["POST"])
def submit_quiz(request, record_uuid):
    """
    异步提交试卷 - 高性能、高安全性
    
    安全措施：
    1. @login_required - 必须登录
    2. @rate_limit - 限制提交频率（10秒内最多3次）
    3. 验证记录属于当前用户
    4. 验证记录状态为 in_progress
    5. 防止重复提交（待处理任务检查）
    6. 记录安全事件日志
    """
    
    # 判断是否为AJAX请求
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
              request.content_type == 'application/json'
    
    try:
        # ==================== 权限校验 ====================
        # 1. 验证记录存在且属于当前用户
        try:
            record = QuizRecord.objects.select_related('quiz').get(
                uuid=record_uuid,
                user=request.user,  # 关键：确保记录属于当前用户
                status='in_progress'  # 只能提交进行中的记录
            )
        except QuizRecord.DoesNotExist:
            # 检查是否是跨用户提交尝试（安全审计）
            other_user_record = QuizRecord.objects.filter(
                uuid=record_uuid,
                status='in_progress'
            ).select_related('user', 'quiz').first()
            
            if other_user_record:
                # 严重安全事件：尝试提交他人的答题记录
                logger.error(
                    f"[安全警告] 跨用户提交尝试: "
                    f"攻击者={request.user.username}(id={request.user.id}), "
                    f"目标记录={record_uuid}, "
                    f"记录所属={other_user_record.user.username}(id={other_user_record.user_id}), "
                    f"竞赛={other_user_record.quiz.slug}, "
                    f"IP={get_client_ip(request)}"
                )
                # 记录安全事件
                log_security_event(
                    'cross_user_submit_attempt',
                    request.user,
                    {
                        'target_record': str(record_uuid),
                        'target_user_id': other_user_record.user_id,
                        'quiz_slug': other_user_record.quiz.slug
                    }
                )
            else:
                # 普通的记录不存在
                logger.warning(
                    f"提交失败 - 记录不存在: user={request.user.username}, "
                    f"record_uuid={record_uuid}, ip={get_client_ip(request)}"
                )
            
            if is_ajax:
                return JsonResponse({
                    'success': False,
                    'error': '答题记录不存在或已提交'
                }, status=404)
            messages.error(request, '答题记录不存在或已提交')
            return redirect('quiz:my_records')
        
        # 2. 检查竞赛是否还允许提交
        if not record.quiz.is_active:
            logger.warning(
                f"提交失败 - 竞赛已关闭: user={request.user.username}, "
                f"quiz={record.quiz.slug}"
            )
            return JsonResponse({
                'success': False,
                'error': '竞赛已关闭，无法提交'
            }, status=403)
        
        # 3. 检查是否有待处理的任务（防止重复提交）
        pending_task_key = f"submit_quiz_task_user:{request.user.id}:{record_uuid}"
        existing_task_id = cache.get(pending_task_key)
        
        if existing_task_id:
            logger.info(
                f"返回现有提交任务: user={request.user.username}, "
                f"task_id={existing_task_id}"
            )
            return JsonResponse({
                'success': True,
                'task_id': existing_task_id,
                'message': '提交任务已在处理中，请勿重复提交'
            })
        
        # ==================== 创建异步提交任务 ====================
        from quiz.tasks import submit_quiz_async
        task = submit_quiz_async.delay(str(record_uuid), request.user.id)
        
        # 缓存任务ID（3分钟）- 关联用户ID和记录UUID
        cache.set(pending_task_key, task.id, timeout=180)
        
        # 缓存任务所属用户（用于后续权限验证）
        task_owner_key = f"submit_quiz_task_owner:{task.id}"
        cache.set(task_owner_key, request.user.id, timeout=300)
        
        # 记录安全事件
        log_security_event(
            'quiz_submit_async',
            request.user,
            {
                'quiz_slug': record.quiz.slug,
                'record_uuid': str(record_uuid),
                'task_id': task.id
            }
        )
        
        logger.info(
            f"创建异步提交任务: user={request.user.username}, "
            f"quiz={record.quiz.slug}, record={record_uuid}, task_id={task.id}"
        )
        
        return JsonResponse({
            'success': True,
            'task_id': task.id,
            'message': '提交任务已创建，正在处理中'
        })
    
    except Exception as e:
        logger.error(
            f"创建异步提交任务失败: user={request.user.username}, "
            f"record={record_uuid}, error={e}",
            exc_info=True
        )
        
        if is_ajax:
            return JsonResponse({
                'success': False,
                'error': '提交失败，请重试'
            }, status=500)
        
        messages.error(request, '提交失败，请重试')
        return redirect('quiz:quiz_answer', record_uuid=record_uuid)


@login_required
def quiz_result(request, record_uuid):
    """成绩查看页面"""
    # 优化查询
    record = get_object_or_404(
        QuizRecord.objects.select_related('quiz', 'user'),
        uuid=record_uuid,
        user=request.user
    )
    
    if record.status == 'in_progress':
        return redirect('quiz:quiz_answer', record_uuid=record.uuid)
    
    quiz = record.quiz
    
    # 优化查询：只在需要时加载答案详情
    answers = None
    if quiz.show_answer:
        answers = record.answers.select_related('question').prefetch_related(
            'selected_options',
            'question__options'
        ).all()
    
    # 使用聚合查询统计答题情况（性能优化）
    from django.db.models import Count, Q
    stats = record.answers.aggregate(
        total=Count('id'),
        correct=Count('id', filter=Q(is_correct=True))
    )
    
    total_questions = stats['total']
    correct_count = stats['correct']
    wrong_count = total_questions - correct_count
    accuracy = (correct_count / total_questions * 100) if total_questions > 0 else 0
    
    # 统计主观题批改情况
    subjective_stats = record.answers.filter(
        question__question_type__in=['fill_blank', 'essay']
    ).aggregate(
        total=Count('id'),
        pending=Count('id', filter=Q(manual_score__isnull=True))
    )
    has_subjective = subjective_stats['total'] > 0
    pending_subjective = subjective_stats['pending']
    
    # 判断是否及格
    is_passed = record.score >= quiz.pass_score if quiz.enable_pass_score else None
    
    context = {
        'record': record,
        'answers': answers,
        'total_questions': total_questions,
        'correct_count': correct_count,
        'wrong_count': wrong_count,
        'accuracy': accuracy,
        'is_passed': is_passed,
        'show_answer': quiz.show_answer,
        'enable_pass_score': quiz.enable_pass_score,
        'has_subjective': has_subjective,
        'pending_subjective': pending_subjective,
    }
    return render(request, 'quiz/quiz_result.html', context)


@login_required
def my_records(request):
    """我的答题记录"""
    # 优化查询：分页和限制数量
    from django.core.paginator import Paginator
    
    records = QuizRecord.objects.filter(
        user=request.user
    ).select_related('quiz').order_by('-start_time')
    
    # 分页（每页20条）
    paginator = Paginator(records, 20)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    context = {
        'records': page_obj,
        'page_obj': page_obj
    }
    return render(request, 'quiz/my_records.html', context)


def quiz_leaderboard(request, quiz_slug):
    """竞赛排行榜（使用缓存 + 分页）"""
    quiz = get_object_or_404(Quiz, slug=quiz_slug, is_active=True)
    
    if not quiz.show_leaderboard:
     
        messages.warning(request, '该竞赛未开启排行榜功能')
        return redirect('quiz:quiz_detail', quiz_slug=quiz.slug)
    
    # 分页参数
    page = request.GET.get('page', 1)
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    
    # 使用缓存的排行榜数据（获取全部数据）
    leaderboard = quiz.get_leaderboard(limit=None)  # 获取足够多的数据
    
    # 添加排名
    for index, item in enumerate(leaderboard, 1):
        item['rank'] = index
    
    # 分页处理（每页显示20条）
    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
    
    paginator = Paginator(leaderboard, 20)
    
    try:
        leaderboard_page = paginator.page(page)
    except PageNotAnInteger:
        leaderboard_page = paginator.page(1)
    except EmptyPage:
        leaderboard_page = paginator.page(paginator.num_pages)
    
    context = {
        'quiz': quiz,
        'leaderboard': list(leaderboard_page),
        'paginator': paginator,
        'page_obj': leaderboard_page,
    }
    return render(request, 'quiz/quiz_leaderboard.html', context)


@login_required
@rate_limit('batch_save_answers', max_requests=20, window=60)
@require_http_methods(["POST"])
def batch_save_answers(request, record_uuid):
    """批量保存答案（用于本地缓存同步）- 优化请求处理"""
    
    # 简化请求体读取逻辑
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError as e:
        logger.error(f"JSON解析失败: {e}")
        return JsonResponse({
            'success': False,
            'message': '请求数据格式错误'
        }, status=400)
    except Exception as e:
        logger.error(f"读取请求失败: {e}")
        return JsonResponse({
            'success': False,
            'message': '请求处理失败'
        }, status=500)
    
    try:
        record = get_object_or_404(
            QuizRecord.objects.select_related('quiz'),
            uuid=record_uuid,
            user=request.user,
            status='in_progress'
        )
        
        # 检查是否超时
        elapsed_time = (timezone.now() - record.start_time).total_seconds()
        if elapsed_time > record.quiz.duration * 60:
            return JsonResponse({
                'success': False,
                'message': '答题时间已结束',
                'timeout': True
            })
        
        # 获取答案数据
        answers_data = data.get('answers', {})
        
        # 安全检查：限制一次最多保存的答案数量
        if len(answers_data) > 200:  # 防止恶意大量请求
            logger.warning(f"批量保存答案超限: 用户 {request.user.username}, 答案数 {len(answers_data)}")
            return JsonResponse({
                'success': False,
                'message': '答案数量超出限制'
            }, status=400)
        
        if not answers_data:
            return JsonResponse({'success': False, 'message': '没有答案数据'})
        
        success_count = 0
        error_count = 0
        
        # 使用事务批量保存
        with transaction.atomic():
            for question_id, answer_info in answers_data.items():
                try:
                    question_id = int(question_id)
                    option_ids = answer_info.get('optionIds', [])
                    text_answer = answer_info.get('textAnswer', None)
                    
                    # 验证题目属于该竞赛
                    if not record.quiz.quiz_questions.filter(question_id=question_id).exists():
                        error_count += 1
                        continue
                    
                    question = Question.objects.get(id=question_id, is_active=True)
                    answer = Answer.objects.select_for_update().get(
                        record=record,
                        question=question
                    )
                    
                    # 保存文本答案（填空题和简答题）
                    if question.question_type in ['fill_blank', 'essay']:
                        answer.text_answer = text_answer
                        answer.save(update_fields=['text_answer'])
                    else:
                        # 清除并设置新选项（选择题和判断题）
                        answer.selected_options.clear()
                        if option_ids:
                            valid_options = Option.objects.filter(
                                id__in=option_ids,
                                question=question
                            )
                            answer.selected_options.set(valid_options)
                    
                    success_count += 1
                    
                except (ValueError, Question.DoesNotExist, Answer.DoesNotExist):
                    error_count += 1
                    continue
        
        logger.info(f"批量保存答案: 用户{request.user.username}, 成功{success_count}题, 失败{error_count}题")
        
        return JsonResponse({
            'success': True,
            'message': f'已保存 {success_count} 题答案',
            'saved_count': success_count,
            'error_count': error_count
        })
        
    except Exception as e:
        logger.error(f"批量保存答案失败: {e}")
        return JsonResponse({
            'success': False,
            'message': '保存失败，请重试'
        })


@login_required
@require_http_methods(["GET"])
@rate_limit('check_submit_task', max_requests=100, window=60)
def check_submit_task(request, task_id):
    """
    查询异步提交任务状态
    
    安全措施：
    1. @login_required - 必须登录
    2. @rate_limit - 防止恶意轮询（60秒内最多100次）
    3. 验证任务所属用户
    4. 敏感信息过滤
    """
    try:
        # ==================== 权限校验 ====================
        # 1. 验证任务所属用户
        task_owner_key = f"submit_quiz_task_owner:{task_id}"
        task_owner_id = cache.get(task_owner_key)
        
        if task_owner_id is None:
            logger.warning(
                f"查询任务失败 - 任务所属者信息不存在: user={request.user.username}, "
                f"task_id={task_id}, ip={get_client_ip(request)}"
            )
            return JsonResponse({
                'success': False,
                'error': '任务不存在或已过期'
            }, status=404)
        
        if task_owner_id != request.user.id:
            logger.warning(
                f"查询任务失败 - 无权访问他人任务: user={request.user.username}, "
                f"task_id={task_id}, task_owner_id={task_owner_id}, "
                f"ip={get_client_ip(request)}"
            )
            return JsonResponse({
                'success': False,
                'error': '无权访问此任务'
            }, status=403)
        
        # 2. 获取任务信息
        cache_key = f"submit_quiz_task:{task_id}"
        task_info = cache.get(cache_key)
        
        if not task_info:
            logger.info(
                f"查询任务失败 - 任务信息不存在: user={request.user.username}, "
                f"task_id={task_id}"
            )
            return JsonResponse({
                'success': False,
                'error': '任务不存在或已过期'
            }, status=404)
        
        # ==================== 返回任务状态 ====================
        # 过滤敏感信息，只返回必要字段
        safe_task_info = {
            'status': task_info.get('status'),
            'progress': task_info.get('progress'),
            'message': task_info.get('message'),
            'task_id': task_info.get('task_id')
        }
        
        # 任务完成时返回结果数据
        if task_info.get('status') == 'success':
            safe_task_info['data'] = task_info.get('data', {})
        
        # 任务失败时返回错误信息
        if task_info.get('status') == 'failed':
            safe_task_info['error'] = task_info.get('error', '未知错误')
        
        return JsonResponse({
            'success': True,
            'task_info': safe_task_info
        })
    
    except Exception as e:
        logger.error(
            f"查询任务状态失败: user={request.user.username}, "
            f"task_id={task_id}, error={e}",
            exc_info=True
        )
        return JsonResponse({
            'success': False,
            'error': '查询失败'
        }, status=500)


@login_required
@require_http_methods(["POST"])
@rate_limit('record_violation', max_requests=20, window=60)
def record_violation(request, record_uuid):
    """记录违规行为（AJAX接口）- 防止刷接口"""
    record = get_object_or_404(
        QuizRecord.objects.select_related('quiz'),
        uuid=record_uuid,
        user=request.user,
        status='in_progress'
    )
    
    try:
        data = json.loads(request.body)
        violation_type = data.get('type', '')
        violation_time = data.get('time', '')
        
        # 记录安全事件
        log_security_event('quiz_violation', request.user, {
            'quiz': record.quiz.title,
            'type': violation_type,
            'ip': get_client_ip(request)
        })
        
        # 记录违规日志
        violation_log = {
            'type': violation_type,
            'time': violation_time,
            'timestamp': timezone.now().isoformat()
        }
        
        # 更新违规记录
        with transaction.atomic():
            record = QuizRecord.objects.select_for_update().get(id=record.id)
            record.violation_count += 1
            
            # 初始化违规日志列表
            if not isinstance(record.violation_logs, list):
                record.violation_logs = []
            
            record.violation_logs.append(violation_log)
            record.save(update_fields=['violation_count', 'violation_logs'])
            
            # 从Quiz配置中获取最大违规次数（默认5次）
            max_violations = getattr(record.quiz, 'max_violations', 5)
            
            # 检查违规次数，超过最大次数自动提交并标记作弊
            if record.violation_count >= max_violations:
                # 检查所有答案
                for answer in record.answers.all():
                    answer.check_and_save()
                
                # 计算分数
                record.calculate_score()
                record.status = 'cheating'
                record.submit_time = timezone.now()
                record.save()
                
                # 清除排行榜缓存
                record.quiz.clear_leaderboard_cache()
                
                return JsonResponse({
                    'success': True,
                    'force_submit': True,
                    'message': '违规次数过多，试卷已自动提交'
                })
        
        return JsonResponse({
            'success': True,
            'violation_count': record.violation_count,
            'max_violations': max_violations,
            'message': f'已记录违规行为，当前违规次数：{record.violation_count}/{max_violations}'
        })
        
    except Exception as e:
        import logging
        logging.error(f"记录违规失败: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': '记录失败'
        })


def is_mobile_device(user_agent):
    """检测是否为移动设备"""
    mobile_patterns = [
        r'Android', r'iPhone', r'iPad', r'iPod',
        r'Windows Phone', r'Mobile', r'Tablet',
        r'BlackBerry', r'IEMobile', r'Opera Mini'
    ]
    
    for pattern in mobile_patterns:
        if re.search(pattern, user_agent, re.IGNORECASE):
            return True
    return False


def get_device_type(user_agent):
    """获取设备类型"""
    if is_mobile_device(user_agent):
        if re.search(r'iPad|Tablet', user_agent, re.IGNORECASE):
            return 'tablet'
        return 'mobile'
    return 'desktop'


@login_required
@rate_limit('get_register_captcha', max_requests=10, window=60)
def get_register_captcha(request, quiz_slug):
    """获取报名验证码（限制：10次/分钟）"""
    quiz = get_object_or_404(Quiz, slug=quiz_slug, is_active=True)
    
    if not quiz.require_registration:
        return JsonResponse({'success': False, 'message': '该竞赛无需报名'})
    
    from public.utils import generate_captcha, generate_captcha_image
    import uuid
    
    captcha_text = generate_captcha()
    captcha_image = generate_captcha_image(captcha_text)
    captcha_key = str(uuid.uuid4())
    
    # 存储到 Redis，5分钟过期
    cache.set(f'quiz_register_captcha_{captcha_key}', captcha_text, 300)
    
    return JsonResponse({
        'success': True,
        'captcha_key': captcha_key,
        'captcha_image': captcha_image
    })


@login_required
@rate_limit('quiz_register', max_requests=10, window=60)
def quiz_register(request, quiz_slug):
    """竞赛报名（限制：10次/分钟）"""
    quiz = get_object_or_404(
        Quiz.objects.prefetch_related('quiz_questions'),
        slug=quiz_slug,
        is_active=True
    )
    
    # 检查是否被CTF竞赛关联（只查询激活的、未结束的比赛）
    from competition.models import Competition
    from django.utils import timezone
    now = timezone.now()
    related_competition = Competition.objects.filter(
        related_quiz=quiz
    ).first()
    if related_competition:
        messages.info(
            request, 
            f'该知识竞赛已关联到CTF竞赛"{related_competition.title}"，请前往CTF竞赛页面报名。'
        )
        return redirect('quiz:quiz_detail', quiz_slug=quiz.slug)
    
    # 检查竞赛是否需要报名
    if not quiz.require_registration:
        
        messages.info(request, '该竞赛无需报名，可直接参加')
        return redirect('quiz:quiz_detail', quiz_slug=quiz.slug)
    
    # 检查是否已经报名
    existing_registration = QuizRegistration.objects.filter(
        quiz=quiz,
        user=request.user
    ).first()
    
    if existing_registration:
        
        if existing_registration.status == 'approved':
            messages.info(request, '您已成功报名该竞赛')
        elif existing_registration.status == 'pending':
            messages.info(request, '您的报名正在审核中，请耐心等待')
        else:
            messages.warning(request, '您的报名已被拒绝，如有疑问请联系管理员')
        return redirect('quiz:quiz_detail', quiz_slug=quiz.slug)
    
    # 检查用户信息是否完整（必需字段）
    user = request.user
    missing_fields = []
    
    # 只检查关键信息
    if not hasattr(user, 'real_name') or not user.real_name:
        missing_fields.append('真实姓名')
    if not hasattr(user, 'phones') or not user.phones:
        missing_fields.append('联系方式')
    
    if missing_fields:
        
        messages.warning(request, f'报名需要完善个人信息：{", ".join(missing_fields)}，请前往个人中心完善后再报名')
        return redirect('quiz:quiz_detail', quiz_slug=quiz.slug)
    
    if request.method == 'POST':
        # 验证验证码
        captcha_key = request.POST.get('captcha_key')
        captcha_value = request.POST.get('captcha_value', '').strip().lower()
        
        if not captcha_key or not captcha_value:
            
            messages.error(request, '请输入验证码')
            return redirect('quiz:quiz_register', quiz_slug=quiz.slug)
        
        cached_captcha = cache.get(f'quiz_register_captcha_{captcha_key}')
        if not cached_captcha or cached_captcha.lower() != captcha_value:
            
            messages.error(request, '验证码错误或已过期，请重新输入')
            return redirect('quiz:quiz_register', quiz_slug=quiz.slug)
        
        # 删除已使用的验证码
        cache.delete(f'quiz_register_captcha_{captcha_key}')
        
        try:
            # 使用分布式锁防止重复报名
            lock_key = f"quiz_register:{quiz.id}:{request.user.id}"
            
            try:
                with RedisLock(lock_key, timeout=5):
                    # 双重检查是否已报名
                    if QuizRegistration.objects.filter(quiz=quiz, user=request.user).exists():
                        
                        messages.warning(request, '您已经报名过该竞赛')
                        return redirect('quiz:quiz_detail', quiz_slug=quiz.slug)
                    
                    with transaction.atomic():
                        # 根据竞赛设置决定报名状态
                        initial_status = 'pending' if quiz.require_approval else 'approved'
                        
                        # 创建报名记录
                        registration = QuizRegistration.objects.create(
                            quiz=quiz,
                            user=request.user,
                            status=initial_status
                        )
                        
                        # 清除相关缓存
                        cache.delete(f'user_registration_{quiz.id}_{request.user.id}')
                        cache.delete(f'quiz_registration_stats_{quiz.id}')
                        
                        logger.info(
                            f"User registration: user={request.user.username}, "
                            f"quiz={quiz.slug}, status={initial_status}, "
                            f"ip={get_client_ip(request)}"
                        )
                        
                       
                        if initial_status == 'approved':
                            messages.success(request, '🎉 报名成功！您现在可以参加竞赛了')
                        else:
                            messages.info(request, '✅ 报名提交成功！请等待管理员审核，审核通过后将通知您')
                        return redirect('quiz:quiz_detail', quiz_slug=quiz.slug)
            
            except Exception as lock_error:
                logger.error(f"获取锁失败: {lock_error}")
               
                messages.error(request, '系统繁忙，请稍后重试')
                return redirect('quiz:quiz_detail', quiz_slug=quiz.slug)
        
        except Exception as e:
            logger.error(f"竞赛报名失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
           
            messages.error(request, '报名失败，请重试或联系管理员')
            return redirect('quiz:quiz_detail', quiz_slug=quiz.slug)
    
    # GET 请求：显示报名确认页面
    context = {
        'quiz': quiz,
    }
    return render(request, 'quiz/quiz_register.html', context)


@login_required
def my_registrations(request):
    """我的报名记录"""
    from django.core.paginator import Paginator
    
    registrations = QuizRegistration.objects.filter(
        user=request.user
    ).select_related('quiz').order_by('-created_at')
    
    # 分页（每页20条）
    paginator = Paginator(registrations, 20)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    context = {
        'registrations': page_obj,
        'page_obj': page_obj
    }
    return render(request, 'quiz/my_registrations.html', context)


# ==================== 阅卷管理 ====================

@login_required
def quiz_grading(request, slug):
    """阅卷管理主页 - 下一题模式"""
    from django.db.models import Q
    from django.utils import timezone
    from datetime import timedelta
    
    quiz = get_object_or_404(Quiz, slug=slug)
    
    # 权限检查：创建者或阅卷人
    if not quiz.can_user_grade(request.user):
        messages.error(request, '您没有权限访问此页面')
        return redirect('quiz:quiz_detail', slug=slug)
    
    # 清理过期的锁定（超过30分钟自动释放）
    expired_time = timezone.now() - timedelta(minutes=30)
    Answer.objects.filter(
        record__quiz=quiz,
        locked_at__lt=expired_time
    ).update(locked_by=None, locked_at=None)
    
    # 检查是否要跳过某题
    skip_id = request.GET.get('skip')
    if skip_id:
        try:
            skip_answer = Answer.objects.get(id=skip_id, locked_by=request.user)
            skip_answer.unlock()
        except Answer.DoesNotExist:
            pass
    
    # 获取下一个待批改的答案
    current_answer = Answer.objects.filter(
        record__quiz=quiz,
        record__status__in=['completed', 'timeout'],
        question__question_type__in=['fill_blank', 'essay'],
        manual_score__isnull=True
    ).filter(
        Q(locked_by=None) | Q(locked_by=request.user)
    ).select_related(
        'question', 'record', 'record__user'
    ).order_by('record__submit_time').first()
    
    # 如果找到答案，自动锁定
    if current_answer and current_answer.locked_by != request.user:
        current_answer.lock_for_grading(request.user)
    
    # 获取已批改的答案（最近20条）
    # 管理员和竞赛创建者可以看到所有批改记录，普通批改人只能看到自己批改的
    graded_answers_query = Answer.objects.filter(
        record__quiz=quiz,
        record__status__in=['completed', 'timeout'],
        question__question_type__in=['fill_blank', 'essay'],
        manual_score__isnull=False
    )
    
    # 如果不是管理员且不是竞赛创建者，只显示自己批改的
    if not request.user.is_staff and quiz.creator != request.user:
        graded_answers_query = graded_answers_query.filter(reviewer=request.user)
    
    graded_answers = graded_answers_query.select_related(
        'question', 'record', 'reviewer'
    ).order_by('-reviewed_at')[:20]
    
    # 统计信息
    total_count = Answer.objects.filter(
        record__quiz=quiz,
        record__status__in=['completed', 'timeout'],
        question__question_type__in=['fill_blank', 'essay']
    ).count()
    
    graded_count = Answer.objects.filter(
        record__quiz=quiz,
        record__status__in=['completed', 'timeout'],
        question__question_type__in=['fill_blank', 'essay'],
        manual_score__isnull=False
    ).count()
    
    pending_count = total_count - graded_count
    progress = int((graded_count / total_count * 100)) if total_count > 0 else 100
    
    # 获取阅卷人列表
    graders = quiz.get_graders()
    
    # 统计每个批改人的工作量（仅管理员和创建者可见）
    grader_stats = []
    if request.user.is_staff or quiz.creator == request.user:
        from django.db.models import Count
        
        # 获取所有批改人（包括创建者），使用集合去重
        grader_users_set = {quiz.creator}
        grader_users_set.update([g.grader for g in graders])
        
        for grader_user in grader_users_set:
            graded_by_user = Answer.objects.filter(
                record__quiz=quiz,
                record__status__in=['completed', 'timeout'],
                question__question_type__in=['fill_blank', 'essay'],
                manual_score__isnull=False,
                reviewer=grader_user
            ).count()
            
            pending_by_user = Answer.objects.filter(
                record__quiz=quiz,
                record__status__in=['completed', 'timeout'],
                question__question_type__in=['fill_blank', 'essay'],
                manual_score__isnull=True,
                locked_by=grader_user
            ).count()
            
            if graded_by_user > 0 or pending_by_user > 0:
                grader_stats.append({
                    'username': grader_user.username,
                    'graded_count': graded_by_user,
                    'pending_count': pending_by_user,
                    'total': graded_by_user + pending_by_user,
                    'is_creator': grader_user == quiz.creator
                })
        
        # 按已批改数量倒序排列
        grader_stats.sort(key=lambda x: x['graded_count'], reverse=True)
    
    context = {
        'quiz': quiz,
        'current_answer': current_answer,
        'graded_answers': graded_answers,
        'total_count': total_count,
        'pending_count': pending_count,
        'graded_count': graded_count,
        'progress': progress,
        'graders': graders,
        'grader_stats': grader_stats,
    }
    
    return render(request, 'quiz/creator/grading.html', context)



@login_required
def submit_grading(request, answer_id):
    """提交评分"""
    if request.method != 'POST':
        return redirect('quiz:my_quizzes')
    
    answer = get_object_or_404(Answer, id=answer_id)
    quiz = answer.record.quiz
    
    # 权限检查
    if not quiz.can_user_grade(request.user):
        messages.error(request, '您没有权限批改此答卷')
        return redirect('quiz:quiz_detail', slug=quiz.slug)
    
    try:
        score = float(request.POST.get('score', 0))
        comment = request.POST.get('comment', '').strip()
        
        # 调用模型的批改方法
        answer.manual_review(score, request.user, comment)
        
        messages.success(request, '评分提交成功！')
    except ValueError as e:
        messages.error(request, f'评分失败：{str(e)}')
    except Exception as e:
        messages.error(request, f'评分失败：{str(e)}')
    
    return redirect('quiz:quiz_grading', slug=quiz.slug)

@login_required
def add_grader(request, slug):
    """添加阅卷人"""
    if request.method != 'POST':
        return redirect('quiz:quiz_grading', slug=slug)
    
    quiz = get_object_or_404(Quiz, slug=slug)
    
    # 权限检查：只有创建者可以添加阅卷人
    if quiz.creator != request.user:
        messages.error(request, '只有竞赛创建者可以添加阅卷人')
        return redirect('quiz:quiz_grading', slug=slug)
    
    username = request.POST.get('username', '').strip()
    if not username:
        messages.error(request, '请输入用户名')
        return redirect('quiz:quiz_grading', slug=slug)
    
    try:
        from django.contrib.auth import get_user_model
        from django.utils.html import escape
        User = get_user_model()
        grader_user = User.objects.get(username=username)
        
        # 检查是否已存在
        from .models import QuizGrader
        if QuizGrader.objects.filter(quiz=quiz, grader=grader_user).exists():
            messages.warning(request, '该用户已经是阅卷人')
        else:
            QuizGrader.objects.create(
                quiz=quiz,
                grader=grader_user,
                added_by=request.user
            )
            
            # 给阅卷人发送系统通知
            notification = SystemNotification.objects.create(
                title='您被添加为阅卷人',
                content=f'''
                    <p>您已被添加为竞赛 <strong>{escape(quiz.title)}</strong> 的阅卷人</p>
                    <p>添加人：{escape(request.user.username)}</p>
                    <p>您可以前往 <a href="/quiz/manage/{quiz.slug}/grading/" class="text-primary">批改页面</a> 开始批改主观题。</p>
                    <p>感谢您的参与！</p>
                '''
            )
            notification.get_p.add(grader_user)
            
            messages.success(request, f'成功添加阅卷人：{username}')
    except User.DoesNotExist:
        messages.error(request, f'用户不存在：{username}')
    except Exception as e:
        messages.error(request, f'添加失败：{str(e)}')
    
    return redirect('quiz:quiz_grading', slug=slug)


@login_required
def remove_grader(request, grader_id):
    """移除阅卷人"""
    if request.method != 'POST':
        return redirect('quiz:my_quizzes')
    
    from .models import QuizGrader
    grader = get_object_or_404(QuizGrader, id=grader_id)
    quiz = grader.quiz
    
    # 权限检查：只有创建者可以移除阅卷人
    if quiz.creator != request.user:
        messages.error(request, '只有竞赛创建者可以移除阅卷人')
        return redirect('quiz:quiz_grading', slug=quiz.slug)
    
    grader.delete()
    messages.success(request, '已移除阅卷人')
    
    return redirect('quiz:quiz_grading', slug=quiz.slug)


@login_required
def lock_answer(request, answer_id):
    """锁定答案开始批改"""
    if request.method != 'POST':
        from django.http import JsonResponse
        return JsonResponse({'success': False, 'error': '请求方法错误'})
    
    answer = get_object_or_404(Answer, id=answer_id)
    quiz = answer.record.quiz
    
    # 权限检查
    if not quiz.can_user_grade(request.user):
        from django.http import JsonResponse
        return JsonResponse({'success': False, 'error': '没有权限'})
    
    success, message = answer.lock_for_grading(request.user)
    
    from django.http import JsonResponse
    return JsonResponse({
        'success': success,
        'message': message
    })


@login_required
def unlock_answer(request, answer_id):
    """解锁答案（放弃批改）"""
    if request.method != 'POST':
        from django.http import JsonResponse
        return JsonResponse({'success': False, 'error': '请求方法错误'})
    
    answer = get_object_or_404(Answer, id=answer_id)
    quiz = answer.record.quiz
    
    # 权限检查
    if not quiz.can_user_grade(request.user):
        from django.http import JsonResponse
        return JsonResponse({'success': False, 'error': '没有权限'})
    
    # 只能解锁自己锁定的
    if answer.locked_by != request.user:
        from django.http import JsonResponse
        return JsonResponse({'success': False, 'error': '不能解锁其他人的锁定'})
    
    answer.unlock()
    
    from django.http import JsonResponse
    return JsonResponse({
        'success': True,
        'message': '已解锁'
    })


@login_required
def answer_detail(request, answer_id):
    """获取答案详情（AJAX）"""
    answer = get_object_or_404(Answer, id=answer_id)
    quiz = answer.record.quiz
    
    # 权限检查
    if not quiz.can_user_grade(request.user):
        return JsonResponse({
            'success': False,
            'message': '您没有权限查看此答案'
        })
    
    # 构建响应数据
    data = {
        'id': answer.id,
        'record_id': answer.record.id,
        'question_type': answer.question.question_type,
        'question_content': answer.question.content,
        'question_score': float(answer.question.score) if answer.question.score else 0,
        'text_answer': answer.text_answer or '',
        'standard_answer': answer.question.standard_answer or '',
        'manual_score': float(answer.manual_score) if answer.manual_score is not None else 0,
        'review_comment': answer.review_comment or '',
        'reviewed_at': answer.reviewed_at.strftime('%Y-%m-%d %H:%M:%S') if answer.reviewed_at else '',
    }
    
    return JsonResponse({
        'success': True,
        'data': data
    })


@login_required
@require_http_methods(["POST"])
def edit_grading(request, answer_id):
    """修改评分（AJAX）"""
    answer = get_object_or_404(Answer, id=answer_id)
    quiz = answer.record.quiz
    
    # 权限检查
    if not quiz.can_user_grade(request.user):
        return JsonResponse({
            'success': False,
            'message': '您没有权限修改此评分'
        })
    
    try:
        score = float(request.POST.get('score', 0))
        comment = request.POST.get('comment', '').strip()
        
        # 验证分数范围
        if score < 0 or score > answer.question.score:
            return JsonResponse({
                'success': False,
                'message': f'分数必须在0到{answer.question.score}之间'
            })
        
        # 更新评分
        answer.manual_score = score
        answer.review_comment = comment
        answer.reviewer = request.user
        answer.reviewed_at = timezone.now()
        answer.save()
        
        # 重新计算总分
        answer.record.calculate_score()
        
        return JsonResponse({
            'success': True,
            'message': '评分修改成功'
        })
        
    except ValueError:
        return JsonResponse({
            'success': False,
            'message': '分数格式错误'
        })
    except Exception as e:
        logger.error(f'修改评分失败: {str(e)}')
        return JsonResponse({
            'success': False,
            'message': f'修改失败：{str(e)}'
        })
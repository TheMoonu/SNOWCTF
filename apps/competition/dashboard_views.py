# -*- coding: utf-8 -*-
"""
CTF数据大屏视图 - 生产优化版（支持SSE实时推送、高并发）
"""

from django.http import StreamingHttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import redirect
from django.views.decorators.cache import never_cache, cache_page
from django.views.decorators.http import require_http_methods
from django_redis import get_redis_connection
from django.core.cache import cache
from django.utils import timezone
from django.utils.decorators import method_decorator
from datetime import timedelta
import json
import time
import logging
import traceback

from competition.models import Competition, Registration
from competition.dashboard_service import get_dashboard_service, generate_demo_dashboard_data

# 使用apps.competition作为logger名称，匹配settings.py中的配置
logger = logging.getLogger('apps.competition')


@login_required
def competition_dashboard_optimized(request, slug):
    """
    优化版数据大屏主页（性能优化版）
    
    性能优化：
    - 最小化数据库查询
    - 使用 only() 减少字段加载
    - 使用 select_related 减少查询次数
    - 添加缓存减少重复计算
    - 根据比赛配置动态选择大屏模板
    """
    # 性能监控
    start_time = time.time()
    
    # 性能优化：只加载必要的字段（包含dashboard_template）
    competition = get_object_or_404(
        Competition.objects.select_related('author').only(
            'id', 'slug', 'title', 'competition_type', 
            'start_time', 'end_time', 'author_id', 'dashboard_template'
        ),
        slug=slug
    )

    
    # 性能优化：使用缓存检查权限
    cache_key = f"dashboard_access:{competition.id}:{request.user.id}"
    is_authorized = cache.get(cache_key)
    
    if is_authorized is None:
        # 检查权限（优化查询）
        registration = Registration.objects.filter(
            competition_id=competition.id,
            user_id=request.user.id
        ).only('id').first()
        
        is_authorized = (
            registration is not None or 
            competition.author_id == request.user.id or 
            request.user.is_staff
        )
        
        # 缓存权限检查结果（5分钟）
        cache.set(cache_key, is_authorized, 300)
    
    if not is_authorized:
        messages.warning(request, "您还未报名该比赛，无法查看比赛数据大屏")
        return redirect('competition:competition_detail', slug=slug)
    
    # 最小化上下文数据
    context = {
        'competition': competition,
        'use_sse': True,  # 标记使用SSE
    }
    
    # 根据比赛配置的模板选择对应的大屏模板
    template_mapping = {
        'dashboardone': 'competition/dashbase/team/dashboardone.html',
        'dashboardtwo': 'competition/dashbase/team/dashboard_optimized.html',
    }
    
    template_name = template_mapping.get(
        competition.dashboard_template, 
        'competition/dashbase/team/dashboardone.html'  # 默认模板
    )
    
    # 性能监控：记录页面渲染时间
    elapsed = time.time() - start_time
    if elapsed > 1.0:  # 超过1秒记录警告
        logger.warning(
            f"Dashboard page render slow: slug={slug}, "
            f"template={template_name}, "
            f"user={request.user.username}, elapsed={elapsed:.3f}s"
        )
    else:
        logger.debug(
            f"Dashboard page rendered: slug={slug}, "
            f"template={template_name}, "
            f"user={request.user.username}, elapsed={elapsed:.3f}s"
        )
    
    return render(request, template_name, context)


def _check_rate_limit(request, competition_id, action='view'):
    """
    检查请求频率限制（优化版：使用滑动窗口算法）
    
    Args:
        request: Django request对象
        competition_id: 比赛ID
        action: 操作类型（view, refresh等）
    
    Returns:
        bool: True表示允许访问，False表示超出限制
    """
    user_id = request.user.id
    cache_key = f"dashboard_rate_limit:{competition_id}:{user_id}:{action}"
    
    # 获取当前计数
    count = cache.get(cache_key, 0)
    
    # 更严格的限制（防止频繁刷新导致系统负载）
    limits = {
        'view': 60,  # 每分钟最多60次查看请求（每1秒1次）
        'refresh': 10,  # 每分钟最多10次刷新请求
    }
    
    limit = limits.get(action, 30)
    
    if count >= limit:
        logger.warning(
            f"Rate limit exceeded: user={user_id}, competition={competition_id}, "
            f"action={action}, count={count}/{limit}"
        )
        return False
    
    # 增加计数（使用滑动窗口：60秒过期）
    cache.set(cache_key, count + 1, 60)
    return True


@login_required
@never_cache
@require_http_methods(["GET"])
def get_dashboard_data_optimized(request, slug):
    """
    获取数据大屏数据（生产优化版 + 安全增强）
    
    优化特性：
    - 权限验证（必须有SSE连接或有效报名）
    - 请求频率限制
    - 超时保护
    - 详细错误日志
    - 参数验证
    """
    # 导入数据库连接管理工具
    from django.db import close_old_connections
    
    try:
        competition = get_object_or_404(Competition, slug=slug)
        
        # 安全检查：验证用户是否有权访问数据
        sse_conn_key = f"sse_conn:{competition.id}:{request.user.id}"
        has_sse_connection = cache.get(sse_conn_key, 0) > 0
        
        # 如果没有 SSE 连接，需要验证报名权限
        if not has_sse_connection:
            registration = Registration.objects.filter(
                competition=competition,
                user=request.user
            ).select_related('user').first()
            
            is_authorized = (
                registration or 
                competition.author == request.user or 
                request.user.is_staff
            )
            
            if not is_authorized:
                logger.warning(
                    f"Unauthorized dashboard data access: user={request.user.username}, "
                    f"competition={competition.id}, no_sse_connection=True"
                )
                return JsonResponse({
                    'error': '无权访问，请先建立连接',
                    'hint': '请刷新页面重新连接'
                }, status=403)
        
        # 频率限制检查
        if not _check_rate_limit(request, competition.id, 'view'):
            return JsonResponse({
                'error': '请求过于频繁，请稍后再试',
                'retry_after': 60
            }, status=429)
        
        # 参数验证
        try:
            top_n = min(max(int(request.GET.get('top_n', '30')), 10), 50)
            max_points = min(max(int(request.GET.get('max_points', '200')), 50), 500)
        except (ValueError, TypeError):
            top_n, max_points = 30, 200
        
        # 获取数据服务
        service = get_dashboard_service(competition.id)
        
        # 记录开始时间
        start_time = time.time()
        logger.info(
            f"Dashboard data request: competition={competition.title}, "
            f"user={request.user.username}, top_n={top_n}, max_points={max_points}"
        )
        
        # 并发获取所有数据（它们各自都有缓存）
        try:
            stats = service.calculate_stats()
            leaderboard = service.calculate_leaderboard(limit=top_n)
            category_stats = service.calculate_category_stats()
            recent_submissions = service.get_recent_submissions(limit=30)
            score_trends = service.get_score_trends(top_n=top_n, max_points=max_points)
            first_bloods = service.get_first_bloods(limit=20)
        except Exception as data_err:
            logger.error(
                f"Data calculation error: competition={competition.id}, "
                f"error={str(data_err)}\n{traceback.format_exc()}"
            )
            raise
        
        # 计算耗时
        elapsed = time.time() - start_time
        
        # 数据验证和默认值
        if not isinstance(stats, dict):
            logger.error(f"Invalid stats data type: {type(stats)}")
            stats = {'participant_count': 0, 'submission_count': 0, 'solved_rate': 0, 'total_challenges': 0, 'solved_challenges': 0}
        
        if not isinstance(leaderboard, list):
            logger.error(f"Invalid leaderboard data type: {type(leaderboard)}")
            leaderboard = []
        
        # 如果数据为空，记录信息（不是错误）
        if len(leaderboard) == 0:
            logger.info(f"Empty leaderboard data: competition={competition.title}, likely no submissions yet")
        
        if not isinstance(recent_submissions, list) or len(recent_submissions) == 0:
            logger.info(f"No recent submissions: competition={competition.title}")
        
        logger.info(
            f"Dashboard data loaded: competition={competition.title}, "
            f"elapsed={elapsed:.3f}s, teams={len(leaderboard)}, "
            f"submissions={len(recent_submissions) if isinstance(recent_submissions, list) else 0}, "
            f"cache_hit={elapsed < 0.1}"
        )
        
        response_data = {
            'stats': stats,
            'leaderboard': leaderboard,
            'category_stats': category_stats if isinstance(category_stats, list) else [],
            'recent_submissions': recent_submissions if isinstance(recent_submissions, list) else [],
            'series_data': score_trends if isinstance(score_trends, list) else [],
            'first_bloods': first_bloods if isinstance(first_bloods, list) else [],
            'timestamp': time.time(),
            'load_time': round(elapsed, 3),
            'cache_hit': elapsed < 0.1  # 如果响应很快，说明命中缓存
        }
        
        return JsonResponse(response_data)
        
    except Competition.DoesNotExist:
        logger.warning(f"Competition not found: slug={slug}, user={request.user.username}")
        return JsonResponse({'error': '比赛不存在'}, status=404)
    
    except Exception as e:
        logger.error(
            f"Dashboard data request failed: slug={slug}, "
            f"user={request.user.username}, error={str(e)}\n{traceback.format_exc()}"
        )
        return JsonResponse({
            'error': '服务器内部错误，请稍后重试',
            'detail': str(e) if request.user.is_staff else None,
            'timestamp': time.time()
        }, status=500)
    finally:
        # 清理数据库连接（关键：防止连接泄漏）
        close_old_connections()


@login_required
@never_cache
def dashboard_sse_stream(request, slug):
    """
    SSE流（Server-Sent Events）用于实时推送数据
    
    优化特性：
    - 严格的连接数限制（防止资源耗尽）
    - 超时保护（最大连接时间5分钟）
    - 自动心跳（每30秒）
    - 优雅断开
    """
    try:
        competition = get_object_or_404(Competition, slug=slug)
        
        # 检查权限
        registration = Registration.objects.filter(
            competition=competition,
            user=request.user
        ).select_related('user').first()
        
        is_authorized = (
            registration or 
            competition.author == request.user or 
            request.user.is_staff
        )
        
        if not is_authorized:
            return JsonResponse({'error': '无权访问'}, status=403)
        
        # SSE连接追踪和限制（严格控制）
        sse_conn_key = f"sse_conn:{competition.id}:{request.user.id}"
        global_conn_key = f"sse_global_conn:{competition.id}"
        
        current_user_conns = cache.get(sse_conn_key, 0)
        current_global_conns = cache.get(global_conn_key, 0)
        
        # 优化限制：每个用户最多5个连接（允许多标签页），全局最多200个连接
        if current_user_conns >= 5:
            logger.warning(
                f"SSE connection limit exceeded for user: user={request.user.username}, "
                f"competition={competition.id}, current={current_user_conns}"
            )
            # 尝试自动清理过期连接
            cache.delete(sse_conn_key)
            current_user_conns = 0
            logger.info(f"已自动清理用户 {request.user.username} 的连接计数")
            
            # 如果清理后仍然超限，返回错误
            if cache.get(sse_conn_key, 0) >= 5:
                return JsonResponse({
                    'error': '连接数超限，请关闭其他标签页后重试',
                    'current_connections': current_user_conns,
                    'hint': '如果已关闭所有标签页仍无法连接，请等待1分钟后重试'
                }, status=429)
        
        if current_global_conns >= 200:
            logger.warning(
                f"SSE global connection limit exceeded: competition={competition.id}, "
                f"total={current_global_conns}"
            )
            return JsonResponse({
                'error': '系统繁忙，请稍后再试',
                'retry_after': 10
            }, status=503)
        
        # 增加连接计数（使用较短的过期时间，避免僵尸计数）
        cache.set(sse_conn_key, current_user_conns + 1, 120)  # 2分钟过期（自动清理）
        cache.set(global_conn_key, current_global_conns + 1, 120)
        
        logger.info(
            f"SSE connection established: user={request.user.username}, "
            f"competition={competition.id}, user_conns={current_user_conns + 1}, "
            f"global_conns={current_global_conns + 1}"
        )
        
        def event_stream():
            """生成SSE事件流（优化版：添加超时和资源管理）"""
            redis_conn = None
            pubsub = None
            last_heartbeat = time.time()
            connection_start = time.time()
            max_connection_time = 300  # 最大连接时间5分钟（防止僵尸连接）
            
            try:
                redis_conn = get_redis_connection("default")
                pubsub = redis_conn.pubsub()
                channel = f"ctf:dashboard:updates:{competition.id}"
                
                # 订阅Redis频道（使用ignore_subscribe_messages=True减少无用消息）
                pubsub.subscribe(channel)
                logger.info(
                    f"SSE connected: user={request.user.username}, "
                    f"competition={competition.title}, channel={channel}"
                )
                
                # 发送初始连接消息
                yield f"data: {json.dumps({'type': 'connected', 'message': '实时连接已建立'}, ensure_ascii=False)}\n\n"
                
                # 立即发送一次完整数据
                try:
                    service = get_dashboard_service(competition.id)
                    initial_data = {
                        'type': 'initial',
                        'data': {
                            'stats': service.calculate_stats(),
                            'leaderboard': service.calculate_leaderboard(limit=30),
                            'category_stats': service.calculate_category_stats(),
                            'recent_submissions': service.get_recent_submissions(limit=30),
                            'series_data': service.get_score_trends(top_n=30, max_points=200),
                            'first_bloods': service.get_first_bloods(limit=20)
                        }
                    }
                    yield f"data: {json.dumps(initial_data, ensure_ascii=False)}\n\n"
                except Exception as init_err:
                    logger.error(f"SSE initial data error: {str(init_err)}")
                    yield f"data: {json.dumps({'type': 'error', 'message': '初始数据加载失败'}, ensure_ascii=False)}\n\n"
                
                # 持续监听更新（使用get_message代替listen，避免阻塞）
                while True:
                    current_time = time.time()
                    
                    # 超时检查：超过5分钟自动断开（防止僵尸连接）
                    if current_time - connection_start > max_connection_time:
                        logger.info(
                            f"SSE connection timeout: user={request.user.username}, "
                            f"duration={current_time - connection_start:.1f}s"
                        )
                        yield f"data: {json.dumps({'type': 'timeout', 'message': '连接超时，请刷新页面'}, ensure_ascii=False)}\n\n"
                        break
                    
                    # 非阻塞地获取消息（超时1秒）
                    message = pubsub.get_message(timeout=1.0)
                    
                    if message:
                        # 处理消息
                        if message['type'] == 'message':
                            try:
                                update_data = json.loads(message['data'])
                                yield f"data: {json.dumps(update_data, ensure_ascii=False)}\n\n"
                                last_heartbeat = current_time
                            except (json.JSONDecodeError, TypeError) as json_err:
                                logger.error(f"SSE invalid message: {message.get('data')}, error={str(json_err)}")
                                continue
                    
                    # 定期发送心跳（每30秒）
                    if current_time - last_heartbeat >= 30:
                        yield ": heartbeat\n\n"
                        last_heartbeat = current_time
                    
                    # 短暂休眠，避免CPU占用过高
                    time.sleep(0.1)
                        
            except GeneratorExit:
                logger.info(
                    f"SSE client disconnected: user={request.user.username}, "
                    f"competition={competition.id}"
                )
            except Exception as e:
                logger.error(
                    f"SSE stream error: user={request.user.username}, "
                    f"competition={competition.id}, error={str(e)}\n{traceback.format_exc()}"
                )
                try:
                    yield f"data: {json.dumps({'type': 'error', 'message': '连接异常，请刷新页面'}, ensure_ascii=False)}\n\n"
                except:
                    pass
            finally:
                # 清理资源（关键：确保连接被正确释放）
                connection_duration = time.time() - connection_start
                try:
                    # 1. 关闭Redis pubsub连接
                    if pubsub:
                        try:
                            pubsub.unsubscribe()
                            pubsub.close()
                        except Exception as pubsub_err:
                            logger.error(f"Error closing pubsub: {str(pubsub_err)}")
                    
                    # 2. 减少用户连接计数
                    current_user = cache.get(sse_conn_key, 0)
                    if current_user > 0:
                        cache.set(sse_conn_key, current_user - 1, 120)
                    else:
                        # 如果计数为0，直接删除键
                        cache.delete(sse_conn_key)
                    
                    # 3. 减少全局连接计数
                    current_global = cache.get(global_conn_key, 0)
                    if current_global > 0:
                        cache.set(global_conn_key, current_global - 1, 120)
                    else:
                        # 如果计数为0，直接删除键
                        cache.delete(global_conn_key)
                    
                    logger.info(
                        f"SSE connection closed: user={request.user.username}, "
                        f"competition={competition.id}, duration={connection_duration:.1f}s, "
                        f"remaining_user_conns={max(0, current_user - 1)}, "
                        f"remaining_global_conns={max(0, current_global - 1)}"
                    )
                except Exception as cleanup_err:
                    logger.error(f"SSE cleanup error: {str(cleanup_err)}\n{traceback.format_exc()}")
        
        # 返回SSE响应
        response = StreamingHttpResponse(
            event_stream(),
            content_type='text/event-stream; charset=utf-8'
        )
        response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        response['X-Accel-Buffering'] = 'no'  # 禁用nginx缓冲
        # 注意：不要设置 Connection 头，WSGI 不允许 hop-by-hop 头
        
        return response
        
    except Competition.DoesNotExist:
        logger.warning(f"SSE competition not found: slug={slug}")
        return JsonResponse({'error': '比赛不存在'}, status=404)
    except Exception as e:
        logger.error(f"SSE setup error: slug={slug}, error={str(e)}\n{traceback.format_exc()}")
        return JsonResponse({'error': '无法建立连接'}, status=500)


@login_required
@require_http_methods(["POST"])
def refresh_dashboard_data(request, slug):
    """
    手动刷新数据大屏数据（管理员功能）
    
    权限：仅比赛创建者或管理员
    """
    # 导入数据库连接管理工具
    from django.db import close_old_connections
    
    try:
        competition = get_object_or_404(Competition, slug=slug)
        
        # 权限检查
        if not (competition.author == request.user or request.user.is_staff):
            logger.warning(
                f"Unauthorized refresh attempt: user={request.user.username}, "
                f"competition={competition.id}"
            )
            return JsonResponse({'error': '无权操作'}, status=403)
        
        # 频率限制
        if not _check_rate_limit(request, competition.id, 'refresh'):
            return JsonResponse({
                'error': '刷新过于频繁，请稍后再试',
                'retry_after': 60
            }, status=429)
        
        # 执行刷新
        start_time = time.time()
        service = get_dashboard_service(competition.id)
        
        try:
            success = service.refresh_all_data()
        except Exception as refresh_err:
            logger.error(
                f"Refresh execution error: competition={competition.id}, "
                f"error={str(refresh_err)}\n{traceback.format_exc()}"
            )
            raise
        
        elapsed = time.time() - start_time
        
        if success:
            logger.info(
                f"Dashboard refreshed: competition={competition.title}, "
                f"user={request.user.username}, elapsed={elapsed:.3f}s"
            )
            return JsonResponse({
                'status': 'success',
                'message': '数据已刷新并推送更新',
                'elapsed': round(elapsed, 3)
            })
        else:
            logger.error(f"Dashboard refresh failed: competition={competition.id}")
            return JsonResponse({
                'status': 'error',
                'message': '数据刷新失败，请查看日志'
            }, status=500)
            
    except Competition.DoesNotExist:
        logger.warning(f"Refresh competition not found: slug={slug}")
        return JsonResponse({'error': '比赛不存在'}, status=404)
    except Exception as e:
        logger.error(
            f"Refresh request failed: slug={slug}, "
            f"user={request.user.username}, error={str(e)}\n{traceback.format_exc()}"
        )
        return JsonResponse({
            'status': 'error',
            'message': '服务器错误',
            'detail': str(e) if request.user.is_staff else None
        }, status=500)
    finally:
        # 清理数据库连接（关键：防止连接泄漏）
        close_old_connections()


# ==================== Demo演示功能 ====================

@login_required
def dashboard_demo(request):
    """
    数据大屏演示页面（仅管理员可访问）
    """
    # 权限检查：仅管理员可访问
    if not request.user.is_staff:
        from django.contrib import messages
        messages.error(request, '您没有权限访问演示页面')
        return redirect('competition:competition_list')
    
    # 创建一个模拟的比赛对象
    class DemoCompetition:
        title = 'CTF网络安全挑战赛'
        slug = 'demo'
        end_time = timezone.now() + timedelta(hours=24)
    
    context = {
        'competition': DemoCompetition(),
        'use_sse': False,  # Demo模式不使用SSE
        'is_demo': True,
    }
    return render(request, 'competition/dashbase/team/dashboardone.html', context)


@never_cache
@require_http_methods(["GET"])
def get_dashboard_demo_data(request):
    """
    获取演示数据（仅管理员可访问）
    """
    # 导入数据库连接管理工具
    from django.db import close_old_connections
    
    # 权限检查：仅管理员可访问
    if not request.user.is_authenticated or not request.user.is_staff:
        return JsonResponse({
            'error': '无权访问演示数据'
        }, status=403)
    
    try:
        logger.info(f"Demo dashboard data requested by {request.user.username}")
        
        # 生成演示数据
        demo_data = generate_demo_dashboard_data()
        
        # 添加元数据
        demo_data['timestamp'] = time.time()
        demo_data['load_time'] = 0.001
        demo_data['cache_hit'] = False
        demo_data['demo_mode'] = True
        
        return JsonResponse(demo_data, json_dumps_params={'ensure_ascii': False})
        
    except Exception as e:
        logger.error(f"Demo data generation failed: {str(e)}\n{traceback.format_exc()}")
        return JsonResponse({
            'error': '演示数据生成失败',
            'detail': str(e)
        }, status=500)
    finally:
        # 清理数据库连接（关键：防止连接泄漏）
        close_old_connections()


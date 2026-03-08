# -*- coding: utf-8 -*-
"""
个人赛数据大屏视图 - 优化版
"""

from django.http import StreamingHttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django_redis import get_redis_connection
from django.core.cache import cache
from django.db import close_old_connections
import json
import time
import logging
import traceback

from competition.models import Competition, Registration
from competition.dashboard_service import get_dashboard_service

# 使用apps.competition作为logger名称，匹配settings.py中的配置
logger = logging.getLogger('apps.competition')


def _check_rate_limit_individual(request, competition_id, action='view'):
    """个人赛频率限制检查"""
    user_id = request.user.id
    cache_key = f"individual_dashboard_rate_limit:{competition_id}:{user_id}:{action}"
    
    count = cache.get(cache_key, 0)
    limits = {
        'view': 60,
        'refresh': 10,
    }
    
    limit = limits.get(action, 30)
    
    if count >= limit:
        logger.warning(
            f"Rate limit exceeded: user={user_id}, competition={competition_id}, "
            f"action={action}, count={count}/{limit}"
        )
        return False
    
    cache.set(cache_key, count + 1, 60)
    return True


@login_required
def individual_dashboard_optimized(request, slug):
    """个人赛数据大屏主页"""
    start_time = time.time()
    
    competition = get_object_or_404(
        Competition.objects.select_related('author').only(
            'id', 'slug', 'title', 'competition_type', 
            'start_time', 'end_time', 'author_id'
        ),
        slug=slug
    )
 
    # 检查是否为个人赛
    if competition.competition_type != Competition.INDIVIDUAL:
        messages.warning(request, "该比赛不是个人赛")
        return redirect('competition:competition_detail', slug=slug)
    
    # 权限检查
    cache_key = f"individual_dashboard_access:{competition.id}:{request.user.id}"
    is_authorized = cache.get(cache_key)
    
    if is_authorized is None:
        registration = Registration.objects.filter(
            competition_id=competition.id,
            user_id=request.user.id
        ).only('id').first()
        
        is_authorized = (
            registration is not None or 
            competition.author_id == request.user.id or 
            request.user.is_staff
        )
        
        cache.set(cache_key, is_authorized, 300)
    
    if not is_authorized:
        messages.warning(request, "您还未报名该比赛，无法查看数据大屏")
        return redirect('competition:competition_detail', slug=slug)
    
    context = {
        'competition': competition,
        'use_sse': True,
        'is_individual': True,
    }


    # 根据比赛配置的模板选择对应的大屏模板
    template_mapping = {
        'dashboardone': 'competition/dashbase/individual/dashboard_individual1.html',
        'dashboardtwo': 'competition/dashbase/individual/dashboard_individual2.html',
    }
    
    template_name = template_mapping.get(
        competition.dashboard_template, 
        'competition/dashbase/individual/dashboard_individual1.html'  # 默认模板
    )
    
    elapsed = time.time() - start_time
    logger.debug(f"Individual dashboard rendered: slug={slug}, elapsed={elapsed:.3f}s")
    
    return render(request, template_name, context)


@login_required
@never_cache
@require_http_methods(["GET"])
def get_individual_dashboard_data(request, slug):
    """获取个人赛数据大屏数据"""
    try:
        competition = get_object_or_404(Competition, slug=slug)
        
        if competition.competition_type != Competition.INDIVIDUAL:
            return JsonResponse({'error': '该比赛不是个人赛'}, status=400)
        
        # SSE连接检查
        sse_conn_key = f"sse_conn_individual:{competition.id}:{request.user.id}"
        has_sse_connection = cache.get(sse_conn_key, 0) > 0
        
        if not has_sse_connection:
            registration = Registration.objects.filter(
                competition=competition,
                user=request.user
            ).first()
            
            is_authorized = (
                registration or 
                competition.author == request.user or 
                request.user.is_staff
            )
            
            if not is_authorized:
                return JsonResponse({'error': '无权访问'}, status=403)
        
        # 频率限制
        if not _check_rate_limit_individual(request, competition.id, 'view'):
            return JsonResponse({'error': '请求过于频繁'}, status=429)
        
        # 参数验证
        try:
            top_n = min(max(int(request.GET.get('top_n', '30')), 10), 50)
            max_points = min(max(int(request.GET.get('max_points', '200')), 50), 500)
        except (ValueError, TypeError):
            top_n, max_points = 30, 200
        
        service = get_dashboard_service(competition.id)
        start_time = time.time()
        
        stats = service.calculate_stats()
        leaderboard = service.calculate_leaderboard(limit=top_n)
        category_stats = service.calculate_category_stats()
        recent_submissions = service.get_recent_submissions(limit=30)
        score_trends = service.get_score_trends(top_n=top_n, max_points=max_points)
        first_bloods = service.get_first_bloods(limit=20)
        
        elapsed = time.time() - start_time
        
        return JsonResponse({
            'stats': stats,
            'leaderboard': leaderboard,
            'category_stats': category_stats,
            'recent_submissions': recent_submissions,
            'series_data': score_trends,
            'first_bloods': first_bloods,
            'load_time': elapsed,
            'is_individual': True
        }, json_dumps_params={'ensure_ascii': False})
        
    except Exception as e:
        logger.error(f"Individual dashboard data error: {str(e)}\n{traceback.format_exc()}")
        return JsonResponse({'error': str(e)}, status=500)
    finally:
        close_old_connections()


def individual_dashboard_sse_stream(request, slug):
    """个人赛 SSE 实时数据推送"""
    try:
        competition = get_object_or_404(Competition, slug=slug)
        
        if competition.competition_type != Competition.INDIVIDUAL:
            def error_stream():
                yield f"data: {json.dumps({'type': 'error', 'message': '该比赛不是个人赛'})}\n\n"
            return StreamingHttpResponse(error_stream(), content_type='text/event-stream')
        
        # 权限检查
        registration = Registration.objects.filter(
            competition=competition,
            user=request.user
        ).first()
        
        is_authorized = (
            registration or 
            competition.author == request.user or 
            request.user.is_staff
        )
        
        if not is_authorized:
            def error_stream():
                yield f"data: {json.dumps({'type': 'error', 'message': '无权访问'})}\n\n"
            return StreamingHttpResponse(error_stream(), content_type='text/event-stream')
        
        # SSE连接管理
        sse_conn_key = f"sse_conn_individual:{competition.id}:{request.user.id}"
        global_conn_key = f"sse_global_conn_individual:{competition.id}"
        
        current_user_conns = cache.get(sse_conn_key, 0)
        current_global_conns = cache.get(global_conn_key, 0)
        
        if current_user_conns >= 5:
            cache.delete(sse_conn_key)
            current_user_conns = 0
            
            if cache.get(sse_conn_key, 0) >= 5:
                return JsonResponse({
                    'error': '连接数超限，请关闭其他标签页后重试'
                }, status=429)
        
        if current_global_conns >= 200:
            return JsonResponse({
                'error': '系统繁忙，请稍后再试'
            }, status=503)
        
        cache.set(sse_conn_key, current_user_conns + 1, 120)
        cache.set(global_conn_key, current_global_conns + 1, 120)
        
        logger.info(
            f"Individual SSE connection established: user={request.user.username}, "
            f"competition={competition.id}"
        )
        
        def event_stream():
            redis_conn = None
            pubsub = None
            last_heartbeat = time.time()
            connection_start = time.time()
            max_connection_time = 300
            
            try:
                redis_conn = get_redis_connection("default")
                pubsub = redis_conn.pubsub()
                channel = f"ctf:dashboard:updates:{competition.id}"
                
                pubsub.subscribe(channel)
                
                yield f"data: {json.dumps({'type': 'connected', 'message': '实时连接已建立'}, ensure_ascii=False)}\n\n"
                
                # 发送初始数据
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
                            'first_bloods': service.get_first_bloods(limit=20),
                            'is_individual': True
                        }
                    }
                    yield f"data: {json.dumps(initial_data, ensure_ascii=False)}\n\n"
                except Exception as init_err:
                    logger.error(f"SSE initial data error: {str(init_err)}")
                    yield f"data: {json.dumps({'type': 'error', 'message': '初始数据加载失败'}, ensure_ascii=False)}\n\n"
                
                # 持续监听
                while True:
                    current_time = time.time()
                    
                    if current_time - connection_start > max_connection_time:
                        yield f"data: {json.dumps({'type': 'timeout', 'message': '连接超时，请刷新页面'}, ensure_ascii=False)}\n\n"
                        break
                    
                    message = pubsub.get_message(timeout=1.0)
                    
                    if message and message['type'] == 'message':
                        try:
                            update_data = json.loads(message['data'])
                            yield f"data: {json.dumps(update_data, ensure_ascii=False)}\n\n"
                            last_heartbeat = current_time
                        except:
                            continue
                    
                    if current_time - last_heartbeat >= 30:
                        yield ": heartbeat\n\n"
                        last_heartbeat = current_time
                    
                    time.sleep(0.1)
                        
            except GeneratorExit:
                logger.info(f"Individual SSE client disconnected: user={request.user.username}")
            except Exception as e:
                logger.error(f"Individual SSE stream error: {str(e)}")
                try:
                    yield f"data: {json.dumps({'type': 'error', 'message': '连接异常'}, ensure_ascii=False)}\n\n"
                except:
                    pass
            finally:
                connection_duration = time.time() - connection_start
                try:
                    if pubsub:
                        pubsub.unsubscribe()
                        pubsub.close()
                    
                    current_user = cache.get(sse_conn_key, 0)
                    if current_user > 0:
                        cache.set(sse_conn_key, current_user - 1, 120)
                    else:
                        cache.delete(sse_conn_key)
                    
                    current_global = cache.get(global_conn_key, 0)
                    if current_global > 0:
                        cache.set(global_conn_key, current_global - 1, 120)
                    else:
                        cache.delete(global_conn_key)
                    
                    logger.info(
                        f"Individual SSE connection closed: user={request.user.username}, "
                        f"duration={connection_duration:.1f}s"
                    )
                except Exception as cleanup_err:
                    logger.error(f"SSE cleanup error: {str(cleanup_err)}")
                
                close_old_connections()
        
        response = StreamingHttpResponse(
            event_stream(),
            content_type='text/event-stream; charset=utf-8'
        )
        response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        response['X-Accel-Buffering'] = 'no'
        
        return response
        
    except Exception as e:
        logger.error(f"Individual SSE error: {str(e)}\n{traceback.format_exc()}")
        def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        return StreamingHttpResponse(error_stream(), content_type='text/event-stream')


from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from public.utils import sanitize_html,validate_docker_compose,escape_xss,unescape_content,check_request_headers
from django.db import transaction
from django.core.cache import cache
from django.views.decorators.cache import cache_page
from django.utils.decorators import method_decorator
from datetime import timedelta
from practice.models import PC_Challenge, Tag, SolveRecord, SolvedFlag
from public.models import CTFUser
from container.models import UserContainer,DockerEngine
from .flag_generator import get_or_generate_flag, verify_flag as verify_flag_func
from .redis_cache import UserContainerCache
from django.db.models import Count, Exists, OuterRef
from django.conf import settings
from django.views import generic
import time
from datetime import datetime
from django.db.models import F
from easytask.tasks import cleanup_container, flag_destroy_web_container
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db.models import Exists, OuterRef, Q
from django.views.decorators.cache import never_cache
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from urllib3.exceptions import NewConnectionError, MaxRetryError
from requests.exceptions import ConnectionError, ReadTimeout
from haystack.query import SearchQuerySet
from django.http import JsonResponse
import docker
import json
import requests
import uuid
from docker.errors import APIError,DockerException
from .view_api import create_container_api
from celery import current_app
from django.views.generic import CreateView, FormView
from django.urls import reverse_lazy
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from .forms import ChallengeForm,DockerImageForm
from functools import wraps
from django.core.validators import RegexValidator
from django.core.exceptions import ValidationError
import re
import json
import yaml
import threading
from django.utils.functional import cached_property
import hashlib
from django.views.generic import ListView
from django.urls import reverse
from public.utils import create_captcha_for_registration


from public.ctf_platform import CTFPlatform

# 核心验证（深度嵌入）

import logging

logger = logging.getLogger('apps.practice')

@login_required
def leaderboard_view(request):
    """
    漏洞靶场综合排行榜页面（支持分页，带缓存）
    """
    # 缓存键
    cache_key = 'leaderboard_users_list'
    
    # 尝试从缓存获取用户列表
    users_list = cache.get(cache_key)
    
    if users_list is None:
        # 缓存未命中，从数据库查询
        users_queryset = CTFUser.objects.filter(score__gt=0).order_by('-score')
        
        # 转换为列表并添加排名和头像信息
        User = get_user_model()
        users_list = []
        for index, ctf_user in enumerate(users_queryset):
            ctf_user.rank = index + 1
            try:
                django_user = User.objects.get(username=ctf_user.user)
                ctf_user.uuid = django_user.uuid
                ctf_user.avatar = django_user.avatar.url if hasattr(django_user, 'avatar') and django_user.avatar else None
            except User.DoesNotExist:
                ctf_user.uuid = None
                ctf_user.avatar = None
            users_list.append(ctf_user)
        
        # 将结果存入缓存，设置过期时间为 5 分钟（300秒）
        cache.set(cache_key, users_list, 3600)
    
    # 分页设置：每页50条
    paginator = Paginator(users_list, 50)
    page = request.GET.get('page', 1)
    
    try:
        users = paginator.page(page)
    except PageNotAnInteger:
        users = paginator.page(1)
    except EmptyPage:
        users = paginator.page(paginator.num_pages)
    
    context = {
        'page_title': '综合排行榜',
        'users': users,
        'paginator': paginator,
    }
    return render(request, 'practice/leaderboard.html', context)



def prevent_duplicate_submission(timeout=5):
    """
    防重复提交装饰器
    timeout: 限制时间(秒)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(self, request, *args, **kwargs):
            # 生成用户特定的缓存key
            cache_key = f"submit_lock:{request.user.id}:{self.__class__.__name__}"
            
            # 检查是否存在锁
            if cache.get(cache_key):
                return JsonResponse({
                    'status': 'error',
                    'message': f'请等待{timeout}秒后再次提交'
                }, status=429)
                
            # 设置锁
            cache.set(cache_key, True, timeout)
            
            try:
                return func(self, request, *args, **kwargs)
            finally:
                # 操作完成后删除锁
                cache.delete(cache_key)
                
        return wrapper
    return decorator

@login_required
@require_http_methods(["GET"])
def check_container_status(request):
    """
    检查容器状态（支持多协议）
    
    返回格式：
    - 新格式缓存：直接返回 container_urls（包含协议信息，支持 HTTP/SSH/MySQL 等）
    - 旧格式缓存：从端口信息生成 HTTP URL（向后兼容）
    """
    try:
        challenge_uuid = request.GET.get('challenge_uuid', '').strip()
        if not challenge_uuid:
            return JsonResponse({"error": "缺少挑战 UUID"}, status=400)
        
        # 验证标准 UUID 格式
        try:
            uuid.UUID(challenge_uuid)
        except ValueError:
            return JsonResponse({"error": "无效的题目标识"}, status=400)

        cached_container = UserContainerCache.get(request.user.id, challenge_uuid)
        if cached_container:
            #  优先使用缓存中的 container_urls（支持多协议、多入口）
            container_urls = cached_container.get('container_urls')
            
            if container_urls:
                return JsonResponse({
                    "status": "active",
                    "container_urls": container_urls,
                    "expires_at": cached_container['expires_at']
                })
            else:
                ports = json.loads(cached_container['port'])
                container_urls = []
                
                url_prefix = cached_container.get('url_prefix')
                
                # 为每个端口生成 HTTP URL（旧数据默认HTTP协议）
                for port in ports.values():
                    if cached_container['domain'] and url_prefix:
                        url = f"http://{url_prefix}.{cached_container['domain']}:{port}"
                    elif cached_container['domain']:
                        # 没有 url_prefix 时生成一个（兼容旧数据）
                        random_prefix = uuid.uuid4().hex[:8]
                        url = f"http://{random_prefix}.{cached_container['domain']}:{port}"
                    else:
                        url = f"http://{cached_container['ip_address']}:{port}"
                    container_urls.append(url)
                    
                return JsonResponse({
                    "status": "active",
                    "container_urls": container_urls,
                    "expires_at": cached_container['expires_at']
                })
        else:
            # 用户没有创建容器
            return JsonResponse({"status": "inactive"})
    except Exception as e:
        logger.error(f"检查容器状态失败: user={request.user.id}, error={str(e)}")
        return JsonResponse({"error": "请求错误"}, status=500)
    
@require_http_methods(["POST"])
@login_required
def create_web_container(request):
    """
    异步创建容器（使用Celery任务队列）
    
    流程：
    1. 检查是否已有容器
    2. 检查是否有待处理的任务
    3. 创建异步任务并立即返回task_id
    4. 前端通过task_id轮询任务状态
    """
    try:
        if not request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({"error": "请求错误"}, status=400)
        
        challenge_uuid = request.POST.get('challenge_uuid', '').strip()
        if not challenge_uuid:
            return JsonResponse({"error": "缺少挑战 UUID"}, status=400)
        
        # 验证标准 UUID 格式
        try:
            uuid.UUID(challenge_uuid)
        except ValueError:
            return JsonResponse({"error": "无效的题目标识"}, status=400)
        
        user = request.user
        
        # 1. 检查是否已有运行中的容器（快速返回）
        cached_container = UserContainerCache.get(user.id, challenge_uuid)
        if cached_container:
            # 优先使用 container_urls（多协议支持）
            container_urls = cached_container.get('container_urls')
            
            if container_urls:
                # 新格式缓存：已包含完整的协议信息
                pass
            else:
                # 兼容旧格式缓存：生成 HTTP URL
                ports = json.loads(cached_container['port'])
                container_urls = []
                
                # 如果有域名，使用相同的随机前缀
                random_prefix = uuid.uuid4().hex[:8] if cached_container['domain'] else None
                
                # 为每个端口生成URL
                for port in ports.values():
                    if cached_container['domain']:
                        url = f"http://{random_prefix}.{cached_container['domain']}:{port}"
                    else:
                        url = f"http://{cached_container['ip_address']}:{port}"
                    container_urls.append(url)
            
            logger.info(f"用户 {user.username} 已有运行中的容器，直接返回")
            return JsonResponse({
                "status": "existing",
                "container_urls": container_urls,
                "expires_at": cached_container['expires_at']
            })
        
        # 2. 检查是否有待处理的任务（防止重复提交）
        pending_task_key = f"container_task_user:{user.id}:{challenge_uuid}"
        existing_task_id = cache.get(pending_task_key)
        
        if existing_task_id:
            # 检查任务是否仍在处理
            task_cache_key = f"container_task:{existing_task_id}"
            task_info = cache.get(task_cache_key)
            
            if task_info and task_info.get('status') in ['pending', 'processing']:
                logger.info(f"用户 {user.username} 已有待处理的任务: {existing_task_id}")
                return JsonResponse({
                    "status": "pending",
                    "task_id": existing_task_id,
                    "message": "已有容器创建任务正在处理中，请稍候..."
                })
                
        rate_limit_key = f"container_rate_limit:{user.id}:{challenge_uuid}"
        if cache.get(rate_limit_key):
            return JsonResponse({
                "error": "一分钟内禁止重复创建容器"
            }, status=429)
        
        # 3. 创建容器创建锁（防止并发）
        container_lock_key = f"container_lock:{user.id}:{challenge_uuid}"
        if cache.get(container_lock_key):
            return JsonResponse({
                "error": "容器创建请求已在处理中，请稍后..."
            }, status=429)
        
        # 设置锁（5分钟超时）
        cache.set(container_lock_key, True, timeout=300)

        # 提取请求元数据（用于日志）
        request_meta = {
            'REMOTE_ADDR': request.META.get('REMOTE_ADDR'),
            'HTTP_USER_AGENT': request.META.get('HTTP_USER_AGENT'),
            'HTTP_X_FORWARDED_FOR': request.META.get('HTTP_X_FORWARDED_FOR'),
        }
        
        # 4. 创建异步任务
        from practice.tasks import create_container_async
        
        # 调用异步任务
        async_result = create_container_async.apply_async(
            args=[challenge_uuid, user.id],
            kwargs={'request_meta': request_meta}
        )
        
        task_id = async_result.id
        
        # 5. 记录任务ID（用于后续查询）
        cache.set(pending_task_key, task_id, timeout=300)
        
        logger.info(
            f" 用户 {user.username} 创建异步容器任务: "
            f"challenge={challenge_uuid}, task_id={task_id}"
        )
        
        # 6. 立即返回task_id（前端轮询）
        return JsonResponse({
            "status": "pending",
            "task_id": task_id,
            "message": "容器创建任务已提交，正在处理中..."
        })
    
    except Exception as e:
        logger.error(f"创建异步任务失败: {str(e)}", exc_info=True)
        
        # 清理锁
        try:
            container_lock_key = f"container_lock:{user.id}:{challenge_uuid}"
            if cache.get(container_lock_key):
                cache.delete(container_lock_key)
        except:
            pass
        
        return JsonResponse({
            "error": "系统错误，请稍后再试或联系管理员"
        }, status=500)


@login_required
@require_http_methods(["GET"])
def query_container_task_status(request, task_id):
    """
    查询容器创建任务状态（轮询接口）
    
    前端通过此接口轮询任务进度
    
    返回格式：
    {
        "status": "pending" | "processing" | "success" | "failed" | "timeout",
        "progress": 0-100,
        "message": "状态描述",
        "data": {容器信息} (仅在success时)
        "error": "错误信息" (仅在failed时)
    }
    """
    try:
        # 优先从缓存中获取任务状态（tasks.py写入的）
        cache_key = f"container_task:{task_id}"
        task_info = cache.get(cache_key)
        
        if task_info:
            # 从cache中读取状态，转换为前端期望的格式
            status = task_info.get('status', 'pending')
            
            # 将自定义状态转换为Celery标准状态
            state_mapping = {
                'pending': 'PENDING',
                'processing': 'PROGRESS',
                'success': 'SUCCESS',
                'failed': 'FAILURE',
                'timeout': 'FAILURE'
            }
            
            response_data = {
                "task_id": task_id,
                "state": state_mapping.get(status, 'PENDING'),
                "message": task_info.get('message', ''),
                "percent": task_info.get('progress', 0)
            }
            
            # 如果任务成功，包含容器数据
            if status == 'success' and task_info.get('data'):
                response_data['result'] = task_info['data']
            
            # 如果任务失败，包含错误信息
            if status in ('failed', 'timeout') and task_info.get('error'):
                response_data['error'] = task_info['error']
            
            return JsonResponse(response_data)
        
        # 如果cache中没有，从Celery的AsyncResult读取
        from celery.result import AsyncResult
        async_result = AsyncResult(task_id)
        
        response_data = {
            "task_id": task_id,
            "state": async_result.state,
        }
        
        if async_result.state == 'PENDING':
            response_data.update({
                "message": "任务等待处理中...",
                "percent": 0
            })
        elif async_result.state == 'PROGRESS':
            info = async_result.info or {}
            response_data.update({
                "message": info.get('message', '正在处理...'),
                "percent": info.get('percent', 0)
            })
        elif async_result.state == 'SUCCESS':
            result = async_result.result or {}
            # 如果result是dict且包含data字段，提取出来
            if isinstance(result, dict):
                if 'data' in result:
                    response_data['result'] = result['data']
                else:
                    response_data['result'] = result
            response_data.update({
                "message": "容器创建成功",
                "percent": 100
            })
        elif async_result.state == 'FAILURE':
            error_info = str(async_result.info) if async_result.info else "未知错误"
            response_data.update({
                "message": "容器创建失败",
                "percent": 0,
                "error": error_info
            })
        elif async_result.state == 'REVOKED':
            response_data.update({
                "message": "任务已取消",
                "percent": 0
            })
        else:
            response_data.update({
                "message": f"未知任务状态: {async_result.state}",
                "percent": 0
            })
        
        return JsonResponse(response_data)
    
    except Exception as e:
        logger.error(f"查询任务状态失败: task_id={task_id}, error={str(e)}", exc_info=True)
        return JsonResponse({
            "error": "查询任务状态失败",
            "message": "查询任务状态失败"
        }, status=500)


@login_required
@require_http_methods(["POST"])
def cancel_container_task(request, task_id):
    """
    取消容器创建任务（健壮版：清理所有相关资源）
    
    用户可以取消正在等待或处理中的任务
    
    清理内容：
    1. 撤销 Celery 任务
    2. 清理任务缓存
    3. 清理容器锁
    4. 清理待处理任务标记
    5. 清理 UserContainerCache
    6. 清理可能已创建的 K8s 资源
    7. 标记数据库中未完成的容器记录
    """
    try:
        from celery import current_app
        from practice.redis_cache import UserContainerCache
        
        # 1. 获取任务信息（用于后续清理）
        cache_key = f"container_task:{task_id}"
        task_info = cache.get(cache_key)
        
        challenge_uuid = None
        if task_info and isinstance(task_info, dict):
            # 从任务信息中提取 challenge_uuid（如果有）
            challenge_uuid = task_info.get('challenge_uuid')
        
        # 2. 撤销 Celery 任务
        current_app.control.revoke(task_id, terminate=True, signal='SIGKILL')
        logger.info(f"已撤销 Celery 任务: {task_id}")
        
        # 3. 更新任务状态为已取消
        if task_info:
            task_info['status'] = 'cancelled'
            task_info['message'] = '任务已被用户取消'
            cache.set(cache_key, task_info, timeout=300)
        
        # 4. 清理待处理任务标记和相关资源
        # 避免使用 cache.keys()，直接尝试删除已知的 key 模式
        user_id = request.user.id
        
        # 如果我们有 challenge_uuid，直接清理
        if challenge_uuid:
            _cleanup_user_resources(user_id, challenge_uuid, task_id)
        else:
            # 兜底：遍历用户可能访问的题目（性能更好的方案）
            # 这里可以从最近的题目访问记录中获取，或者接受challenge_uuid作为参数
            logger.warning(f"无法从任务信息中获取 challenge_uuid，仅清理任务缓存: {task_id}")
        
        logger.info(
            f"✓ 用户 {request.user.username} 成功取消容器任务: {task_id}"
            f"{f' (题目: {challenge_uuid})' if challenge_uuid else ''}"
        )
        
        return JsonResponse({
            "status": "cancelled",
            "message": "任务已取消，相关资源已清理"
        })
    
    except Exception as e:
        logger.error(f"取消任务失败: task_id={task_id}, error={str(e)}", exc_info=True)
        return JsonResponse({
            "error": "取消任务失败，请稍后再试"
        }, status=500)


def _cleanup_user_resources(user_id, challenge_uuid, task_id):
    """
    清理用户的容器相关资源（内部函数）
    
    Args:
        user_id: 用户ID
        challenge_uuid: 题目UUID
        task_id: 任务ID
    """
    from practice.redis_cache import UserContainerCache
    from container.models import UserContainer
    
    # 1. 清理待处理任务标记
    pending_task_key = f"container_task_user:{user_id}:{challenge_uuid}"
    if cache.get(pending_task_key) == task_id:
        cache.delete(pending_task_key)
        logger.debug(f"✓ 已清理待处理任务标记: {pending_task_key}")
    
    # 2. 清理容器创建锁
    container_lock_key = f"container_lock:{user_id}:{challenge_uuid}"
    if cache.delete(container_lock_key):
        logger.debug(f"✓ 已清理容器创建锁: {container_lock_key}")
    
    # 3. 清理 UserContainerCache
    UserContainerCache.delete(user_id, challenge_uuid)
    logger.debug(f"✓ 已清理 UserContainerCache: user={user_id}, challenge={challenge_uuid}")
    
    # 4. 清理可能已创建的 K8s/Docker 资源
    try:
        # 查找该用户该题目的最新容器记录
        user_containers = UserContainer.objects.filter(
            user_id=user_id,
            challenge_uuid=challenge_uuid,
            status__in=['creating', 'running']  # 只处理活跃状态
        ).order_by('-created_at')[:1]
        
        if user_containers.exists():
            container = user_containers.first()
            
            # 检查容器是否是刚创建的（5分钟内）
            from django.utils import timezone
            from datetime import timedelta
            if timezone.now() - container.created_at < timedelta(minutes=5):
                logger.info(
                    f"发现刚创建的容器，尝试清理: "
                    f"container_id={container.container_id}, "
                    f"engine={container.docker_engine.name}"
                )
                
                # 删除 Pod/容器（通过引擎）
                try:
                    from container.container_factory import ContainerServiceFactory
                    docker_service = ContainerServiceFactory.create_service(
                        container.docker_engine
                    )
                    docker_service.stop_and_remove_container(container.container_id)
                    logger.info(f"✓ 已删除容器资源: {container.container_id}")
                except Exception as e:
                    logger.warning(f"删除容器资源失败（可能已被清理）: {e}")
                
                # 更新数据库状态
                container.status = 'stopped'
                container.save(update_fields=['status'])
                logger.debug(f"✓ 已更新容器状态为 stopped")
    
    except Exception as e:
        logger.warning(f"清理容器资源时出错（忽略）: {e}")




@login_required
@require_http_methods(["DELETE"])
def remove_container(request, container_id):
    container = get_object_or_404(UserContainer, container_id=container_id, user=request.user)
    docker_engine = container.docker_engine
    

    if docker_engine.tls_enabled:
        tls_configs = docker_engine.get_tls_config()
    else:
        tls_configs = None 
    docker_service = DockerService(
        url=container.docker_engine.url,
        tls_config=tls_configs
    )
    
    try:
        docker_service.remove_container(container_id)
        container.delete()
        return JsonResponse({"message": "容器已成功移除"})
    except Exception as e:
        return JsonResponse({"error": f"移除容器失败: {str(e)}"}, status=500)
        

@login_required
@require_http_methods(["POST"])
def verify_flag(request):
    if not request.headers.get('x-requested-with') == 'XMLHttpRequest' and not request.method == "POST":
        return JsonResponse({"error": "请求错误"}, status=500)
    
    challenge_uuid = request.POST.get('challenge_uuid', '').strip()
    submitted_flag = request.POST.get('flag', '').strip()
    
    if not challenge_uuid or not submitted_flag:
        return JsonResponse({'status': 'error', 'message': '缺少必要参数'}, status=400)
    
    # 验证标准 UUID 格式
    try:
        uuid.UUID(challenge_uuid)
    except ValueError:
        return JsonResponse({'status': 'error', 'message': '无效的题目标识'}, status=400)
    challenge = get_object_or_404(PC_Challenge, uuid=challenge_uuid)
    user = request.user
    
    if not challenge.is_disable:
        return JsonResponse({'status': 'error', 'message': '该题目当前未启用，暂时无法访问'}, status=400)
    
    # 检查是否为Docker题目（支持新旧模式）
    is_docker = False
    if challenge.docker_image:
        is_docker = True
        
        # 检查容器是否存在且未过期
        cached_container = UserContainerCache.get(user.id, challenge_uuid)
        if cached_container:
            expires_at = datetime.fromisoformat(cached_container['expires_at'])
            if expires_at < timezone.now():
                return JsonResponse({'status': 'error', 'message': '容器已过期，禁止提交 flag'}, status=400)
            if cached_container['challenge_uuid'] != str(challenge_uuid):
                return JsonResponse({'status': 'error', 'message': '题目环境未创建或环境已过期'}, status=400)
        else:
            logger.warning(
                f"用户 {user.username} 尝试提交Flag但容器不存在: "
                f"题目={challenge.title}, UUID={challenge_uuid}"
            )
            return JsonResponse({'status': 'error', 'message': '题目环境未创建或环境已过期，请先启动容器'}, status=400)
    # 检查是否有静态文件（支持 static_files 或 static_file_url）
    if challenge.static_files or challenge.static_file_url:
        if challenge.coins > challenge.get_coins(user):
            return JsonResponse({'status': 'error', 'message': '您的金币不足，无法提交flag'}, status=400)
        
        # 使用统一方法获取文件下载URL（自动处理权限检查）
        file_url = None
        if challenge.is_member:
            # 会员题目：需要验证会员权限
            if (user.is_authenticated and user.is_valid_member) or user.is_superuser:
                file_url = challenge.get_file_download_url(user)
            else:
                file_url = None
        else:
            # 普通题目：直接获取下载URL
            file_url = challenge.get_file_download_url(user)
        
        # 如果有文件但用户没有权限或文件不存在，禁止提交
        if not file_url:
            return JsonResponse({'status': 'error', 'message': '您没有权限访问题目文件或文件不存在，无法提交flag'}, status=400)
        
    try:
        # 确保用户已认证且有有效ID
        if not user.is_authenticated or not user.pk:
            logger.error(f"用户认证无效: user={user}, is_authenticated={user.is_authenticated}, pk={user.pk}")
            return JsonResponse({'status': 'error', 'message': '用户认证无效，请重新登录'}, status=401)
        
        # 确保题目有有效ID
        if not challenge.pk:
            logger.error(f"题目ID无效: challenge={challenge.title}, pk={challenge.pk}")
            return JsonResponse({'status': 'error', 'message': '题目数据异常'}, status=500)
        
        with transaction.atomic():
            # 使用get_or_create创建CTFUser，明确指定默认值
            ctf_user, created = CTFUser.objects.prefetch_related('solved_challenges').get_or_create(
                user=user,
                defaults={
                    'score': 0,
                    'coins': 10,
                    'rank': 0,
                    'solves': 0
                }
            )
            
            # 验证flag并获取索引（支持多段flag）
            is_correct, flag_index = verify_flag_func(submitted_flag, challenge, user)
            
            if not is_correct:
                # Flag 错误
                return JsonResponse({'status': 'error', 'message': 'Flag 错误，请重新检查'})
            
            # Flag 正确，检查是否已经解过这个特定的flag
            already_solved = SolvedFlag.objects.filter(
                user=ctf_user,
                challenge=challenge,
                flag_index=flag_index
            ).exists()
            
            if already_solved:
                return JsonResponse({
                    'status': 'error', 
                    'message': f'您已经提交过这个flag，请勿重复提交'
                })
            
            # 获取该flag对应的分数
            flag_points = challenge.get_flag_point(flag_index)
            
            # 计算flag的哈希（用于记录）
            import hashlib
            flag_hash = hashlib.sha256(submitted_flag.encode()).hexdigest()
            
            # 创建SolvedFlag记录
            SolvedFlag.objects.create(
                user=ctf_user,
                challenge=challenge,
                flag_index=flag_index,
                points_earned=flag_points,
                flag_hash=flag_hash
            )
            
            logger.info(
                f"用户解出flag: user={user.username}, challenge={challenge.title}, "
                f"flag_index={flag_index}, points={flag_points}"
            )
            
            # 检查是否解出了所有flag
            solved_flags_count = SolvedFlag.objects.filter(
                user=ctf_user,
                challenge=challenge
            ).count()
            
            is_challenge_completed = (solved_flags_count >= challenge.flag_count)
            
            # 更新用户得分和统计（使用update()方法确保并发安全且绕过模型验证）
            if is_challenge_completed:
                # 所有flag都解出了，标记题目为完成
                if challenge not in ctf_user.solved_challenges.all():
                    ctf_user.solved_challenges.add(challenge)
                    logger.info(f"用户完成题目: user={user.username}, challenge={challenge.title}")
                
                # 计算奖励金币
                solve_count = challenge.solves + 1  # 当前解题次数（包括这次）
                
                if challenge.category in ['签到']:  # 其他题目
                    reward = challenge.get_reward()
                else:
                    reward = challenge.calculate_reward()
                
                # 完成所有flag：更新分数、解题数和金币
                CTFUser.objects.filter(pk=ctf_user.pk).update(
                    score=F('score') + flag_points,
                    solves=F('solves') + 1,
                    coins=F('coins') + reward
                )
            else:
                # 只解出部分flag：仅更新分数
                CTFUser.objects.filter(pk=ctf_user.pk).update(
                    score=F('score') + flag_points
                )
            

            try:
                
                cache_key = f'user_ctf_stats_{request.user.id}' 
                cache.delete(cache_key)
                cache.delete(f'challenge_{challenge.uuid}_limit_10')
                cache_key = f'user_island_progress:{user.id}'
                cache.delete(cache_key)
                cache.delete(f'users_ranked_by_solves_nonzero_limit_10')
                # 清除排行榜页面缓存
                cache.delete('leaderboard_users_list')
            except Exception as e:
                logger.error(
                    f"清除缓存失败: user={user.username if user else 'None'}, "
                    f"错误类型={type(e).__name__}, 错误信息={str(e)}"
                )

            # 只有完成所有flag时才更新题目统计和创建解题记录
            if is_challenge_completed:
                # 更新题目解题数和一血（使用update()绕过模型的full_clean验证）
                update_kwargs = {'solves': F('solves') + 1}
                if challenge.first_blood_user is None:
                    update_kwargs['first_blood_user'] = user
                    update_kwargs['first_blood_time'] = timezone.now()
                PC_Challenge.objects.filter(pk=challenge.pk).update(**update_kwargs)
                
                # 创建解题记录（需要确保ctf_user有有效ID）
                if not ctf_user.pk:
                    logger.error(f"CTFUser ID无效: user={user.username}, ctf_user.pk={ctf_user.pk}")
                    raise ValueError("CTFUser记录创建失败")
                
                try:
                    SolveRecord.objects.create(
                        user=ctf_user,
                        challenge=challenge
                    )
                except Exception as e:
                    logger.error(
                        f"创建解题记录失败: user={user.username}, user_pk={user.pk}, "
                        f"ctf_user_pk={ctf_user.pk}, challenge={challenge.title}, "
                        f"challenge_pk={challenge.pk}, 错误={str(e)}"
                    )
                    raise
                
                # 刷新challenge以获取最新的solves值
                challenge.refresh_from_db()
                platform = CTFPlatform()
                
                # 给题目作者奖励（需要检查作者是否存在）
                if challenge.allocated_coins < 200 and challenge.author:
                    total_solved_count = challenge.solves
                    difficulty = str(challenge.difficulty)
                    author_total_reward = platform.calculate_reward_for_creator(total_solved_count, difficulty)
                    
                    logger.debug(
                        f"计算作者奖励: challenge={challenge.title}, "
                        f"allocated_coins={challenge.allocated_coins}, "
                        f"solves={total_solved_count}, difficulty={difficulty}, "
                        f"reward={author_total_reward}"
                    )
                    
                    # 只有奖励大于0时才发放
                    if author_total_reward > 0:
                        try:
                            # 检查作者是否有CTFUser记录
                            if not CTFUser.objects.filter(user=challenge.author).exists():
                                raise CTFUser.DoesNotExist
                            
                            # 使用update()方法确保并发安全且绕过模型验证
                            CTFUser.objects.filter(user=challenge.author).update(
                                coins=F('coins') + author_total_reward
                            )
                            
                            # 更新题目分配的总金币（使用update()绕过模型的full_clean验证）
                            PC_Challenge.objects.filter(pk=challenge.pk).update(
                                allocated_coins=F('allocated_coins') + author_total_reward
                            )
                            
                            logger.info(
                                f"题目作者获得奖励: challenge={challenge.title}, "
                                f"author={challenge.author.username}, reward={author_total_reward}"
                            )
                        except CTFUser.DoesNotExist:
                            logger.warning(
                                f"题目作者 {challenge.author.username} 没有CTFUser记录，跳过奖励发放"
                            )
                        except Exception as e:
                            logger.error(
                                f"发放题目作者奖励失败: challenge={challenge.title}, "
                                f"author={challenge.author.username if challenge.author else 'None'}, "
                                f"错误={str(e)}"
                            )
                # 完成所有flag，更新排名和清除缓存
                try:
                    ctf_user.update_rank()
                except Exception as e:
                    logger.error(
                        f"更新排名失败: user={user.username if user else 'None'}, "
                        f"错误类型={type(e).__name__}, 错误信息={str(e)}"
                    )
                return JsonResponse({
                    'status': 'success',
                    'is_docker': is_docker,
                    'message': f'🎉 恭喜！题目完成！获得 {reward} 金币奖励！',
                    'flag_progress': f'{solved_flags_count}/{challenge.flag_count}',
                    'completed': True
                })
            else:
                # 解出了部分flag，但未完成题目
                return JsonResponse({
                    'status': 'not_completed',
                    'is_docker': is_docker,
                    'message': f' Flag 正确！获得 {flag_points} 分',
                    'points_earned': flag_points,
                    'flag_progress': f'{solved_flags_count}/{challenge.flag_count}',
                    'completed': False
                })
    except Exception as e:
        logger.error(
            f"Flag验证过程异常: user={user.username if user else 'None'}, "
            f"user_pk={user.pk if user else 'None'}, "
            f"challenge={challenge.title if challenge else 'None'}, "
            f"challenge_pk={challenge.pk if challenge else 'None'}, "
            f"错误类型={type(e).__name__}, 错误信息={str(e)}"
        )
        import traceback
        logger.error(f"详细堆栈: {traceback.format_exc()}")
        return JsonResponse({'status': 'error','message': f'系统错误，请联系管理员'}, status=500)

def update_user_rank(ctf_user):
    # 更新用户排名
    ctf_user.rank = CTFUser.objects.filter(score__gt=ctf_user.score).count() + 1
    ctf_user.save()





class CTFChallengeListView(ListView):
    model = PC_Challenge
    template_name = 'practice/pre_index.html'
    context_object_name = 'challenges'
    paginate_by = 28
    paginate_orphans = getattr(settings, 'BASE_ORPHANS', 0)

    @method_decorator(never_cache)
    @method_decorator(require_http_methods(["GET", "POST"]))
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        """获取查询集，包含所有过滤条件"""
        # 基础查询集
        queryset = super().get_queryset().filter(is_disable=True)

        # 应用类型过滤
        challenge_type = self.request.GET.get('type')
        if challenge_type and challenge_type != 'ALL':
            queryset = queryset.filter(category=challenge_type)
        
        # 应用难度过滤
        difficulty = self.request.GET.get('difficulty')
        if difficulty and difficulty != 'ALL':
            queryset = queryset.filter(difficulty=difficulty)

        # 应用作者过滤
        author = self.request.GET.get('author')
        if author == 'me' and self.request.user.is_authenticated:
            queryset = queryset.filter(author=self.request.user)

        

        # 应用标签过滤
        tag = self.request.GET.get('tag')
        if tag:
            queryset = queryset.filter(tags__name=tag)

        # 应用搜索过滤
        search_query = self.request.GET.get('q', '').strip()
        if search_query:
            # 将搜索词分割成单独的关键词
            keywords = search_query.split()
            search_results = SearchQuerySet().models(PC_Challenge)
            
            # 对每个关键词进行搜索
            for keyword in keywords:
                search_results = search_results.filter(
                    content__contains=keyword
                )
                    
            challenge_ids = [result.pk for result in search_results]
            if not challenge_ids:
                # 如果全文搜索没有结果，尝试使用数据库模糊搜索
                query = Q()
                for keyword in keywords:
                    query |= Q(title__icontains=keyword) | Q(description__icontains=keyword)
                queryset = queryset.filter(query)
            else:
                queryset = queryset.filter(id__in=challenge_ids)

        # 应用解决状态过滤
        if self.request.user.is_authenticated:
            solved_subquery = CTFUser.objects.filter(
                user=self.request.user,
                solved_challenges=OuterRef('pk')
            )
            queryset = queryset.annotate(is_solved=Exists(solved_subquery))
            
            status = self.request.GET.get('status')
            if status == 'solved':
                queryset = queryset.filter(is_solved=True)
            elif status == 'unsolved':
                queryset = queryset.filter(is_solved=False)

        
        if self.request.user.is_authenticated:
            collect_subquery = CTFUser.objects.filter(
                user=self.request.user,
                collect_challenges=OuterRef('pk')
            )
            queryset = queryset.annotate(is_collect=Exists(collect_subquery))
            
            collect = self.request.GET.get('author')
            if collect == 'collect':
                queryset = queryset.filter(is_collect=True)
            

        # 添加解题次数注解
        queryset = queryset.annotate(solve_count=Count('solverecord'))

        # 应用排序
        sort_by = self.request.GET.get('sort_by', 'id')
        if sort_by == 'solve_count':
            queryset = queryset.order_by('-solve_count', 'id')
        elif sort_by == 'points':
            queryset = queryset.order_by('-points', 'id')
        else:
            queryset = queryset.order_by('-is_top', '-id')

        return queryset.distinct()

    def paginate_queryset(self, queryset, page_size):
        """重写分页方法，处理空页面情况"""
        paginator = self.get_paginator(
            queryset, 
            page_size,
            orphans=self.paginate_orphans,
            allow_empty_first_page=True
        )
        page_kwarg = self.page_kwarg
        page = self.kwargs.get(page_kwarg) or self.request.GET.get(page_kwarg) or 1
        
        try:
            page_number = int(page)
            page = paginator.page(page_number)
        except (ValueError, EmptyPage):
            # 如果页码无效或超出范围，重定向到第一页
            page = paginator.page(1)
            
        return (paginator, page, page.object_list, page.has_other_pages())

    def get(self, request, *args, **kwargs):
        """重写 get 方法，处理分页重定向"""
        try:
            return super().get(request, *args, **kwargs)
        except EmptyPage:
            # 如果页面为空，重定向到第一页
            url = request.path
            query = request.GET.copy()
            query['page'] = '1'
            if query:
                url += '?' + query.urlencode()
            return redirect(url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # 添加分页范围
        if 'paginator' in context and 'page_obj' in context:
            paginator = context['paginator']
            page_obj = context['page_obj']
            context['page_range'] = self.get_page_range(paginator, page_obj)
        
        # 获取挑战类型
        challenge_types_key = "ctf_challenge_types"
        challenge_types = cache.get(challenge_types_key)
        if challenge_types is None:
            challenge_types = list(PC_Challenge.objects.values_list('category', flat=True).distinct())
            cache.set(challenge_types_key, challenge_types, 60 * 60)  # 缓存1小时
        context['challenge_types'] = challenge_types

        # 获取难度级别
        difficulties_key = "ctf_difficulties"
        difficulties = cache.get(difficulties_key)
        if difficulties is None:
            difficulties = list(PC_Challenge.objects.values_list('difficulty', flat=True).distinct())
            cache.set(difficulties_key, difficulties, 60 * 60)  # 缓存1小时
        context['difficulties'] = difficulties

        # 获取所有标签
        tags_key = "ctf_tags"
        tags = cache.get(tags_key)
        if tags is None:
            tags = Tag.objects.annotate(count=Count('pc_challenge')).filter(count__gt=0).order_by('-count', 'name')
            cache.set(tags_key, list(tags.values('name', 'count')), 60 * 60)  # 缓存1小时
        context['tags'] = tags

        # 添加当前的查询参数到上下文
        context.update({
            'current_type': self.request.GET.get('type', 'ALL'),
            'current_difficulty': self.request.GET.get('difficulty', 'ALL'),
            'current_status': self.request.GET.get('status', 'ALL'),
            'current_sort': self.request.GET.get('sort_by', 'id'),
            'current_author': self.request.GET.get('author', 'all'),
            'current_tag': self.request.GET.get('tag', ''),
            'search_query': self.request.GET.get('q', ''),
            
            # 过滤选项
            'sort_options': [
                ('id', '默认'),
                ('solve_count', '解题次数'),
                ('points', '分数')
            ],
            'author_options': [
                ('all', '所有题目'),
                ('me', '我的题目')
            ],
            'status_options': ['ALL', 'solved', 'unsolved'],
            'user_authenticated': self.request.user.is_authenticated,
            'hide_footer': True,
        })

        return context

    def get_page_range(self, paginator, page_obj, on_each_side=2, on_ends=1):
        """生成分页范围，与 TagView 保持一致"""
        page_range = []
        number = page_obj.number
        total_pages = paginator.num_pages
        
        # 左侧处理
        if number > on_each_side + on_ends + 1:
            for i in range(1, on_ends + 1):
                page_range.append(i)
            page_range.append("...")
            for i in range(number - on_each_side, number):
                page_range.append(i)
        else:
            for i in range(1, number):
                page_range.append(i)
        
        # 当前页
        page_range.append(number)
        
        # 右侧处理
        if number < total_pages - on_each_side - on_ends:
            for i in range(number + 1, number + on_each_side + 1):
                page_range.append(i)
            page_range.append("...")
            for i in range(total_pages - on_ends + 1, total_pages + 1):
                page_range.append(i)
        else:
            for i in range(number + 1, total_pages + 1):
                page_range.append(i)
        
        return page_range

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)



# 更新 create_web_container_view 函数
def create_web_container_view(request):
    return CTFChallengeListView.as_view()(request)


def challenge_detail(request, uuid):
    challenge = get_object_or_404(PC_Challenge, uuid=uuid)
    
    # 检查访问权限
    if not challenge.is_disable:
        messages.error(request, "该题目当前未启用，暂时无法访问")
        return redirect('practice:challenge_list')
    if not challenge.is_active:
        # 如果题目未激活，只有管理员和作者可以访问
        if not (challenge.user_can_manage(request.user)):
            messages.warning(request, "该题目当前作者未公开，暂时无法访问")
            return redirect('practice:challenge_list')
    
    is_collected = False
    user_coins = 0
    can_view_writeup = False
    
    if request.user.is_authenticated:
        ctf_user, _ = CTFUser.objects.get_or_create(user=request.user)
        is_collected = ctf_user.collect_challenges.filter(uuid=challenge.uuid).exists()
        user_coins = ctf_user.coins
        # 检查用户是否可以查看题解
        can_view_writeup = challenge.user_can_view_writeup(request.user)
    
    # 获取文件下载URL（支持 static_files 和 static_file_url）
    file_url = None
    if challenge.static_files or challenge.static_file_url:
        if challenge.is_member:
            # 会员题目：需要验证会员权限
            if (request.user.is_authenticated and request.user.is_valid_member) or request.user.is_superuser:
                file_url = challenge.get_file_download_url(request.user)
            else:
                file_url = None  # 无权限
        else:
            # 普通题目：直接获取下载URL
            file_url = challenge.get_file_download_url(request.user)

        # 设置浏览量增加时间判断,同一题目两次浏览超过半小时才重新统计阅览量,作者浏览忽略
    u = request.user
    if check_request_headers(request.headers):
        ses = request.session
        the_key = 'challenge:read:{}'.format(challenge.id)
        is_read_time = ses.get(the_key)
        if u == challenge.author or u.is_superuser:
            pass
        else:
            if not is_read_time:
                challenge.update_views()
                ses[the_key] = time.time()
            else:
                now_time = time.time()
                t = now_time - is_read_time
                if t > 60 * 30:
                    challenge.update_views()
                    ses[the_key] = time.time()
    
    context = {
        'challenge': challenge,
        'file_url': file_url,
        'is_collected': is_collected,
        'can_view_writeup': can_view_writeup,
        'user_coins': user_coins,
    }
    return render(request, 'practice/tags/challenge_detail.html', context)


@login_required
@require_http_methods(["POST"])
def destroy_web_container(request):
    """
    异步销毁容器（练习模式）
    触发异步任务后立即返回成功，后台继续处理
    """
    if not request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({"error": "请求错误"}, status=400)
    
    user = request.user
    challenge_uuid = request.POST.get('challenge_uuid', '').strip()
    
    if not challenge_uuid:
        return JsonResponse({"error": "缺少必要参数"}, status=400)
    
    # 验证标准 UUID 格式
    try:
        uuid.UUID(challenge_uuid)
    except ValueError:
        return JsonResponse({"error": "无效的题目标识"}, status=400)
    
    try:
        # 验证题目是否存在
        challenge = get_object_or_404(PC_Challenge, uuid=challenge_uuid)
        
        # 调用异步任务（后台执行，不等待结果）
        from practice.tasks import destroy_container_async
        task = destroy_container_async.delay(user.id, challenge_uuid)
        
        logger.info(f"用户 {user.username} 发起异步销毁容器任务: task_id={task.id}, challenge={challenge.title}", extra={'request': request})
        
        # 立即返回成功响应，让前端可以继续操作
        return JsonResponse({
            'status': 'success',
            'message': '容器正在销毁中'
        })
    
    except PC_Challenge.DoesNotExist:
        return JsonResponse({'error': '找不到指定的题目'}, status=404)
    
    except Exception as e:
        error_msg = f"销毁容器时发生未知错误"
        logger.error(f"用户 {user.username} 发起销毁任务失败: {str(e)}", extra={'request': request}, exc_info=True)
        return JsonResponse({'error': error_msg}, status=500)







class ChallengeCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = PC_Challenge 
    template_name = 'practice/challenge_create.html'
    form_class = ChallengeForm
    success_url = reverse_lazy('practice:challenge_list')
    
    def test_func(self):
        # 检查用户是否有权限创建题目

        
        # 只有比赛拥有者或管理员才能创建题目
        return self.request.user.is_staff or self.request.user.is_superuser or self.request.user.is_member
    def get_form_kwargs(self):
        """
        将当前用户添加到表单参数中，解决用户为空的问题
        """
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def clean_title(self, title):
        """验证标题字段"""
        if not title:
            raise ValidationError('标题不能为空')
            
        # 限制标题长度为20个字符
        if len(title) > 20:
            raise ValidationError('标题长度不能超过20个字符')
            
        # 过滤危险字符
        dangerous_chars = ['<', '>', '"', "'", ';', '&', '|', '`', '$', '#']
        for char in dangerous_chars:
            if char in title:
                raise ValidationError(f'标题包含非法字符: {char}')
                
        # 只允许中文、英文、数字和基本标点
        pattern = r'^[\u4e00-\u9fa5a-zA-Z0-9_\-\s.,!?]+$'
        if not re.match(pattern, title):
            raise ValidationError('标题只能包含中文、英文、数字和基本标点符号')
            
        return title
        
    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            messages.warning(self.request, '请先登录后再创建题目')
            return redirect('account_login')
        messages.error(self.request, '您没有权限创建题目')
        return redirect('practice:challenge_list')
    
    def get_initial(self):
        # 生成验证码并添加到表单初始数据中
        initial = super().get_initial()
        captcha_data = create_captcha_for_registration()
        initial['captcha_key'] = captcha_data['captcha_key']
        self.captcha_image = captcha_data['captcha_image']
        return initial
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # 将验证码图片添加到上下文中
        context['captcha_image'] = getattr(self, 'captcha_image', None)
        return context
    
    def form_valid(self, form):
        try:
            with transaction.atomic():
                # 设置作者
                challenge = form.save(commit=False)
                challenge.author = self.request.user
                
                # 验证标题
                title = form.cleaned_data.get('title', '')
                try:
                    challenge.title = self.clean_title(title)
                except ValidationError as e:
                    form.add_error('title', str(e))
                    return self.form_invalid(form, '标题验证失败')
                
                # 检查并扣除金币
                coins = int(form.cleaned_data.get('coins', 0))
                if coins < 0 or coins > 10:
                    return self.form_invalid(form, '金币不能小于0个，且不能超过10个')
                
                coins_needed = int(form.cleaned_data.get('reward_coins', 0))
                if coins_needed > 10 or coins_needed < 0:
                    #messages.warning(self.request, '')
                    return self.form_invalid(form, '奖励金币不能超过10个，且不能小于0个')
                points = int(form.cleaned_data.get('points', 0))
                if points > 200 or points < 0:
                    return self.form_invalid(form, '分数不能超过200，且不能小于0')
                
                # 验证题解金币成本
                writeup_is_public = form.cleaned_data.get('writeup_is_public', False)
                writeup_cost = int(form.cleaned_data.get('writeup_cost', 1))
                if not writeup_is_public and (writeup_cost < 0 or writeup_cost > 10):
                    return self.form_invalid(form, '题解金币成本必须在0-10之间')
                
                static_files = form.cleaned_data.get('static_files')
                docker_image = form.cleaned_data.get('docker_image')
                network_topology_config = form.cleaned_data.get('network_topology_config')
                static_file_url = form.cleaned_data.get('static_file_url')
                if not static_files and not docker_image and not network_topology_config and not static_file_url:
                    return self.form_invalid(form, '部署类型必须选择一种')
                try:
                    ctf_user = CTFUser.objects.select_for_update().get(user = self.request.user)
                    
                    if ctf_user.coins < coins_needed:
                        
                       
                        return self.form_invalid(form,'你的金币不足，请重新设置奖励金币')
                    
                    ctf_user.coins -= coins_needed
                    ctf_user.save()
                except Exception as e:
                    #messages.error(self.request, '未找到用户信息')
                    return self.form_invalid(form)
                
                # 设置部署类型为COMPOSE
                
                
                # 获取Docker镜像配置
                if static_file_url:
                    challenge.static_file_url = static_file_url
                if docker_image:
                    challenge.docker_image = docker_image
                
                if static_files:
                    challenge.static_files = static_files
                
                if network_topology_config:
                    challenge.network_topology_config = network_topology_config
                # 保存题目
                challenge.save()
                form.save_m2m()
                logger.info(f"用户 {self.request.user}创建了标题为：{title}题目，扣除{coins_needed}个金币", extra={'request': self.request})
                cache.delete("pcchallenge_categories")
                cache.delete("all_pcchallenge_tags")
                messages.success(self.request, f'题目创建成功！扣除 {coins_needed} 个金币')
                return redirect(self.success_url)
        except Exception as e:
            messages.error(self.request, f'创建题目失败: {str(e)}')
            
            # 如果是AJAX请求，返回JSON响应
            
            return self.form_invalid(form)
    
    def form_invalid(self, form, info=""):
        # 生成新的验证码
        captcha_data = create_captcha_for_registration()
        form.initial['captcha_key'] = captcha_data['captcha_key']
        self.captcha_image = captcha_data['captcha_image']
            
        messages.error(self.request, f'题目创建失败{info}')
        
        # 如果是AJAX请求，返回JSON响应
        
        return super().form_invalid(form)





@login_required
@require_http_methods(["POST"])
def refresh_captcha(request):
    """刷新验证码"""
    try:
        captcha_data = create_captcha_for_registration()
        return JsonResponse({
            'success': True,
            'captcha_key': captcha_data['captcha_key'],
            'captcha_image': captcha_data['captcha_image']
        })
    except Exception as e:
        logger.error(f'刷新验证码失败: {str(e)}', exc_info=True)
        return JsonResponse({
            'success': False,
            'message': '刷新验证码失败'
        }, status=500)






@login_required
@require_http_methods(["POST"])
def delete_challenge(request):
    try:
        if not request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({"error": "请求错误"}, status=400)
        
        user = request.user
        challenge_uuid = request.POST.get('challenge_uuid', '').strip()
        
        if not challenge_uuid:
            return JsonResponse({"error": "缺少必要参数"}, status=400)
        
        # 验证标准 UUID 格式
        try:
            uuid.UUID(challenge_uuid)
        except ValueError:
            return JsonResponse({"error": "无效的题目标识"}, status=400)

        try:
            challenge = get_object_or_404(PC_Challenge, uuid=challenge_uuid)
            if not challenge.user_can_manage(user):
                return JsonResponse({
                    "error": "您没有权限删除此题目",
                    "redirect": reverse('practice:challenge_detail', kwargs={'uuid': challenge_uuid})
                }, status=403)
            
            success, message = challenge.safe_delete(user)
            
            if success:
                messages.success(request, message)
                logger.info(f"用户 {user}删除了：{challenge.title} 题目,题目作者：{challenge.author}", extra={'request': request})
                return JsonResponse({
                    "redirect": reverse('practice:challenge_list')
                })
            else:
                messages.error(request, message)
                logger.error(f"用户 {user}删除：{challenge.title}题目发生错误{message},题目作者：{challenge.author}", extra={'request': request})
                return JsonResponse({
                    "redirect": reverse('practice:challenge_detail', kwargs={'uuid': challenge_uuid})
                }, status=400)
        
        except PC_Challenge.DoesNotExist:
            return JsonResponse({"error": "题目不存在"}, status=404)
    except Exception as e:
        logger.error(f"用户 {user}删除：{challenge.title}题目发生错误{e},题目作者：{challenge.author}", extra={'request': request})

@login_required
@require_http_methods(["POST"])
def toggle_active_challenge(request):
    if not request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({"error": "请求错误"}, status=400)
    
    user = request.user
    challenge_uuid = request.POST.get('challenge_uuid', '').strip()
    
    if not challenge_uuid:
        return JsonResponse({"error": "缺少必要参数"}, status=400)
    
    # 验证标准 UUID 格式
    try:
        uuid.UUID(challenge_uuid)
    except ValueError:
        return JsonResponse({"error": "无效的题目标识"}, status=400)
    
    try:
        challenge = get_object_or_404(PC_Challenge, uuid=challenge_uuid)
        

        if not challenge.user_can_manage(user):
            return JsonResponse({
                "error": "您没有权限修改此题目",
                "redirect": reverse('practice:challenge_detail', kwargs={'uuid': str(challenge.uuid)})
            }, status=403)
        
        success, message = challenge.toggle_active(user)
        
        if success:
            messages.success(request, message)
            return JsonResponse({
                "redirect": reverse('practice:challenge_detail', kwargs={'uuid': str(challenge.uuid)})
            }, status=200)
        else:
            messages.error(request, message)
            return JsonResponse({
                "redirect": reverse('practice:challenge_detail', kwargs={'uuid': str(challenge.uuid)})
            }, status=400)
    
    except PC_Challenge.DoesNotExist:
        return JsonResponse({"error": "题目不存在"}, status=404)





@login_required
@require_http_methods(["POST"])
def edit_challenge(request):
    """
    修改题目的视图函数
    
    只允许修改 description, hint 和 title
    只有题目作者和管理员可以修改
    """
    try:
        if not request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({"error": "请求错误"}, status=400)
            
        uuid = request.POST.get('uuid')
        if not uuid:
            return JsonResponse({"error": "题目不存在"}, status=404)
            
        challenge = get_object_or_404(PC_Challenge, uuid=uuid)
        
        # 检查权限
        if not challenge.user_can_manage(request.user):
            return JsonResponse({
                "error": "您没有权限修改此题目",
                "redirect": reverse('practice:challenge_detail', kwargs={'uuid': str(challenge.uuid)})
            }, status=403)
        
        # 获取要更新的字段
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        hint = request.POST.get('hint')

        if len(title) > 20:
            messages.warning(request, "题目标题不能超过20个字符")  # 标题最长100字符
            return JsonResponse({
                "redirect": reverse('practice:challenge_detail', kwargs={'uuid': str(challenge.uuid)})
            }, status=400)
            
        if len(description) > 2000:
            messages.warning(request, "题目描述不能超过2000个字符")  # 描述最长5000字符
            return JsonResponse({
               
                "redirect": reverse('practice:challenge_detail', kwargs={'uuid': str(challenge.uuid)})
            }, status=400)
            
        if len(hint) > 10000:  # 提示最长500字符
            messages.warning(request, "题目提示不能超过2000个字符")
            return JsonResponse({
               
                "redirect": reverse('practice:challenge_detail', kwargs={'uuid': str(challenge.uuid)})
            }, status=400)
        
        
        # 验证标题不能为空
        if not title:
            messages.warning(request, "题目标题不能为空")
            return JsonResponse({
                "redirect": reverse('practice:challenge_detail', kwargs={'uuid': str(challenge.uuid)})
            }, status=400)
            
        try:
            # 检查是否有实际修改（不需要转义，保存原始 Markdown 内容）
            # XSS 防护在模板渲染时通过 markdown 过滤器处理
            if (title == challenge.title and 
                description == challenge.description and 
                hint == challenge.hint):
                messages.info(request, "未做任何修改")
                return JsonResponse({
                    "redirect": reverse('practice:challenge_detail', kwargs={'uuid': str(challenge.uuid)})
                })
            
            # 有修改才更新（保存原始内容）
            challenge.title = title
            challenge.description = description
            challenge.hint = hint
            challenge.save()
            
            messages.success(request, "题目信息修改成功")
            return JsonResponse({
                "redirect": reverse('practice:challenge_detail', kwargs={'uuid': str(challenge.uuid)})
            })
            
        except Exception as e:
            messages.error(request, f"更新失败: {str(e)}")
            return JsonResponse({
                "redirect": reverse('practice:challenge_detail', kwargs={'uuid': str(challenge.uuid)})
            }, status=500)
            
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
        
        
    
class TagView(generic.ListView):
    model = PC_Challenge
    template_name = 'practice/tag.html'
    context_object_name = 'challenges'
    paginate_orphans = getattr(settings, 'BASE_ORPHANS', 0)

    def get_paginate_by(self, queryset):
        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return 28
        return 28

    def get_ordering(self):
        sort = self.request.GET.get('sort')
        if sort == 'time':
            return '-created_at', '-id'
        return 'difficulty'

    def get_queryset(self, **kwargs):
        queryset = super(TagView, self).get_queryset()
        tag = get_object_or_404(Tag, slug=self.kwargs.get('slug'))
        
        if self.request.user.is_superuser or self.request.user.is_member:
            return queryset.filter(tags=tag, is_disable=True)
        else: 
            return queryset.filter(tags=tag, is_disable=True)

    def get_context_data(self, **kwargs):
        context_data = super(TagView, self).get_context_data()
        tag = get_object_or_404(Tag, slug=self.kwargs.get('slug'))
        context_data['search_tag'] = '学习岛'
        context_data['search_instance'] = tag
        
        # 添加分页范围
        paginator = context_data['paginator']
        page_obj = context_data['page_obj']
        context_data['page_range'] = self.get_page_range(paginator, page_obj)
        
        # 计算用户解决情况
        if self.request.user.is_authenticated:
            try:
                from public.models import CTFUser
                ctf_user = CTFUser.objects.get(user=self.request.user)
                solved_challenges = set(ctf_user.solved_challenges.values_list('id', flat=True))
                
                # 获取该标签下的所有题目 ID
                tag_challenge_ids = set(tag.pc_challenge_set.filter(is_disable=True).values_list('id', flat=True))
                
                # 计算已完成的题目数
                solved_count = len(tag_challenge_ids & solved_challenges)
                total_count = len(tag_challenge_ids)
                
                # 计算进度百分比
                progress = int((solved_count / total_count * 100)) if total_count > 0 else 0
                
                context_data['tag_solved'] = solved_count
                context_data['tag_total'] = total_count
                context_data['tag_progress'] = progress
            except Exception:
                context_data['tag_solved'] = 0
                context_data['tag_total'] = paginator.count
                context_data['tag_progress'] = 0
        else:
            context_data['tag_solved'] = 0
            context_data['tag_total'] = paginator.count
            context_data['tag_progress'] = 0
        
        return context_data

    def get_page_range(self, paginator, page_obj, on_each_side=2, on_ends=1):
        page_range = []
        number = page_obj.number
        total_pages = paginator.num_pages
        
        # 左侧处理
        if number > on_each_side + on_ends + 1:
            for i in range(1, on_ends + 1):
                page_range.append(i)
            page_range.append("...")
            for i in range(number - on_each_side, number):
                page_range.append(i)
        else:
            for i in range(1, number):
                page_range.append(i)
        
        # 当前页
        page_range.append(number)
        
        # 右侧处理
        if number < total_pages - on_each_side - on_ends:
            for i in range(number + 1, number + on_each_side + 1):
                page_range.append(i)
            page_range.append("...")
            for i in range(total_pages - on_ends + 1, total_pages + 1):
                page_range.append(i)
        else:
            for i in range(number + 1, total_pages + 1):
                page_range.append(i)
        
        return page_range

    def get(self, request, *args, **kwargs):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            try:
                page = int(request.GET.get('page', 1))
                challenges = self.get_queryset()
                total_count = challenges.count()
                
                per_page = 6
                start = (page - 1) * per_page
                end = min(start + per_page, total_count)
                
                has_next = end < total_count
                current_challenges = challenges[start:end]
                
                
                context = Context({
                    'challenges': current_challenges,
                    'user': request.user
                })
                
                html_content = template.render(context)
                
                return JsonResponse({
                    'html': html_content,
                    'has_next': has_next,
                    'total': total_count,
                    'current_page': page,
                    'loaded': end,
                    'remaining': total_count - end
                })
            except Exception as e:
                print(f"Error loading more articles: {e}")
                return JsonResponse({
                    'html': '', 
                    'has_next': False, 
                    'error': str(e)
                })
                
        return super().get(request, *args, **kwargs)


###题目收藏接口

@login_required
@require_http_methods(["POST"])
def toggle_collect(request):
    uuid = request.POST.get('challenge_uuid')
    if not uuid:
        return JsonResponse({"error": "参数缺失"}, status=400)

    try:
        challenge = PC_Challenge.objects.get(uuid=uuid)
    except PC_Challenge.DoesNotExist:
        return JsonResponse({"error": "题目不存在"}, status=404)

    ctf_user, created = CTFUser.objects.get_or_create(user=request.user)

    if challenge in ctf_user.collect_challenges.all():
        ctf_user.collect_challenges.remove(challenge)
        return JsonResponse({"message": "已取消收藏", "collected": False})
    else:
        ctf_user.collect_challenges.add(challenge)
        return JsonResponse({"message": "收藏成功", "collected": True})


@login_required
@require_http_methods(["POST"])
def purchase_writeup(request, uuid):
    """
    购买题解的API视图
    
    安全措施：
    1. 防止并发购买（使用缓存锁）
    2. 请求频率限制
    3. 异常日志记录
    4. 事务安全保障
    """
    import logging
    logger = logging.getLogger(__name__)
    
    # 1. 防止并发购买 - 使用缓存锁
    lock_key = f'purchase_writeup_lock_{request.user.id}_{uuid}'
    if cache.get(lock_key):
        return JsonResponse({
            "success": False,
            "message": "操作进行中，请勿重复提交"
        }, status=429)
    
    # 2. 请求频率限制 - 每个用户每分钟最多购买3次
    rate_limit_key = f'purchase_rate_limit_{request.user.id}'
    rate_count = cache.get(rate_limit_key, 0)
    if rate_count >= 3:
        return JsonResponse({
            "success": False,
            "message": "操作过于频繁，请稍后再试"
        }, status=429)
    
    # 设置请求锁，防止并发（5秒内）
    cache.set(lock_key, True, 5)
    
    try:
        # 3. 验证题目是否存在（不使用 select_for_update，避免事务问题）
        try:
            challenge = PC_Challenge.objects.get(uuid=uuid)
            if not request.user.is_valid_member and challenge.is_member:
                return JsonResponse({
                    "success": False,
                    "message": "您不是会员，无法购买会员题目题解"
                }, status=403)

            if not challenge.is_disable:
                return JsonResponse({
                    "success": False,
                    "message": "题目已禁用"
                }, status=403)
            if not challenge.is_active:
                return JsonResponse({
                    "success": False,
                    "message": "题目未激活"
                }, status=403)
            if not challenge.is_member:
                return JsonResponse({
                    "success": False,
                    "message": "题目不是会员题目"
                }, status=403)
        except PC_Challenge.DoesNotExist:
            logger.warning(f"用户 {request.user.username} 尝试购买不存在的题目: {uuid}")
            return JsonResponse({
                "success": False,
                "message": "题目不存在"
            }, status=404)
        
        # 4. 提前检查是否已经有权限查看（避免不必要的购买）
        if challenge.user_can_view_writeup(request.user):
            return JsonResponse({
                "success": True,
                "message": "您已经可以查看该题解"
            })
        
        # 5. 调用模型方法处理购买逻辑（已包含事务处理）
        success, message = challenge.purchase_writeup(request.user)
        
        # 6. 购买成功后清除用户的 CTF 统计缓存
        if success:
            cache_key = f'user_ctf_stats_{request.user.id}'
            cache.delete(cache_key)
            
            # 记录成功日志
            logger.info(f"用户 {request.user.username} 成功购买题目 {challenge.title} 的题解，消耗 {challenge.writeup_cost} 金币")
            
            # 更新请求频率计数
            cache.set(rate_limit_key, rate_count + 1, 60)
        else:
            # 记录失败日志
            logger.warning(f"用户 {request.user.username} 购买题目 {challenge.title} 题解失败: {message}")
        
        return JsonResponse({
            "success": success,
            "message": message
        })
        
    except Exception as e:
        # 7. 异常处理和日志记录
        logger.error(f"用户 {request.user.username} 购买题解时发生异常: {str(e)}", exc_info=True)
        return JsonResponse({
            "success": False,
            "message": "系统错误，请稍后重试"
        }, status=500)
        
    finally:
        # 8. 释放锁
        cache.delete(lock_key)


@login_required
@require_http_methods(["GET"])
def secure_url_download(request, challenge_uuid, token):
    """
    安全的URL文件代理下载
    
    为 static_file_url 提供安全控制：
    1. 令牌验证（防止URL被篡改）
    2. 时效性检查（默认5分钟有效期）
    3. 频率限制（防止恶意下载）
    4. IP限制（记录和限制下载来源）
    
    Args:
        request: Django request 对象
        challenge_uuid: 题目UUID
        token: 下载令牌
        
    Returns:
        HttpResponse: 重定向到实际文件URL或错误响应
    """
    from django.http import HttpResponse, HttpResponseForbidden, HttpResponseRedirect
    from container.download_security import (
        DownloadTokenGenerator,
        DownloadRateLimiter,
        get_client_ip
    )

    
    try:
        # 1. 获取题目对象
        challenge = get_object_or_404(PC_Challenge, uuid=challenge_uuid)
        
        # 2. 验证令牌
        token_generator = DownloadTokenGenerator()
        is_valid, error_msg = token_generator.verify_token(
            token,
            challenge.id,  # 使用 challenge.id 作为 file_id
            request.user.id
        )
        
        if not is_valid:
            logger.warning(
                f"URL下载令牌验证失败: 用户={request.user.username}, "
                f"题目={challenge.title}, 原因={error_msg}"
            )
            return HttpResponseForbidden(f"<h1>403 禁止访问</h1><p>{error_msg}</p>")
        
        # 3. 检查题目是否有静态文件URL
        if not challenge.static_file_url:
            logger.warning(
                f"题目无静态文件URL: 用户={request.user.username}, "
                f"题目={challenge.title}"
            )
            return HttpResponseForbidden("<h1>403 禁止访问</h1><p>题目没有可用的文件下载链接</p>")
        
        # 4. 检查频率限制
        client_ip = get_client_ip(request)
        can_download, rate_error_msg, remaining_time = DownloadRateLimiter.check_rate_limit(
            request.user.id,
            challenge.id,
            client_ip
        )
        
        if not can_download:
            logger.warning(
                f"URL下载频率限制: 用户={request.user.username}, "
                f"题目={challenge.title}, IP={client_ip}, "
                f"原因={rate_error_msg}"
            )
            return HttpResponse(
                f"<h1>429 请求过多</h1><p>{rate_error_msg}</p>",
                status=429
            )
        
        # 5. 记录下载次数
        DownloadRateLimiter.record_download(
            request.user.id,
            challenge.id,
            client_ip
        )
        
        # 6. 记录访问日志
        logger.info(
            f"URL文件下载: 用户={request.user.username}, "
            f"题目={challenge.title}, IP={client_ip}, "
            f"URL={challenge.static_file_url}"
        )
        
        # 7. 重定向到实际的文件URL
        return HttpResponseRedirect(challenge.static_file_url)
        
    except PC_Challenge.DoesNotExist:
        logger.error(f"题目不存在: UUID={challenge_uuid}")
        return HttpResponseForbidden("<h1>404 未找到</h1><p>题目不存在</p>")
    
    except Exception as e:
        logger.error(
            f"URL下载异常: 用户={request.user.username}, "
            f"题目UUID={challenge_uuid}, 错误={str(e)}",
            exc_info=True
        )
        return HttpResponse(
            "<h1>500 服务器错误</h1><p>文件下载失败，请稍后重试</p>",
            status=500
        )
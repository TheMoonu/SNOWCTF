from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from public.utils import clear_competition_cache,clear_user_teams_cache,sanitize_html,validate_docker_compose,escape_xss,unescape_content,create_captcha_for_registration,site_full_url,clear_ranking_cache
from django.db import transaction
from django.core.cache import cache
from secsnow.celery import app
from celery.result import AsyncResult
from django.core.paginator import EmptyPage, PageNotAnInteger
import logging

# 使用apps.competition作为logger名称，匹配settings.py中的配置

from django.views.decorators.cache import cache_page
from django.utils.decorators import method_decorator
from datetime import timedelta
from container.models import UserContainer, DockerEngine
from competition.models import Competition, ScoreUser, ScoreTeam, Team,CheatingLog,Registration,Submission,Challenge,Tag,Writeup,WriteupTemplate
from competition.flag_generator import get_or_generate_flag, verify_flag as verify_flag_func
from competition.redis_cache import UserContainerCache
from django.db.models import Count, Exists, OuterRef
from django.conf import settings
from django.views import generic
import time
from django.views.generic import TemplateView
from datetime import datetime
from django.db.models import F
from easytask.tasks import cleanup_container
from django.contrib.auth.decorators import login_required
from django.db.models import Exists, OuterRef, Q
from django.views.decorators.cache import never_cache
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from urllib3.exceptions import NewConnectionError, MaxRetryError
from requests.exceptions import ConnectionError, ReadTimeout
from haystack.query import SearchQuerySet
import docker
import json
import requests
import urllib.parse
import uuid
import random
import math
import csv
import codecs
from django.utils.text import slugify
from docker.errors import APIError,DockerException
from .view_api import create_container_api, DistributedLock
from celery import current_app
from django.views.generic import CreateView, FormView
from django.urls import reverse_lazy
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from functools import wraps
from django.core.validators import RegexValidator
from django.core.exceptions import ValidationError
import re


# 核心验证（深度嵌入）

import yaml
from django.utils.functional import cached_property
import hashlib
from django.views.generic import ListView
from django.urls import reverse
from .forms import TeamSelectionForm, PersonalInfoForm, RegistrationConfirmForm, ChallengeCreateForm, ChallengeForm
from django.views.decorators.cache import cache_page
from comment.models import SystemNotification
from django.http import HttpResponse
import logging
import codecs
from django.utils.text import slugify
from django.views.decorators.http import require_POST
from django.utils.html import escape

logger = logging.getLogger('apps.competition')

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
    try:
        challenge_uuid = request.GET.get('challenge_uuid', '').strip()
        if not challenge_uuid:
            return JsonResponse({"error": "缺少挑战 UUID"}, status=400)
        

        if not (len(challenge_uuid) == 8 and challenge_uuid.isalnum() or len(challenge_uuid) == 10 and challenge_uuid.isalnum()):
            return JsonResponse({"error": "无效的题目标识,请检查题目标识是否正确"}, status=400)

        cached_container = UserContainerCache.get(request.user.id, challenge_uuid)
        if cached_container:
            # 优先使用缓存中的 container_urls（支持多协议）
            container_urls = cached_container.get('container_urls')
            
            if container_urls:
                # 使用缓存的URL列表（已包含协议信息）
                return JsonResponse({
                    "status": "active",
                    "container_urls": container_urls,
                    "expires_at": cached_container['expires_at']
                })
            else:

                ports = json.loads(cached_container['port'])
                container_urls = []
                
                url_prefix = cached_container.get('url_prefix')
                
                for port in ports.values():
                    if cached_container['domain'] and url_prefix:
                        url = f"http://{url_prefix}.{cached_container['domain']}:{port}"
                    elif cached_container['domain']:
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
        return JsonResponse({"error": "请求错误"}, status=500)

   
@require_http_methods(["POST"])
#@csrf_exempt  # 临时禁用CSRF验证用于负载测试
@login_required  # 临时注释用于测试
def create_web_container(request, slug):
    """
    创建 Web 容器（异步队列版 - 支持高并发）
    
    优化策略：
    -  使用Celery异步任务队列，避免阻塞Web Worker
    -  快速返回任务ID，前端轮询查询状态
    -  支持瞬时高并发（队列削峰填谷）
    -  完整的权限检查和输入验证
    -  分级限流（用户级 + 题目级）
    
    流程：
    1. 前置检查（速率限制、参数验证、权限检查）
    2. 提交异步任务到队列
    3. 返回任务ID
    4. 前端通过task_id轮询任务状态
    """
    challenge_uuid = None
    
    try:
        # 0. 测试模式：从请求头获取用户ID（用于负载测试）
        """ if not request.user.is_authenticated:
            test_user_id = request.headers.get('X-Test-User-Id')
            if test_user_id:
                try:
                    from django.contrib.auth import get_user_model
                    User = get_user_model()
                    request.user = User.objects.get(id=int(test_user_id))
                    logger.debug(f"测试模式：使用用户ID {test_user_id} ({request.user.username})")
                except (User.DoesNotExist, ValueError) as e:
                    logger.warning(f"无效的测试用户ID: {test_user_id}, 错误: {str(e)}")
                    return JsonResponse({"error": f"无效的测试用户ID: {test_user_id}"}, status=400)
            else:
                logger.warning("未登录且未提供测试用户ID")
                return JsonResponse({"error": "未登录，请提供 X-Test-User-Id 请求头"}, status=401) """
        
        # 1. 安全检查：验证请求来源
        if not request.headers.get('x-requested-with') == 'XMLHttpRequest':
            logger.warning(f"用户 {request.user.id} 发起非 AJAX 请求创建容器")
            return JsonResponse({"error": "无效的请求"}, status=400)
        
        # 2. 参数验证
        challenge_uuid = request.POST.get('challenge_uuid', '').strip()
        if not challenge_uuid:
            logger.warning(f"用户 {request.user.id} 请求缺少 challenge_uuid")
            return JsonResponse({"error": "缺少必要参数"}, status=400)
        
        # 3. 短 UUID 格式验证（8位字母数字组合，防止注入）
        if not (len(challenge_uuid) == 8 and challenge_uuid.isalnum() or len(challenge_uuid) == 10 and challenge_uuid.isalnum()):
            logger.warning(f"用户 {request.user.id} 提供了无效的题目标识: {challenge_uuid}")
            return JsonResponse({"error": "无效的题目标识"}, status=400)
        
        # 4. 快速失败检查：速率限制（在数据库查询前检查缓存）
        rate_limit_key = f"rate_limit:container:{request.user.id}"
        if cache.get(rate_limit_key):
            return JsonResponse({"error": "操作过于频繁，请稍后再试"}, status=429)
        
        # 5. 检查是否有正在处理的任务
        pending_task_key = f"container_task_user:{request.user.id}:{challenge_uuid}"
        pending_task_id = cache.get(pending_task_key)
        if pending_task_id:
            logger.info(f"用户 {request.user.id} 已有待处理的容器创建任务: {pending_task_id}")
            return JsonResponse({
                "status": "queued",
                "task_id": pending_task_id,
                "message": "容器创建任务已在队列中，请等待..."
            }, status=202)
        
        # 5.1 检查容器创建锁（防止刷新页面时的竞态条件）
        container_lock_key = f"container_lock:{request.user.id}:{challenge_uuid}"
        if cache.get(container_lock_key):
            logger.info(f"用户 {request.user.id} 的容器创建锁存在，等待中...")
            return JsonResponse({
                "error": "容器创建请求正在处理中，请稍候...",
                "retry_after": 3
            }, status=429)
        
        # 6. 获取题目和比赛信息（使用 select_related 减少查询）
        try:
            challenge = Challenge.objects.select_related(
                'docker_image'
            ).get(uuid=challenge_uuid)
        except Challenge.DoesNotExist:
            logger.warning(f"用户 {request.user.id} 尝试访问不存在的题目: {challenge_uuid}")
            return JsonResponse({"error": "题目不存在"}, status=404)
        
        # 7. 使用缓存获取比赛信息
        competition_cache_key = f"competition:{slug}"
        competition = cache.get(competition_cache_key)
        if not competition:
            competition = get_object_or_404(Competition, slug=slug)
            cache.set(competition_cache_key, competition, timeout=300)  # 缓存5分钟
        
        # 8. 权限检查：题目是否属于该比赛（使用缓存）
        challenge_in_competition_key = f"challenge_in_comp:{challenge_uuid}:{slug}"
        is_in_competition = cache.get(challenge_in_competition_key)
        if is_in_competition is None:
            is_in_competition = competition.challenges.filter(uuid=challenge_uuid).exists()
            cache.set(challenge_in_competition_key, is_in_competition, timeout=300)
        
        if not is_in_competition:
            logger.warning(
                f"用户 {request.user.id} 尝试访问不属于比赛 {slug} 的题目 {challenge_uuid}"
            )
            return JsonResponse({"error": "该题目不属于当前比赛"}, status=403)
        
        # 9. 权限检查：用户是否有权参赛
        is_competition_author = (
            request.user.is_superuser or 
            request.user.is_staff or 
            competition.author == request.user
        )
        if not is_competition_author:
            # 使用缓存检查报名状态
            reg_cache_key = f"registration:{competition.id}:{request.user.id}"
            registration = cache.get(reg_cache_key)
            
            if registration is None:
                try:
                    registration = Registration.objects.select_related('team_name').get(
                        competition=competition,
                        user=request.user
                    )
                    cache.set(reg_cache_key, registration, timeout=60)  # 缓存1分钟
                except Registration.DoesNotExist:
                    cache.set(reg_cache_key, False, timeout=60)
                    registration = False
            
            if not registration:
                logger.info(f"用户 {request.user.id} 未报名比赛 {slug}")
                return JsonResponse({
                    "error": "您还未报名该比赛，请先报名后再尝试"
                }, status=403)
            
            # 检查审核状态
            if competition.is_audit and not registration.audit:
                logger.info(f"用户 {request.user.id} 的比赛 {slug} 审核未通过")
                return JsonResponse({
                    "error": "您的报名审核尚未通过，暂时无法创建题目"
                }, status=403)
            
            # 检查团队赛要求
            if competition.competition_type == Competition.TEAM:
                if not registration.team_name:
                    logger.info(f"用户 {request.user.id} 未加入团队")
                    return JsonResponse({
                        "error": "团队赛需要加入队伍后才能参与"
                    }, status=403)
        
        # 10. 快速检查：是否已有运行中的容器（优先检查缓存）

        
        # 先检查缓存
        cached_container = UserContainerCache.get(request.user.id, challenge_uuid)
        
        if cached_container:
            logger.info(f"用户 {request.user.id} 已有缓存的容器: {cached_container['container_id']}")
            
            # 优先使用 container_urls（多协议支持）
            container_urls = cached_container.get('container_urls')
            
            if container_urls:
                # 新格式缓存：已包含完整的协议信息
                access_url = container_urls
            else:
                # 兼容旧格式缓存：生成 HTTP URL
                access_url = []
                if cached_container.get('domain'):
                    url_prefix = cached_container.get('url_prefix', '')
                    access_url = [f"http://{url_prefix}.{cached_container['domain']}"]
                elif cached_container.get('ip_address') and cached_container.get('port'):
                    ports = cached_container['port']
                    if isinstance(ports, str):
                        ports = [p.strip() for p in ports.split(',') if p.strip()]
                    elif isinstance(ports, int):
                        ports = [str(ports)]
                    access_url = [f"http://{cached_container['ip_address']}:{p}" for p in ports]
            
            return JsonResponse({
                "status": "running",
                "container_id": cached_container['container_id'],
                "access_url": access_url,
                "expires_at": cached_container.get('expires_at'),
                "message": "容器已存在"
            }, status=200)
        
        # 缓存未命中，查询数据库
        now = timezone.now()
        existing_container = UserContainer.objects.filter(
            user=request.user,
            challenge_uuid=challenge_uuid,
            competition=competition,
            status='RUNNING',
            expires_at__gt=now
        ).select_related('docker_engine').first()
        
        if existing_container:
            logger.info(
                f"用户 {request.user.id} 已有运行中的容器: {existing_container.container_id}"
            )
            
            # 回写缓存（使用 UserContainerCache）
            UserContainerCache.set(existing_container)
            
            # 优先使用 container_urls（多协议支持）
            if existing_container.container_urls:
                # 新格式：已包含完整的协议信息
                try:
                    import json
                    access_url = json.loads(existing_container.container_urls) if isinstance(existing_container.container_urls, str) else existing_container.container_urls
                except:
                    access_url = existing_container.container_urls
            else:
                # 兼容旧格式：生成 HTTP URL
                access_url = []
                if existing_container.domain:
                    access_url = [f"http://{existing_container.domain}"]
                elif existing_container.ip_address and existing_container.port:
                    ports = existing_container.port
                    if isinstance(ports, str):
                        ports = [p.strip() for p in ports.split(',') if p.strip()]
                    elif isinstance(ports, int):
                        ports = [str(ports)]
                    access_url = [f"http://{existing_container.ip_address}:{p}" for p in ports]
            
            return JsonResponse({
                "status": "running",
                "container_id": existing_container.container_id,
                "access_url": access_url,
                "expires_at": existing_container.expires_at.isoformat(),
                "message": "容器已存在"
            }, status=200)
        
        # 11. 关键：提交任务前的资源预检（防止集群被爆）
        # 使用新的资源预检模块（统一管理，避免代码重复）
        precheck_result = None
        try:
            # 确定资源限制（单镜像 or 拓扑）
            memory_limit = 512
            cpu_limit = 1.0
            
            if challenge.docker_image:
                # 单镜像场景
                docker_image = challenge.docker_image
                memory_limit = docker_image.memory_limit or 512
                cpu_limit = docker_image.cpu_limit or 1.0
            elif hasattr(challenge, 'network_topology_config') and challenge.network_topology_config:
                # 拓扑场景：从拓扑配置中获取最大资源限制
                topology_config = challenge.network_topology_config
                memory_limit, cpu_limit = topology_config.get_max_resources()
            
            # 检查题目是否需要容器
            if challenge.docker_image or (hasattr(challenge, 'network_topology_config') and challenge.network_topology_config):
                from container.container_resource_precheck import (
                    ContainerResourcePrecheck,
                    get_http_status_for_error
                )
                
                # 创建资源预检管理器
                precheck = ContainerResourcePrecheck(
                    memory_limit=memory_limit,
                    cpu_limit=cpu_limit,
                    challenge=challenge
                )
                
                # 执行资源预检（多层防护：并发限流 + K8s节点选择 + Docker资源检查）
                success, error_msg = precheck.check(user_id=request.user.id)
                
                if not success:
                    # 预检失败，返回友好的错误信息
                    status_code = get_http_status_for_error(error_msg)
                    
                    logger.warning(
                        f"资源预检失败，拒绝任务提交: user={request.user.id}, "
                        f"challenge={challenge_uuid}, 错误={error_msg}"
                    )
                    
                    return JsonResponse({
                        "error": error_msg,
                        "retry_after": 5  # 建议5秒后重试
                    }, status=status_code)
                
                # 预检通过，保存结果（用于传递给Celery任务）
                precheck_result = precheck.get_result_for_celery()
                
                logger.info(
                    f"资源预检通过: user={request.user.id}, "
                    f"engine_type={precheck_result['engine_type']}, "
                    f"engine_id={precheck_result['engine_id']}, "
                    f"details={precheck_result}"
                )
            
        except Exception as e:
            # 预检过程异常（非预期错误）
            logger.error(
                f"资源预检异常: user={request.user.id}, "
                f"challenge={challenge_uuid}, 错误={str(e)}",
                exc_info=True
            )
            
            # 清理可能已预占的资源
            if precheck_result:
                try:
                    precheck.cleanup_on_error()
                except:
                    pass
            
            return JsonResponse({
                "error": "资源预检失败，请稍后再试或联系管理员",
                "retry_after": 5
            }, status=500)
        
        # 12. 提交异步任务到Celery队列
        from competition.tasks import create_container_async
        
        # 准备请求元数据
        request_meta = {
            'REMOTE_ADDR': request.META.get('REMOTE_ADDR'),
            'HTTP_USER_AGENT': request.META.get('HTTP_USER_AGENT'),
        }
        
        # 合并预检结果（如果有）
        if precheck_result:
            request_meta.update(precheck_result)
        
        logger.debug(f"提交任务元数据: {request_meta}")
        
        # 异步执行容器创建任务
        task = create_container_async.apply_async(
            args=[challenge_uuid, request.user.id, competition.id, request_meta],
        )
        
        # 记录任务ID（防止重复提交）
        cache.set(pending_task_key, task.id, timeout=300)
        
        logger.info(
            f"用户 {request.user.username} 提交容器创建任务: "
            f"task_id={task.id}, 题目={challenge.title}"
        )
        
        # 13. 返回任务ID（前端通过此ID轮询状态）
        return JsonResponse({
            "status": "queued",
            "task_id": task.id,
            "message": "容器创建任务已提交，请稍候...",
            "poll_url": f"/api/v1/{slug}/container/task/{task.id}/"
        }, status=202)
    
    except Challenge.DoesNotExist:
        logger.error(f"题目不存在: {challenge_uuid}")
        return JsonResponse({"error": "题目不存在"}, status=404)
    
    except Competition.DoesNotExist:
        logger.error(f"比赛不存在: {slug}")
        return JsonResponse({"error": "比赛不存在"}, status=404)
    
    except Exception as e:
        logger.error(
            f"提交容器创建任务失败: 用户={request.user.id}, "
            f"题目={challenge_uuid}, 错误={str(e)}", 
            exc_info=True
        )
        return JsonResponse({
            "error": "系统错误，请稍后再试或联系管理员"
        }, status=500)


@login_required
@require_http_methods(["GET"])
def query_container_task_status(request, slug, task_id):
    """
    查询容器创建任务状态（轮询接口）
    
    支持两种任务类型：
    - 单容器任务（container_task:xxx）
    - 场景任务（scenario_task:xxx）
    
    前端通过此接口轮询任务进度
    
    返回格式：
    {
        "status": "pending" | "processing" | "success" | "failed" | "timeout",
        "progress": 0-100,
        "message": "状态描述",
        "data": {容器信息} (仅在success时)
        "error": "错误信息" (仅在failed时)
        "task_type": "container" | "scenario"
    }
    """
    try:
        # 优先从缓存中获取任务状态（tasks.py写入的）
        # 同时检查容器任务和场景任务
        cache_key = f"container_task:{task_id}"
        task_info = cache.get(cache_key)
        task_type = "container"
        
        if not task_info:
            # 尝试查找场景任务
            cache_key = f"scenario_task:{task_id}"
            task_info = cache.get(cache_key)
            task_type = "scenario"
        
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
                "task_type": task_type,
                "state": state_mapping.get(status, 'PENDING'),
                "message": task_info.get('message', ''),
                "percent": task_info.get('progress', 0)
            }
            
            # 如果任务成功，包含容器/场景数据
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
            "status": "error",
            "message": "查询任务状态失败"
        }, status=500)


@login_required
@require_http_methods(["POST"])
def cancel_container_task(request, slug, task_id):
    """
    取消容器创建任务
    
    用户可以取消正在等待或处理中的任务
    """
    try:
        from celery import current_app
        
        # 尝试撤销任务
        current_app.control.revoke(task_id, terminate=True, signal='SIGKILL')
        
        # 更新缓存中的任务状态
        cache_key = f"container_task:{task_id}"
        task_info = cache.get(cache_key)
        if task_info:
            task_info['status'] = 'cancelled'
            task_info['message'] = '任务已被用户取消'
            cache.set(cache_key, task_info, timeout=300)
        
        # 清理待处理任务标记
        pending_task_keys = cache.keys(f"container_task_user:{request.user.id}:*")
        for key in pending_task_keys:
            if cache.get(key) == task_id:
                challenge_uuid = key.split(':')[-1]  # 从key中提取challenge_uuid
                
             
                container_lock_key = f"container_lock:{request.user.id}:{challenge_uuid}"
                if cache.get(container_lock_key):
                    cache.delete(container_lock_key)
                    logger.info(f"清除容器创建锁: {container_lock_key}")
                
                cache.delete(key)
        
        logger.info(f"用户 {request.user.username} 取消了容器创建任务: {task_id}")
        
        return JsonResponse({
            "status": "cancelled",
            "message": "任务已取消"
        })
    
    except Exception as e:
        logger.error(f"取消任务失败: task_id={task_id}, error={str(e)}", exc_info=True)
        return JsonResponse({
            "error": "取消任务失败"
        }, status=500)




def update_rankings_async(competition_id, is_team=False):
    """
    异步更新排名（在事务提交后调用）
    使用独立事务，避免阻塞主事务
    """
    try:
        competition = Competition.objects.get(id=competition_id)
        
        if is_team:
            # 更新团队排名
            all_teams = ScoreTeam.objects.filter(competition=competition).order_by('-score', 'time')
            teams_to_update = []
            for index, team in enumerate(all_teams, 1):
                if team.rank != index:
                    team.rank = index
                    teams_to_update.append(team)
            
            if teams_to_update:
                ScoreTeam.objects.bulk_update(teams_to_update, ['rank'], batch_size=100)
        
        # 个人排名始终需要更新
        all_users = ScoreUser.objects.filter(competition=competition).order_by('-points', 'created_at')
        users_to_update = []
        for index, user in enumerate(all_users, 1):
            if user.rank != index:
                user.rank = index
                users_to_update.append(user)
        
        if users_to_update:
            ScoreUser.objects.bulk_update(users_to_update, ['rank'], batch_size=100)
            
        # 清除排名缓存
        clear_ranking_cache(competition)
        
    except Exception as e:
        logger.error(f"更新排名时发生错误: {str(e)}", exc_info=True)


def trigger_dashboard_update(competition_id, submission):
    """
    触发数据大屏实时更新（在事务提交后调用）
    """
    try:
        from .dashboard_service import get_dashboard_service
        service = get_dashboard_service(competition_id)
        service.on_flag_submitted(submission)
    except Exception as e:
        logger.error(f"触发数据大屏更新失败: {str(e)}", exc_info=True)


def clear_competition_ranking_cache(competition_id, is_team=False, user_id=None, team_id=None):
    """
    清除比赛排行榜缓存（优化版 - 清除所有分页缓存）
    
    Args:
        competition_id: 比赛ID
        is_team: 是否为团队赛（True时额外清除队伍排行榜）
        user_id: 用户ID（用于清除特定用户相关缓存）
        team_id: 队伍ID（团队赛时传入，用于清除团队缓存）
    """
    # 获取比赛信息以确定竞赛类型
    try:
        competition = Competition.objects.get(id=competition_id)
        competition_type = competition.competition_type
    except Competition.DoesNotExist:
        logger.error(f"清除缓存失败：比赛 {competition_id} 不存在")
        return
    
    # 清除个人排行榜数据缓存（所有常用limit值）
    for limit in [10, 20, 50, 100]:
        cache.delete(f'user_ranking:{competition_id}:{limit}')
    
    # 清除个人排行榜分页缓存（清除前20页，覆盖1000条数据）
    try:
        # 尝试使用 Redis 的模糊删除（推荐）
        cache.delete_pattern(f'rankings_page:{competition_id}:individual:page_*')
    except AttributeError:
        # 如果不支持 delete_pattern，手动删除前20页
        for page in range(1, 21):
            cache.delete(f'rankings_page:{competition_id}:individual:page_{page}')
    
    # 清除新增的CTF排行榜API缓存
    try:
        cache.delete_pattern(f'ctf_rankings_{competition_id}_{competition_type}_page_*')
    except AttributeError:
        for page in range(1, 21):
            cache.delete(f'ctf_rankings_{competition_id}_{competition_type}_page_{page}')
    
    # 如果是团队赛，额外清除队伍排行榜缓存
    if is_team:
        for limit in [10, 20, 50, 100]:
            cache.delete(f'team_ranking:{competition_id}:{limit}')
        
        # 清除队伍排行榜分页缓存
        try:
            cache.delete_pattern(f'rankings_page:{competition_id}:team:page_*')
        except AttributeError:
            for page in range(1, 21):
                cache.delete(f'rankings_page:{competition_id}:team:page_{page}')
    
    # 清除解题动态页面缓存（清理所有分页的缓存）
    try:
        # 尝试使用 Redis 的模糊删除（推荐）
        cache.delete_pattern(f'submission_dynamic_v2:{competition_id}:page:*')
    except AttributeError:
        # 如果不支持 delete_pattern，则清理前几页的缓存（最多清理50页）
        keys_to_delete = [f'submission_dynamic_v2:{competition_id}:page:{i}' for i in range(1, 51)]
        cache.delete_many(keys_to_delete)
    
    # 清除用户统计数据缓存
    if is_team and team_id:
        # 团队赛：清除团队级别缓存和个人级别缓存
        cache.delete(f'team_score_data:{team_id}:{competition_id}')
        # 同时清除个人缓存
        if user_id:
            cache.delete(f'user_stats:{user_id}:{competition_id}')
    elif user_id:
        # 个人赛：清除个人统计缓存
        cache.delete(f'user_stats:{user_id}:{competition_id}')
    


def _build_score_message(total_points, blood_bonus, time_bonus, solve_rank, is_team=False):
    """构建得分消息（提取重复逻辑）"""
    prefix = '您的队伍' if is_team else '您'
    message_parts = [f'恭喜！Flag 正确，{prefix}获得 {total_points} 分']
    
    if blood_bonus > 0:
        blood_ranks = {1: '一血', 2: '二血', 3: '三血'}
        blood_rank = blood_ranks.get(solve_rank, f'第{solve_rank}名')
        message_parts.append(f'({blood_rank}奖励+{blood_bonus})')
    
    if time_bonus > 0:
        message_parts.append(f'(时间奖励+{time_bonus})')
    
    return ' '.join(message_parts)


def _check_rate_limit(user_id):
    """检查用户提交频率限制"""
    rate_limit_key = f"flag_submit:{user_id}"
    if cache.get(rate_limit_key):
        return False, '提交过于频繁，请稍后再试'
    cache.set(rate_limit_key, True, timeout=2)  # 2秒限流
    return True, None


def _get_user_ip(request):
    """获取用户真实IP"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    return x_forwarded_for.split(',')[0] if x_forwarded_for else request.META.get('REMOTE_ADDR')


@login_required
@require_http_methods(["POST"])
def verify_flag(request, slug,filedownload=False):
    """
    验证flag提交接口（高并发优化版 v2）
    1. 添加频率限制防止暴力破解
    2. 使用分布式锁防止队伍重复计分
    3. 修复并发安全问题
    """
    # ===== 第一阶段：快速失败检查 =====
    
    # 请求方法检查
    if not request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.method != "POST":
        return JsonResponse({"status": "error", "message": "请求错误"}, status=500)
    
    # 频率限制检查
    rate_ok, rate_msg = _check_rate_limit(request.user.id)
    if not rate_ok:
        return JsonResponse({'status': 'error', 'message': rate_msg}, status=429)
    
    # 获取参数
    challenge_uuid = request.POST.get('challenge_uuid', '').strip()
    submitted_flag = request.POST.get('flag', '').strip()
    file_downloaded = request.POST.get('filedownload', 'false').lower() == 'true'
    
    if not all([challenge_uuid, submitted_flag]):
        return JsonResponse({'status': 'error', 'message': '缺少必要参数'}, status=400)
    
    # 验证短 UUID 格式（8位字母数字组合）
    if not (len(challenge_uuid) == 8 and challenge_uuid.isalnum() or len(challenge_uuid) == 10 and challenge_uuid.isalnum()):
        return JsonResponse({'status': 'error', 'message': '无效的题目标识'}, status=400)
    
    # 提前获取IP
    ip = _get_user_ip(request)
    user = request.user
    
    # ===== 第二阶段：数据获取与权限检查 =====
    
    # 获取比赛
    competition = get_object_or_404(Competition, slug=slug)
    
    # 优化：一次性查询题目并验证是否属于比赛
    try:
        challenge = competition.challenges.get(uuid=challenge_uuid)
    except Challenge.DoesNotExist:
        return JsonResponse({
            'status': 'error',
            'message': '题目不存在或不属于当前比赛'
        }, status=400)
    
    # 比赛作者/管理员测试模式（不计分）
    is_competition_author = competition.author == user or user.is_superuser or user.is_staff
    if is_competition_author:
        is_correct, error_msg = verify_flag_func(submitted_flag, challenge, user, competition, ip, is_admin_test=True)
        return JsonResponse({
            'status': 'success' if is_correct else 'error',
            'is_docker': bool(challenge.docker_image),
            'message': 'flag回答正确（测试模式，不计分）' if is_correct else 'FLAG回答错误'
        }, status=200 if is_correct else 400)
    
    # 检查用户是否报名
    try:
        registration = Registration.objects.get(competition=competition, user=user)
    except Registration.DoesNotExist:
        return JsonResponse({
            'status': 'error',
            'message': '您还未报名该比赛，请先报名后再尝试'
        }, status=403)

    # 检查审核状态
    if competition.is_audit and not registration.audit:
        return JsonResponse({
            'status': 'error',
            'message': '您还未通过审核，暂时无法提交flag'
        }, status=403)
    
    # 检查题目和比赛状态
    if not challenge.is_active:
        return JsonResponse({'status': 'error', 'message': '该题目当前未启用'}, status=400)
    
    now = timezone.now()
    if now < competition.start_time:
        return JsonResponse({'status': 'error', 'message': '比赛尚未开始'}, status=400)
    if now > competition.end_time:
        return JsonResponse({'status': 'error', 'message': '比赛已经结束'}, status=400)
    
    # 检查容器题目
    is_docker = bool(challenge.docker_image)
    if is_docker:
        cached_container = UserContainerCache.get(user.id, challenge_uuid)
        if not cached_container or \
           datetime.fromisoformat(cached_container['expires_at']) < timezone.now() or \
           cached_container['challenge_uuid'] != str(challenge_uuid):
            logger.warning(
                f"用户 {user.username} 尝试提交Flag但容器不存在: "
                f"题目={challenge.title}, UUID={challenge_uuid}, 比赛={competition.title}"
            )
            return JsonResponse({'status': 'error', 'message': '题目环境未创建或环境已过期，请先启动容器'}, status=400)
    
    # ===== 第三阶段：分布式锁 + 原子事务处理 =====
    
    # 获取用户所在队伍（如果是团队赛）
    team = None
    if competition.competition_type == Competition.TEAM:
        team = Team.objects.filter(members=user, competition=competition).first()
        if not team:
            return JsonResponse({
                'status': 'error', 
                'message': '团队赛需要加入队伍后才能参与'
            }, status=400)
    
    # 关键优化：使用两级分布式锁防止并发问题
    # 1. 题目级锁：防止不同队伍同时解题导致solve_rank冲突（所有队伍共享）
    # 2. 队伍/用户级锁：防止同一队伍重复提交（每个队伍独立）
    from competition.distributed_lock import CacheLock
    
    # 题目级锁：确保solve_rank的顺序性
    challenge_lock_key = f"flag_submit:challenge:{competition.id}:{challenge_uuid}"
    challenge_lock = CacheLock(challenge_lock_key, timeout=15, retry_times=10, retry_delay=0.2)
    
    # 队伍/用户级锁：防止重复提交
    participant_lock_key = f"flag_submit:{competition.id}:{challenge_uuid}:{'team_' + str(team.id) if team else 'user_' + str(user.id)}"
    participant_lock = CacheLock(participant_lock_key, timeout=10, retry_times=5, retry_delay=0.3)
    
    try:
        # 先获取题目级锁（粗粒度，保护solve_rank）
        with challenge_lock() as challenge_acquired:
            if not challenge_acquired:
                logger.warning(f"用户 {user.username} 获取题目锁失败: {challenge_lock_key}")
                return JsonResponse({
                    'status': 'error', 
                    'message': '系统繁忙，请稍后再试'
                }, status=503)
            
            # 再获取队伍/用户级锁（细粒度，防止重复提交）
            with participant_lock() as participant_acquired:
                if not participant_acquired:
                    logger.warning(f"用户 {user.username} 获取参与者锁失败: {participant_lock_key}")
                    return JsonResponse({
                        'status': 'error', 
                        'message': '系统繁忙，请稍后再试'
                    }, status=503)
                
                with transaction.atomic():
                    # 在锁内重新查询题目，获取最新的solves数据（关键！）
                    challenge = Challenge.objects.select_for_update().get(uuid=challenge_uuid)

                    # 二次检查：在分布式锁内再次检查是否已解决（防止并发重复提交）
                    if competition.competition_type == Competition.TEAM:
                        score_team = ScoreTeam.objects.select_for_update().filter(
                            team=team, 
                            competition=competition
                        ).first()
                        
                        # 检查队伍是否已解决
                        if score_team and score_team.solved_challenges.filter(uuid=challenge_uuid).exists():
                            return JsonResponse({
                                'status': 'error', 
                                'message': '您的队伍已经解决了这道题目'
                            }, status=400)
                    else:
                        score_user = ScoreUser.objects.select_for_update().filter(
                            user=user,
                            competition=competition
                        ).first()
                        
                        # 检查个人是否已解决
                        if score_user and score_user.solved_challenges.filter(uuid=challenge_uuid).exists():
                            return JsonResponse({
                                'status': 'error', 
                                'message': '您已经解决了这道题目'
                            }, status=400)
                    
                    # 验证flag（使用锁内重新查询的challenge）
                    is_correct, error_msg = verify_flag_func(submitted_flag, challenge, user, competition, ip, file_downloaded=file_downloaded)

                    # 创建提交记录
                    submission = Submission.objects.create(
                        challenge=challenge,
                        user=user,
                        competition=competition,
                        team=team,
                        flag=submitted_flag,
                        status='correct' if is_correct else 'wrong',
                        ip=ip,
                        points_earned=0
                    )

                    if error_msg:
                        return JsonResponse({'status': 'error', 'message': error_msg}, status=400)
                    
                    if not is_correct:
                        return JsonResponse({'status': 'error', 'message': 'Flag 不正确，请再试一次'})
                    
                    # ===== Flag 正确，开始计分 =====
                    
                    from competition.scoring_system import calculate_ctf_score
                    
                    # 获取当前解题数（在更新前，从锁内重查的challenge获取）
                    current_solves = challenge.solves
                    solve_rank = current_solves + 1
                    
                    # 更新题目解题次数和动态分数
                    challenge.add_solve(competition)
                    challenge.refresh_from_db()
                    
                    # 计算比赛时间
                    competition_duration = (competition.end_time - competition.start_time).total_seconds()
                    time_elapsed = (now - competition.start_time).total_seconds()
                    
                    # 使用新计分系统计算总分
                    total_points, breakdown = calculate_ctf_score(
                        initial_points=challenge.initial_points,
                        minimum_points=challenge.minimum_points,
                        current_solves=current_solves,
                        solve_rank=solve_rank,
                        time_elapsed=time_elapsed,
                        total_duration=competition_duration,
                        difficulty=challenge.difficulty
                    )
                    
                    # 保存得分明细到提交记录
                    submission.points_earned = total_points
                    submission.base_score = breakdown['base_score']
                    submission.blood_bonus = breakdown['blood_bonus']
                    submission.time_bonus = breakdown['time_bonus']
                    submission.solve_rank = solve_rank
                    submission.save()
                    
                    # 更新个人分数（无论团队赛还是个人赛，都要更新个人分数）
                    score_user, _ = ScoreUser.objects.get_or_create(
                        user=user,
                        team=team,
                        competition=competition,
                        defaults={'points': 0}
                    )
                    score_user.update_score(total_points)
                    score_user.solved_challenges.add(challenge)
         
                    # 修复：保存变量值，避免lambda闭包陷阱
                    comp_id = competition.id
                    usr_id = user.id
                    submission_id = submission.id
                    
                    # 如果是团队赛，更新团队分数
                    if competition.competition_type == Competition.TEAM:
                        score_team, _ = ScoreTeam.objects.get_or_create(
                            team=team,
                            competition=competition,
                            defaults={'score': 0}
                        )
                        
                        score_team.update_score(total_points)
                        score_team.solved_challenges.add(challenge)
                        
                        # 修复：使用局部变量避免闭包陷阱
                        team_id_var = team.id
                        def clear_cache_task():
                            clear_competition_ranking_cache(comp_id, is_team=True, user_id=usr_id, team_id=team_id_var)
                        
                        def update_rank_task():
                            #  优化：使用新的异步排名更新任务，延迟60秒批量更新
                            from competition.tasks import update_competition_rankings
                            update_competition_rankings.apply_async(
                                args=[comp_id],
                                countdown=60  # 延迟60秒，合并多个提交的排名更新
                            )
                        
                        def dashboard_task():
                            # 重新获取submission对象
                            from competition.models import Submission
                            sub = Submission.objects.filter(id=submission_id).first()
                            if sub:
                                trigger_dashboard_update(comp_id, sub)
                        
                        transaction.on_commit(clear_cache_task)
                        transaction.on_commit(update_rank_task)
                        transaction.on_commit(dashboard_task)
                        
                        # 构建得分明细消息
                        message = _build_score_message(
                            total_points, 
                            breakdown['blood_bonus'], 
                            breakdown['time_bonus'], 
                            solve_rank, 
                            is_team=True
                        )
                        
                        return JsonResponse({
                            'status': 'success',
                            'is_docker': is_docker,
                            'message': message,
                            'score_breakdown': breakdown
                        })
                    else:
                        # 个人赛
                        def clear_cache_task():
                            clear_competition_ranking_cache(comp_id, is_team=False, user_id=usr_id, team_id=None)
                        
                        def update_rank_task():
                            #  优化：使用新的异步排名更新任务，延迟60秒批量更新
                            from competition.tasks import update_competition_rankings
                            update_competition_rankings.apply_async(
                                args=[comp_id],
                                countdown=60  # 延迟60秒，合并多个提交的排名更新
                            )
                        
                        def dashboard_task():
                            from competition.models import Submission
                            sub = Submission.objects.filter(id=submission_id).first()
                            if sub:
                                trigger_dashboard_update(comp_id, sub)
                        
                        transaction.on_commit(clear_cache_task)
                        transaction.on_commit(update_rank_task)
                        transaction.on_commit(dashboard_task)
                        
                        # 构建得分明细消息
                        message = _build_score_message(
                            total_points, 
                            breakdown['blood_bonus'], 
                            breakdown['time_bonus'], 
                            solve_rank, 
                            is_team=False
                        )
                        
                        return JsonResponse({
                            'status': 'success',
                            'is_docker': is_docker,
                            'message': message,
                            'score_breakdown': breakdown
                        })
    
    except Challenge.DoesNotExist:
        # 锁内重新查询challenge时可能不存在（极少情况：题目被删除）
        logger.error(f"题目在锁内查询时不存在: challenge_uuid={challenge_uuid}")
        return JsonResponse({'status': 'error', 'message': '题目不存在'}, status=400)
    
    except Exception as e:
        logger.error(f"验证flag时发生错误: user={user.username}, challenge={challenge_uuid}, error={str(e)}", exc_info=True)
        return JsonResponse({'status': 'error', 'message': '系统错误，请稍后重试'}, status=500)   


# 更新 create_web_container_view 函数
def create_web_container_view(request):
    return CTFChallengeListView.as_view()(request)

@login_required
def challenge_detail(request, slug, uuid):
    # 获取对应的比赛
    competition = get_object_or_404(Competition, slug=slug)

    if not competition.is_active:
        messages.warning(request, '该比赛未激活')
        return redirect('competition:competition_detail', slug=slug)
    # 获取特定的挑战
    challenge = get_object_or_404(competition.challenges.all(), uuid=uuid)
    
    if challenge not in competition.challenges.all():
        messages.warning(request, "该题目不属于当前比赛")
        return redirect('competition:competition_detail', slug=slug)
    
    # 检查是否为管理员或比赛创建者
    is_admin_or_creator = request.user.is_superuser or (hasattr(competition, 'author') and competition.author == request.user)
    
    # 如果不是管理员或创建者，则需要检查报名状态和比赛状态
    if not is_admin_or_creator:
        if competition:  
            registration = Registration.objects.filter(
                competition=competition,
                user=request.user
            ).first()
            
            if not registration:
                messages.warning(request, "您还未报名该比赛，请先报名后再尝试")
                return redirect('competition:competition_detail', slug=slug)
            
            if competition.is_audit and not registration.audit:
                messages.warning(request, "您还未通过审核，暂时无法访问")
                return redirect('competition:competition_detail', slug=slug)
        
       
        # 检查访问权限
        if not competition.is_running():
            if competition.status == 'pending':
                messages.warning(request, f"比赛尚未开始，将于 {competition.start_time.strftime('%Y-%m-%d %H:%M')} 开始")
            else:  # ended
                messages.warning(request, f"比赛已于 {competition.end_time.strftime('%Y-%m-%d %H:%M')} 结束")
            return redirect('competition:competition_detail', slug=slug)
        
        if not challenge.is_active:
            messages.warning(request, "该题目当前未启用，暂时无法访问")
            return redirect('competition:competition_detail', slug=slug)

    # 如果是管理员或创建者，添加提示信息
    if is_admin_or_creator and (not competition.is_running() or not challenge.is_active):
        admin_message = ""
        if not competition.is_running():
            if competition.status == 'pending':
                admin_message = f"比赛尚未开始，将于 {competition.start_time.strftime('%Y-%m-%d %H:%M')} 开始。"
            else:  # ended
                admin_message = f"比赛已于 {competition.end_time.strftime('%Y-%m-%d %H:%M')} 结束。"
        
        if not challenge.is_active:
            admin_message += " 该题目当前未启用，普通用户无法访问。"
        
        
    
    # 获取文件下载URL（支持 static_files 和 static_file_url）
    file_url = None
    if (challenge.static_files or challenge.static_file_url) and (competition.is_running() or is_admin_or_creator):
        file_url = challenge.get_file_download_url(request.user, competition)
       
    context = {
        'challenge': challenge,
        'competition': competition,
        'file_url': file_url,  # 添加相关比赛到上下文
        'is_admin_or_creator': is_admin_or_creator,  # 添加管理员/创建者标志到上下文
    }
    
    # 根据比赛的theme字段选择不同的模板
    if competition.theme == 'tech':
        template_name = 'competition/tech/competition_detail.html'
    else:
        template_name = 'competition/anime/competition_detail.html'
    
    return render(request, template_name, context)


@login_required
@require_http_methods(["POST"])
def destroy_web_container(request):
    """
    异步销毁容器（CTF竞赛）
    触发异步任务后立即返回成功，后台继续处理
    """
    if not request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({"error": "请求错误"}, status=400)
    
    user = request.user
    challenge_uuid = request.POST.get('challenge_uuid', '').strip()
    
    if not challenge_uuid:
        return JsonResponse({"error": "缺少必要参数"}, status=400)
    
    # 验证短 UUID 格式（8位字母数字组合）
    if not (len(challenge_uuid) == 8 and challenge_uuid.isalnum() or len(challenge_uuid) == 10 and challenge_uuid.isalnum()):
        return JsonResponse({"error": "无效的题目标识"}, status=400)
    
    try:
        # 验证题目是否存在
        challenge = get_object_or_404(Challenge, uuid=challenge_uuid)
        
        # 调用异步任务（后台执行，不等待结果）
        from competition.tasks import destroy_container_async
        task = destroy_container_async.delay(user.id, challenge_uuid)
        
        logger.info(f"用户 {user.username} 发起异步销毁容器任务: task_id={task.id}, challenge={challenge.title}", extra={'request': request})
        
        # 立即返回成功响应，让前端可以继续操作
        return JsonResponse({
            'status': 'success',
            'message': '容器正在销毁中'
        })
    
    except Challenge.DoesNotExist:
        return JsonResponse({'error': '找不到指定的题目'}, status=404)
    
    except Exception as e:
        error_msg = f"销毁容器时发生未知错误"
        logger.error(f"用户 {user.username} 发起销毁任务失败: {str(e)}", extra={'request': request}, exc_info=True)
        return JsonResponse({'error': error_msg}, status=500)


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
        
        # 验证短 UUID 格式（8位字母数字组合）
        if not (len(challenge_uuid) == 8 and challenge_uuid.isalnum() or len(challenge_uuid) == 10 and challenge_uuid.isalnum()):
            return JsonResponse({"error": "无效的题目标识"}, status=400)

        try:
            challenge = get_object_or_404(Challenge, uuid=challenge_uuid)
            if not challenge.user_can_manage(user):
                return JsonResponse({
                    "error": "您没有权限删除此题目",
                    "redirect": reverse('ctf:challenge_detail', kwargs={'uuid': challenge_uuid})
                }, status=403)
            
            success, message = challenge.safe_delete(user)
            
            if success:
                messages.success(request, message)
                return JsonResponse({
                    "redirect": reverse('ctf:challenge_list')
                })
            else:
                messages.error(request, message)
                return JsonResponse({
                    "redirect": reverse('ctf:challenge_detail', kwargs={'uuid': challenge_uuid})
                }, status=400)
        
        except Challenge.DoesNotExist:
            return JsonResponse({"error": "题目不存在"}, status=404)
    except Exception as e:
        print(e)


def CompetitionViewList(request):

    # 获取所有比赛对象
    competitions = Competition.objects.filter(is_active=True).order_by('-start_time')  # 按创建时间倒序

    # 获取搜索参数
    search_query = request.GET.get('q', '')
    
    # 应用搜索过滤
    if search_query:
        competitions = competitions.filter(
            Q(title__icontains=search_query) | 
            Q(description__icontains=search_query) |
            Q(slug__icontains=search_query)
        )

    # 获取筛选参数
    status_filter = request.GET.get('status')
    type_filter = request.GET.get('type')

    # 根据状态筛选
    if status_filter == 'upcoming':
        competitions = competitions.filter(start_time__gt=timezone.now())
    elif status_filter == 'ongoing':
        competitions = competitions.filter(start_time__lte=timezone.now(), end_time__gte=timezone.now())
    elif status_filter == 'ended':
        competitions = competitions.filter(end_time__lt=timezone.now())

    # 根据类型筛选
    if type_filter == 'individual':
        competitions = competitions.filter(competition_type='individual')
    elif type_filter == 'team':
        competitions = competitions.filter(competition_type='team')

    # 设置分页
    page = request.GET.get('page', 1)
    paginate_by = getattr(settings, 'COMPETITION_PER_PAGE', 8)  # 从settings获取每页显示数量，默认10
    paginator = Paginator(competitions, paginate_by, 
                         orphans=getattr(settings, 'BASE_ORPHANS', 0))  # orphans防止最后一页数量太少

    try:
        competitions = paginator.page(page)
    except PageNotAnInteger:
        competitions = paginator.page(1)
    except EmptyPage:
        competitions = paginator.page(paginator.num_pages)

    context = {
        'competitions': competitions,
        'now': timezone.now(),
        'paginator': paginator,
        'is_paginated': paginator.num_pages > 1,  # 是否需要分页
        'page_obj': competitions,  # 当前页对象
        'search_query': search_query,  # 将搜索词传递给模板
        'status_filter': status_filter,  # 将状态筛选传递给模板
        'type_filter': type_filter,  # 将类型筛选传递给模板
        'total_count': paginator.count,
        'hide_footer': True,  # 总结果数
    }
    # 渲染模板并传递比赛对象列表和当前时间
    return render(request, 'competition/competition.html', context)
    

@method_decorator(login_required, name='dispatch')
class Competition_detail(ListView):
    model = Competition
    context_object_name = 'challenges'
    paginate_by = 30
    paginate_orphans = getattr(settings, 'BASE_ORPHANS', 0)
    
    def get_template_names(self):
        """根据比赛的theme字段动态选择模板"""
        competition_slug = self.kwargs.get('slug')
        competition = get_object_or_404(Competition, slug=competition_slug)
        if not competition.is_active:
            messages.warning(self.request, '该比赛未激活')
            return redirect('competition:competition_detail', slug=competition_slug)

        if competition.theme == 'tech':
            return ['competition/tech/com_index.html']
        else:
            return ['competition/anime/com_index.html']

    @method_decorator(never_cache)
    @method_decorator(require_http_methods(["GET", "POST"]))
    def dispatch(self, *args, **kwargs):

        competition_slug = self.kwargs.get('slug')
        competition = get_object_or_404(Competition, slug=competition_slug)
        
        # 如果竞赛关联了知识竞赛，且是从主入口访问（不是从双赛道页面点击进来），跳转到双赛道入口
        bypass_dual_track = kwargs.get('bypass_dual_track', False)
        if competition.related_quiz and not bypass_dual_track:
            return redirect('competition:dual_track_entrance', slug=competition_slug)
        
        # 检查访问权限
        if competition.visibility_type == 'internal':  # 如果是内部赛
            if not self.request.user.is_authenticated:
                # 未登录用户重定向到登录页
                return redirect('account_login')
            
            # 检查用户是否报名该比赛
            if competition.competition_type == 'team':
                # 团队赛 - 检查用户是否在参赛团队中
                user_registered = Registration.objects.filter(
                    user=self.request.user,
                    competition=competition,
                    audit=True
                ).exists()
            else:
                # 个人赛 - 检查用户是否报名
                user_registered = Registration.objects.filter(
                    user=self.request.user,
                    competition=competition,
                    audit=True  # 只有审核通过的报名才有效
                ).exists()
            
            if not user_registered and not self.request.user.is_staff and not competition.author == self.request.user:  # 管理员可以访问所有比赛
                # 用户未报名，显示提示信息并重定向
                messages.warning(self.request, '您没有暂时无权限访问')
                return redirect('competition:CompetitionView')
        
        # 保存比赛对象，避免在其他方法中重复查询
        self.competition = competition
        
        return super().dispatch(*args, **kwargs)

    def get_queryset(self):
        """获取查询集，包含所有过滤条件"""
        # 基础查询集
        competition_slug = self.kwargs.get('slug')
        competition = get_object_or_404(Competition, slug=competition_slug)
        queryset = competition.challenges.filter(is_active=True)

        # 应用类型过滤
        challenge_type = self.request.GET.get('type')
        if challenge_type and challenge_type != 'ALL':
            queryset = queryset.filter(category=challenge_type)
        
        # 应用难度过滤
        difficulty = self.request.GET.get('difficulty')
        if difficulty and difficulty != 'ALL':
            queryset = queryset.filter(difficulty=difficulty)


        # 应用搜索过滤
        search_query = self.request.GET.get('q', '').strip()
        if search_query:
            # 将搜索词分割成单独的关键词
            keywords = search_query.split()
            search_results = SearchQuerySet().models(Challenge)
            
            # 对每个关键词进行搜索
            for keyword in keywords:
                search_results = search_results.filter(content__contains=keyword)
                    
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
            if competition.competition_type == 'team':
                # 团队赛 - 检查用户所在队伍的解题记录
                team = Team.objects.filter(
                    members=self.request.user,
                    competition=competition
                ).first()
                
                if team:
                    solved_subquery = ScoreTeam.objects.filter(
                        team=team,
                        competition=competition,
                        solved_challenges=OuterRef('pk')
                    )
                else:
                    # 用户不在队伍中，显示所有题目为未解决
                    solved_subquery = ScoreTeam.objects.none()
            else:
                # 个人赛 - 检查用户个人的解题记录
                solved_subquery = ScoreUser.objects.filter(
                    user=self.request.user,
                    competition=competition,
                    solved_challenges=OuterRef('pk')
                )

            queryset = queryset.annotate(is_solved=Exists(solved_subquery))
            
            status = self.request.GET.get('status')
            if status == 'solved':
                queryset = queryset.filter(is_solved=True)
            elif status == 'unsolved':
                queryset = queryset.filter(is_solved=False)

        # 添加解题次数注解
        if competition.competition_type == 'team':
            # 团队赛 - 统计解决该题目的团队数
            queryset = queryset.annotate(
                solve_count=Count('scoreteam', filter=Q(scoreteam__competition=competition))
            )
        else:
            # 个人赛 - 统计解决该题目的用户数
            queryset = queryset.annotate(
                solve_count=Count('scoreuser', filter=Q(scoreuser__competition=competition))
            )

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
            page = paginator.page(1)
            
        return (paginator, page, page.object_list, page.has_other_pages())

    def get(self, request, *args, **kwargs):
        """重写 get 方法，处理分页重定向"""
        try:
            return super().get(request, *args, **kwargs)
        except EmptyPage:
            url = request.path
            query = request.GET.copy()
            query['page'] = '1'
            if query:
                url += '?' + query.urlencode()
            return redirect(url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        competition_slug = self.kwargs.get('slug')
        competition = get_object_or_404(Competition, slug=competition_slug)

        # 获取挑战类型
        challenge_types_key = f"competition_{competition_slug}_challenge_types"
        challenge_types = cache.get(challenge_types_key)
        if challenge_types is None:
            challenge_types = list(competition.challenges.values_list('category', flat=True).distinct())
            cache.set(challenge_types_key, challenge_types, 60*60)
        context['challenge_types'] = challenge_types

        # 获取难度级别
        difficulties_key = f"competition_{competition_slug}_difficulties"
        difficulties = cache.get(difficulties_key)
        if difficulties is None:
            difficulties = list(competition.challenges.values_list('difficulty', flat=True).distinct())
            cache.set(difficulties_key, difficulties, 60*60)
        context['difficulties'] = difficulties

        # 添加比赛信息
        context.update({
            'competition_title': competition.title,
            'end_time': competition.end_time,
            'competition_slug': competition.slug,
            'competition': competition,
        })

        # 添加团队信息（如果是团队赛）
        if competition.competition_type == 'team' and self.request.user.is_authenticated:
            context['user_team'] = Team.objects.filter(
                members=self.request.user,
                competition=competition
            ).first()

        # 添加用户得分信息
        if self.request.user.is_authenticated:
            if competition.competition_type == 'team':
                team = context.get('user_team')
                if team:
                    score_info = ScoreTeam.objects.filter(
                        team=team,
                        competition=competition
                    ).first()
                    context['score_info'] = score_info
            else:
                score_info = ScoreUser.objects.filter(
                    user=self.request.user,
                    competition=competition
                ).first()
                context['score_info'] = score_info

        # 添加当前的查询参数到上下文
        context.update({
            'current_type': self.request.GET.get('type', 'ALL'),
            'current_difficulty': self.request.GET.get('difficulty', 'ALL'),
            'current_status': self.request.GET.get('status', 'ALL'),
            'current_sort': self.request.GET.get('sort_by', 'id'),
            'current_author': self.request.GET.get('author', 'all'),
            'current_tag': self.request.GET.get('tag', ''),
            'search_query': self.request.GET.get('q', ''),
            
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
        })

        return context

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)



def _check_competition_time(competition, redirect_slug):
    """检查比赛时间是否允许报名"""
    now = timezone.now()
    if now >= competition.start_time:
        return ('error', '比赛已开始，无法报名', redirect_slug)
    if now >= competition.end_time:
        return ('error', '比赛已结束，无法报名', redirect_slug)
    return None


def _validate_user_profile(user):
    """验证用户个人信息是否完整"""
    if not user.real_name or not user.phones:
        return ('warning', '请先在个人中心完善个人信息（真实姓名和联系方式）', 'oauth:change_profile')
    return None


def _create_registration_notification(user, registration, team_name=None):
    """创建报名成功通知"""
    if team_name:
        content = f'''
            <div class="notification-content">
                <p>您已成功报名比赛：<strong>{escape(registration.competition.title)}</strong></p>
                <p>所在队伍：<strong>{escape(team_name)}</strong></p>
                <p class="text-muted mt-2">部分比赛可能需要审核报名信息，如有疑问，请联系比赛管理员。</p>
            </div>
        '''
    else:
        content = f'''
            <div class="notification-content">
                <p>您已成功报名比赛：<strong>{escape(registration.competition.title)}</strong></p>
                <p class="text-muted mt-2">部分比赛可能需要审核报名信息，如有疑问，请联系比赛管理员。</p>
            </div>
        '''
    
    notification = SystemNotification.objects.create(
        title='比赛报名通知',
        content=content
    )
    notification.get_p.add(user)

def _notification_admin(registration, user):
    """创建报名成功通知给管理员"""
    try:
        if registration:
            # 构建审核页面URL
            manage_url = reverse('competition:competition_manage', args=[registration.competition.slug])
            audit_url = f"{manage_url}?active_tab=audit"
            
            content = f'''
                <div class="notification-content">
                    <p>用户 <strong>{escape(user.username)}</strong> 报名了比赛 <strong>{escape(registration.competition.title)}</strong></p>
                    <p>请前往 <a href="{audit_url}" style="color: #1890ff; text-decoration: underline; font-weight: 500;">审核页面</a> 进行审核。</p>
                </div>
            '''
            
            notification = SystemNotification.objects.create(
                title='比赛报名审核通知',
                content=content
            )
            notification.get_p.add(registration.competition.author)
    except Exception as e:
        logger.error(f'创建报名成功通知失败: {str(e)}')
        pass




@login_required
def registrationView(request, slug, re_slug):
    """比赛报名视图"""
    competition = get_object_or_404(Competition, slug=slug, re_slug=re_slug)
    if not competition.is_register:
        messages.warning(request, '该比赛不允许报名')
        return redirect('competition:competition_detail', slug=slug)
    if not competition.is_active:
        messages.warning(request, '该比赛未激活')
        return redirect('competition:competition_detail', slug=slug)
    is_team_competition = competition.competition_type == Competition.TEAM
    
    # ========== 前置检查 ==========
    # 1. 检查是否已经报名

    if Registration.objects.filter(competition=competition, user=request.user).exists():
        messages.warning(request, '您已经报名过该比赛')
        return redirect('competition:competition_detail', slug=slug)
    
    # 2. 检查比赛时间
    time_check = _check_competition_time(competition, ('competition:competition_detail', slug))
    if time_check:
        msg_type, msg_text, redirect_to = time_check
        getattr(messages, msg_type)(request, msg_text)
        return redirect(*redirect_to) if isinstance(redirect_to, tuple) else redirect(redirect_to)
    
    # 3. 检查用户个人信息是否完整
    profile_check = _validate_user_profile(request.user)
    if profile_check and request.method == 'GET':
        # GET请求时显示提示，不阻止显示表单
        msg_type, msg_text, _ = profile_check
        getattr(messages, msg_type)(request, msg_text)
    
    # 4. 团队赛：检查用户是否已在队伍中
    existing_team = None
    if is_team_competition:
        existing_team = Team.objects.filter(competition=competition, members=request.user).first()

    # ========== POST 请求处理 ==========
    if request.method == 'POST':
        if is_team_competition:
            return _handle_team_registration(request, competition, slug, existing_team)
        else:
            return _handle_individual_registration(request, competition, slug)

    # ========== GET 请求处理 ==========
    if is_team_competition:
        form = TeamSelectionForm(competition=competition)
    else:
        form = RegistrationConfirmForm(competition=competition)

    captcha_data = create_captcha_for_registration()
    
    context = {
        'form': form,
        'competition': competition,
        'is_team_competition': is_team_competition,
        'existing_team': existing_team,
        'captcha_key': captcha_data['captcha_key'],
        'captcha_image': captcha_data['captcha_image']
    }
    return render(request, 'competition/registration.html', context)


def _handle_team_registration(request, competition, slug, existing_team):
    """处理团队赛报名"""
    
    # ========== 1. 验证验证码 ==========
    captcha = request.POST.get('captcha', '').strip().lower()
    captcha_key = request.POST.get('captcha_key', '').strip()
    
    if not captcha or not captcha_key:
        messages.error(request, '请输入验证码')
        return redirect('competition:registration_detail', slug=slug, re_slug=competition.re_slug)
    
    cache_key = f'registration_captcha_{captcha_key}'
    correct_captcha = cache.get(cache_key)
    
    if not correct_captcha:
        messages.error(request, '验证码已过期，请刷新页面')
        return redirect('competition:registration_detail', slug=slug, re_slug=competition.re_slug)
    
    if captcha != correct_captcha.lower():
        messages.error(request, '验证码错误，请重新输入')
        return redirect('competition:registration_detail', slug=slug, re_slug=competition.re_slug)
    
    # 验证成功，删除缓存
    cache.delete(cache_key)
    
    # ========== 2. 如果用户已在队伍中，直接使用existing_team ==========
    if existing_team:
        team = existing_team
    else:
        # ========== 3. 表单验证 ==========
        form = TeamSelectionForm(request.POST, competition=competition)
        if not form.is_valid():
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, error)
            return redirect('competition:registration_detail', slug=slug, re_slug=competition.re_slug)
        
        # 获取表单数据
        team_action = form.cleaned_data['team_action']
        team_name = form.cleaned_data['team_name']
        team_code = form.cleaned_data.get('team_code', '')
        invitation_code = form.cleaned_data.get('invitation_code', '')
        
        # ========== 4. 验证报名码（内部赛） ==========
        if competition.visibility_type == Competition.INTERNAL:
            if invitation_code != competition.invitation_code:
                messages.error(request, '报名码错误')
                return redirect('competition:registration_detail', slug=slug, re_slug=competition.re_slug)
        
        # ========== 5. 验证用户个人信息 ==========
        profile_check = _validate_user_profile(request.user)
        if profile_check:
            msg_type, msg_text, redirect_to = profile_check
            getattr(messages, msg_type)(request, msg_text)
            return redirect(redirect_to)
        
        # ========== 6. 创建或加入队伍 ==========
        try:
            with transaction.atomic():
                if team_action == 'create':
                    # 创建新队伍
                    if Team.objects.filter(name=team_name, competition=competition).exists():
                        messages.error(request, '该队伍名称已存在，请更换队伍名称')
                        return redirect('competition:registration_detail', slug=slug, re_slug=competition.re_slug)
                    
                    team = Team.objects.create(
                        name=team_name,
                        leader=request.user,
                        competition=competition
                    )
                    team.members.add(request.user)
                    
                    # 提示队伍配置信息
                    messages.info(
                        request, 
                        f'队伍创建成功！'
                    )
                    
                else:  # 'join'
                    # 加入已有队伍 - 必须三个条件都匹配
                    try:
                        team = Team.objects.get(
                            name=team_name,
                            competition=competition,
                            team_code=team_code
                        )
                    except Team.DoesNotExist:
                        messages.error(request, '队伍名称或认证码错误，请检查后重试')
                        return redirect('competition:registration_detail', slug=slug, re_slug=competition.re_slug)
                    
                    # 检查队伍人数（使用 Team 模型的辅助方法）
                    if not team.can_add_member():
                        current_count = team.get_current_member_count()
                        max_count = team.member_count
                        messages.error(request, f'该队伍成员已满（{current_count}/{max_count}人），无法加入')
                        return redirect('competition:registration_detail', slug=slug, re_slug=competition.re_slug)
                    
                    # 记录加入前的人数
                    current_count_before = team.get_current_member_count()
                    
                    team.members.add(request.user)
                    
                    # 刷新队伍数据
                    team.refresh_from_db()
                    
                    # 提示加入成功信息
                    current_count_after = team.get_current_member_count()
                    messages.info(
                        request,
                        f'成功加入队伍！'
                    )
                    
                    
        except Exception as e:
            logger.error(f'创建或加入队伍失败: {str(e)}')
            messages.error(request, f'操作失败,请稍后重试')
            return redirect('competition:registration_detail', slug=slug, re_slug=competition.re_slug)
    
    # ========== 7. 创建报名记录 ==========
    try:
        registration = Registration.objects.create(
            user=request.user,
            competition=competition,
            registration_type=Competition.TEAM,
            team_name=team
        )
        
        # 8. 创建系统通知
        _create_registration_notification(request.user, registration, team.name)
        
        # 9. 清除用户队伍缓存
        clear_user_teams_cache(request.user.id)
        
        # 10. 成功提示并跳转
        if competition.is_audit:
            _notification_admin(registration, request.user)

        messages.success(request, '报名成功！您所在的队伍为：' + team_name)
        return redirect('competition:competition_detail', slug=slug)
        
    except Exception as e:
        logger.error(f'报名失败: {str(e)}')
        messages.error(request, f'报名失败,请稍后重试')
        return redirect('competition:registration_detail', slug=slug, re_slug=competition.re_slug)


def _handle_individual_registration(request, competition, slug):
    """处理个人赛报名"""
    
    # ========== 1. 验证验证码 ==========
    captcha = request.POST.get('captcha', '').strip().lower()
    captcha_key = request.POST.get('captcha_key', '').strip()
    
    if not captcha or not captcha_key:
        messages.error(request, '请输入验证码')
        return redirect('competition:registration_detail', slug=slug, re_slug=competition.re_slug)
    
    cache_key = f'registration_captcha_{captcha_key}'
    correct_captcha = cache.get(cache_key)
    
    if not correct_captcha:
        messages.error(request, '验证码已过期，请刷新页面')
        return redirect('competition:registration_detail', slug=slug, re_slug=competition.re_slug)
    
    if captcha != correct_captcha.lower():
        messages.error(request, '验证码错误，请重新输入')
        return redirect('competition:registration_detail', slug=slug, re_slug=competition.re_slug)
    
    # 验证成功，删除缓存
    cache.delete(cache_key)
    
    # ========== 2. 表单验证 ==========
    form = RegistrationConfirmForm(request.POST, competition=competition)
    if not form.is_valid():
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect('competition:registration_detail', slug=slug, re_slug=competition.re_slug)
    
    # 获取表单数据
    invitation_code = form.cleaned_data.get('invitation_code', '')
    
    # ========== 3. 验证报名码（内部赛） ==========
    if competition.visibility_type == Competition.INTERNAL:
        if invitation_code != competition.invitation_code:
            messages.error(request, '报名码错误')
            return redirect('competition:registration_detail', slug=slug, re_slug=competition.re_slug)
    
    # ========== 4. 验证用户个人信息 ==========
    profile_check = _validate_user_profile(request.user)
    if profile_check:
        msg_type, msg_text, redirect_to = profile_check
        getattr(messages, msg_type)(request, msg_text)
        return redirect(redirect_to)
    
    # ========== 5. 创建报名记录 ==========
    try:
        registration = Registration.objects.create(
            user=request.user,
            competition=competition,
            registration_type=Competition.INDIVIDUAL
        )
        
        # 6. 创建系统通知
        _create_registration_notification(request.user, registration, None)
        if competition.is_audit:
            _notification_admin(registration, request.user)
        
        # 7. 成功提示并跳转
        messages.success(request, '报名成功！')
        return redirect('competition:competition_detail', slug=slug)
        
    except Exception as e:
        messages.error(request, f'报名失败：{str(e)}')
        return redirect('competition:registration_detail', slug=slug, re_slug=competition.re_slug)




@method_decorator(login_required, name='dispatch')
class RankingsView(TemplateView):
    """
    排行榜视图（优化版 - 支持分页）
    
    性能优化：
    1. 使用自定义缓存key，便于精确清除
    2. 当有人解题时立即清除缓存
    3. 数据库查询优化（select_related减少查询）
    4. 30秒缓存平衡性能和实时性
    5. 分页显示，每页50条
    
    权限控制：
    1. 需要登录才能查看
    2. 需要报名参赛才能查看排行榜
    """
    
    paginate_by = 50  # 每页显示50条
    
    def dispatch(self, request, *args, **kwargs):
        """在dispatch阶段进行权限验证"""
        slug = self.kwargs['slug']
        competition = get_object_or_404(Competition, slug=slug)
        
        #  权限验证：检查用户是否报名参赛
        registration = Registration.objects.filter(
            competition=competition,
            user=request.user
        ).first()
        
        # 如果用户未报名，且不是比赛创建者，也不是管理员，则拒绝访问
        if not registration and not competition.author == request.user and not request.user.is_staff:
            messages.warning(request, "您还未报名该比赛，无法查看排行榜")
            return redirect('competition:competition_detail', slug=slug)
        
        return super().dispatch(request, *args, **kwargs)
    
    def get_template_names(self):
        """根据 ranking_type 动态选择模板"""
        slug = self.kwargs['slug']
        ranking_type = self.kwargs.get('ranking_type', 'individual')
        competition = get_object_or_404(Competition, slug=slug)
        
        # 根据排行榜类型选择不同模板
        if ranking_type == 'team':
            if competition.theme == 'tech':
                return ['competition/tech/rankings_team.html', 'competition/rankings_team.html']
            else:
                return ['competition/anime/rankings_team.html']
        else:  # individual
            if competition.theme == 'tech':
                return ['competition/tech/rankings_individual.html', 'competition/rankings_individual.html']
            else:
                return ['competition/anime/rankings_individual.html']
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        slug = self.kwargs['slug']
        ranking_type = self.kwargs['ranking_type']
        
        competition = get_object_or_404(Competition, slug=slug)
        context['competition'] = competition
        
        #  保存比赛类型到上下文，供模板判断
        context['is_team_competition'] = competition.competition_type == Competition.TEAM
        context['is_individual_competition'] = competition.competition_type == Competition.INDIVIDUAL
        
        # 检查是否是AJAX请求
        is_ajax = self.request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        
        # 使用缓存优化性能（30秒缓存，平衡实时性和性能）
        # 缓存键包含页码，确保每页数据独立缓存
        page = self.request.GET.get('page', 1)
        cache_key = f'rankings_page:{competition.id}:{ranking_type}:page_{page}'
        cached_data = cache.get(cache_key)
        
        if cached_data:
            # 缓存命中，直接返回
            context.update(cached_data)
            if is_ajax:
                return self._return_json_response(context, competition, ranking_type)
            return context
        
        # 缓存未命中，查询数据库
        if ranking_type == 'individual':
            # 优化：使用 only() 只获取需要的字段
            rankings = ScoreUser.objects.filter(
                competition=competition
            ).select_related('user', 'team').only(
                'user__username',
                'user__avatar',
                'user__uuid',
                'team__name',
                'points',
                'created_at',
                'solved_challenges'
            ).order_by('-points', 'created_at')
            
            ranking_title = '个人排行'
            is_individual = True
        else:
            # 优化：使用 only() 只获取需要的字段
            rankings = ScoreTeam.objects.filter(
                competition=competition
            ).select_related('team').prefetch_related('team__members').only(
                'team__name',
                'score',
                'time',
                'solved_challenges'
            ).order_by('-score', 'time')
            
            ranking_title = '队伍排行'
            is_individual = False
        
        # 分页处理
        paginator = Paginator(rankings, self.paginate_by)
        
        try:
            page_obj = paginator.page(page)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)
        
        # 准备上下文数据
        context_data = {
            'rankings': page_obj.object_list,
            'ranking_title': ranking_title,
            'is_individual': is_individual,
            'ranking_type': ranking_type,
            'paginator': paginator,
            'page_obj': page_obj,
            'is_paginated': paginator.num_pages > 1,
        }
        
        context.update(context_data)
        
        # 缓存当前页数据（30秒缓存，解题时会自动清除）
        cache_data = {
            'rankings': list(page_obj.object_list),
            'ranking_title': ranking_title,
            'is_individual': is_individual,
            'ranking_type': ranking_type,
            'paginator': paginator,
            'page_obj': page_obj,
            'is_paginated': paginator.num_pages > 1,
        }
        cache.set(cache_key, cache_data, 3600)  # 30秒缓存
        
        return context


@require_http_methods(["GET"])
def ctf_rankings_api(request, slug):
    """CTF排行榜API - 返回JSON格式数据（带缓存优化）"""
   
    competition = get_object_or_404(Competition, slug=slug)
    
    # 获取分页参数
    page = request.GET.get('page', 1)
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    
    page_size = 50
    
    # 生成缓存key（区分团队赛和个人赛）
    cache_key = f'ctf_rankings_{competition.id}_{competition.competition_type}_page_{page}'
    
    # 尝试从缓存获取
    cached_data = cache.get(cache_key)
    if cached_data:
        return JsonResponse(cached_data)
    
    # 根据竞赛类型获取排行榜
    if competition.competition_type == Competition.TEAM:
        rankings = ScoreTeam.objects.filter(
            competition=competition
        ).select_related('team').prefetch_related('team__members').order_by('-score', 'time')
    else:
        rankings = ScoreUser.objects.filter(
            competition=competition
        ).select_related('user', 'team').order_by('-points', 'created_at')
    
    # 分页
    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
    paginator = Paginator(rankings, page_size)
    
    try:
        rankings_page = paginator.page(page)
    except PageNotAnInteger:
        rankings_page = paginator.page(1)
    except EmptyPage:
        rankings_page = paginator.page(paginator.num_pages)
    
    # 构建返回数据
    rankings_data = []
    for index, item in enumerate(rankings_page.object_list, start=(page - 1) * page_size + 1):
        if competition.competition_type == Competition.TEAM:
            rankings_data.append({
                'rank': index,
                'team_name': item.team.name,
                'team_code': item.team.team_code,
                'score': float(item.score),
                'solved_count': item.solved_challenges.count(),  # 使用.count()获取数量
            })
        else:
            rankings_data.append({
                'rank': index,
                'username': item.user.username,
                'score': float(item.points),
                'solved_count': item.solved_challenges.count(),  # 使用.count()获取数量
            })
    
    response_data = {
        'success': True,
        'rankings': rankings_data,
        'pagination': {
            'page': page,
            'total_pages': paginator.num_pages,
            'total_count': paginator.count,
            'has_previous': rankings_page.has_previous(),
            'has_next': rankings_page.has_next(),
        }
    }
    
    # 缓存30秒（比赛进行中数据变化快，缓存时间短一些）
    cache.set(cache_key, response_data, 30)
    
    return JsonResponse(response_data)


@require_http_methods(["GET"])
def quiz_rankings_api(request, slug):
    """知识竞赛排行榜API - 返回JSON格式数据（带缓存优化）"""


    
    try:
        competition = get_object_or_404(Competition, slug=slug)
        
        # 检查是否关联了知识竞赛
        if not competition.related_quiz:
            return JsonResponse({
                'success': False,
                'message': '该竞赛未关联知识竞赛'
            }, status=404)
        
        quiz = competition.related_quiz
        
        # 获取分页参数
        page = request.GET.get('page', 1)
        try:
            page = int(page)
        except (TypeError, ValueError):
            page = 1
        
        page_size = 50
        
        # 生成缓存key
        cache_key = f'quiz_rankings_{quiz.id}_page_{page}'
        
        # 尝试从缓存获取
        cached_data = cache.get(cache_key)
        if cached_data:
            return JsonResponse(cached_data)
        
        # 使用Quiz模型的get_leaderboard方法获取排行榜（它内部已有缓存）
        try:
            leaderboard = quiz.get_leaderboard(limit=None)
        except Exception as e:
            logger.error(f"获取知识竞赛排行榜失败: {e}")
            return JsonResponse({
                'success': False,
                'message': '获取排行榜数据失败'
            }, status=500)
        
        # 添加排名
        for index, item in enumerate(leaderboard, 1):
            item['rank'] = index
        
        # 分页处理
        from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
        paginator = Paginator(leaderboard, page_size)
        
        try:
            leaderboard_page = paginator.page(page)
        except PageNotAnInteger:
            leaderboard_page = paginator.page(1)
        except EmptyPage:
            leaderboard_page = paginator.page(paginator.num_pages)
        
        # 构建返回数据
        rankings_data = []
        for item in leaderboard_page.object_list:
            try:
                rankings_data.append({
                    'rank': item.get('rank', 0),
                    'username': item.get('user__username', ''),
                    'real_name': item.get('user__username', ''),  # Quiz排行榜没有real_name
                    'score': float(item.get('best_score', 0)),
                    'duration': item.get('duration_formatted', '-'),
                })
            except Exception as e:
                logger.error(f"处理排行榜数据失败: {e}, item: {item}")
                continue
        
        response_data = {
            'success': True,
            'rankings': rankings_data,
            'pagination': {
                'page': page,
                'total_pages': paginator.num_pages,
                'total_count': paginator.count,
                'has_previous': leaderboard_page.has_previous(),
                'has_next': leaderboard_page.has_next(),
            }
        }
        
        # 缓存60秒（知识竞赛数据相对稳定）
        cache.set(cache_key, response_data, 60)
        
        return JsonResponse(response_data)
        
    except Exception as e:
        logger.error(f"知识竞赛排行榜API异常: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'message': '服务器错误，请稍后重试'
        }, status=500)


@login_required
def competition_dashboard(request, slug):

    competition = get_object_or_404(Competition, slug=slug)
 
    if competition.competition_type == Competition.INDIVIDUAL:
        messages.warning(request, "该比赛为个人赛，无法查看比赛数据")
        return redirect('competition:competition_detail', slug=slug)
    
    if competition:  
        registration = Registration.objects.filter(
            competition=competition,
            user=request.user
        ).first()
        
        if not registration and not competition.author == request.user and not request.user.is_staff:
            messages.warning(request, "您还未报名该比赛，无法查看比赛数据")
            return redirect('competition:competition_detail', slug=slug)
    context = {
        'competition': competition,
    }
    return render(request, 'competition/dashboard.html', context)







from django.views.generic import CreateView, ListView, DetailView, UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.urls import reverse_lazy
from .forms import CompetitionForm


class CompetitionCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Competition
    form_class = CompetitionForm
    template_name = 'competition/competition_create.html'
    permission_denied_message = '您没有权限创建比赛'
    
    def test_func(self):
        # 检查用户是否有权限创建比赛
        has_permission = self.request.user.is_staff or self.request.user.is_superuser or self.request.user.is_member
        
        # 如果用户有基本权限，再检查是否已有未结束的比赛
        if has_permission and not (self.request.user.is_staff or self.request.user.is_superuser):
            # 获取用户创建的未结束比赛数量
            active_competitions = Competition.objects.filter(
                author=self.request.user,
                end_time__gt=timezone.now()  # 结束时间在当前时间之后的比赛
            ).count()
            
            # 如果已有未结束的比赛，则不允许创建新比赛
            if active_competitions > 0:
                self.permission_denied_message = '您已有未结束的比赛，暂时无法创建新比赛'
                return False
        
        return has_permission
    
    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return redirect('account_login')

        messages.warning(self.request, self.permission_denied_message)
        return redirect('competition:CompetitionView')
    
    def get_initial(self):
        # 生成验证码并添加到表单初始数据中
        initial = super().get_initial()
        captcha_data = create_captcha_for_registration()
        initial['captcha_key'] = captcha_data['captcha_key']
        self.captcha_image = captcha_data['captcha_image']
        return initial
    
    def get_form_kwargs(self):
        """将当前用户传递给表单"""
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # 将验证码图片添加到上下文中
        context['captcha_image'] = getattr(self, 'captcha_image', None)
        
        # 获取可关联的知识竞赛（未被其他比赛关联的知识竞赛）
        from quiz.models import Quiz
        
        # 基础查询条件：未被关联且启用状态
        queryset = Quiz.objects.filter(
            is_active=True,
            related_competition__isnull=True  # 未被关联
        )
        
        # 非管理员只能看到自己创建的知识竞赛
        if not (self.request.user.is_staff or self.request.user.is_superuser):
            queryset = queryset.filter(creator=self.request.user)
        
        available_quizzes = queryset.order_by('-created_at')
        context['available_quizzes'] = available_quizzes
        
        return context
    
    def form_valid(self, form):
        try:
            with transaction.atomic():
                competition = form.save(commit=False)
                
                # 设置创建人为当前用户
                competition.author = self.request.user
                
                # 如果没有提供slug，生成随机slug
                if not competition.slug:
                    competition.slug = competition.generate_random_slug()
                
                # 生成报名路由
                competition.re_slug = competition.generate_random_slug()
                
                # 保存比赛
                competition.save()
                
                # 返回JSON响应
                return JsonResponse({
                    'success': True,
                    'id': competition.id,
                    'slug': competition.slug,
                    'message': '比赛创建成功'
                })
        except Exception as e:
            return JsonResponse({
                'success': False,
                'message': f'创建失败: {str(e)}',
                'errors': {'system': str(e)}
            }, status=400)
    
    def form_invalid(self, form):
        # 生成新的验证码
        captcha_data = create_captcha_for_registration()
        
        errors = {}
        for field, error_list in form.errors.items():
            errors[field] = error_list[0]
        
        return JsonResponse({
            'success': False,
            'message': f'验证失败：{errors}',
            'errors': errors,
            'captcha_key': captcha_data['captcha_key'],
            'captcha_image': captcha_data['captcha_image']
        }, status=400)

@require_http_methods(["POST"])
def refresh_captcha(request):
    """刷新验证码的API端点"""
    if request.method == 'POST' and request.headers.get('x-requested-with') == 'XMLHttpRequest':
        captcha_data = create_captcha_for_registration()
        return JsonResponse({
            'success': True,
            'captcha_key': captcha_data['captcha_key'],
            'captcha_image': captcha_data['captcha_image']
        })
    return JsonResponse({'success': False, 'message': '非法请求'}, status=400)

class CompetitionAddChallengesView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Competition
    template_name = 'competition/competition_add_challenges.html'
    fields = ['challenges']
    
    def test_func(self):
        # 检查用户是否有权限添加题目
        return self.request.user.is_staff or self.request.user.is_superuser or self.request.user.is_member
    
    def get_object(self):
        return get_object_or_404(Competition, slug=self.kwargs.get('slug'))
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        competition = self.get_object()
        user = self.request.user
        
        # 获取已经添加到其他比赛的题目ID
        challenges_in_competitions = Challenge.objects.filter(
            competition__isnull=False  # 已添加到任何比赛的题目
        ).exclude(
            competition=competition  # 排除当前比赛的题目
        ).values_list('id', flat=True).distinct()
        
        # 构建查询条件
        # 1. 管理员创建的已激活题目 或 
        # 2. 当前用户创建的题目(不考虑激活状态)
        from django.db.models import Q
        available_challenges = Challenge.objects.filter(
            Q(author__is_staff=True, is_active=True) |  # 管理员创建的已激活题目
            Q(author=user)                              # 当前用户创建的题目
        ).exclude(
            id__in=challenges_in_competitions           # 排除已添加到其他比赛的题目
        )
        
        context['available_challenges'] = available_challenges
        context['current_challenges'] = competition.challenges.all()
        
        # 添加已被其他比赛使用的题目，供参考
        context['used_in_other_competitions'] = Challenge.objects.filter(
            id__in=challenges_in_competitions
        )
        
        # 获取所有可用题目的分类列表（去重）
        available_categories = available_challenges.values_list('category', flat=True).distinct().order_by('category')
        context['available_categories'] = [cat for cat in available_categories if cat]
        
        return context
    
    def form_valid(self, form):
        try:
            with transaction.atomic():
                competition = self.get_object()
                
                # 获取表单提交的题目
                submitted_challenges = form.cleaned_data.get('challenges')
                
                # 验证：不能添加已经在其他比赛中的题目
                challenges_in_other_competitions = Challenge.objects.filter(
                    id__in=[c.id for c in submitted_challenges],
                    competition__isnull=False
                ).exclude(
                    competition=competition
                ).distinct()
                
                if challenges_in_other_competitions.exists():
                    duplicate_names = ", ".join([c.title for c in challenges_in_other_competitions])
                    raise ValidationError(f"以下题目已经在其他比赛中使用，不能重复添加: {duplicate_names}")
                
                # 更新比赛的题目
                competition.challenges.set(submitted_challenges)
                competition.save()
                try:
                    print(f'清除缓存: competition_stats_{competition.id}')
                    cache.delete(f'competition_stats_{competition.id}')
                except Exception as e:
                    logger.error(f"清除缓存失败: {str(e)}")
                messages.success(self.request, '题目添加成功')
                clear_competition_cache(competition)
                
                # 如果是AJAX请求，返回JSON响应
                if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({
                        'success': True,
                        'message': '题目添加成功',
                        'redirect': reverse('competition:competition_detail', kwargs={'slug': competition.slug})
                    })
                
                base_url = reverse('competition:competition_manage', kwargs={'slug': competition.slug})
                return redirect(f'{base_url}?active_tab=challenges')
        except ValidationError as e:
            messages.error(self.request, str(e))
            
            # 如果是AJAX请求，返回JSON响应
            if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': False,
                    'message': str(e),
                }, status=400)
            
            return self.render_to_response(self.get_context_data(form=form))
        except Exception as e:
            messages.error(self.request, f'添加题目失败: {str(e)}')
            
            # 如果是AJAX请求，返回JSON响应
            if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                errors = {}
                for field, error_list in form.errors.items():
                    errors[field] = error_list[0]
                
                return JsonResponse({
                    'success': False,
                    'message': '表单验证失败',
                    'errors': errors
                }, status=400)
            
            return self.form_invalid(form)
    
    def form_invalid(self, form):
        messages.error(self.request, '表单验证失败，请检查您的输入')
        
        # 如果是AJAX请求，返回JSON响应
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            errors = {}
            for field, error_list in form.errors.items():
                errors[field] = error_list[0]
            
            return JsonResponse({
                'success': False,
                'message': '表单验证失败',
                'errors': errors
            }, status=400)
        
        return super().form_invalid(form)

class CompetitionChallengeCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Challenge
    form_class = ChallengeCreateForm
    template_name = 'competition/competition_challenge_create.html'
    
    def test_func(self):
        # 检查用户是否有权限创建题目
        competition_id = self.kwargs.get('slug')
        competition = get_object_or_404(Competition, slug=competition_id)
        is_owner = competition.author == self.request.user
        
        # 只有比赛拥有者或管理员才能创建题目
        return is_owner or self.request.user.is_staff or self.request.user.is_superuser or self.request.user.is_member
    def get_form_kwargs(self):
        """
        将当前用户添加到表单参数中，解决用户为空的问题
        """
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs
        
    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            messages.warning(self.request, '请先登录后再创建题目')
            return redirect('competition:CompetitionView')
        messages.error(self.request, '您没有权限创建题目')
        return redirect('competition:CompetitionView')
    
    def get_initial(self):
        # 生成验证码并添加到表单初始数据中
        initial = super().get_initial()
        captcha_data = create_captcha_for_registration()
        initial['captcha_key'] = captcha_data['captcha_key']
        self.captcha_image = captcha_data['captcha_image']
        return initial
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # 获取比赛信息
        competition_id = self.kwargs.get('slug')
        context['competition'] = get_object_or_404(Competition, slug=competition_id)
        # 将验证码图片添加到上下文中
        context['captcha_image'] = getattr(self, 'captcha_image', None)
        return context
    
    def form_valid(self, form):
        try:
            with transaction.atomic():
                # 设置作者
                form.instance.author = self.request.user
                
                # 检查是否使用默认Flag模板
                
                # 设置部署类型
                
                
                # 保存题目
                challenge = form.save()
                
                # 将题目添加到比赛中
                competition_id = self.kwargs.get('slug')
                competition = get_object_or_404(Competition, slug=competition_id)
                competition.challenges.add(challenge)
                
                messages.success(self.request, f'题目创建成功并添加到比赛')
                clear_competition_cache(competition)
                # 如果是AJAX请求，返回JSON响应
                """ if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({
                        'success': True,
                        'message': '题目创建成功',
                        'redirect': reverse('competition:competition_add_challenges', kwargs={'slug': competition.slug})
                    }) """
                
                return redirect('competition:competition_add_challenges', slug=competition.slug)
        except Exception as e:
            messages.error(self.request, f'创建题目失败: {str(e)}')
            
            # 如果是AJAX请求，返回JSON响应
            if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': False,
                    'message': f'创建题目失败: {str(e)}'
                }, status=400)
            
            return self.form_invalid(form)
    
    def form_invalid(self, form):
        # 生成新的验证码
        captcha_data = create_captcha_for_registration()
        form.initial['captcha_key'] = captcha_data['captcha_key']
        self.captcha_image = captcha_data['captcha_image']
            
        messages.error(self.request, '表单验证失败，请检查您的输入')
        
        # 如果是AJAX请求，返回JSON响应
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            errors = {}
            non_field_errors = form.non_field_errors()
            errors = {field: error_list for field, error_list in form.errors.items()}
         
            return JsonResponse({
                'success': False,
                'message': '表单验证失败',
                'errors': errors,
                'non_field_errors': non_field_errors,
                'captcha_key': captcha_data['captcha_key'],
                'captcha_image': captcha_data['captcha_image']
            }, status=400)
        
        return super().form_invalid(form)




@login_required
def competition_manage(request, slug):
    """比赛管理页面"""
    competition = get_object_or_404(Competition, slug=slug)
    
    # 检查权限 - 只有比赛创建者可以管理
    if competition.author != request.user and not request.user.is_superuser:
        messages.warning(request, '您没有权限访问此页面')
        return redirect('competition:competition_detail', slug=slug)
    
    active_tab = request.GET.get('active_tab', 'info')
    only_page = request.GET.get('only_page', False)
    
    # 处理只更新当前标签页分页的逻辑
    if only_page:
        # 根据当前活动标签页，清除其他标签页的分页参数
        if active_tab == 'info':
            # 信息标签页没有分页，但为了一致性仍然保留
            request.GET._mutable = True
            request.GET.pop('reg_page', None)
            request.GET.pop('score_page', None)
            request.GET.pop('team_page', None)
            request.GET.pop('team_score_page', None)
            request.GET.pop('challenges_page', None)
            request.GET.pop('audit_page', None)
            request.GET.pop('log_page', None)
            request.GET.pop('writeup_page', None)
            request.GET.pop('not_submitted_page', None)
            request.GET._mutable = False
        elif active_tab == 'registrations':
            # 报名信息标签页
            request.GET._mutable = True
            request.GET.pop('score_page', None)
            request.GET.pop('team_score_page', None)
            request.GET.pop('challenges_page', None)
            request.GET.pop('audit_page', None)
            request.GET.pop('log_page', None)
            request.GET.pop('writeup_page', None)
            request.GET.pop('not_submitted_page', None)
            request.GET._mutable = False
        elif active_tab == 'challenges':
            # 题目管理标签页
            request.GET._mutable = True
            request.GET.pop('reg_page', None)
            request.GET.pop('score_page', None)
            request.GET.pop('team_page', None)
            request.GET.pop('team_score_page', None)
            request.GET.pop('audit_page', None)
            request.GET.pop('log_page', None)
            request.GET.pop('writeup_page', None)
            request.GET.pop('not_submitted_page', None)
            request.GET._mutable = False
        elif active_tab == 'rankings':
            # 分数排行标签页
            request.GET._mutable = True
            request.GET.pop('reg_page', None)
            request.GET.pop('team_page', None)
            request.GET.pop('challenges_page', None)
            request.GET.pop('audit_page', None)
            request.GET.pop('log_page', None)
            request.GET.pop('writeup_page', None)
            request.GET.pop('not_submitted_page', None)
            request.GET._mutable = False
        elif active_tab == 'statistics':
            # 数据统计标签页
            request.GET._mutable = True
            request.GET.pop('reg_page', None)
            request.GET.pop('score_page', None)
            request.GET.pop('team_page', None)
            request.GET.pop('challenges_page', None)
            request.GET.pop('team_score_page', None)
            request.GET.pop('audit_page', None)
            request.GET.pop('writeup_page', None)
            request.GET.pop('not_submitted_page', None)
            request.GET._mutable = False
        elif active_tab == 'audit':
            # 审核管理标签页
            request.GET._mutable = True
            request.GET.pop('reg_page', None)
            request.GET.pop('score_page', None)
            request.GET.pop('team_page', None)
            request.GET.pop('team_score_page', None)
            request.GET.pop('challenges_page', None)
            request.GET.pop('log_page', None)
            request.GET.pop('writeup_page', None)
            request.GET.pop('not_submitted_page', None)
            request.GET._mutable = False
        elif active_tab == 'writeups':
            # WP管理标签页 - 保留 writeup_page 和 not_submitted_page
            request.GET._mutable = True
            request.GET.pop('reg_page', None)
            request.GET.pop('score_page', None)
            request.GET.pop('team_page', None)
            request.GET.pop('team_score_page', None)
            request.GET.pop('challenges_page', None)
            request.GET.pop('log_page', None)
            request.GET.pop('audit_page', None)
            request.GET._mutable = False
   
    # 处理表单提交 - 更新比赛信息
    if request.method == 'POST':
        title = request.POST.get('title')
        description = request.POST.get('description')
        competition_type = request.POST.get('competition_type')
        start_time_str = request.POST.get('start_time')
        end_time_str = request.POST.get('end_time')
        is_audit = request.POST.get('is_audit')
        visibility_type = request.POST.get('visibility_type')
        theme = request.POST.get('theme')
        related_quiz_id = request.POST.get('related_quiz')
        combined_score_ctf_weight = request.POST.get('combined_score_ctf_weight')
        combined_score_top_percent = request.POST.get('combined_score_top_percent')
        is_register = request.POST.get('is_register')
        is_active = request.POST.get('is_active')
        dashboard_template = request.POST.get('dashboard_template')
        competition.title = title
        competition.description = description
        # 检查是否可以修改竞赛类型
        if competition.competition_type != competition_type:
            # 检查是否已有报名记录
            has_registrations = Registration.objects.filter(competition=competition).exists()
            if has_registrations:
                messages.error(request, '比赛已有人报名，无法修改竞赛类型！如需修改，请先清空所有报名记录。')
                return redirect('competition:competition_manage', slug=slug)
        
        competition.competition_type = competition_type
        competition.theme = theme
        competition.dashboard_template = dashboard_template
        # 检查是否可以修改审核设置
        if str(competition.is_audit) != str(is_audit):
            has_registrations = Registration.objects.filter(competition=competition).exists()
            if has_registrations:
                messages.error(request, '比赛已有人报名，无法修改审核设置！如需修改，请先清空所有报名记录。')
                return redirect('competition:competition_manage', slug=slug)
        
        # 检查是否可以修改可见性类型
        if competition.visibility_type != visibility_type:
            has_registrations = Registration.objects.filter(competition=competition).exists()
            if has_registrations:
                messages.error(request, '比赛已有人报名，无法修改可见性类型！如需修改，请先清空所有报名记录。')
                return redirect('competition:competition_manage', slug=slug)
        
        # 检查关联知识竞赛是否有变化
        current_quiz_id = competition.related_quiz.id if competition.related_quiz else None
        new_quiz_id = int(related_quiz_id) if related_quiz_id else None
        
        if current_quiz_id != new_quiz_id:
            from quiz.models import QuizRegistration
            
            # 如果新关联了知识竞赛（不是取消关联），检查该知识竞赛是否有报名
            if new_quiz_id:
                has_quiz_registrations = QuizRegistration.objects.filter(quiz_id=new_quiz_id).exists()
                if has_quiz_registrations:
                    messages.error(request, '该知识竞赛已有人报名，无法关联！如需关联，请先清空该知识竞赛的所有报名记录。')
                    return redirect('competition:competition_manage', slug=slug)
            
            # 检查当前比赛是否有报名
            has_comp_registrations = Registration.objects.filter(competition=competition).exists()
            if has_comp_registrations:
                messages.error(request, '比赛已有人报名，无法修改关联的知识竞赛！如需修改，请先清空所有报名记录。')
                return redirect('competition:competition_manage', slug=slug)

        # 转换字符串为布尔值
        is_register_bool = is_register == 'True' if is_register else False
        is_active_bool = is_active == 'True' if is_active else False
        is_audit_bool = is_audit == 'True' if is_audit else False
        
        # 转换日期时间字符串为日期时间对象
        try:
            from django.utils.dateparse import parse_datetime
            
            start_time = parse_datetime(start_time_str)
            end_time = parse_datetime(end_time_str)
            
            # 验证日期
            if end_time <= start_time:
                messages.error(request, '结束时间必须晚于开始时间')
                return redirect('competition:competition_manage', slug=slug)
            
            competition.start_time = start_time
            competition.end_time = end_time
            
            # 处理关联知识竞赛
            from quiz.models import Quiz
            from decimal import Decimal, ROUND_HALF_UP
            
            if related_quiz_id:
                try:
                    quiz = Quiz.objects.get(id=related_quiz_id, creator=request.user)
                    competition.related_quiz = quiz
                    
                    # 保存综合分数配置（限制小数位数为2位）
                    if combined_score_ctf_weight:
                        weight = Decimal(str(combined_score_ctf_weight))
                        # 四舍五入到2位小数
                        competition.combined_score_ctf_weight = weight.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                    if combined_score_top_percent:
                        competition.combined_score_top_percent = int(combined_score_top_percent)
                except Quiz.DoesNotExist:
                    messages.error(request, '选择的知识竞赛不存在或您无权关联该知识竞赛')
                    return redirect('competition:competition_manage', slug=slug)
            else:
                # 取消关联
                competition.related_quiz = None
            
            if 'img_link' in request.FILES:
                competition.img_link = request.FILES['img_link']
            
            # 更新布尔值字段
            competition.is_register = is_register_bool
            competition.is_active = is_active_bool
            competition.is_audit = is_audit_bool
            competition.visibility_type = visibility_type
            
            competition.save()
            messages.success(request, '比赛信息更新成功')
        except Exception as e:
            messages.error(request, f'更新失败: {str(e)}')
        
        return redirect('competition:competition_manage', slug=slug)
    
    # 获取统计数据
    cache_key = f'competition_stats_{competition.id}'
    stats = cache.get(cache_key)
    
    if not stats:
        # 强制从数据库重新加载 competition 对象，避免使用 ORM 缓存的旧数据
        competition.refresh_from_db()
        
        # 报名人数/队伍数
        if competition.competition_type == 'individual':
            registration_count = Registration.objects.filter(
                competition=competition, 
                registration_type='individual'
            ).count()
        else:
            registration_count = Team.objects.filter(competition=competition).count()
        
        # 题目数量 - 使用显式查询避免关联缓存
        challenge_count = competition.challenges.count()
        
        # 提交次数和解题次数 (这里需要根据你的模型结构调整)
        if competition.competition_type == 'individual':
            
            
            submission_count=Submission.objects.filter(competition=competition).count()  
            solve_count = ScoreUser.objects.filter(
                competition=competition
            ).aggregate(total=Count('solved_challenges'))['total'] or 0
        else:
            submission_count=Submission.objects.filter(competition=competition).count()  # 需要根据实际模型获取
            solve_count = ScoreTeam.objects.filter(
                competition=competition
            ).aggregate(total=Count('solved_challenges'))['total'] or 0
        
        stats = {
            'registration_count': registration_count,
            'challenge_count': challenge_count,
            'submission_count': submission_count,
            'solve_count': solve_count
        }
        
        # 缓存统计数据 - 10分钟
        cache.set(cache_key, stats, 600)
    
    # 获取报名信息
    site_url = site_full_url()
    
    # 获取可用的知识竞赛列表
    # 条件：本人创建的 且（未被关联 或 已关联到当前比赛）
    from quiz.models import Quiz
    available_quizzes = Quiz.objects.filter(
        creator=request.user
    ).filter(
        Q(related_competition__isnull=True) | Q(related_competition=competition)
    ).order_by('-created_at')
    
    # 分页相关参数
   

    items_per_page = 10  # 每页显示的条目数
    
    context = {
        'competition': competition,
        'site_url': site_url,
        'active_tab': active_tab,
        'available_quizzes': available_quizzes,
        **stats
    }
    
    if competition.competition_type == 'individual':
        # 个人报名信息分页
        individual_registrations_list = Registration.objects.filter(
            competition=competition, 
            registration_type='individual'
        ).select_related('user').order_by('id')
        
        reg_paginator = Paginator(individual_registrations_list, items_per_page)
        reg_page = request.GET.get('reg_page', 1)
        try:
            individual_registrations = reg_paginator.page(reg_page)
        except PageNotAnInteger:
            individual_registrations = reg_paginator.page(1)
        except EmptyPage:
            individual_registrations = reg_paginator.page(reg_paginator.num_pages)
        
        # 个人排名分页（按分数排序，避免频繁更新rank字段影响性能）
        individual_scores_list = ScoreUser.objects.filter(
            competition=competition
        ).select_related('user').order_by('-points', 'created_at')
        
        score_paginator = Paginator(individual_scores_list, items_per_page)
        score_page = request.GET.get('score_page', 1)
        try:
            individual_scores = score_paginator.page(score_page)
        except PageNotAnInteger:
            individual_scores = score_paginator.page(1)
        except EmptyPage:
            individual_scores = score_paginator.page(score_paginator.num_pages)
        
        context.update({
            'individual_registrations': individual_registrations,
            'individual_scores': individual_scores,
        })
    else:
        # 团队报名信息分页
        team_registrations_list = Team.objects.filter(
            id__in=Registration.objects.filter(
                competition=competition,
                registration_type='team'
            ).values_list('team_name', flat=True)
        ).select_related('leader').prefetch_related('members').order_by('id')
        
        team_paginator = Paginator(team_registrations_list, items_per_page)
        team_page = request.GET.get('team_page', 1)
        try:
            team_registrations = team_paginator.page(team_page)
        except PageNotAnInteger:
            team_registrations = team_paginator.page(1)
        except EmptyPage:
            team_registrations = team_paginator.page(team_paginator.num_pages)
        
        # 团队排名分页（按分数排序，避免频繁更新rank字段影响性能）
        team_scores_list = ScoreTeam.objects.filter(
            competition=competition
        ).select_related('team').order_by('-score', 'time')
        
        team_score_paginator = Paginator(team_scores_list, items_per_page)
        team_score_page = request.GET.get('team_score_page', 1)
        try:
            team_scores = team_score_paginator.page(team_score_page)
        except PageNotAnInteger:
            team_scores = team_score_paginator.page(1)
        except EmptyPage:
            team_scores = team_score_paginator.page(team_score_paginator.num_pages)
        
        context.update({
            'team_registrations': team_registrations,
            'team_scores': team_scores,
        })
    
    # 题目列表分页
    challenges_list = competition.challenges.all().order_by('id')
    challenges_paginator = Paginator(challenges_list, items_per_page)
    challenges_page = request.GET.get('challenges_page', 1)
    
    try:
        challenges = challenges_paginator.page(challenges_page)
    except PageNotAnInteger:
        challenges = challenges_paginator.page(1)
    except EmptyPage:
        challenges = challenges_paginator.page(challenges_paginator.num_pages)
    
    context['challenges'] = challenges
    
    # 审核列表分页
    if competition.is_audit == True:
        registrations_list = Registration.objects.filter(
            competition=competition,
            audit=False
        ).order_by('id')
        
        audit_paginator = Paginator(registrations_list, items_per_page)
        audit_page = request.GET.get('audit_page', 1)
        try:
            registrations = audit_paginator.page(audit_page)
        except PageNotAnInteger:
            registrations = audit_paginator.page(1)
        except EmptyPage:
            registrations = audit_paginator.page(audit_paginator.num_pages)
        
        context['registrations'] = registrations


    if competition:
        CheatingLogs_list = CheatingLog.objects.filter(
            competition=competition,
        ).order_by('id')
        
        log_paginator = Paginator(CheatingLogs_list, items_per_page)
        log_page = request.GET.get('log_page', 1)
        try:
            CheatingLogs = log_paginator.page(log_page)
        except PageNotAnInteger:
            CheatingLogs = log_paginator.page(1)
        except EmptyPage:
            registrations = log_paginator.page(log_paginator.num_pages)
        
        context['CheatingLogs'] = CheatingLogs
    
    # Writeup 列表分页
    writeups_list = Writeup.objects.filter(
        competition=competition
    ).select_related('user', 'team').order_by('-created_at')
    
    writeup_paginator = Paginator(writeups_list, items_per_page)
    writeup_page = request.GET.get('writeup_page', 1)
    try:
        writeups = writeup_paginator.page(writeup_page)
    except PageNotAnInteger:
        writeups = writeup_paginator.page(1)
    except EmptyPage:
        writeups = writeup_paginator.page(writeup_paginator.num_pages if writeup_paginator.num_pages > 0 else 1)
    
    # 获取未提交 WP 的列表
    if competition.competition_type == 'individual':
        # 个人赛：获取所有已报名但未提交 WP 的用户
        submitted_user_ids = Writeup.objects.filter(
            competition=competition
        ).values_list('user_id', flat=True)
        
        not_submitted_list = Registration.objects.filter(
            competition=competition,
            registration_type='individual'
        ).exclude(
            user_id__in=submitted_user_ids
        ).select_related('user').order_by('-created_at')
    else:
        # 团队赛：获取所有已报名但未提交 WP 的队伍
        submitted_team_ids = Writeup.objects.filter(
            competition=competition,
            team__isnull=False
        ).values_list('team_id', flat=True)
        
        not_submitted_list = Team.objects.filter(
            competition=competition
        ).exclude(
            id__in=submitted_team_ids
        ).select_related('leader').prefetch_related('members').order_by('-created_at')
    
    # 未提交 WP 列表分页
    not_submitted_paginator = Paginator(not_submitted_list, items_per_page)
    not_submitted_page = request.GET.get('not_submitted_page', 1)
    try:
        not_submitted_writeups = not_submitted_paginator.page(not_submitted_page)
    except PageNotAnInteger:
        not_submitted_writeups = not_submitted_paginator.page(1)
    except EmptyPage:
        not_submitted_writeups = not_submitted_paginator.page(not_submitted_paginator.num_pages if not_submitted_paginator.num_pages > 0 else 1)
    
    context.update({
        'writeups': writeups,
        'not_submitted_writeups': not_submitted_writeups,
    })
    
    return render(request, 'competition/competition_manage.html', context)

# API视图函数
@login_required
def get_team_members(request, team_id):
    """获取团队成员信息"""
    team = get_object_or_404(Team, id=team_id)
    
    # 检查权限
    competition = team.competition
    if competition.author != request.user and not request.user.is_superuser:
        return JsonResponse({'error': '没有权限'}, status=403)
    
    members_data = []
    # 获取团队所有成员，包括队长
    all_members = list(team.members.all())
    
    for member in all_members:
        registration = None
        # 获取成员的报名信息
        registration = Registration.objects.filter(
            competition=competition,
            user=member,
            team_name=team,
            registration_type='team'  # 确保是团队报名
        ).select_related('user').first()
        
        # 个人信息现在从用户模型读取（已自动解密）
        member_data = {
            'username': member.username,
            'name': member.real_name_masked if member.real_name else '-',
            'student_id': member.student_id_masked if member.student_id else '-',
            'role': member.department_masked if member.department else '-',
            'phone': member.phones_masked if member.phones else '-',  # 脱敏显示
            'student_id': member.student_id_masked if member.student_id else '-',
            'audit': '已审核' if (registration and registration.audit) else '未审核',
            'is_leader': member == team.leader  # 添加是否为队长的标识
        }

        
        members_data.append(member_data)
    
    return JsonResponse({
        'success': True,
        'team_name': team.name,
        'members': members_data
    })

@login_required
def delete_registration(request, registration_id):
    """删除报名信息"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': '方法不允许'}, status=405)
    
    registration = get_object_or_404(Registration, id=registration_id)
    competition = registration.competition
    
    # 检查权限
    if competition.author != request.user and not request.user.is_superuser:
        return JsonResponse({'success': False, 'message': '没有权限'}, status=403)
    
    try:
        registration.delete()
        # 清除缓存
        cache.delete(f'competition_stats_{competition.id}')
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})

@login_required
def delete_team(request, team_id):
    """删除团队"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': '方法不允许'}, status=405)
    
    team = get_object_or_404(Team, id=team_id)
    competition = team.competition
    
    # 检查权限
    if competition.author != request.user and not request.user.is_superuser:
        return JsonResponse({'success': False, 'message': '没有权限'}, status=403)
    
    try:
        # 删除团队成员的报名信息
        Registration.objects.filter(competition=competition, team_name=team).delete()
        # 删除团队
        team.delete()
        # 清除缓存
        cache.delete(f'competition_stats_{competition.id}')
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})

@login_required
def remove_challenge_from_competition(request, competition_id, challenge_id):
    """从比赛中移除题目"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': '方法不允许'}, status=405)
    
    competition = get_object_or_404(Competition, id=competition_id)
    challenge = get_object_or_404(Challenge, id=challenge_id)
    
    # 检查权限
    if competition.author != request.user and not request.user.is_superuser:
        return JsonResponse({'success': False, 'message': '没有权限'}, status=403)
    
    try:
        # 记录移除前的题目数量
        before_count = competition.challenges.count()
        
        # 移除题目
        competition.challenges.remove(challenge)
        
        # 强制刷新 competition 对象，确保从数据库重新加载
        competition.refresh_from_db()
        
        # 清除缓存
        cache_key = f'competition_stats_{competition.id}'
        cache.delete(cache_key)
        
        return JsonResponse({
            'success': True,
            'message': f'题目已移除'
        })
    except Exception as e:
        logger.error(f"移除题目失败: {str(e)}", exc_info=True)
        return JsonResponse({'success': False, 'message': str(e)})

@login_required
def get_competition_statistics(request, slug):
    """获取比赛统计数据"""
    
    competition = get_object_or_404(Competition, slug=slug)
    
    # 检查权限
    if competition.author != request.user and not request.user.is_superuser:
        return JsonResponse({'error': '没有权限'}, status=403)
    
    # 从缓存获取数据
    cache_key = f'competition_statistics_{competition.id}'
    statistics = cache.get(cache_key)
    
    if not statistics:
        # 解题时间线数据
        solve_timeline = {
            'labels': [],
            'data': []
        }
        
        # 题目难度分布
        difficulty_distribution = {
            'labels': ['简单', '中等', '困难'],
            'data': [0, 0, 0]
        }
        
        # 计算题目难度分布
        for challenge in competition.challenges.all():
            if challenge.difficulty == 'easy':
                difficulty_distribution['data'][0] += 1
            elif challenge.difficulty == 'medium':
                difficulty_distribution['data'][1] += 1
            elif challenge.difficulty == 'hard':
                difficulty_distribution['data'][2] += 1
           
        
        # 生成解题时间线
        # 这里需要根据你的模型结构调整
        # ...
        
        statistics = {
            'solve_timeline': solve_timeline,
            'difficulty_distribution': difficulty_distribution
        }
        
        # 缓存统计数据 - 10分钟
        
        cache.set(cache_key, statistics, 600)
    
    return JsonResponse(statistics)






# 设置日志


@login_required
@require_http_methods(["GET"])  # 限制只能使用GET方法
def export_registrations(request, competition_id):
    """导出比赛报名信息"""
    try:
        # 使用事务确保数据一致性
        with transaction.atomic():
            # 安全地获取比赛对象
            competition = get_object_or_404(Competition, id=competition_id)
            
            # 严格的权限检查
            if competition.author != request.user and not request.user.is_staff and not request.user.is_superuser:
                messages.error(request, '您没有权限导出此比赛的报名信息')
                return redirect('competition:competition_detail', slug=competition.slug)
            
            # 创建CSV响应
            response = HttpResponse(content_type='text/csv')
            
            # 安全处理文件名，避免注入攻击
            safe_title = slugify(competition.title) or f"{competition.title}"
            filename = f"{safe_title}_报名信息_{datetime.now().strftime('%Y%m%d')}.csv"
            
            # 设置Content-Disposition头，防止文件名注入
            filename_ascii = urllib.parse.quote(filename)
            filename_utf8 = urllib.parse.quote(filename.encode('utf-8'))
            
            # 设置Content-Disposition头，使用多种编码方式兼容不同浏览器
            response['Content-Disposition'] = (
                f'attachment; '
                f'filename="{filename_ascii}"; '
                f'filename*=UTF-8\'\'{filename_utf8}'
            )
            
            response['X-Content-Type-Options'] = 'nosniff'  # 防止MIME类型嗅探
            
            # 添加BOM以支持中文
            response.write(codecs.BOM_UTF8)
            
            writer = csv.writer(response)
            
            if competition.competition_type == 'individual':
                # 个人赛报名信息（个人信息从用户模型读取）
                writer.writerow(['用户名', '真实姓名', '学号/工号', '学院/部门', '联系方式', '报名时间'])
                
                # 查询报名记录（个人信息从关联的用户获取）
                registrations = Registration.objects.filter(
                    competition=competition, 
                    registration_type='individual'
                ).select_related('user')
                
                if not registrations:
                    messages.warning(request, "无报名数据导出")
                    base_url = reverse('competition:competition_manage', kwargs={'slug': competition.slug})
                    return redirect(f'{base_url}?active_tab=registrations')
                
                for reg in registrations:
                    # 从用户模型获取个人信息（自动解密）
                    writer.writerow([
                        reg.user.username,
                        reg.user.real_name_masked or '-',
                        reg.user.student_id_masked or '-',
                        reg.user.department_masked or '-',
                        reg.user.phones_masked or '-',  # 导出时显示完整手机号（管理员权限）
                        reg.created_at.strftime('%Y-%m-%d %H:%M:%S')
                    ])
            else:
                # 团队赛报名信息
                writer.writerow(['队伍名称', '队长', '成员数量', '成员列表', '创建时间'])
                
                # 从Registration中安全获取队伍模型
                team_ids = Registration.objects.filter(
                    competition=competition,
                    registration_type='team'
                ).values_list('team_name', flat=True).distinct()
                

                if not team_ids:
                    messages.warning(request, "无报名数据导出")
                    base_url = reverse('competition:competition_manage', kwargs={'slug': competition.slug})
                    return redirect(f'{base_url}?active_tab=registrations')
                # 限制查询字段，提高安全性
                teams = Team.objects.filter(
                    id__in=team_ids
                ).select_related('leader').prefetch_related('members').only(
                    'name', 'leader__username', 'created_at'
                )
                
                for team in teams:
                    members = team.members.all().only('username')
                    member_names = ', '.join([member.username for member in members])
                    
                    writer.writerow([
                        team.name,
                        team.leader.username,
                        members.count(),
                        member_names,
                        team.created_at.strftime('%Y-%m-%d %H:%M:%S')
                    ])
            
            # 记录导出操作日志
            logger.info(
                f"用户 {request.user.username} 导出了比赛 '{competition.title}' 的报名信息",
                extra={'request': request}
            )
            
            return response
            
    except Exception as e:
        # 记录详细错误信息，但不向用户展示敏感信息
        logger.error(
            f"导出报名信息失败: {str(e)}",
            exc_info=True,
            extra={'request': request}
        )
        messages.error(request, '导出报名信息失败，请稍后重试')
        return redirect('competition:competition_detail', slug=competition.slug)

@login_required
@require_http_methods(["GET"])  # 限制只能使用GET方法
def export_statistics(request, competition_id):
    """导出比赛作弊日志数据"""
    try:
        # 使用事务确保数据一致性
        with transaction.atomic():
            # 安全地获取比赛对象
            competition = get_object_or_404(Competition, id=competition_id)
            
            # 严格的权限检查
            if competition.author != request.user and not request.user.is_staff and not request.user.is_superuser:
                messages.error(request, '您没有权限导出此比赛的作弊日志数据')
                return redirect('competition:competition_detail', slug=competition.slug)
            
            # 创建CSV响应
            response = HttpResponse(content_type='text/csv')
            
            # 安全处理文件名，避免注入攻击
            safe_title = slugify(competition.title) or f"{competition.title}"
            filename = f"{safe_title}_监控日志_{datetime.now().strftime('%Y%m%d')}.csv"
            
            # 设置Content-Disposition头，防止文件名注入
            filename_ascii = urllib.parse.quote(filename)
            filename_utf8 = urllib.parse.quote(filename.encode('utf-8'))
            
            # 设置Content-Disposition头，使用多种编码方式兼容不同浏览器
            response['Content-Disposition'] = (
                f'attachment; '
                f'filename="{filename_ascii}"; '
                f'filename*=UTF-8\'\'{filename_utf8}'
            )
            
            response['X-Content-Type-Options'] = 'nosniff' # 防止MIME类型嗅探
            
            # 添加BOM以支持中文
            response.write(codecs.BOM_UTF8)
            
            writer = csv.writer(response)
            
            # 设置CSV标题行
            writer.writerow(['用户', '队伍', '作弊类型', '描述', '记录时间', '检测方式'])
            
            # 查询该比赛的所有作弊日志，按时间倒序排列
            cheating_logs = CheatingLog.objects.filter(
                competition=competition
            ).select_related('user', 'team').order_by('-timestamp')
            if not cheating_logs:
                messages.warning(request, "无排行日志导出")
                base_url = reverse('competition:competition_manage', kwargs={'slug': competition.slug})
                return redirect(f'{base_url}?active_tab=statistics')
            # 导出作弊日志数据
            for log in cheating_logs:
                # 获取用户名
                username = log.user.username if log.user else "未知用户"
                
                # 获取队伍名称
                team_name = log.team.name if log.team else "无队伍"
                
                # 获取作弊类型显示名称
                cheating_type_display = log.get_cheating_type()
                
                # 格式化时间
                timestamp = log.timestamp.strftime('%Y-%m-%d %H:%M:%S') if log.timestamp else "未知时间"
                
                # 写入一行数据
                writer.writerow([
                    username,
                    team_name,
                    cheating_type_display,
                    log.description,
                    timestamp,
                    log.detected_by
                ])
            
            # 记录导出操作日志
            logger.info(
                f"用户 {request.user.username} 导出了比赛 '{competition.title}' 的作弊日志信息",
                extra={'request': request}
            )
            
            return response
            
    except Exception as e:
        # 记录详细错误信息，但不向用户展示敏感信息
        logger.error(
            f"导出作弊日志信息失败: {str(e)}",
            exc_info=True,
            extra={'request': request}
        )
        messages.error(request, '导出作弊日志数据失败，请稍后重试')
        return redirect('competition:competition_detail', slug=competition.slug)



@login_required
def challenge_edit(request, slug,uuid):
    """编辑题目"""
    challenge = get_object_or_404(Challenge, uuid=uuid)
    competition = get_object_or_404(Competition, slug=slug)
    # 检查权限
    if not challenge.user_can_manage(request.user):
        messages.warning(request, "您没有权限编辑此题目")
        base_url = reverse('competition:competition_manage', kwargs={'slug': competition.slug})
        return redirect(f'{base_url}?active_tab=challenges')
    
    if request.method == 'POST':
        form = ChallengeForm(request.POST, instance=challenge, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "题目更新成功")
            
            # 如果是从比赛管理页面来的，返回到比赛管理页面
            
            base_url = reverse('competition:competition_manage', kwargs={'slug': competition.slug})
            return redirect(f'{base_url}?active_tab=challenges')
    else:
        form = ChallengeForm(instance=challenge, user=request.user)
    
    context = {
        'form': form,
        'challenge': challenge,
        'competitions': competition,
        'is_edit': True
    }
    
    return render(request, 'competition/competition_challenge_edit.html', context)


@login_required
@require_http_methods(["POST"])
def adjust_score(request, slug):
    """
    前台分数调整功能
    - 区分团队赛和个人赛
    - 权限控制（仅比赛创建者或管理员）
    - 发送通知
    - 更新综合排行榜
    """

    from decimal import Decimal
    
    competition = get_object_or_404(Competition, slug=slug)
    
    # 权限检查
    if competition.author != request.user and not request.user.is_superuser:
        return JsonResponse({
            'success': False,
            'message': '您没有权限执行此操作'
        }, status=403)
    
    try:
        score_type = request.POST.get('type')  # 'team' 或 'individual'
        score_id = request.POST.get('score_id')
        new_score = request.POST.get('new_score')
        reason = request.POST.get('reason', '').strip()
        
        # 验证参数
        if not all([score_type, score_id, new_score]):
            return JsonResponse({
                'success': False,
                'message': '参数不完整'
            })
        
        if not reason:
            return JsonResponse({
                'success': False,
                'message': '请填写调整原因'
            })
        
        try:
            new_score = int(new_score)
        except ValueError:
            return JsonResponse({
                'success': False,
                'message': '分数格式不正确'
            })
        
        # 使用事务确保数据一致性
        with transaction.atomic():
            if score_type == 'team':
                # 团队赛分数调整
                score_obj = get_object_or_404(ScoreTeam, id=score_id, competition=competition)
                old_score = score_obj.score
                
                if old_score == new_score:
                    return JsonResponse({
                        'success': False,
                        'message': '新分数与当前分数相同'
                    })
                
                score_diff = new_score - old_score
                
                # 更新团队分数
                score_obj.score = new_score
                score_obj.time = timezone.now()
                score_obj.save()
                
                # 更新团队成员的个人分数（平均分配差值）
                team_members = list(score_obj.team.members.all())
                member_count = len(team_members)
                
                if member_count > 0:
                    
                    base_diff = score_diff // member_count  
                    remainder = score_diff % member_count     # 余数
                    
                    for index, member in enumerate(team_members):
                        # 前remainder个成员多分配1分，确保总和准确
                        member_diff = base_diff + (1 if index < remainder else 0)
                        
                        try:
                            user_score = ScoreUser.objects.get(
                                user=member,
                                competition=competition
                            )
                            user_score.points += member_diff
                            user_score.created_at = timezone.now()
                            user_score.save()
                        except ScoreUser.DoesNotExist:
                            # 如果成员没有分数记录，创建一个
                            # 注意：这里需要获取该成员的当前分数基础值
                            ScoreUser.objects.create(
                                user=member,
                                team=score_obj.team,
                                competition=competition,
                                points=member_diff,
                                rank=0
                            )
                
                # 发送通知给队伍所有成员
                notification = SystemNotification.objects.create(
                    title='队伍分数调整通知',
                    content=f'''
                        <div style="padding: 12px; background: #f8f9fa; border-radius: 4px; font-size: 0.9rem;">
                            <p style="margin: 0 0 8px 0;"><strong>队伍：</strong>{escape(score_obj.team.name)} | <strong>竞赛：</strong>{escape(competition.title)}</p>
                            <p style="margin: 0 0 8px 0;"><strong>分数变化：</strong>{old_score} → {new_score} <span style="color: {'#28a745' if score_diff > 0 else '#dc3545'}; font-weight: bold;">({'+' if score_diff > 0 else ''}{score_diff})</span></p>
                            <p style="margin: 0;"><strong>原因：</strong>{escape(reason)}</p>
                        </div>
                    '''
                )
                
                # 通知队伍所有成员
                for member in team_members:
                    notification.get_p.add(member)
                
                # 更新排名
                update_rankings_async(competition.id, is_team=True)
                
                # 清除团队分数缓存
                try:
                    clear_competition_ranking_cache(
                        competition.id,
                        is_team=True,
                        team_id=score_obj.team.id
                    )
                except Exception as e:
                    logger.error(f'清除缓存失败: {e}', exc_info=True)
                
                message = f'队伍 "{score_obj.team.name}" 分数已调整：{old_score} → {new_score}'
                logger.warning(f'[分数调整] 用户:{request.user.username}调整了 {score_obj.team.name} 的分数，竞赛: {competition.title} ，消息: {message}')
                
            else:   
                # 个人赛分数调整
                score_obj = get_object_or_404(ScoreUser, id=score_id, competition=competition)
                old_score = score_obj.points
                
                if old_score == new_score:
                    return JsonResponse({
                        'success': False,
                        'message': '新分数与当前分数相同'
                    })
                
                score_diff = new_score - old_score
                
                # 更新个人分数
                score_obj.points = new_score
                score_obj.created_at = timezone.now()
                score_obj.save()
                
                # 发送通知给用户
                notification = SystemNotification.objects.create(
                    title='个人分数调整通知',
                    content=f'''
                        <div style="padding: 12px; background: #f8f9fa; border-radius: 4px; font-size: 0.9rem;">
                            <p style="margin: 0 0 8px 0;"><strong>用户：</strong>{escape(score_obj.user.username)} | <strong>竞赛：</strong>{escape(competition.title)}</p>
                            <p style="margin: 0 0 8px 0;"><strong>分数变化：</strong>{old_score} → {new_score} <span style="color: {'#28a745' if score_diff > 0 else '#dc3545'}; font-weight: bold;">({'+' if score_diff > 0 else ''}{score_diff})</span></p>
                            <p style="margin: 0;"><strong>原因：</strong>{escape(reason)}</p>
                        </div>
                    '''
                )
                
                notification.get_p.add(score_obj.user)
                
                # 更新排名
                update_rankings_async(competition.id, is_team=False)
                
                # 清除个人分数缓存
                try:
                    clear_competition_ranking_cache(
                        competition.id,
                        is_team=False,
                        user_id=score_obj.user.id
                    )
                except Exception as e:
                    logger.error(f'清除缓存失败: {e}', exc_info=True)
                
                message = f'用户 "{score_obj.user.username}" 分数已调整：{old_score} → {new_score}'
                logger.warning(f'[分数调整] 用户:{request.user.username}调整了用户:{score_obj.user.username} 的分数，竞赛: {competition.title} ，消息: {message}')
            
            
            combined_msg = ''
            if competition.related_quiz and timezone.now() > competition.end_time:
                try:
                    from competition.utils_optimized import CombinedLeaderboardCalculator
                    
                    calculator = CombinedLeaderboardCalculator(competition, competition.related_quiz)
                    result = calculator.calculate_leaderboard_with_lock(force=True, force_recreate=True)
                    
                    if result.get('success'):
                        combined_msg = '，综合排行榜已自动更新'
                    else:
                        combined_msg = f'，但综合排行榜更新失败：{result.get("message", "未知错误")}'
                except Exception as e:
                    combined_msg = f'，但综合排行榜更新失败：{str(e)}'
                    logger.error(f'更新综合排行榜失败: {e}', exc_info=True)
            
            return JsonResponse({
                'success': True,
                'message': message + combined_msg
            })
            
    except Exception as e:
        logger.error(f'分数调整失败: {e}', exc_info=True)
        return JsonResponse({
            'success': False,
            'message': f'调整失败：{str(e)}'
        }, status=500)


def export_rankings(request, competition_id):
    """导出比赛排行榜"""
    competition = get_object_or_404(Competition, id=competition_id)
    
    # 检查权限
    if competition.author != request.user and not request.user.is_superuser:
        return redirect('competition:competition_detail', slug=competition.slug)
    
    # 创建CSV响应
    response = HttpResponse(content_type='text/csv')
    safe_title = slugify(competition.title) or f"{competition.title}"
    filename = f"{safe_title}_排名信息_{datetime.now().strftime('%Y%m%d')}.csv"
    
    filename_ascii = urllib.parse.quote(filename)
    filename_utf8 = urllib.parse.quote(filename.encode('utf-8'))
    
    # 设置Content-Disposition头，使用多种编码方式兼容不同浏览器
    response['Content-Disposition'] = (
        f'attachment; '
        f'filename="{filename_ascii}"; '
        f'filename*=UTF-8\'\'{filename_utf8}'
    )
    
    response['X-Content-Type-Options'] = 'nosniff'
    writer = csv.writer(response)
    
    if competition.competition_type == 'individual':
        # 个人赛排行榜
        writer.writerow(['排名', '用户名', '分数', '解题数量', '最后提交时间'])
        
        scores = ScoreUser.objects.filter(
            competition=competition
        ).select_related('user').order_by('-points', 'created_at')
        if not scores:
            messages.warning(request, "无排行数据导出")
            base_url = reverse('competition:competition_manage', kwargs={'slug': competition.slug})
            return redirect(f'{base_url}?active_tab=rankings')
        for score in scores:
            writer.writerow([
                score.rank,
                score.user.username,
                score.points,
                score.solved_challenges.count(),
                score.created_at.strftime('%Y-%m-%d %H:%M:%S')
            ])
    else:
        # 团队赛排行榜
        writer.writerow(['排名', '队伍名称', '分数', '解题数量', '最后提交时间'])
        
        scores = ScoreTeam.objects.filter(
            competition=competition
        ).select_related('team').order_by('-score', 'time')
        if not scores:
            messages.warning(request, "无排行数据导出")
            base_url = reverse('competition:competition_manage', kwargs={'slug': competition.slug})
            return redirect(f'{base_url}?active_tab=rankings')
        for score in scores:
            writer.writerow([
                score.rank,
                score.team.name,
                score.score,
                score.solved_challenges.count(),
                score.time.strftime('%Y-%m-%d %H:%M:%S')
            ])
    return response


@login_required
@require_POST
def audit_registration(request):
    """通过AJAX处理报名审核"""
    # 获取请求数据
    registration_id = request.POST.get('registration_id')
    user_id = request.POST.get('user_id')
    competition_id = request.POST.get('competition_id')
    audit_status = request.POST.get('audit')
    audit_comment = request.POST.get('audit_comment', '')
    
    # 验证数据
    if not all([registration_id, user_id, competition_id, audit_status]):
        return JsonResponse({'success': False, 'message': '缺少必要参数'})
    
    # 获取相关对象
    try:
        registration = Registration.objects.get(
            id=registration_id,
            user_id=user_id,
            competition_id=competition_id
        )
    except Registration.DoesNotExist:
        return JsonResponse({'success': False, 'message': '未找到报名信息'})
    
    # 检查权限
    if registration.competition.author != request.user and not request.user.is_superuser:
        return JsonResponse({'success': False, 'message': '您没有权限进行此操作'})
    
    # 检查比赛是否需要审核
    if not registration.competition.is_audit:
        return JsonResponse({'success': False, 'message': '此比赛不需要审核'})
    
    # 保存用户和比赛信息（用于发送通知）
    user = registration.user
    competition = registration.competition
    competition_title = competition.title
    
    if audit_status == 'approve':
        # 审核通过：更新状态
        registration.is_audit = True
        registration.audit = True
        registration.audit_comment = audit_comment or '审核通过'
        registration.save()
        message = '已通过报名申请'
        result_status = '✓ 审核通过'
        result_class = 'text-success'
        result_comment = audit_comment if audit_comment else ''
        extra_info = ''
    else:
        # 审核不通过：删除报名记录
        comment = audit_comment or '未填写原因'
        extra_info = ''
        
        # 团队赛特殊处理
        if registration.registration_type == Registration.TEAM and registration.team_name:
            team = registration.team_name
            if team.leader == user:
                # 用户是队长：删除整个队伍，同时删除所有队员的报名记录
                team_name = team.name
                team_members = list(team.members.all())
                
                # 删除该队伍所有成员的报名记录
                Registration.objects.filter(
                    competition=competition,
                    team_name=team
                ).delete()
                
                # 删除队伍
                team.delete()
                
                # 通知其他队员
                for member in team_members:
                    if member != user:
                        member_notification = SystemNotification.objects.create(
                            title='队伍解散通知',
                            content=f'''
                                <div class="notification-content">
                                    <p>您所在的队伍 <strong>{escape(team_name)}</strong> 已被解散</p>
                                    <p>原因：队长报名审核未通过</p>
                                    <p>比赛：<strong>{escape(competition_title)}</strong></p>
                                    <p class="text-muted mt-2">您可以重新创建或加入其他队伍报名。</p>
                                </div>
                            '''
                        )
                        member_notification.get_p.add(member)
                
                message = '已拒绝报名申请，队伍已解散'
                extra_info = f'（队伍"{team_name}"已解散，所有成员报名已清除）'
            else:
                # 用户是队员：仅移除该队员
                team.members.remove(user)
                registration.delete()
                message = '已拒绝报名申请，已从队伍移除'
                extra_info = f'（已从队伍"{team.name}"中移除）'
        else:
            # 个人赛：直接删除报名记录
            registration.delete()
            message = '已拒绝报名申请，报名记录已清除'
        
        result_status = '✗ 审核未通过'
        result_class = 'text-danger'
        result_comment = comment
    
    # 发送通知给被审核用户
    notification_content = f'''
        <div class="notification-content">
            <p>您报名的比赛：<strong>{escape(competition_title)}</strong></p>
            <p>审核结果：<span class="{result_class}"><strong>{escape(result_status)}</strong></span></p>
    '''
    
    # 添加审核意见（如果有）
    if result_comment:
        notification_content += f'<p>审核意见：{escape(result_comment)}</p>'
    
    # 添加底部提示
    if audit_status == 'approve':
        notification_content += '<p class="text-muted mt-2">祝您比赛顺利！</p>'
    else:
        notification_content += '<p class="text-muted mt-2">您可以重新报名参加此比赛。</p>'
    
    notification_content += '</div>'
    
    notification = SystemNotification.objects.create(
        title='比赛报名审核结果通知',
        content=notification_content
    )
    notification.get_p.add(user)
    
    return JsonResponse({'success': True, 'message': message + extra_info})


@login_required
@require_POST
def refresh_invitation_code(request):
    """刷新比赛报名码"""
    import random
    import string
    
    slug = request.POST.get('slug')
    
    if not slug:
        return JsonResponse({'success': False, 'message': '缺少比赛标识'})
    
    try:
        competition = Competition.objects.get(slug=slug)
    except Competition.DoesNotExist:
        return JsonResponse({'success': False, 'message': '比赛不存在'})
    
    # 检查权限：只有比赛创建者或超级管理员可以刷新
    if competition.author != request.user and not request.user.is_superuser:
        return JsonResponse({'success': False, 'message': '您没有权限执行此操作'})
    
    # 检查是否是内部赛
    if competition.visibility_type != Competition.INTERNAL:
        return JsonResponse({'success': False, 'message': '只有内部赛才需要报名码'})
    
    # 生成新的报名码（8位随机字母数字）
    new_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    competition.invitation_code = new_code
    competition.save(update_fields=['invitation_code'])
    
    return JsonResponse({
        'success': True, 
        'message': '报名码已刷新',
        'new_code': new_code
    })


@method_decorator(login_required, name='dispatch')
class SubmissionDynamicView(TemplateView):
    """
    解题动态视图 - 表格形式展示解题情况
    
    性能优化：
    1. 使用自定义缓存key，解题时立即清除
    2. 优化算法复杂度：O(n²) → O(n)
    3. 使用 defaultdict 简化代码
    4. 一次遍历完成所有计算
    5. 数据库查询优化（only减少字段）
    6. 30秒缓存平衡实时性和性能
    
    权限控制：
    1. 需要登录才能查看
    2. 需要报名参赛才能查看解题动态
    """
    
    def dispatch(self, request, *args, **kwargs):
        """在dispatch阶段进行权限验证"""
        slug = self.kwargs['slug']
        competition = get_object_or_404(Competition, slug=slug)
        
        #  权限验证：检查用户是否报名参赛
        registration = Registration.objects.filter(
            competition=competition,
            user=request.user
        ).first()
        
        # 如果用户未报名，且不是比赛创建者，也不是管理员，则拒绝访问
        if not registration and not competition.author == request.user and not request.user.is_staff:
            messages.warning(request, "您还未报名该比赛，无法查看解题动态")
            return redirect('competition:competition_detail', slug=slug)
        
        return super().dispatch(request, *args, **kwargs)

    def get_template_names(self):
        """根据比赛的theme字段动态选择模板"""
        slug = self.kwargs['slug']
        competition = get_object_or_404(Competition, slug=slug)
        
        if competition.theme == 'tech':
            return ['competition/tech/submission_dynamic.html']
        else:
            return ['competition/anime/submission_dynamic.html']

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        slug = self.kwargs['slug']

        # 获取比赛
        competition = get_object_or_404(Competition, slug=slug)
        context['competition'] = competition
        
        #  判断比赛类型
        is_team_competition = competition.competition_type == Competition.TEAM
        
        #  分页参数
        page = self.request.GET.get('page', 1)
        try:
            page = int(page)
        except (TypeError, ValueError):
            page = 1
        
        #  获取当前用户的信息（实时获取，不缓存）
        current_user_info = self._get_current_user_info(competition, is_team_competition)
        
        #  性能优化：全局缓存（所有用户共享，包含分页）
        # v2 表示版本，修改版本号会自动失效旧缓存
        cache_key = f'submission_dynamic_v2:{competition.id}:page:{page}'
        cached_data = cache.get(cache_key)
        
        if cached_data:
            # 缓存命中，添加用户信息后返回
            context.update(cached_data)
            context['current_user_info'] = current_user_info
            return context

        # 优化：使用 only() 只获取需要的字段，减少数据传输
        submissions = Submission.objects.filter(
            competition=competition,
            status='correct'
        ).select_related(
            'challenge',
            'user',
            'team'
        ).only(
            'challenge__id',
            'user__username',
            'team__name',
            'created_at'
        ).order_by('created_at')

      
        challenges = list(competition.challenges.all().order_by('id'))
        context['challenges'] = challenges

       
        from collections import defaultdict
        
        # 初始化数据结构
        players_dict = {}  # {solver_key: {'name': ..., 'submissions': {...}}}
        first_solves = defaultdict(list)  # {challenge_id: [solver_key1, solver_key2, ...]}
        
      
        if is_team_competition:
            # 团队赛：从 ScoreTeam 获取得分和排名
            score_records = ScoreTeam.objects.filter(
                competition=competition
            ).select_related('team').prefetch_related('solved_challenges')
            
            # 建立队伍得分映射
            team_score_map = {}
            for score_record in score_records:
                team_score_map[score_record.team.name] = {
                    'score': score_record.score,
                    'rank': score_record.rank,
                    'team': score_record.team,
                    'solved_count': score_record.solved_challenges.count()
                }
        else:
            # 个人赛：从 ScoreUser 获取得分和排名
            score_records = ScoreUser.objects.filter(
                competition=competition
            ).select_related('user').prefetch_related('solved_challenges')
            
            # 建立用户得分映射
            user_score_map = {}
            for score_record in score_records:
                user_score_map[score_record.user.username] = {
                    'score': score_record.points,
                    'rank': score_record.rank,
                    'team': score_record.team,
                    'solved_count': score_record.solved_challenges.count()
                }
        
        for s in submissions:
            if is_team_competition:
               
                if not s.team:
                    continue 
                solver = s.team.name
                solver_data = team_score_map.get(solver, {
                    'score': 0, 'rank': 0, 'team': s.team, 'solved_count': 0
                })
            else:
            
                solver = s.user.username
                solver_data = user_score_map.get(solver, {
                    'score': 0, 'rank': 0, 'team': None, 'solved_count': 0
                })
            
            cid = s.challenge.id
            
            if solver not in players_dict:
                players_dict[solver] = {
                    'name': solver,
                    'submissions': {},
                    'team': solver_data.get('team'),
                    'user': s.user.username,
                    'score': solver_data.get('score', 0),
                    'rank': solver_data.get('rank', 0),
                    'solved_count': solver_data.get('solved_count', 0)
                }
            
            if solver not in first_solves[cid]:
                first_solves[cid].append(solver)
        
      
        for cid, solvers in first_solves.items():
            for idx, solver in enumerate(solvers):
                rank = idx + 1
                
              
                if rank == 1:
                    status = "一血"
                elif rank == 2:
                    status = "二血"
                elif rank == 3:
                    status = "三血"
                else:
                    status = "已解决"
                
               
                if solver in players_dict:
                    players_dict[solver]['submissions'][cid] = status

        
        players_list = sorted(
            players_dict.values(), 
            key=lambda x: (x['rank'] if x['rank'] > 0 else 999999, -x['score'])  # 按排名升序，得分降序
        )
        
        if is_team_competition:
            for player in players_list:
                if player['team']:
                    
                    members = player['team'].members.values_list('username', flat=True)
                    player['members'] = list(members)
                else:
                    player['members'] = []
        

        paginator = Paginator(players_list, 15)  # 每页15条
        
        try:
            players_page = paginator.page(page)
        except PageNotAnInteger:
            players_page = paginator.page(1)
        except EmptyPage:
            players_page = paginator.page(paginator.num_pages)
        
        players_list = list(players_page)
        

        cache_data = {
            'challenges': challenges,
            'players': players_list,
            'is_team_competition': is_team_competition,
            'paginator': paginator,
            'page_obj': players_page,
        }
        
        cache.set(cache_key, cache_data, 30)
        
        context.update(cache_data)
        context['current_user_info'] = current_user_info  # 用户信息单独添加，不缓存
        return context
    
    def _get_current_user_info(self, competition, is_team_competition):
        """
        获取当前用户的比赛信息
        无论缓存是否命中，都要获取最新的用户数据
        """
        current_user_info = None
        
        if is_team_competition:
          
            try:
               
                user_team = Team.objects.filter(
                    members=self.request.user,
                    competition=competition
                ).first()
                
                if user_team:
                    
                    members = user_team.members.values_list('username', flat=True)
                    
                    
                    score_team = ScoreTeam.objects.filter(
                        team=user_team,
                        competition=competition
                    ).first()
                    
                    if score_team:
                        # 有得分记录，显示实际数据
                        current_user_info = {
                            'user_name': self.request.user.username,
                            'team_name': user_team.name,
                            'members': list(members),
                            'score': score_team.score,
                            'rank': score_team.rank,
                            'solved_count': score_team.solved_challenges.count()
                        }
                    else:
                        # 没有得分记录，显示初始数据
                        current_user_info = {
                            'user_name': self.request.user.username,
                            'team_name': user_team.name,
                            'members': list(members),
                            'score': 0,
                            'rank': 0,  # 0表示未上榜
                            'solved_count': 0
                        }
            except Exception as e:
                pass  
        else:
           
            try:
                
                registration = Registration.objects.filter(
                    user=self.request.user,
                    competition=competition
                ).first()
                
                if registration:
                   
                    score_user = ScoreUser.objects.filter(
                        user=self.request.user,
                        competition=competition
                    ).first()
                    
                    if score_user:
                        
                        current_user_info = {
                            'user_name': self.request.user.username,
                            'team_name': None,
                            'members': [],
                            'score': score_user.points,
                            'rank': score_user.rank,
                            'solved_count': score_user.solved_challenges.count()
                        }
                    else:
                        # 没有得分记录，显示初始数据
                        current_user_info = {
                            'user_name': self.request.user.username,
                            'team_name': None,
                            'members': [],
                            'score': 0,
                            'rank': 0,  # 0表示未上榜
                            'solved_count': 0
                        }
            except Exception as e:
                pass  # 如果获取失败，保持 None
        
        return current_user_info

@method_decorator(cache_page(30), name='dispatch')
class SubmissionDynamicAPIView(generic.View):
    """解题动态API视图 - 提供JSON格式的解题数据"""
    
    def get(self, request, slug):
        """获取解题动态API数据"""
        competition = get_object_or_404(Competition, slug=slug)
        
        # 获取分页参数
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 20))
        
        # 计算偏移量
        offset = (page - 1) * page_size
        
        # 获取解题动态数据
        submissions = Submission.objects.filter(
            competition=competition,
            status='correct'
        ).select_related(
            'challenge', 
            'user', 
            'team'
        ).order_by('-created_at')[offset:offset + page_size]
        
        # 构建响应数据
        data = []
        for submission in submissions:
            is_first_blood = not Submission.objects.filter(
                challenge=submission.challenge,
                competition=competition,
                status='correct',
                created_at__lt=submission.created_at
            ).exists()
            
            data.append({
                'id': submission.id,
                'challenge': {
                    'id': submission.challenge.id,
                    'title': submission.challenge.title,
                    'category': submission.challenge.category if hasattr(submission.challenge, 'category') else '其他',
                    'points': submission.challenge.points
                },
                'user': {
                    'id': submission.user.id,
                    'username': submission.user.username,
                    'display_name': submission.user.get_full_name() or submission.user.username
                },
                'team': {
                    'id': submission.team.id if submission.team else None,
                    'name': submission.team.name if submission.team else '个人参赛'
                },
                'submission_time': submission.created_at.isoformat(),
                'formatted_time': submission.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                'points_earned': submission.points_earned,
                'is_first_blood': is_first_blood,
                'relative_time': self.get_relative_time(submission.created_at)
            })
        
        # 获取总数用于分页
        total_count = Submission.objects.filter(
            competition=competition,
            status='correct'
        ).count()
        
        return JsonResponse({
            'data': data,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': total_count,
                'pages': (total_count + page_size - 1) // page_size
            }
        })
    
    def get_relative_time(self, dt):
        """获取相对时间描述"""
        now = timezone.now()
        diff = now - dt
        
        if diff.days > 0:
            return f"{diff.days}天前"
        elif diff.seconds > 3600:
            return f"{diff.seconds // 3600}小时前"
        elif diff.seconds > 60:
            return f"{diff.seconds // 60}分钟前"
        else:
            return "刚刚"


@method_decorator(cache_page(60 * 5), name='dispatch')  # 缓存5分钟
class SubmissionDynamicDemoView(TemplateView):
    
    template_name = 'competition/tech/submission_dynamic.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        #  分页参数
        page = self.request.GET.get('page', 1)
        try:
            page = int(page)
        except (TypeError, ValueError):
            page = 1
        
        # 模拟比赛信息
        class DemoCompetition:
            title = "2024 春季网络安全挑战赛"
            slug = "demo-ctf-2024"
            
        context['competition'] = DemoCompetition()
        
        # 模拟题目列表（横向表头）- 支持大量题目展示
        class DemoChallenge:
            def __init__(self, id, title):
                self.id = id
                self.title = title
        
        challenges = [
            # Web 分类
            DemoChallenge(1, "简单的Web"),
            DemoChallenge(2, "SQL注入"),
            DemoChallenge(3, "XSS攻击"),
            DemoChallenge(4, "文件上传"),
            DemoChallenge(5, "SSRF漏洞"),
            DemoChallenge(6, "XXE注入"),
            # Pwn 分类
            DemoChallenge(7, "栈溢出"),
            DemoChallenge(8, "格式化字符串"),
            DemoChallenge(9, "堆溢出"),
            DemoChallenge(10, "UAF漏洞"),
            DemoChallenge(11, "ROP链"),
            # Crypto 分类
            DemoChallenge(12, "RSA加密"),
            DemoChallenge(13, "AES解密"),
            DemoChallenge(14, "古典密码"),
            DemoChallenge(15, "哈希碰撞"),
            # Misc 分类
            DemoChallenge(16, "编码转换"),
            DemoChallenge(17, "流量分析"),
            DemoChallenge(18, "取证分析"),
            DemoChallenge(19, "隐写术"),
            DemoChallenge(20, "社工题"),
            # Reverse 分类
            DemoChallenge(21, "逆向基础"),
            DemoChallenge(22, "反调试"),
            DemoChallenge(23, "壳破解"),
            DemoChallenge(24, "算法还原"),
        ]
        context['challenges'] = challenges
        
        # 模拟队伍/选手数据（纵向表头）
        team_names = [
            "网络安全战队",
            "代码破解者",
            "白帽黑客",
            "赛博勇士",
            "二进制忍者",
            "逆向工程师",
            "漏洞猎人",
            "Pwn星人",
            "密码学专家",
            "Web安全小组",
            "CTF爱好者",
            "蓝莲花战队",
            "天枢战队",
            "r3kapig",
            "龙猫学院",
        ]
        
        
        import random
        
       
        players_dict = {
            name: {
                'name': name, 
                'submissions': {},
                'score': 0,
                'solved_count': 0,
                'members': [f'选手{i+1}' for i in range(random.randint(1, 5))]  # 模拟1-5个队员
            } 
            for name in team_names
        }
        
        first_bloods = {} 
        
        challenge_scores = {c.id: random.choice([100, 200, 300, 400, 500]) for c in challenges}
        
        # 一次性生成所有解题数据
        for team_name in team_names:
            # 随机决定这个队伍解出哪些题（40%-80%）
            num_solved = random.randint(
                int(len(challenges) * 0.4),
                int(len(challenges) * 0.8)
            )
            solved_challenges = random.sample(challenges, num_solved)
            
            for challenge in solved_challenges:
                cid = challenge.id
                # 记录解题顺序
                if cid not in first_bloods:
                    first_bloods[cid] = []
                first_bloods[cid].append(team_name)
                # 累加分数
                players_dict[team_name]['score'] += challenge_scores.get(cid, 100)
                players_dict[team_name]['solved_count'] += 1
        
        
        for cid, solvers in first_bloods.items():
            for idx, team_name in enumerate(solvers):
                rank = idx + 1
                if rank == 1:
                    status = "一血"
                elif rank == 2:
                    status = "二血"
                elif rank == 3:
                    status = "三血"
                else:
                    status = "已解决"
                players_dict[team_name]['submissions'][cid] = status
        
        # 转换为列表并按得分排序，计算名次
        players_list = sorted(
            players_dict.values(), 
            key=lambda x: (-x['score'], -x['solved_count'])  # 得分降序，解题数降序
        )
        
        # 计算名次（处理并列情况）
        for idx, player in enumerate(players_list):
            if idx == 0:
                player['rank'] = 1
            else:
                # 如果得分和解题数都相同，则名次相同
                prev_player = players_list[idx - 1]
                if (player['score'] == prev_player['score'] and 
                    player['solved_count'] == prev_player['solved_count']):
                    player['rank'] = prev_player['rank']
                else:
                    player['rank'] = idx + 1
        
      

        
        paginator = Paginator(players_list, 10)  # 每页10条
        
        try:
            players_page = paginator.page(page)
        except PageNotAnInteger:
            players_page = paginator.page(1)
        except EmptyPage:
            players_page = paginator.page(paginator.num_pages)
        
        # 分页后的数据
        players_list_paged = list(players_page)
        
        context['players'] = players_list_paged
        context['is_team_competition'] = True  # 演示视图默认为团队赛
        context['paginator'] = paginator
        context['page_obj'] = players_page
        
        #  模拟当前用户信息（演示用）
        import random
        # 从所有队伍中随机选择（不只是当前页）
        demo_team = random.choice(list(players_dict.values()))
        context['current_user_info'] = {
            'user_name': '演示用户',
            'team_name': demo_team['name'],
            'members': demo_team.get('members', []),
            'score': demo_team['score'],
            'rank': demo_team['rank'],
            'solved_count': demo_team['solved_count']
        }
        
        return context


# ============ 综合排行榜视图 ============
@login_required
def combined_rankings_view(request, slug):
    """综合排行榜页面（CTF+知识竞赛）- 高并发优化版"""
    
    competition = get_object_or_404(Competition, slug=slug)
    
    if not competition.related_quiz:
        # 如果没有关联知识竞赛，重定向到普通排行榜
        return redirect('competition:rankings', slug=slug, ranking_type=competition.competition_type)
    
    # 权限检查（只有比赛结束后才能查看）
    can_view = False
    can_force_refresh = False
    
    # 只有比赛结束后才开放综合排行榜
    if timezone.now() > competition.end_time:
        if request.user.is_authenticated:
            can_view = True
            
            # 管理员或比赛创建者可以强制刷新
            if request.user.is_staff or request.user.is_superuser or competition.author == request.user:
                can_force_refresh = True
    
    if not can_view:
        messages.info(request, f'综合排行榜将在比赛结束后开放查看（比赛结束时间：{competition.end_time.strftime("%Y-%m-%d %H:%M")}）')
        return redirect('competition:competition_detail', slug=slug)
    
   
    page = request.GET.get('page', 1)
    page_size = int(request.GET.get('page_size', 20))
    page_size = min(page_size, 500)  # 最大500条
    force_refresh = request.GET.get('force_refresh') == '1'
    
    
    if force_refresh and not can_force_refresh:
        messages.warning(request, '没有权限强制刷新排行榜')
        force_refresh = False
    
    
    from competition.models import CombinedLeaderboard, LeaderboardCalculationTask
    from competition.utils_optimized import CombinedLeaderboardCalculator
    from competition.distributed_lock import get_leaderboard_lock
    import time
    
    existing_count = CombinedLeaderboard.objects.filter(competition=competition).count()
    
    
    need_calculation = existing_count == 0 or (force_refresh and can_force_refresh)
    
    if need_calculation and timezone.now() > competition.end_time:
        # 检查是否有正在运行的任务
        running_task = LeaderboardCalculationTask.objects.filter(
            competition=competition,
            status='running'
        ).first()
        
        if running_task:
            
            messages.info(request, '排行榜正在计算中，请稍等...')
            time.sleep(1)
            
            # 再次检查
            running_task.refresh_from_db()
            if running_task.status == 'running':
                messages.warning(request, f'排行榜正在计算中，进度：{running_task.progress_percentage}%，请稍后刷新')
                # 如果已有部分数据，可以先显示
                if existing_count > 0 and not force_refresh:
                    pass  # 继续显示现有数据
                else:
                    return redirect('competition:competition_detail', slug=slug)
        else:
           
            try:
                if force_refresh:
                    CombinedLeaderboardCalculator.clear_cache(competition.id)
                
                logger.info(f'[VIEW] 开始计算综合排行榜: competition_id={competition.id}')
                calculator = CombinedLeaderboardCalculator(competition, competition.related_quiz)
                result = calculator.calculate_leaderboard_with_lock(force=force_refresh)
                
                if not result.get('success'):
                    logger.error(f'[VIEW] 计算失败: {result.get("message")}')
                    messages.error(request, result.get('message', '计算综合排行榜失败'))
                    return redirect('competition:competition_detail', slug=slug)
                
                messages.success(request, f'排行榜已更新为最新数据')
                
            except Exception as e:
                logger.error(f'[VIEW] 计算排行榜异常: {e}', exc_info=True)
                messages.error(request, '计算排行榜失败，请稍后重试')
                
                # 如果已有数据，继续显示
                if existing_count == 0:
                    return redirect('competition:competition_detail', slug=slug)
    
    # 从数据库直接查询分页数据
    if competition.competition_type == 'individual':
        queryset = CombinedLeaderboard.objects.filter(
            competition=competition,
            user__isnull=False
        ).select_related('user').order_by('rank')
    else:
        queryset = CombinedLeaderboard.objects.filter(
            competition=competition,
            team__isnull=False
        ).select_related('team', 'team__leader').prefetch_related('team__members').order_by('rank')
    
    # 标准Django分页
    paginator = Paginator(queryset, page_size)
    
    try:
        leaderboard_page = paginator.page(page)
    except PageNotAnInteger:
        leaderboard_page = paginator.page(1)
    except EmptyPage:
        leaderboard_page = paginator.page(paginator.num_pages)
    
    total_count = paginator.count
    
    # 查找当前用户/队伍的成绩
    my_score = None
    if request.user.is_authenticated:
        try:
            if competition.competition_type == 'individual':
                my_record = CombinedLeaderboard.objects.filter(
                    competition=competition,
                    user=request.user
                ).first()
                if my_record:
                    my_score = {
                        'rank': my_record.rank,
                        'user_id': my_record.user.id,
                        'username': my_record.user.username,
                        'real_name': getattr(my_record.user, 'real_name', my_record.user.username),
                        'ctf_score': float(my_record.ctf_score),
                        'ctf_rank': my_record.ctf_rank,
                        'quiz_score': float(my_record.quiz_score),
                        'combined_score': float(my_record.combined_score),
                    }
            else:
                from competition.models import Team
                user_team = Team.objects.filter(
                    competition=competition,
                    members=request.user
                ).first()
                
                if user_team:
                    my_record = CombinedLeaderboard.objects.filter(
                        competition=competition,
                        team=user_team
                    ).first()
                    if my_record:
                        my_score = {
                            'rank': my_record.rank,
                            'team_id': my_record.team.id,
                            'team_name': my_record.team.name,
                            'team_code': my_record.team.team_code,
                            'ctf_score': float(my_record.ctf_score),
                            'ctf_rank': my_record.ctf_rank,
                            'quiz_score': float(my_record.quiz_score),
                            'combined_score': float(my_record.combined_score),
                        }
        except Exception as e:
            logger.error(f'获取我的排名失败: {e}', exc_info=True)
    
    # 获取最新的计算任务信息（仅管理员）
    latest_task = None
    if can_force_refresh:
        latest_task = LeaderboardCalculationTask.objects.filter(
            competition=competition
        ).order_by('-created_at').first()
    
    if competition.theme == 'tech':
        template = 'competition/tech/combined_rankings.html'
    else:
        template = 'competition/anime/combined_rankings.html'
    
    context = {
        'competition': competition,
        'can_view_early': can_force_refresh,
        'leaderboard': leaderboard_page,
        'my_score': my_score,
        'total_count': total_count,
        'ctf_weight': int(float(competition.combined_score_ctf_weight) * 100),
        'quiz_weight': int((1 - float(competition.combined_score_ctf_weight)) * 100),
        'top_percent': competition.combined_score_top_percent,
        'latest_task': latest_task,
    }
    
    return render(request, template, context)



@login_required
def writeup_captcha(request):
    """生成 Writeup 上传验证码"""
    from public.utils import create_captcha_for_writeup
    result = create_captcha_for_writeup()
    return JsonResponse(result)


@login_required
@require_http_methods(["POST"])
def writeup_upload(request, slug):
    """处理用户上传 Writeup"""
    from public.utils import verify_writeup_captcha
    
    try:
        competition = get_object_or_404(Competition, slug=slug)
        
        if not competition.is_ended():
            return JsonResponse({'success': False, 'message': '比赛尚未结束，暂不能提交 Writeup'})
        
        
        registration = competition.registrations.filter(user=request.user, audit=True).first()
        if not registration:
            return JsonResponse({'success': False, 'message': '您未报名此比赛，无法提交 Writeup'})
        
       
        team = None
        if competition.competition_type == 'team':
            team = registration.team_name
            if not team:
                return JsonResponse({'success': False, 'message': '未找到您的队伍信息'})
        
        if competition.competition_type == 'team':
            if Writeup.objects.filter(competition=competition, team=team).exists():
                return JsonResponse({'success': False, 'message': '您的队伍已经提交过 Writeup，每个队伍只能提交一次'})
        else:
            if Writeup.objects.filter(competition=competition, user=request.user).exists():
                return JsonResponse({'success': False, 'message': '您已经提交过 Writeup，每人只能提交一次'})
        
        captcha_key = request.POST.get('captcha_key', '')
        captcha_input = request.POST.get('captcha', '').strip()
        if not verify_writeup_captcha(captcha_key, captcha_input):
            return JsonResponse({'success': False, 'message': '验证码错误'})
        
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        pdf_file = request.FILES.get('pdf_file')
        
        
        if not title:
            return JsonResponse({'success': False, 'message': '请输入标题'})
        
        if len(title) < 5:
            return JsonResponse({'success': False, 'message': '标题至少需要5个字符'})
        
        if not pdf_file:
            return JsonResponse({'success': False, 'message': '请上传 PDF 文件'})
        
        # 验证文件类型
        if not pdf_file.name.lower().endswith('.pdf'):
            return JsonResponse({'success': False, 'message': '只支持 PDF 格式'})
        
        # 验证文件大小（20MB）
        max_size = 20 * 1024 * 1024
        if pdf_file.size > max_size:
            size_mb = pdf_file.size / (1024 * 1024)
            return JsonResponse({'success': False, 'message': f'文件大小 {size_mb:.2f}MB 超过限制（最大 20MB）'})
        
        # 验证文件不为空
        if pdf_file.size < 1024:  # 小于1KB可能是空文件
            return JsonResponse({'success': False, 'message': '文件内容异常，请检查文件'})
        
        with transaction.atomic():
            if competition.competition_type == 'team':
                if Writeup.objects.select_for_update().filter(competition=competition, team=team).exists():
                    return JsonResponse({'success': False, 'message': '您的队伍已经提交过 Writeup'})
            else:
                if Writeup.objects.select_for_update().filter(competition=competition, user=request.user).exists():
                    return JsonResponse({'success': False, 'message': '您已经提交过 Writeup'})
            
            # 创建记录
            Writeup.objects.create(
                competition=competition,
                user=request.user,
                team=team,
                title=title,
                description=description,
                pdf_file=pdf_file
            )
        
        logger.info(f"Writeup 提交成功 - 用户: {request.user.username}, 比赛: {competition.title}, 队伍: {team.name if team else '无'}")
        return JsonResponse({'success': True, 'message': 'Writeup 提交成功！感谢您的分享 🎉'})
        
    except Exception as e:
        logger.error(f"Writeup 上传失败 - 用户: {request.user.username}, 错误: {str(e)}")
        return JsonResponse({'success': False, 'message': '提交失败，请稍后重试'})


@login_required
def template_download(request, template_uuid):
    """下载 Writeup 模板"""
    from django.http import FileResponse
    import urllib.parse
    
    template = get_object_or_404(WriteupTemplate, uuid=template_uuid)
    
    # 验证模板是否启用
    if not template.is_active:
        return JsonResponse({'success': False, 'message': '该模板已停用'})
    
    # 构建文件名：比赛名称 + "Writeup模板"
    if template.competition:
        filename = f"{template.competition.title} Writeup模板"
    else:
        filename = f"{template.title}"
    
    file_extension = template.template_file.name.split('.')[-1]
    filename = f"{filename}.{file_extension}"
    
    encoded_filename = urllib.parse.quote(filename)
    
    content_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' if file_extension == 'docx' else 'application/msword'
    
    # 返回文件
    response = FileResponse(template.template_file.open('rb'), content_type=content_type)
    response['Content-Disposition'] = f"attachment; filename*=UTF-8''{encoded_filename}"
    
    return response


@login_required
@require_http_methods(["POST"])
def delete_writeup(request, writeup_id):
    """删除 Writeup"""
    try:
        writeup = get_object_or_404(Writeup, id=writeup_id)
        competition = writeup.competition
        
        # 检查权限 - 只有比赛创建者可以删除
        if competition.author != request.user and not request.user.is_superuser:
            return JsonResponse({'success': False, 'message': '没有权限'}, status=403)
        
        # 删除文件
        if writeup.pdf_file:
            writeup.pdf_file.delete()
        
        # 删除记录
        writeup.delete()
        
        logger.info(f"Writeup 删除成功 - 管理员: {request.user.username}, Writeup ID: {writeup_id}")
        return JsonResponse({'success': True})
        
    except Exception as e:
        logger.error(f"删除 Writeup 失败 - 管理员: {request.user.username}, 错误: {str(e)}")
        return JsonResponse({'success': False, 'message': '删除失败，请稍后重试'}, status=500)


@login_required
@require_http_methods(["GET"])
def secure_url_download(request, slug, challenge_uuid, token):
    """
    安全的URL文件代理下载（比赛模块）
    
    为 static_file_url 提供安全控制：
    1. 令牌验证（防止URL被篡改）
    2. 时效性检查（默认5分钟有效期）
    3. 频率限制（防止恶意下载）
    4. IP限制（记录和限制下载来源）
    
    Args:
        request: Django request 对象
        slug: 比赛的slug标识符
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
        challenge = get_object_or_404(Challenge, uuid=challenge_uuid)
        
        # 2. 验证令牌
        token_generator = DownloadTokenGenerator()
        is_valid, error_msg = token_generator.verify_token(
            token,
            challenge.id,  # 使用 challenge.id 作为 file_id
            request.user.id
        )
        
        if not is_valid:
            logger.warning(
                f"比赛URL下载令牌验证失败: 用户={request.user.username}, "
                f"题目={challenge.title}, 原因={error_msg}"
            )
            return HttpResponseForbidden(f"<h1>403 禁止访问</h1><p>{error_msg}</p>")
        
        # 3. 检查题目是否有静态文件URL
        if not challenge.static_file_url:
            logger.warning(
                f"比赛题目无静态文件URL: 用户={request.user.username}, "
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
                f"比赛URL下载频率限制: 用户={request.user.username}, "
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
            f"比赛URL文件下载: 用户={request.user.username}, "
            f"题目={challenge.title}, IP={client_ip}, "
            f"URL={challenge.static_file_url}"
        )
        
        # 7. 重定向到实际的文件URL
        return HttpResponseRedirect(challenge.static_file_url)
        
    except Challenge.DoesNotExist:
        logger.error(f"比赛题目不存在: UUID={challenge_uuid}")
        return HttpResponseForbidden("<h1>404 未找到</h1><p>题目不存在</p>")
    
    except Exception as e:
        logger.error(
            f"比赛URL下载异常: 用户={request.user.username}, "
            f"题目UUID={challenge_uuid}, 错误={str(e)}",
            exc_info=True
        )
        return HttpResponse(
            "<h1>500 服务器错误</h1><p>文件下载失败，请稍后重试</p>",
            status=500
        )


@login_required
def dual_track_entrance(request, slug):
    """
    双赛道竞赛入口页面
    当CTF竞赛关联了知识竞赛时，显示左右两个赛道入口
    """
    from django.db.models import Count
    
    competition = get_object_or_404(Competition, slug=slug)
   
    if not competition.related_quiz:
        return redirect('competition:com_index', slug=slug)
    
    if competition.visibility_type == 'internal':
        if not request.user.is_authenticated:
            return redirect('account_login')
        
        
        user_registered = Registration.objects.filter(
            user=request.user,
            competition=competition,
            audit=True
        ).exists()
        
        # 管理员和创建者可以访问
        if not user_registered and not request.user.is_staff and competition.author != request.user:
            messages.warning(request, '您暂时无权限访问该竞赛')
            return redirect('competition:CompetitionView')
    
    ctf_challenge_count = competition.challenges.filter(is_active=True).count()
    
    if competition.competition_type == 'team':
        ctf_participant_count = Team.objects.filter(competition=competition).count()
    else:
        ctf_participant_count = Registration.objects.filter(
            competition=competition,
            audit=True
        ).count()
    
    quiz = competition.related_quiz
    from quiz.models import QuizRecord
    
    quiz_question_count = quiz.questions.count()
    quiz_participant_count = QuizRecord.objects.filter(
        quiz=quiz,
        status__in=['in_progress', 'completed']
    ).values('user').distinct().count()
    
    # 根据主题选择模板
    if competition.theme == 'tech':
        template = 'competition/tech/dual_track_entrance.html'
    else:
        template = 'competition/anime/dual_track_entrance.html'
    
    context = {
        'competition': competition,
        'now': timezone.now(),
        'ctf_challenge_count': ctf_challenge_count,
        'ctf_participant_count': ctf_participant_count,
        'quiz_question_count': quiz_question_count,
        'quiz_participant_count': quiz_participant_count,
    }
    
    return render(request, template, context)

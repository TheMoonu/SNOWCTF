# -*- coding: utf-8 -*-
"""
练习模块异步任务
包括异步容器创建、容器清理等
"""
import time
import logging
from celery import shared_task
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone

from practice.models import PC_Challenge
from practice.view_api import ContainerManager
from practice.redis_cache import UserContainerCache

logger = logging.getLogger('apps.practice')
User = get_user_model()


class TaskStatus:
    """任务状态常量"""
    PENDING = 'pending'      # 等待处理
    PROCESSING = 'processing'  # 处理中
    SUCCESS = 'success'      # 成功
    FAILED = 'failed'        # 失败
    TIMEOUT = 'timeout'      # 超时


@shared_task(bind=True, max_retries=0)
def create_container_async(self, challenge_uuid, user_id, request_meta=None):
    """
    异步创建容器任务
    
    Args:
        self: Celery task实例（bind=True自动注入）
        challenge_uuid: 题目UUID
        user_id: 用户ID
        request_meta: 请求元数据（用于日志，包含target_node、资源需求等）
        
    Returns:
        dict: 任务结果
        {
            'status': 'success' | 'failed',
            'data': {容器信息} 或 None,
            'error': 错误信息 或 None,
            'task_id': 任务ID
        }
    """
    from django.db import close_old_connections
    from container.resource_reservation import ResourceReservationManager
    
    task_id = self.request.id
    cache_key = f"container_task:{task_id}"
    container_lock_key = f"container_lock:{user_id}:{challenge_uuid}"
    
    #  提取资源预占标识、目标节点和资源需求
    reserve_key = None
    target_node = None
    memory_requests = None
    cpu_requests = None
    if request_meta:
        reserve_key = request_meta.get('reserve_key')
        target_node = request_meta.get('target_node')
        memory_requests = request_meta.get('memory_requests')
        cpu_requests = request_meta.get('cpu_requests')
    
    # 任务开始时，确保数据库连接是新鲜的
    close_old_connections()
    
    # 初始化任务状态（保存完整信息供取消时使用）
    task_info = {
        'status': TaskStatus.PROCESSING,
        'progress': 10,
        'message': '正在初始化容器创建任务...',
        'task_id': task_id,
        'user_id': user_id,  #  新增：用户ID
        'challenge_uuid': challenge_uuid,  #  新增：题目UUID
        'started_at': timezone.now().isoformat()
    }
    cache.set(cache_key, task_info, timeout=300)  # 5分钟过期
    
    try:
        # 1. 获取用户对象
        logger.info(f"[Task {task_id}] 开始创建容器: user={user_id}, challenge={challenge_uuid}")
        
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist as e:
            error_msg = f"用户不存在: {str(e)}"
            logger.error(f"[Task {task_id}] {error_msg}")
            task_info.update({
                'status': TaskStatus.FAILED,
                'error': error_msg,
                'progress': 0
            })
            cache.set(cache_key, task_info, timeout=300)
            return task_info
        
        # 2. 更新进度：验证题目
        task_info.update({
            'progress': 20,
            'message': '正在验证题目配置...'
        })
        cache.set(cache_key, task_info, timeout=300)
        
        # 检查是否已有容器（可能用户重复提交）
        #  验证缓存有效性：检查数据库中容器的真实状态
        cached_container = UserContainerCache.get(user_id, challenge_uuid)
        if cached_container:
            from datetime import datetime
            from container.models import UserContainer
            import json
            import uuid
            
            # 验证缓存中的容器是否真实存在且状态正常
            try:
                db_container = UserContainer.objects.get(
                    user_id=user_id,
                    challenge_uuid=challenge_uuid,
                    status='RUNNING'  # 只接受运行中的容器
                )
                
                # 检查是否过期
                if db_container.expires_at > timezone.now():
                    logger.info(f"[Task {task_id}] 用户已有运行中的容器，直接返回")
                    
                    expires_at = datetime.fromisoformat(cached_container['expires_at'])
                    ports = json.loads(cached_container['port'])
                    container_urls = []
                    
                    #  使用缓存中的 url_prefix 确保一致性
                    random_prefix = cached_container.get('url_prefix') or (uuid.uuid4().hex[:8] if cached_container['domain'] else None)
                    
                    for port in ports.values():
                        if cached_container['domain']:
                            url = f"http://{random_prefix}.{cached_container['domain']}:{port}"
                        else:
                            url = f"http://{cached_container['ip_address']}:{port}"
                        container_urls.append(url)
                    
                    result_data = {
                        "status": "existing",
                        "container_urls": container_urls,
                        "expires_at": cached_container['expires_at']
                    }
                    
                    task_info.update({
                        'status': TaskStatus.SUCCESS,
                        'progress': 100,
                        'message': '容器已存在',
                        'data': result_data
                    })
                    cache.set(cache_key, task_info, timeout=300)
                    return task_info
                else:
                    # 容器已过期，清理缓存
                    logger.info(f"[Task {task_id}] 缓存的容器已过期，清理缓存")
                    UserContainerCache.delete(user_id, challenge_uuid)
                    
            except UserContainer.DoesNotExist:
                # 缓存存在但数据库中没有对应的运行中容器，清理无效缓存
                logger.warning(f"[Task {task_id}] 发现无效缓存（数据库中无对应容器），清理缓存")
                UserContainerCache.delete(user_id, challenge_uuid)
        
        # 3. 更新进度：准备创建容器
        task_info.update({
            'progress': 40,
            'message': '正在分配服务器资源...'
        })
        cache.set(cache_key, task_info, timeout=300)
        
        # 4. 创建模拟的request对象（用于ContainerManager）
        class MockRequest:
            def __init__(self, meta):
                self.META = meta or {}
                self.user = user
        
        mock_request = MockRequest(request_meta)
        
        # 5. 调用容器创建逻辑
        task_info.update({
            'progress': 60,
            'message': '正在创建容器...'
        })
        cache.set(cache_key, task_info, timeout=300)
        
        # 🆕 提取目标节点（如果有）
        if target_node:
            logger.debug(f"[Task {task_id}] 使用预选节点: {target_node}")
        
        container_manager = ContainerManager(
            user=user,
            challenge_uuid=challenge_uuid,
            request=mock_request,
            target_node=target_node  # 传递目标节点
        )
        
        # 执行容器创建
        result = container_manager.create_container()
        
        # 6. 创建成功
        #print(result)
        task_info.update({
            'status': TaskStatus.SUCCESS,
            'progress': 100,
            'message': '容器创建成功',
            'data': result,
            'completed_at': timezone.now().isoformat()
        })
        cache.set(cache_key, task_info, timeout=300)
        
        logger.info(
            f"[Task {task_id}] 容器创建成功: "
            f"user={user.username}, challenge={challenge_uuid}, "
            f"urls={result.get('container_urls')}"
        )
        
        return task_info
        
    except Exception as e:
        error_msg = str(e)
        error_type = type(e).__name__
        
        logger.error(
            f"[Task {task_id}] 容器创建失败: "
            f"错误类型={error_type}, 错误信息={error_msg}, "
            f"user={user_id}, challenge={challenge_uuid}",
            exc_info=True  
        )
        
        logger.info(f"[Task {task_id}] 重试机制已禁用，直接标记为失败")
        
        # 清理所有缓存
        logger.info(f"[Task {task_id}] 容器创建最终失败，清理所有缓存")
        UserContainerCache.delete(user_id, challenge_uuid)
        
        pending_task_key = f"container_task_user:{user_id}:{challenge_uuid}"
        cache.delete(pending_task_key)
        
        task_info.update({
            'status': TaskStatus.FAILED,
            'progress': 0,
            'error': error_msg,
            'completed_at': timezone.now().isoformat()
        })
        cache.set(cache_key, task_info, timeout=300)
        
        return task_info
    
    finally:
        # 释放全局并发槽位（关键！防止计数器泄漏）
        try:
            redis_client = cache.client.get_client()
            current = redis_client.decr('active_container_creates')
            logger.info(f"[Task {task_id}] 释放并发槽位: 当前并发={max(0, current)}")
        except Exception as e:
            logger.error(f"[Task {task_id}] 释放并发槽位失败: {str(e)}")
        
        # 确保清理容器创建锁（无论成功、失败、取消还是异常）
        try:
            if cache.get(container_lock_key):
                cache.delete(container_lock_key)
                logger.debug(f"[Task {task_id}] 清除容器创建锁: {container_lock_key}")
        except Exception as e:
            logger.error(f"[Task {task_id}] 清理容器锁失败: {str(e)}")
        
        #  释放K8s节点预占（关键！防止节点资源假性占用）
        try:
            if target_node and memory_requests and cpu_requests:
                # 获取K8s服务实例以释放节点预占
                from container.models import DockerEngine
                from container.k8s_service import K8sService
                
                k8s_engines = DockerEngine.objects.filter(
                    is_active=True,
                    engine_type='KUBERNETES',
                    health_status__in=['HEALTHY', 'WARNING', 'UNKNOWN']
                )
                
                if k8s_engines.exists():
                    k8s_service = K8sService(k8s_engines.first())
                    k8s_service.resource_monitor.release_node_reservation(
                        target_node, memory_requests, cpu_requests
                    )
                    logger.debug(
                        f"[Task {task_id}] 已释放节点预占: {target_node} "
                        f"({memory_requests}MB/{cpu_requests}核)"
                    )
        except Exception as e:
            logger.error(f"[Task {task_id}] 释放节点预占失败: {str(e)}")
        
        try:
            if reserve_key:
                ResourceReservationManager.release(reserve_key)
                logger.debug(f"[Task {task_id}] 已归还 Docker 资源令牌: {reserve_key}")
        except Exception as e:
            logger.error(f"[Task {task_id}] 归还令牌失败: {str(e)}")
        
        # 🔧 确保清理数据库连接（防止连接泄漏）
        try:
            from django.db import close_old_connections
            close_old_connections()
            logger.debug(f"[Task {task_id}] 已清理数据库连接")
        except Exception as e:
            logger.error(f"[Task {task_id}] 清理数据库连接失败: {str(e)}")


@shared_task(bind=True, max_retries=0)
def destroy_container_async(self, user_id, challenge_uuid):
    """
    异步销毁容器任务（练习模式）
    
    Args:
        self: Celery task实例
        user_id: 用户ID
        challenge_uuid: 题目UUID（标准UUID格式）
        
    Returns:
        dict: 任务结果
        {
            'status': 'success' | 'failed',
            'message': 消息,
            'error': 错误信息（如果有）,
            'task_id': 任务ID
        }
    """
    from django.db import close_old_connections
    from container.models import UserContainer
    
    task_id = self.request.id
    cache_key = f"destroy_task:{task_id}"
    
    # 确保数据库连接是新鲜的
    close_old_connections()
    
    # 初始化任务状态
    task_info = {
        'status': TaskStatus.PROCESSING,
        'progress': 10,
        'message': '正在查找容器...',
        'task_id': task_id,
        'started_at': timezone.now().isoformat()
    }
    cache.set(cache_key, task_info, timeout=300)
    
    def _cleanup_all_caches(user_id, challenge_uuid):
        """统一的缓存清理函数"""
        try:
            # 1. 清理容器缓存（自动清理 flag）
            UserContainerCache.delete(user_id, challenge_uuid)
            
            # 2. 清理任务相关缓存
            pending_task_key = f"container_task_user:{user_id}:{challenge_uuid}"
            old_task_id = cache.get(pending_task_key)
            if old_task_id:
                cache.delete(f"container_task:{old_task_id}")
                logger.debug(f"清理任务缓存: container_task:{old_task_id}")
            cache.delete(pending_task_key)
            
            # 3. 清理容器创建锁
            container_lock_key = f"container_lock:{user_id}:{challenge_uuid}"
            if cache.get(container_lock_key):
                cache.delete(container_lock_key)
                logger.debug(f"清理容器创建锁: {container_lock_key}")
            
        except Exception as e:
            logger.error(f"清理缓存失败: {str(e)}")
    
    try:
        # 1. 验证题目
        try:
            challenge = PC_Challenge.objects.get(uuid=challenge_uuid)
        except PC_Challenge.DoesNotExist:
            error_msg = '找不到指定的题目'
            task_info.update({
                'status': TaskStatus.FAILED,
                'progress': 0,
                'error': error_msg
            })
            cache.set(cache_key, task_info, timeout=300)
            return task_info
        
        # 2. 更新进度：查询容器
        task_info.update({
            'progress': 30,
            'message': '正在停止容器...'
        })
        cache.set(cache_key, task_info, timeout=300)
        
        # 只查询练习中运行的容器
        user_containers = UserContainer.objects.filter(
            user_id=user_id, 
            container_type='PRACTICE',
            status='RUNNING'
        )
        
        if not user_containers.exists():
            # 容器不存在，清理可能残留的缓存
            _cleanup_all_caches(user_id, challenge_uuid)
            task_info.update({
                'status': TaskStatus.SUCCESS,
                'progress': 100,
                'message': '容器已被摧毁'
            })
            cache.set(cache_key, task_info, timeout=300)
            return task_info
        
        # 3. 更新进度：开始销毁
        task_info.update({
            'progress': 50,
            'message': f'正在销毁 {user_containers.count()} 个容器...'
        })
        cache.set(cache_key, task_info, timeout=300)
        
        # 使用容器服务工厂（支持 Docker 和 K8s）
        from container.container_service_factory import ContainerServiceFactory
        
        docker_services = {}
        destroyed_count = 0
        
        for user_container in user_containers:
            try:
                docker_engine = user_container.docker_engine
                if docker_engine.id not in docker_services:
                    docker_services[docker_engine.id] = ContainerServiceFactory.create_service(docker_engine)
                
                docker_service = docker_services[docker_engine.id]
                
                # 停止并移除容器
                docker_service.stop_and_remove_container(user_container.container_id)
                
                # 软删除：标记为已删除
                user_container.mark_deleted(deleted_by='USER')
                
                destroyed_count += 1
                logger.info(f"[Task {task_id}] 容器销毁成功: {user_container.container_id[:12]}")
                
            except Exception as e:
                logger.error(f"[Task {task_id}] 销毁容器失败: {user_container.container_id}, 错误: {str(e)}")
                # 继续处理其他容器
        
        # 4. 清理所有相关缓存
        _cleanup_all_caches(user_id, challenge_uuid)
        
        # 5. 完成
        task_info.update({
            'status': TaskStatus.SUCCESS,
            'progress': 100,
            'message': f'成功销毁 {destroyed_count} 个容器',
            'completed_at': timezone.now().isoformat()
        })
        cache.set(cache_key, task_info, timeout=300)
        
        logger.info(f"[Task {task_id}] 异步销毁容器完成: user={user_id}, challenge={challenge_uuid}, count={destroyed_count}")
        return task_info
        
    except Exception as e:
        error_msg = f"销毁容器时发生错误: {str(e)}"
        logger.error(f"[Task {task_id}] {error_msg}", exc_info=True)
        
        # 即使失败也要清理缓存
        _cleanup_all_caches(user_id, challenge_uuid)
        
        task_info.update({
            'status': TaskStatus.FAILED,
            'progress': 0,
            'error': error_msg,
            'completed_at': timezone.now().isoformat()
        })
        cache.set(cache_key, task_info, timeout=300)
        return task_info
    
    finally:
        # 确保无论如何都清理缓存（兜底保护）
        _cleanup_all_caches(user_id, challenge_uuid)
        
        # 清理数据库连接
        try:
            from django.db import close_old_connections
            close_old_connections()
            logger.debug(f"[Task {task_id}] 已清理数据库连接")
        except Exception as e:
            logger.error(f"[Task {task_id}] 清理数据库连接失败: {str(e)}")


@shared_task
def cleanup_pending_tasks():
    """
    清理超时的待处理任务
    
    定期任务，每5分钟执行一次，清理超过10分钟还在pending状态的任务
    """
    from celery import current_app
    
    # 获取所有活跃的任务
    inspect = current_app.control.inspect()
    
    # 获取活跃任务
    active_tasks = inspect.active()
    reserved_tasks = inspect.reserved()
    
    cleaned_count = 0
    
    # 检查超时任务
    timeout_threshold = timezone.now() - timezone.timedelta(minutes=10)
    
    if active_tasks:
        for worker, tasks in active_tasks.items():
            for task in tasks:
                if task.get('name') == 'practice.tasks.create_container_async':
                    task_id = task.get('id')
                    cache_key = f"container_task:{task_id}"
                    task_info = cache.get(cache_key)
                    
                    if task_info:
                        started_at = timezone.datetime.fromisoformat(task_info.get('started_at'))
                        if started_at < timeout_threshold:
                            # 标记为超时
                            task_info['status'] = TaskStatus.TIMEOUT
                            task_info['error'] = '任务执行超时'
                            cache.set(cache_key, task_info, timeout=300)
                            cleaned_count += 1
                            logger.warning(f"任务超时: {task_id}")
    
    logger.info(f"清理超时任务完成，共清理 {cleaned_count} 个任务")
    return {'cleaned': cleaned_count}


@shared_task
def batch_cleanup_expired_containers():
    """
    批量清理过期容器
    
    这是一个定期任务，由celery beat调度执行
    """
    from container.models import UserContainer
    
    now = timezone.now()
    expired_containers = UserContainer.objects.filter(
        container_type='PRACTICE',  # 只清理练习容器
        status='RUNNING',
        expires_at__lte=now
    )
    
    total = expired_containers.count()
    success = 0
    failed = 0
    
    logger.info(f"开始批量清理过期的练习容器，共 {total} 个")
    
    for container in expired_containers:
        try:
            # 调用单个容器清理任务
            from easytask.tasks import cleanup_container
            cleanup_container.delay(
                container.container_id,
                container.user.id,
                container.docker_engine.id
            )
            success += 1
        except Exception as e:
            logger.error(f"调度容器清理任务失败: {container.container_id}, 错误: {str(e)}")
            failed += 1
    
    result = {
        'total': total,
        'success': success,
        'failed': failed
    }
    
    logger.info(f"批量清理练习容器任务调度完成: {result}")
    return result


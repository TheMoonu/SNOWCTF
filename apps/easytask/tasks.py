# -*- coding: utf-8 -*-
import time
from django.utils import timezone
from django.utils.html import strip_tags
from celery import shared_task
from django.core.management import call_command
from django.core.cache import cache
from datetime import timedelta
from easytask.utils import TaskResponse
from .actions import (
    action_update_article_cache,
    action_check_friend_links,
    action_clear_notification,
    action_cleanup_task_result,
    action_baidu_push,
    action_publish_article_by_task,
    action_write_or_update_view,
    action_get_feed_data
)
from celery import current_app
from django.db.models import Count
from blog.templatetags.blog_tags import get_blog_infos

from container.models import UserContainer, DockerEngine
from practice.redis_cache import UserContainerCache as PracticeCache  # Practice 模块的新缓存
import docker
from docker.errors import NotFound
import logging
from comment.models import SystemNotification

# 导入需要的模块
from practice.models import PC_Challenge
from container.docker_service import DockerService
from django.db import connection
from public.utils import site_full_url
logger = logging.getLogger('apps.easytask')


@shared_task
def flag_destroy_web_container(user_id, challenge_uuid):
    """
    异步销毁用户容器的任务
    
    Args:
        user_id: 用户ID (int)
        challenge_uuid: 挑战UUID (str)
    
    Returns:
        dict: 任务响应数据
    """
    response = TaskResponse()
    docker_clients = {}
    
    try:
        # 查询挑战
        try:
            challenge = PC_Challenge.objects.get(uuid=challenge_uuid)
        except PC_Challenge.DoesNotExist:
            response.error = f"题目 {challenge_uuid} 不存在"
            return response.as_dict()
            
        # 以原生SQL查询用户容器，避免ORM问题
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT id, docker_engine_id, container_id 
                FROM container_usercontainer 
                WHERE user_id = %s
            """, [user_id])
            containers = cursor.fetchall()
            
        if not containers:
            # 如果没有容器，只清理缓存（容器缓存会自动清理 flag）
            PracticeCache.delete(user_id, challenge_uuid)
            response.data['message'] = "没有找到需要清理的容器"
            return response.as_dict()
            
        # 获取需要的Docker引擎
        engine_ids = set(row[1] for row in containers)
        engines_dict = {}
        for engine in DockerEngine.objects.filter(id__in=engine_ids):
            engines_dict[engine.id] = engine
            
        # 清理每个容器
        cleaned_containers = []
        
        for container_record in containers:
            container_id = container_record[0]  # 数据库ID
            engine_id = container_record[1]  # 引擎ID
            docker_container_id = container_record[2]  # Docker容器ID
            
            # 跳过缺少引擎的情况
            if engine_id not in engines_dict:
                continue
                
            # 获取引擎
            engine = engines_dict[engine_id]
            
            try:
                # 为每个引擎创建一个Docker客户端
                if engine_id not in docker_clients:
                    if engine.host_type == 'LOCAL':
                        docker_url = "unix:///var/run/docker.sock"
                    else:
                        docker_url = f"tcp://{engine.host}:{engine.port}"
                        
                    tls_config = None
                    if engine.tls_enabled:
                        tls_config = engine.get_tls_config()
                        
                    docker_clients[engine_id] = docker.DockerClient(
                        base_url=docker_url, 
                        tls=tls_config,
                        timeout=30
                    )
                
                # 获取Docker客户端
                client = docker_clients[engine_id]
                
                # 查找并收集网络信息
                try:
                    container = client.containers.get(docker_container_id)
                    networks = container.attrs.get('NetworkSettings', {}).get('Networks', {})
                    
                    # 停止容器前先断开所有网络连接
                    for network_name in networks:
                        try:
                            network = client.networks.get(network_name)
                            network.disconnect(docker_container_id, force=True)
                            logger.info(f"已断开容器 {docker_container_id} 与网络 {network_name} 的连接")
                        except Exception as e:
                            logger.warning(f"断开网络连接失败: {str(e)}")
                    
                    # 停止并移除容器
                    container.stop(timeout=5)
                    container.remove(force=True)
                    
                    # 清理网络
                    for network_name in networks:
                        try:
                            network = client.networks.get(network_name)
                            network.remove()
                            logger.info(f"已移除网络 {network_name}")
                        except Exception as e:
                            if "has active endpoints" in str(e):
                                logger.warning(f"网络 {network_name} 仍有活动端点，无法删除")
                            else:
                                logger.warning(f"移除网络失败: {str(e)}")
                    
                    # 撤销清理任务
                    task_id = cache.get(f"cleanup_task_{docker_container_id}")
                    if task_id:
                        current_app.control.revoke(task_id, terminate=True)
                        cache.delete(f"cleanup_task_{docker_container_id}")
                    
                    cleaned_containers.append(docker_container_id)
                    
                except docker.errors.NotFound:
                    logger.info(f"容器 {docker_container_id} 不存在，可能已被删除")
                except Exception as e:
                    logger.error(f"操作容器 {docker_container_id} 时发生错误: {str(e)}")
                
                # 不论Docker操作是否成功，都删除数据库记录
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM container_usercontainer WHERE id = %s", [container_id])
                    
            except Exception as e:
                logger.error(f"处理容器记录 {container_id} 时发生错误: {str(e)}")
        
        # 清理缓存（容器缓存会自动清理 flag）
        PracticeCache.delete(user_id, challenge_uuid)
        
        if cleaned_containers:
            response.data['cleaned_containers'] = cleaned_containers
            response.data['message'] = f"成功清理了 {len(cleaned_containers)} 个容器"
            logger.info(f"用户ID {user_id} 成功解决题目 {challenge.title} 后容器已异步销毁")
        else:
            response.data['message'] = "没有容器被清理"
            
    except Exception as e:
        error_msg = f"异步销毁容器时发生错误: {str(e)}"
        logger.error(error_msg)
        response.error = error_msg
        
    finally:
        # 确保关闭所有Docker客户端
        for client in docker_clients.values():
            try:
                client.close()
            except:
                pass
    
    return response.as_dict()


@shared_task
def cleanup_expired_containers_bucket(bucket_time_iso):
    """
    [分桶批量清理] 清理指定时间桶内的所有过期容器
    
    **性能优化核心**：
    - 按时间分桶（每5分钟一个桶）
    - 同一桶内的容器批量清理
    - 大幅减少 Celery 任务数量
    
    **示例**：
    - 14:01 创建的容器 → 归入 14:05 桶
    - 14:03 创建的容器 → 归入 14:05 桶
    - 14:06 创建的容器 → 归入 14:10 桶
    - 一个桶只需要一个 Celery 任务
    
    **性能对比**：
    - 100个容器同时创建 → ETA方案需要100个任务，分桶方案只需1个任务
    - 一天创建1000个容器 → ETA方案1000个任务，分桶方案最多288个任务（24*12）
    
    Args:
        bucket_time_iso: 时间桶的ISO格式时间戳（如 "2025-12-30T14:05:00+08:00"）
    """
    from django.utils import timezone
    from datetime import datetime, timedelta
    from container.models import UserContainer
    from django.core.cache import cache
    from docker.errors import NotFound, APIError
    
    try:
        # 解析时间桶
        bucket_time = datetime.fromisoformat(bucket_time_iso)
        if timezone.is_naive(bucket_time):
            bucket_time = timezone.make_aware(bucket_time)
        
        now = timezone.now()
        
        #  修复：兜底清理前10分钟内的所有过期容器（避免任务延迟导致遗漏）
        # 查询范围：bucket_time - 10分钟 <= expires_at <= now
        # 这样可以：
        # 1. 清理当前桶的容器
        # 2. 兜底清理上一个桶遗漏的容器（如果有）
        # 3. 避免扫描过远的历史容器
        bucket_start = bucket_time - timedelta(minutes=10)
        
        expired_containers = UserContainer.objects.filter(
            expires_at__gte=bucket_start,  # 最近10分钟内过期的
            expires_at__lte=now,  # 到当前时间为止
            status='RUNNING'
        ).select_related('docker_engine')
        
        # ⚠️ 记录任务延迟情况
        if now > bucket_time:
            delay_seconds = (now - bucket_time).total_seconds()
            if delay_seconds > 60:  # 延迟超过1分钟才警告
                logger.warning(
                    f"⚠️ 分桶任务延迟执行: bucket={bucket_time.strftime('%H:%M:%S')}, "
                    f"当前时间={now.strftime('%H:%M:%S')}, 延迟={delay_seconds:.1f}秒"
                )
        
        total_count = expired_containers.count()
        
        if total_count == 0:
            logger.info(
                f" 时间桶无需清理: bucket={bucket_time.strftime('%H:%M')}, count=0"
            )
            return {
                "status": "success",
                "bucket_time": bucket_time_iso,
                "total": 0,
                "message": "时间桶内无容器需要清理"
            }
        
        #  统计引擎类型分布（Docker vs K8s）
        engine_stats = {}
        for container in expired_containers:
            engine_type = container.docker_engine.engine_type
            engine_name = container.docker_engine.name
            key = f"{engine_type}:{engine_name}"
            engine_stats[key] = engine_stats.get(key, 0) + 1
        
        logger.info(
            f" 开始清理时间桶: bucket={bucket_time.strftime('%Y-%m-%d %H:%M')}, "
            f"查询范围=[{bucket_start.strftime('%H:%M')} - {now.strftime('%H:%M')}], "
            f"总容器数={total_count}, 引擎分布={dict(engine_stats)}"
        )
        
        success_count = 0
        failed_count = 0
        failed_containers = []
        container_services = {}  
        
        #  批量清理容器（支持同时处理 Docker 和 K8s 容器）
        for user_container in expired_containers:
            container_id = user_container.container_id
            docker_engine = user_container.docker_engine
            
            try:
                #  获取或创建容器服务实例（自动识别 Docker/K8s）
                if docker_engine.id not in container_services:
                    from container.container_service_factory import ContainerServiceFactory
                    # 工厂会根据 engine.engine_type 自动创建对应服务
                    container_services[docker_engine.id] = ContainerServiceFactory.create_service(docker_engine)
                
                container_service = container_services[docker_engine.id]
                
                #  停止并删除容器（统一接口，支持 Docker 和 K8s）
                try:
                    container_service.stop_and_remove_container(container_id)
                    logger.debug(
                        f"容器清理成功: {container_id[:12]}, "
                        f"引擎={docker_engine.engine_type}/{docker_engine.name}"
                    )
                except NotFound:
                    logger.debug(
                        f"容器不存在: {container_id[:12]}, "
                        f"引擎={docker_engine.engine_type}"
                    )
                except APIError as e:
                    logger.error(f"容器清理 API 错误: {container_id[:12]}, {str(e)}")
                    raise
                
                # 软删除数据库记录
                user_container.mark_expired()
                
                # 清理所有相关缓存
                user_id = user_container.user_id
                challenge_uuid = str(user_container.challenge_uuid)
                
                # 清理容器信息缓存
                if user_container.container_type == 'COMPETITION':
                    from competition.redis_cache import UserContainerCache as CompetitionCache
                    CompetitionCache.delete(user_id, challenge_uuid)
                else:
                    from practice.redis_cache import UserContainerCache as PracticeCache
                    PracticeCache.delete(user_id, challenge_uuid)
                
                # 清理其他缓存
                cache.delete(f"container_lock:{user_id}:{challenge_uuid}")
                cache.delete(f"rate_limit:{user_id}:{challenge_uuid}")
                pending_task_key = f"container_task_user:{user_id}:{challenge_uuid}"
                old_task_id = cache.get(pending_task_key)
                if old_task_id:
                    cache.delete(f"container_task:{old_task_id}")
                cache.delete(pending_task_key)
                
                success_count += 1
                
            except Exception as e:
                failed_count += 1
                failed_containers.append({
                    'container_id': container_id[:12],
                    'user': user_container.user.username,
                    'engine_type': docker_engine.engine_type,  #
                    'engine_name': docker_engine.name,
                    'error': str(e)
                })
                logger.error(
                    f"清理容器失败: {container_id[:12]}, "
                    f"引擎={docker_engine.engine_type}/{docker_engine.name}, "
                    f"错误={str(e)}"
                )
        
    
        logger.debug(f"清理容器服务连接: {len(container_services)} 个引擎")
        for engine_id, service in container_services.items():
            try:
                if hasattr(service, 'close'):
                    service_type = 'K8s' if hasattr(service, 'core_api') else 'Docker'
                    service.close()
                    logger.debug(f"已关闭 {service_type} 服务连接（引擎ID: {engine_id}）")
            except Exception as e:
                logger.warning(f"关闭容器服务失败（引擎ID: {engine_id}）: {str(e)}")
        
        result = {
            "status": "completed",
            "bucket_time": bucket_time_iso,
            "total": total_count,
            "success": success_count,
            "failed": failed_count,
            "failed_containers": failed_containers,
            "engine_stats": engine_stats,  #  包含引擎类型分布统计
            "message": f"时间桶清理完成：成功 {success_count}/{total_count}"
        }
        
        logger.info(
            f" 时间桶清理完成: bucket={bucket_time.strftime('%H:%M')}, "
            f"总数={total_count}, 成功={success_count}, 失败={failed_count}, "
            f"引擎分布={dict(engine_stats)}"
        )
        
        return result
        
    except Exception as e:
        error_msg = f"时间桶清理任务失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {"status": "error", "message": error_msg, "bucket_time": bucket_time_iso}


@shared_task
def cleanup_expired_containers():
    """
    [兜底清理任务] 定期清理过期容器和幽灵容器（低频率兜底机制）
    
    **角色定位**：兜底保障，非主要清理机制
    - 主要清理：容器创建时通过 ETA 调度的精确清理任务
    - 兜底清理：本任务用于清理调度失败或异常情况下的容器
    
    **执行频率**：建议每10-20分钟执行一次
    - 过期容器清理：兜底机制，大部分已被 ETA 任务清理
    - 幽灵容器清理：检测引擎中不存在但数据库标记为RUNNING的容器
    
    **优化效果**：
    - 统一清理逻辑，避免重复代码
    - 同时处理过期容器和幽灵容器
    - 降低系统资源消耗
    - 保留兜底保障
    
    **工作原理**：
    1. 清理 expires_at <= now 的运行中容器（已过期）
    2. 检测幽灵容器：数据库RUNNING但引擎中不存在的容器
    3. 记录统计信息
    """
    from django.utils import timezone
    from datetime import timedelta
    from container.models import UserContainer, DockerEngine
    from django.core.cache import cache
    from docker.errors import NotFound, APIError
    
    try:
        # 快速检查：如果没有任何运行中的容器，直接返回
        running_count = UserContainer.objects.filter(status='RUNNING').count()
        if running_count == 0:
            return {"status": "success", "message": "无运行中容器，跳过清理"}
        
        # 优化：只清理真正过期的容器（不使用预过期机制）
        # 主要清理已由 ETA 任务完成，此处仅兜底
        now = timezone.now()
        
        expired_containers = UserContainer.objects.filter(
            expires_at__lte=now,  # 只清理已过期的容器
            status='RUNNING'  # 只处理运行中的容器
        ).select_related('docker_engine')
        
        total_count = expired_containers.count()
       
        if total_count == 0:
            return {"status": "success", "message": f"无需兜底清理（运行中: {running_count}，ETA 任务已处理）"}
        
        #  如果清理数量较多，记录警告（说明 ETA 调度可能有问题）
        if total_count > 10:
            logger.warning(
                f"⚠️ 兜底清理发现较多过期容器: {total_count}个，"
                f"可能 ETA 任务调度失败，请检查 Celery 配置"
            )
        
        # ==================== 🆕 识别拓扑容器并分组 ====================
        topology_groups = {}  # topology_config_id -> [user_containers]
        single_containers = []  # 非拓扑容器列表
        
        for user_container in expired_containers:
            if user_container.topology_config_id:
                # 拓扑容器：按 topology_config_id 分组
                topology_id = user_container.topology_config_id
                if topology_id not in topology_groups:
                    topology_groups[topology_id] = []
                topology_groups[topology_id].append(user_container)
            else:
                # 单容器
                single_containers.append(user_container)
        
        logger.info(
            f"容器分类完成: 拓扑场景={len(topology_groups)}个（共{sum(len(v) for v in topology_groups.values())}个容器）, "
            f"单容器={len(single_containers)}个"
        )
        
        success_count = 0
        failed_count = 0
        failed_containers = []
        container_services = {}  # 缓存容器服务实例（Docker/K8s）
        topology_cleaned = 0  # 已清理的拓扑场景数
        
        # ==================== 🆕 批量清理拓扑场景 ====================
        for topology_id, containers in topology_groups.items():
            try:
                # 取第一个容器的引擎（同一拓扑的所有容器应该在同一引擎）
                first_container = containers[0]
                docker_engine = first_container.docker_engine
                
                if not docker_engine or docker_engine.engine_type != 'K8S':
                    logger.warning(
                        f"拓扑 {topology_id} 的引擎不是 K8s，跳过批量清理，改用单容器清理"
                    )
                    single_containers.extend(containers)
                    continue
                
                # 获取或创建 K8s 服务实例
                if docker_engine.id not in container_services:
                    from container.container_service_factory import ContainerServiceFactory
                    container_services[docker_engine.id] = ContainerServiceFactory.create_service(docker_engine)
                
                container_service = container_services[docker_engine.id]
                
                # 调用 K8s 批量清理方法
                logger.info(f"开始批量清理拓扑场景: topology_id={topology_id}, 容器数={len(containers)}")
                container_service._cleanup_topology_containers(topology_id)
                
                # 批量软删除所有相关容器记录
                for user_container in containers:
                    user_container.mark_expired()
                    
                    # 清理所有相关缓存
                    user_id = user_container.user_id
                    challenge_uuid = str(user_container.challenge_uuid)
                    
                    # 清理容器信息缓存
                    if user_container.container_type == 'COMPETITION':
                        from competition.redis_cache import UserContainerCache as CompetitionCache
                        CompetitionCache.delete(user_id, challenge_uuid)
                    else:
                        PracticeCache.delete(user_id, challenge_uuid)
                    
                    # 清理其他缓存
                    cache.delete(f"container_lock:{user_id}:{challenge_uuid}")
                    cache.delete(f"rate_limit:{user_id}:{challenge_uuid}")
                    
                    pending_task_key = f"container_task_user:{user_id}:{challenge_uuid}"
                    old_task_id = cache.get(pending_task_key)
                    if old_task_id:
                        cache.delete(f"container_task:{old_task_id}")
                    cache.delete(pending_task_key)
                    cache.delete(f"cleanup_task_{user_container.container_id}")
                    
                    success_count += 1
                
                topology_cleaned += 1
                logger.info(
                    f"✅ 拓扑场景清理成功: topology_id={topology_id}, "
                    f"容器数={len(containers)}, "
                    f"用户={first_container.user.username if first_container.user else 'N/A'}"
                )
                
            except Exception as e:
                failed_count += len(containers)
                for container in containers:
                    failed_containers.append({
                        'container_id': container.container_id[:12],
                        'user': container.user.username if container.user else 'N/A',
                        'engine_type': docker_engine.engine_type if docker_engine else 'N/A',
                        'topology_id': topology_id,
                        'error': str(e)
                    })
                logger.error(
                    f"清理拓扑场景失败: topology_id={topology_id}, "
                    f"容器数={len(containers)}, 错误={str(e)}",
                    exc_info=True
                )
        
        # ==================== 批量清理单容器 ====================
        for user_container in single_containers:
            container_id = user_container.container_id
            docker_engine = user_container.docker_engine
            
            try:
                # 获取或创建容器服务实例（支持 Docker 和 K8s）
                if docker_engine.id not in container_services:
                    from container.container_service_factory import ContainerServiceFactory
                    container_services[docker_engine.id] = ContainerServiceFactory.create_service(docker_engine)
                
                container_service = container_services[docker_engine.id]
                
                # 1. 停止并删除容器（统一接口，支持 Docker 和 K8s）
                try:
                    container_service.stop_and_remove_container(container_id)
                    logger.info(f"容器清理成功: {container_id[:12]} ({docker_engine.engine_type})")
                except NotFound:
                    logger.info(f"容器不存在（可能已被删除）: {container_id[:12]}")
                except APIError as e:
                    logger.error(f"容器清理 API 错误: {container_id[:12]}, {str(e)}")
                    raise
                except Exception as e:
                    logger.error(f"容器清理失败: {container_id[:12]}, {str(e)}")
                    raise
                
                # 2. 软删除数据库记录
                user_container.mark_expired()
                
                # 3. 清理所有相关缓存
                user_id = user_container.user_id
                challenge_uuid = str(user_container.challenge_uuid)
                
                # 3.1 清理容器信息缓存（包含 flag）
                if user_container.container_type == 'COMPETITION':
                    # 比赛容器：使用比赛模块的缓存（整合了 flag）
                    from competition.redis_cache import UserContainerCache as CompetitionCache
                    CompetitionCache.delete(user_id, challenge_uuid)
                else:
                    # 练习容器：使用练习模块的缓存（整合了 flag）
                    PracticeCache.delete(user_id, challenge_uuid)
                
                # 3.2 清理容器创建锁缓存
                container_lock_key = f"container_lock:{user_id}:{challenge_uuid}"
                cache.delete(container_lock_key)
                
                # 3.3 清理速率限制缓存（防止用户频繁创建）
                rate_limit_key = f"rate_limit:{user_id}:{challenge_uuid}"
                cache.delete(rate_limit_key)
                
                # 3.4 清理待处理任务标记
                pending_task_key = f"container_task_user:{user_id}:{challenge_uuid}"
                old_task_id = cache.get(pending_task_key)
                if old_task_id:
                    # 清理任务状态缓存
                    cache.delete(f"container_task:{old_task_id}")
                cache.delete(pending_task_key)
                
                # 3.5 清理容器清理任务标记
                cache.delete(f"cleanup_task_{container_id}")
                
                logger.debug(
                    f"清理缓存完成: user={user_id}, challenge={challenge_uuid}, "
                    f"lock={container_lock_key}, rate_limit={rate_limit_key}"
                )
                
                success_count += 1

            except Exception as e:
                failed_count += 1
                failed_containers.append({
                    'container_id': container_id[:12],
                    'user': user_container.user.username,
                    'engine_type': docker_engine.engine_type,
                    'error': str(e)
                })
                logger.error(
                    f"清理容器失败: {container_id[:12]}, "
                    f"引擎={docker_engine.engine_type}, "
                    f"用户={user_container.user.username}, "
                    f"错误={str(e)}"
                )
        
        # ==================== 🆕 幽灵容器检测与清理 ====================
        logger.info("🔍 开始检测幽灵容器...")
        
        # 获取所有运行中且未过期的容器（可能存在幽灵容器）
        ghost_candidates = UserContainer.objects.filter(
            status='RUNNING',
            expires_at__gt=now
        ).select_related('docker_engine')
        
        ghost_checked = 0
        ghost_cleaned = 0
        ghost_errors = 0
        
        for container in ghost_candidates:
            ghost_checked += 1
            
            try:
                docker_engine = container.docker_engine
                if not docker_engine:
                    # 没有引擎信息，标记为幽灵容器
                    container.mark_deleted(deleted_by='SYSTEM_AUTO_CLEANUP')
                    ghost_cleaned += 1
                    logger.info(f"✓ 清理幽灵容器（无引擎）: {container.container_id[:12]}")
                    continue
                
                # 复用已有的容器服务实例
                if docker_engine.id not in container_services:
                    from container.container_service_factory import ContainerServiceFactory
                    container_services[docker_engine.id] = ContainerServiceFactory.create_service(docker_engine)
                
                container_service = container_services[docker_engine.id]
                
                try:
                    status = container_service.get_container_status(container.container_id)
                    
                    # 容器不存在或已停止
                    if status not in ['RUNNING', 'STARTING']:
                        # 标记为已删除
                        container.mark_deleted(deleted_by='SYSTEM_AUTO_CLEANUP')
                        
                        # 清理所有相关缓存（与过期容器清理逻辑一致）
                        user_id = container.user_id
                        challenge_uuid = str(container.challenge_uuid)
                        
                        if container.container_type == 'COMPETITION':
                            from competition.redis_cache import UserContainerCache as CompetitionCache
                            CompetitionCache.delete(user_id, challenge_uuid)
                        else:
                            PracticeCache.delete(user_id, challenge_uuid)
                        
                        cache.delete(f"container_lock:{user_id}:{challenge_uuid}")
                        cache.delete(f"rate_limit:{user_id}:{challenge_uuid}")
                        
                        pending_task_key = f"container_task_user:{user_id}:{challenge_uuid}"
                        old_task_id = cache.get(pending_task_key)
                        if old_task_id:
                            cache.delete(f"container_task:{old_task_id}")
                        cache.delete(pending_task_key)
                        cache.delete(f"cleanup_task_{container.container_id}")
                        
                        ghost_cleaned += 1
                        logger.info(
                            f"✓ 清理幽灵容器: {container.container_id[:12]}, "
                            f"数据库状态=RUNNING, 引擎状态={status}"
                        )
                except Exception as e:
                    # 无法获取状态，可能容器不存在
                    if 'not found' in str(e).lower() or 'does not exist' in str(e).lower():
                        container.mark_deleted(deleted_by='SYSTEM_AUTO_CLEANUP')
                        ghost_cleaned += 1
                        logger.info(f"✓ 清理幽灵容器（不存在）: {container.container_id[:12]}")
                    else:
                        raise
                        
            except Exception as e:
                ghost_errors += 1
                logger.warning(f"检测幽灵容器失败: {container.container_id[:12]}, 错误: {str(e)}")
        
        logger.info(
            f" 幽灵容器检测完成: 检查={ghost_checked}, 清理={ghost_cleaned}, 错误={ghost_errors}"
        )
        
        # 清理所有容器服务实例的资源（包括幽灵容器检测使用的连接）
        logger.debug(f"清理容器服务连接: {len(container_services)} 个引擎")
        for engine_id, service in container_services.items():
            try:
                if hasattr(service, 'close'):
                    service.close()
            except Exception as e:
                logger.warning(f"关闭容器服务失败（引擎ID: {engine_id}）: {str(e)}")
        
        result = {
            "status": "completed",
            "mode": "fallback",  # 兜底模式
            "cleanup_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            # 过期容器清理统计
            "expired_total": total_count,
            "expired_success": success_count,
            "expired_failed": failed_count,
            "failed_containers": failed_containers,
            # 🆕 拓扑场景清理统计
            "topology_total": len(topology_groups),
            "topology_cleaned": topology_cleaned,
            "topology_containers": sum(len(v) for v in topology_groups.values()),
            # 幽灵容器清理统计
            "ghost_checked": ghost_checked,
            "ghost_cleaned": ghost_cleaned,
            "ghost_errors": ghost_errors,
            # 总计
            "total_cleaned": success_count + ghost_cleaned,
            "message": (
                f"兜底清理完成：过期容器 {success_count}/{total_count}" +
                (f"（⚠️ 数量较多，请检查 ETA 任务）" if total_count > 10 else "") +
                f"，拓扑场景 {topology_cleaned}/{len(topology_groups)}" +
                f"，幽灵容器 {ghost_cleaned}/{ghost_checked}"
            )
        }
        
        logger.info(
            f"✅ 批量清理任务完成: "
            f"过期容器={success_count}/{total_count}, "
            f"拓扑场景={topology_cleaned}/{len(topology_groups)}（{sum(len(v) for v in topology_groups.values())}个Pod）, "
            f"幽灵容器={ghost_cleaned}/{ghost_checked}, "
            f"总计清理={success_count + ghost_cleaned}"
        )
        
        return result
        
    except Exception as e:
        error_msg = f"批量清理任务执行失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {"status": "error", "message": error_msg}


@shared_task
def cleanup_container(container_id, user_id, docker_engine_id):
    """
    清理单个容器（被批量清理任务或手动撤销调用）
    """
    response = TaskResponse()
    try:
        # 首先检查容器记录是否还存在
        # 如果用户已经手动删除，就直接返回
        from container.models import UserContainer
        
        try:
            user_container = UserContainer.objects.get(container_id=container_id)
        except UserContainer.DoesNotExist:
            response.data['status'] = 'skipped'
            response.data['message'] = f"容器记录不存在，可能已被用户手动删除: {container_id[:12]}"
            logger.info(f"跳过清理任务，容器记录不存在: {container_id[:12]}")
            return response.as_dict()
        
        # 获取 DockerEngine 实例
        docker_engine = DockerEngine.objects.get(id=docker_engine_id)
        
        # 使用DockerEngine的方法获取正确的URL
        docker_url = docker_engine.get_docker_url()
        
        # 构建 TLS 配置
        tls_config = None
        if docker_engine.tls_enabled:
            tls_config = docker_engine.get_tls_config()
        
        # 创建 Docker 客户端
        docker_client = docker.DockerClient(base_url=docker_url, tls=tls_config)
        
        try:
            container = docker_client.containers.get(container_id)
            # 获取容器的网络信息
            network_ids = [net_id for net_id in container.attrs['NetworkSettings']['Networks'].keys()]
            
            # 先处理网络连接
            for network_id in network_ids:
                try:
                    network = docker_client.networks.get(network_id)
                    # 断开网络上的所有容器连接
                    if 'Containers' in network.attrs:
                        for container_in_network in network.attrs['Containers'].keys():
                            try:
                                network.disconnect(container_in_network, force=True)
                            except Exception as e:
                                response.data['network_disconnect_errors'] = f"断开容器 {container_in_network} 的网络连接时出错: {str(e)}"
                except Exception as e:
                    response.data['network_errors'] = f"处理网络 {network_id} 时出错: {str(e)}"
            
            # 停止并删除容器
            container.stop(timeout=10)
            container.remove(force=True)
            response.data['container'] = f"容器 {container_id} 已停止并删除"
            
            # 清理相关网络
            removed_networks = []
            for network_id in network_ids:
                try:
                    network = docker_client.networks.get(network_id)
                    network.remove()
                    removed_networks.append(network_id)
                except docker.errors.NotFound:
                    continue
                except Exception as e:
                    response.data['network_errors'] = f"清理网络 {network_id} 时出错: {str(e)}"
            
            if removed_networks:
                response.data['networks'] = f"已清理网络: {', '.join(removed_networks)}"
            
        except docker.errors.NotFound:
            response.data['container'] = f"容器 {container_id} 未找到，可能已被删除"
        
        # 软删除：标记为已过期，保留记录用于审计和统计
        try:
            user_container.mark_expired()
            response.data['database'] = f"容器已标记为过期: {container_id[:12]}"
            logger.info(
                f"容器已标记为过期: container_id={container_id[:12]}, "
                f"运行时长={user_container.get_lifetime_seconds():.0f}秒"
            )
            
            # 清理相关缓存（根据容器类型选择不同的缓存模块）
            if user_container.container_type == 'COMPETITION':
                # 比赛容器：使用比赛模块的缓存（整合了 flag）
                from competition.redis_cache import UserContainerCache as CompetitionCache
                CompetitionCache.delete(user_id, user_container.challenge_uuid)
            else:
                # 练习容器：使用练习模块的缓存（整合了 flag）
                PracticeCache.delete(user_id, user_container.challenge_uuid)
            
            cache.delete(f"cleanup_task_{container_id}")
            
        except Exception as e:
            logger.error(f"标记容器过期失败: {str(e)}")
            response.data['database_error'] = str(e)
        
    except DockerEngine.DoesNotExist:
        response.error = f"Docker引擎 ID {docker_engine_id} 不存在"
    except Exception as e:
        response.error = f"清理容器 {container_id} 时发生错误: {str(e)}"
    finally:
        if 'docker_client' in locals():
            docker_client.close()
    
    return response.as_dict()


@shared_task
def simple_task(x, y):
    time.sleep(2)
    return x + y



@shared_task
def clear_all_caches():
    """
    [清理所有系统缓存]
    清理系统中的所有缓存数据，包括：
    1. 容器相关缓存（container_lock, rate_limit, container_task等）
    2. 用户容器信息缓存
    3. Django框架缓存
    4. 文章和博客缓存
    5. 其他业务缓存
    
    适用场景：
    - 系统维护
    - 缓存数据异常
    - 释放Redis内存
    - 管理员手动清理
    """
    from django.core.cache import cache
    from django.utils import timezone
    
    logger.info("=" * 60)
    logger.info("开始清理所有系统缓存")
    logger.info("=" * 60)
    
    result = {
        'status': 'success',
        'started_at': timezone.now().isoformat(),
        'cleared': {},
        'errors': []
    }
    
    try:
        # 1. 清理容器相关缓存
        logger.info("1. 清理容器相关缓存...")
        container_cache_patterns = [
            'container_lock:*',
            'rate_limit:*',
            'container_task:*',
            'container_task_user:*',
            'cleanup_task_*',
            'user_container:*',
        ]
        
        container_cleared = 0
        for pattern in container_cache_patterns:
            try:
                keys = cache.keys(pattern)
                if keys:
                    for key in keys:
                        cache.delete(key)
                    container_cleared += len(keys)
                    logger.info(f"   清理 {pattern}: {len(keys)} 个键")
            except Exception as e:
                logger.warning(f"   清理 {pattern} 失败: {e}")
        
        result['cleared']['container_cache'] = container_cleared
        logger.info(f"   容器缓存清理完成: {container_cleared} 个键")
        
        # 2. 清理用户容器信息缓存（使用专用的缓存类）
        logger.info("2. 清理用户容器信息缓存...")
        try:
            from competition.redis_cache import UserContainerCache as CompetitionCache
            from practice.redis_cache import UserContainerCache as PracticeCache
            from container.models import UserContainer
            
            # 获取所有运行中的容器
            running_containers = UserContainer.objects.filter(status='RUNNING').values_list('user_id', 'challenge_uuid')
            
            cache_cleared = 0
            for user_id, challenge_uuid in running_containers:
                try:
                    # 清理比赛容器缓存
                    CompetitionCache.delete(user_id, challenge_uuid)
                    # 清理练习容器缓存
                    PracticeCache.delete(user_id, challenge_uuid)
                    cache_cleared += 1
                except Exception as e:
                    logger.warning(f"   清理用户 {user_id} 容器缓存失败: {e}")
            
            result['cleared']['user_container_cache'] = cache_cleared
            logger.info(f"   用户容器缓存清理完成: {cache_cleared} 个")
        except Exception as e:
            logger.error(f"   用户容器缓存清理失败: {e}")
            result['errors'].append(f"用户容器缓存: {str(e)}")
        
        # 3. 清理 Celery 任务相关缓存
        logger.info("3. 清理 Celery 任务缓存...")
        try:
            celery_patterns = [
                'celery-task-meta-*',
                '_kombu.binding.*',
            ]
            celery_cleared = 0
            for pattern in celery_patterns:
                try:
                    keys = cache.keys(pattern)
                    if keys:
                        for key in keys:
                            cache.delete(key)
                        celery_cleared += len(keys)
                        logger.info(f"   清理 {pattern}: {len(keys)} 个键")
                except Exception as e:
                    logger.warning(f"   清理 {pattern} 失败: {e}")
            
            result['cleared']['celery_cache'] = celery_cleared
            logger.info(f"   Celery 缓存清理完成: {celery_cleared} 个键")
        except Exception as e:
            logger.error(f"   Celery 缓存清理失败: {e}")
            result['errors'].append(f"Celery缓存: {str(e)}")
        
        # 4. 清理文章和博客缓存
        logger.info("4. 清理文章和博客缓存...")
        try:
            article_patterns = [
                'article:*',
                'blog:*',
                'post:*',
            ]
            article_cleared = 0
            for pattern in article_patterns:
                try:
                    keys = cache.keys(pattern)
                    if keys:
                        for key in keys:
                            cache.delete(key)
                        article_cleared += len(keys)
                        logger.info(f"   清理 {pattern}: {len(keys)} 个键")
                except Exception as e:
                    logger.warning(f"   清理 {pattern} 失败: {e}")
            
            result['cleared']['article_cache'] = article_cleared
            logger.info(f"   文章缓存清理完成: {article_cleared} 个键")
        except Exception as e:
            logger.error(f"   文章缓存清理失败: {e}")
            result['errors'].append(f"文章缓存: {str(e)}")
        
        # 5. 清理会话和认证缓存
        logger.info("5. 清理会话和认证缓存...")
        try:
            session_patterns = [
                'session:*',
                'auth:*',
                'user:*',
            ]
            session_cleared = 0
            for pattern in session_patterns:
                try:
                    keys = cache.keys(pattern)
                    if keys:
                        for key in keys:
                            cache.delete(key)
                        session_cleared += len(keys)
                        logger.info(f"   清理 {pattern}: {len(keys)} 个键")
                except Exception as e:
                    logger.warning(f"   清理 {pattern} 失败: {e}")
            
            result['cleared']['session_cache'] = session_cleared
            logger.info(f"   会话缓存清理完成: {session_cleared} 个键")
        except Exception as e:
            logger.error(f"   会话缓存清理失败: {e}")
            result['errors'].append(f"会话缓存: {str(e)}")
        
        # 6. 清理其他业务缓存
        logger.info("6. 清理其他业务缓存...")
        try:
            other_patterns = [
                'flag:*',
                'score:*',
                'rank:*',
                'docker_*',
                'k8s_*',
            ]
            other_cleared = 0
            for pattern in other_patterns:
                try:
                    keys = cache.keys(pattern)
                    if keys:
                        for key in keys:
                            cache.delete(key)
                        other_cleared += len(keys)
                        logger.info(f"   清理 {pattern}: {len(keys)} 个键")
                except Exception as e:
                    logger.warning(f"   清理 {pattern} 失败: {e}")
            
            result['cleared']['other_cache'] = other_cleared
            logger.info(f"   其他缓存清理完成: {other_cleared} 个键")
        except Exception as e:
            logger.error(f"   其他缓存清理失败: {e}")
            result['errors'].append(f"其他缓存: {str(e)}")
        
        # 统计总计
        total_cleared = sum(result['cleared'].values())
        result['total_cleared'] = total_cleared
        result['completed_at'] = timezone.now().isoformat()
        
        logger.info("=" * 60)
        logger.info(f"缓存清理完成！共清理 {total_cleared} 个缓存键")
        logger.info(f"详细统计: {result['cleared']}")
        if result['errors']:
            logger.warning(f"部分清理失败: {result['errors']}")
        logger.info("=" * 60)
        
        return result
        
    except Exception as e:
        error_msg = f"清理缓存任务执行失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        result['status'] = 'error'
        result['error'] = error_msg
        result['completed_at'] = timezone.now().isoformat()
        return result




@shared_task
def clear_notification(day=200, is_read=True):
    """
    [清理过期通知信息]
    清理过期通知信息
    @param is_read:
    @param day:
    @return:
    """
    response = TaskResponse()
    result = action_clear_notification(day=day, is_read=is_read)
    response.data = result
    return response.as_dict()


@shared_task
def cleanup_task_result(day=3):
    """
    [清理任务结果]
    清理任务结果
    清理day天前成功或结束的，其他状态的一概不清理
    @param day:
    @return:
    """
    response = TaskResponse()
    result = action_cleanup_task_result(day=day)
    response.data = result
    return response.as_dict()


@shared_task
def baidu_push(baidu_url, weeks=1):
    """
    [百度推送]
    百度推送
    @param baidu_url:
    @param weeks:
    @return:
    """
    response = TaskResponse()
    result = action_baidu_push(baidu_url=baidu_url, weeks=weeks)
    response.data = result
    return response.as_dict()



@shared_task
def publish_article_by_task(article_ids):
    """
    [定时将草稿发布出去]
    定时将草稿发布出去
    @param article_ids: 需要发布的文章ID
    @return:
    """
    response = TaskResponse()
    result = action_publish_article_by_task(article_ids)
    response.data = result
    return response.as_dict()



@shared_task
def set_views_to_redis():
    """
    [定时读取每日的文章访问量]
    定时读取每日的文章访问量，写入redis，一定要设置一个23:59分执行的任务
    @return:
    """
    response = TaskResponse()
    # 先将统计数据写入模型，然后分析后写入redis
    action_write_or_update_view()
    response.data = {'msg': 'write ok'}
    return response.as_dict()


@shared_task
def set_feed_data():
    """
    [定时采集feed数据]
    定时采集feed数据，回写到数据库
    """
    response = TaskResponse()
    # 先将统计数据写入模型，然后分析后写入redis
    response.data = action_get_feed_data()
    return response.as_dict()


@shared_task
def clear_expired_sessions():
    """
    [定时清理过期的session]
    定时清理过期的session
    @return:
    """
    response = TaskResponse()
    call_command('clearsessions')
    response.data = {'msg': 'clear sessions done'}
    return response.as_dict()





@shared_task
def check_docker_engines_health():
    """
    [定期检查所有容器引擎的健康状态]
    定期检查所有容器引擎（Docker + K8s）的健康状态
    
    功能：
    - 检查所有激活的 Docker 引擎
    - 检查所有激活的 K8s 集群
    - 更新健康状态、资源使用率等信息
    - 发现不健康引擎时通知管理员
    - 记录详细日志
    
    使用方法：
    在 Django Admin 的 Celery Beat 定时任务中配置此任务
    建议频率：每 5-10 分钟执行一次
    
    Returns:
        dict: 健康检查结果摘要
    """
    from django.contrib.auth import get_user_model
    
    response = TaskResponse()
    User = get_user_model()
    
    try:
        logger.info("=" * 60)
        logger.info("开始定期容器引擎健康检查（Docker + K8s）...")
        logger.info("=" * 60)
        
        # 调用 DockerEngine 的静态方法检查所有引擎
        results = DockerEngine.check_all_health()
        
        # 统计 Docker 和 K8s 引擎数量
        docker_engines = DockerEngine.objects.filter(is_active=True, engine_type='DOCKER')
        k8s_engines = DockerEngine.objects.filter(is_active=True, engine_type='KUBERNETES')
        
        docker_count = docker_engines.count()
        k8s_count = k8s_engines.count()
        
        # 记录检查结果
        logger.info(
            f"容器引擎健康检查完成:\n"
            f"  总数: {results['total']} (Docker: {docker_count}, K8s: {k8s_count})\n"
            f"  健康: {results['healthy']}\n"
            f"  ⚠️ 警告: {results['warning']}\n"
            f"  🔴 严重: {results['critical']}\n"
            f"  ⚫ 离线: {results['offline']}"
        )
        
        # 如果有引擎处于严重或离线状态，发送通知给管理员
        if results['critical'] > 0 or results['offline'] > 0:
            logger.warning(
                f"发现不健康的容器引擎！"
                f"严重: {results['critical']}, 离线: {results['offline']}"
            )
            
            # 构建通知内容（区分 Docker 和 K8s）
            critical_docker = []
            critical_k8s = []
            offline_docker = []
            offline_k8s = []
            
            for engine in DockerEngine.objects.filter(is_active=True):
                engine_label = f"{engine.name} ({'K8s' if engine.engine_type == 'KUBERNETES' else 'Docker'})"
                error_info = engine.health_check_error or '未知错误'
                
                if engine.health_status == 'CRITICAL':
                    if engine.engine_type == 'KUBERNETES':
                        critical_k8s.append(f"{engine_label} - {error_info}")
                    else:
                        critical_docker.append(f"{engine_label} - {error_info}")
                elif engine.health_status == 'OFFLINE':
                    if engine.engine_type == 'KUBERNETES':
                        offline_k8s.append(f"{engine_label} - {error_info}")
                    else:
                        offline_docker.append(f"{engine_label} - {error_info}")
            
            # 构建通知消息（HTML 格式，区分 Docker 和 K8s）
            content_parts = []
            
            # Docker 引擎严重状态
            if critical_docker:
                docker_items = "".join(f"<li>{e}</li>" for e in critical_docker)
                content_parts.append(
                    f'<div style="margin-bottom: 15px;">'
                    f'<p><strong>🔴 Docker 引擎严重状态 ({len(critical_docker)}个)</strong></p>'
                    f'<ul style="margin-left: 20px;">{docker_items}</ul>'
                    f'</div>'
                )
            
            # K8s 集群严重状态
            if critical_k8s:
                k8s_items = "".join(f"<li>{e}</li>" for e in critical_k8s)
                content_parts.append(
                    f'<div style="margin-bottom: 15px;">'
                    f'<p><strong>🔴 K8s 集群严重状态 ({len(critical_k8s)}个)</strong></p>'
                    f'<ul style="margin-left: 20px;">{k8s_items}</ul>'
                    f'</div>'
                )
            
            # Docker 引擎离线
            if offline_docker:
                docker_items = "".join(f"<li>{e}</li>" for e in offline_docker)
                content_parts.append(
                    f'<div style="margin-bottom: 15px;">'
                    f'<p><strong>⚫ Docker 引擎离线 ({len(offline_docker)}个)</strong></p>'
                    f'<ul style="margin-left: 20px;">{docker_items}</ul>'
                    f'</div>'
                )
            
            # K8s 集群离线
            if offline_k8s:
                k8s_items = "".join(f"<li>{e}</li>" for e in offline_k8s)
                content_parts.append(
                    f'<div style="margin-bottom: 15px;">'
                    f'<p><strong>⚫ K8s 集群离线 ({len(offline_k8s)}个)</strong></p>'
                    f'<ul style="margin-left: 20px;">{k8s_items}</ul>'
                    f'</div>'
                )
            
            # 统计信息
            total_issues = len(critical_docker) + len(critical_k8s) + len(offline_docker) + len(offline_k8s)
            
            content = (
                f'<div style="padding: 15px; background: #fff3cd; border-left: 4px solid #ffc107; border-radius: 4px;">'
                f'<h4 style="color: #856404; margin-top: 0;">⚠️ 容器引擎健康检查发现异常！</h4>'
                f'<p style="color: #856404;">检测到 {total_issues} 个引擎存在问题（Docker: {len(critical_docker) + len(offline_docker)}, K8s: {len(critical_k8s) + len(offline_k8s)}）</p>'
                f'<hr style="border: none; border-top: 1px solid #ffeaa7; margin: 15px 0;">'
                f'{"".join(content_parts)}'
                f'<hr style="border: none; border-top: 1px solid #ffeaa7; margin: 15px 0;">'
                f'<p style="color: #856404;"><strong>💡 建议：</strong>请及时检查不健康的引擎，确保容器服务正常运行！</p>'
                f'<ul style="color: #856404; margin-left: 20px;">'
                f'<li>Docker 引擎：检查 Docker 服务状态、网络连接、磁盘空间</li>'
                f'<li>K8s 集群：检查节点状态、API Server 连接、命名空间配置</li>'
                f'</ul>'
                f'</div>'
            )
            
            # 发送通知给所有管理员
            try:
                # 获取所有管理员用户
                admin_users = User.objects.filter(is_staff=True, is_active=True)
                
                if admin_users.exists():
                    # 创建系统通知
                    notification = SystemNotification.objects.create(
                        title='容器引擎健康告警（Docker + K8s）',
                        content=content
                    )
                    
                    # 添加所有管理员为接收者
                    notification.get_p.add(*admin_users)
                    
                    logger.info(
                        f"已向 {admin_users.count()} 位管理员发送容器引擎健康告警通知\n"
                        f"  Docker 问题: {len(critical_docker) + len(offline_docker)} 个\n"
                        f"  K8s 问题: {len(critical_k8s) + len(offline_k8s)} 个"
                    )
                else:
                    logger.warning("没有找到管理员用户，无法发送告警通知")
                    
            except Exception as notify_err:
                logger.error(f"发送管理员通知失败: {str(notify_err)}", exc_info=True)
        
        logger.info("=" * 60)
        logger.info(f"容器引擎健康检查任务完成")
        logger.info("=" * 60)
        
        response.data = {
            'status': 'success',
            'summary': results,
            'engine_breakdown': {
                'docker': {
                    'total': docker_count,
                    'critical': len(critical_docker) if results['critical'] > 0 or results['offline'] > 0 else 0,
                    'offline': len(offline_docker) if results['critical'] > 0 or results['offline'] > 0 else 0,
                },
                'k8s': {
                    'total': k8s_count,
                    'critical': len(critical_k8s) if results['critical'] > 0 or results['offline'] > 0 else 0,
                    'offline': len(offline_k8s) if results['critical'] > 0 or results['offline'] > 0 else 0,
                }
            },
            'message': f"健康检查完成：健康 {results['healthy']}/{results['total']} (Docker: {docker_count}, K8s: {k8s_count})",
            'notification_sent': results['critical'] > 0 or results['offline'] > 0
        }
        
    except Exception as e:
        error_msg = f"容器引擎健康检查失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        response.error = error_msg
    
    return response.as_dict()


def _send_image_pull_failure_notification(image_name, error_message):
    """
    发送单个镜像拉取失败通知给管理员
    
    Args:
        image_name: 镜像名称
        error_message: 错误消息
    """
    try:
        User = get_user_model()
        from comment.models import SystemNotification
        
        # 构建通知内容
        content = f"""Docker镜像拉取失败

        **镜像名称：** {image_name}

        **错误信息：** {error_message}

        **时间：** {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}

        **建议操作：**
        1. 检查镜像名称是否正确
        2. 检查网络连接是否正常
        3. 检查镜像仓库是否可访问
        4. 检查Docker引擎状态
        """
        
        # 获取所有管理员
        admin_users = User.objects.filter(is_staff=True, is_active=True)
        
        if admin_users.exists():
            # 创建系统通知
            notification = SystemNotification.objects.create(
                title='Docker镜像拉取失败',
                content=content
            )
            
            # 添加所有管理员为接收者
            notification.get_p.add(*admin_users)
            
        else:
            logger.warning("没有找到管理员用户，无法发送通知")
            
    except Exception as e:
        logger.error(f"发送镜像拉取失败通知时出错: {str(e)}", exc_info=True)


def _send_batch_pull_failure_notification(success_count, failed_count, failed_images):
    """
    发送批量镜像拉取失败通知给管理员
    
    Args:
        success_count: 成功数量
        failed_count: 失败数量
        failed_images: 失败镜像列表
    """
    try:
        from oauth.models import Ouser as User
        from comment.models import SystemNotification
        
        # 构建失败镜像列表
        failed_list = '\n'.join([
            f"- {img.get('image_name', img.get('image_id', '未知'))}: {img.get('message', '未知错误')}"
            for img in failed_images[:10]  # 最多显示10个
        ])
        
        if len(failed_images) > 10:
            failed_list += f"\n... 还有 {len(failed_images) - 10} 个镜像拉取失败"
        
        # 构建通知内容
        content = f"""Docker批量镜像拉取任务完成

                    **任务结果：**
                    - 成功：{success_count} 个
                    -  失败：{failed_count} 个

                    **失败镜像列表：**
                    {failed_list}

                    **时间：** {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}

                    """
        
        # 获取所有管理员
        admin_users = User.objects.filter(is_staff=True, is_active=True)
        
        if admin_users.exists():
            # 创建系统通知
            notification = SystemNotification.objects.create(
                title=f'批量镜像拉取完成（{failed_count}个失败）',
                content=content
            )
            
            # 添加所有管理员为接收者
            notification.get_p.add(*admin_users)
            
            logger.info(f"已向 {admin_users.count()} 位管理员发送批量拉取结果通知")
        else:
            logger.warning("没有找到管理员用户，无法发送通知")
            
    except Exception as e:
        logger.error(f"发送批量拉取通知时出错: {str(e)}", exc_info=True)


@shared_task
def pull_docker_image(image_id):
    """
    异步拉取单个 Docker 镜像
    
    Args:
        image_id: DockerImage 模型的 ID
    
    Returns:
        dict: 任务响应数据
    """
    response = TaskResponse()
    
    try:
        from container.models import DockerImage
        
        # 获取镜像对象
        try:
            docker_image = DockerImage.objects.get(id=image_id)
        except DockerImage.DoesNotExist:
            response.error = f"镜像 ID {image_id} 不存在"
            return response.as_dict()
        
        # 获取容器引擎
        engine = DockerEngine.objects.filter(is_active=True).first()
        if not engine:
            response.error = "没有可用的容器引擎"
            return response.as_dict()
        
        # K8s 引擎
        if engine.engine_type == 'KUBERNETES':
            try:
                logger.info(f"开始K8s引擎拉取镜像: {docker_image.full_name}")
                from container.k8s_service import K8sService
                
                k8s_service = K8sService(engine=engine)
                logger.info(f"K8s服务已初始化")
                
                # 创建一个临时 Job 来拉取镜像（K8s 名称只允许小写字母、数字、- 和 .）
                import re
                safe_name = re.sub(r'[^a-z0-9\-.]', '-', docker_image.name.lower())
                safe_name = re.sub(r'-+', '-', safe_name).strip('-.')  # 合并连续的 - 并移除首尾的 - 或 .
                job_name = f"pre-pull-{safe_name}-{int(timezone.now().timestamp())}"[:63].rstrip('-.')
                logger.info(f"Job名称: {job_name}")
                
                from kubernetes import client as k8s_client
                # 使用K8sService中已配置的客户端
                batch_api = k8s_client.BatchV1Api(api_client=k8s_service.core_api.api_client)
                logger.info(f"Batch API已初始化")
                
                job = k8s_client.V1Job(
                    metadata=k8s_client.V1ObjectMeta(name=job_name[:63]),
                    spec=k8s_client.V1JobSpec(
                        ttl_seconds_after_finished=300,  # 完成或失败后5分钟自动删除（给检查留足够时间）
                        active_deadline_seconds=120,  # 最长运行2分钟（镜像拉取超时则直接失败）
                        template=k8s_client.V1PodTemplateSpec(
                            spec=k8s_client.V1PodSpec(
                                restart_policy='Never',  # Pod 失败后不重启
                                containers=[
                                    k8s_client.V1Container(
                                        name='pre-pull',
                                        image=docker_image.full_name,
                                        command=['sh', '-c', 'echo "Image pulled successfully"'],
                                        image_pull_policy='Always'  # 总是尝试拉取镜像
                                    )
                                ]
                            )
                        ),
                        backoff_limit=0  # Job 失败后不创建新的 Pod，只执行一次
                    )
                )
                
                namespace = engine.namespace or 'ctf-challenges'
                logger.info(f"准备创建Job到命名空间: {namespace}")
                
                created_job = batch_api.create_namespaced_job(
                    namespace=namespace,
                    body=job
                )
                logger.info(f"✓ K8s Job 已创建: {job_name} (命名空间: {namespace})")
                
                # 等待 Job 完成（最多等待120秒）
                import time
                max_wait_time = 120
                check_interval = 3
                elapsed_time = 0
                job_succeeded = False
                job_failed = False
                failure_reason = ""
                
                while elapsed_time < max_wait_time:
                    try:
                        job_status = batch_api.read_namespaced_job_status(
                            name=job_name,
                            namespace=namespace
                        )
                        
                        # 检查 Job 是否成功
                        if job_status.status.succeeded and job_status.status.succeeded > 0:
                            job_succeeded = True
                            logger.info(f"✓ K8s Job 成功: {job_name} - 镜像 {docker_image.full_name} 已拉取")
                            break
                        
                        # 检查 Job 是否失败
                        if job_status.status.failed and job_status.status.failed > 0:
                            job_failed = True
                            # 获取失败原因
                            try:
                                core_api = k8s_client.CoreV1Api()
                                pods = core_api.list_namespaced_pod(
                                    namespace=namespace,
                                    label_selector=f"job-name={job_name}"
                                )
                                if pods.items:
                                    pod = pods.items[0]
                                    if pod.status.container_statuses:
                                        container_status = pod.status.container_statuses[0]
                                        if container_status.state.waiting:
                                            failure_reason = f"{container_status.state.waiting.reason}: {container_status.state.waiting.message}"
                                        elif container_status.state.terminated:
                                            failure_reason = f"{container_status.state.terminated.reason}: {container_status.state.terminated.message}"
                            except Exception as pod_err:
                                logger.debug(f"获取Pod失败原因出错: {pod_err}")
                            
                            logger.error(f" K8s Job 失败: {job_name} - {failure_reason}")
                            break
                        
                        # 继续等待
                        time.sleep(check_interval)
                        elapsed_time += check_interval
                        
                    except Exception as status_err:
                        logger.warning(f"检查Job状态失败: {status_err}")
                        break
                
                # 根据结果设置响应
                if job_succeeded:
                    # 更新镜像状态
                    docker_image.is_pulled = True
                    docker_image.last_pulled = timezone.now()
                    docker_image.save(update_fields=['is_pulled', 'last_pulled'])
                    
                    response.data = {
                        'image_name': docker_image.full_name,
                        'status': 'success',
                        'message': f'K8s 引擎成功拉取镜像: {docker_image.full_name}'
                    }
                elif job_failed:
                    error_msg = f"K8s Job 失败: {failure_reason or '未知错误'}"
                    response.error = error_msg
                    logger.error(f"K8s 拉取镜像 {docker_image.full_name} 失败: {error_msg}")
                    _send_image_pull_failure_notification(docker_image.full_name, error_msg)
                else:
                    # 超时
                    error_msg = f"K8s Job 超时（{max_wait_time}秒）"
                    response.error = error_msg
                    logger.error(f"K8s 拉取镜像 {docker_image.full_name} 超时: Job {job_name}")
                    _send_image_pull_failure_notification(docker_image.full_name, error_msg)
                
            except Exception as e:
                error_msg = f"K8s 拉取镜像失败: {str(e)}"
                response.error = error_msg
                logger.error(f"K8s 拉取镜像 {docker_image.full_name} 失败: {str(e)}")
                _send_image_pull_failure_notification(docker_image.full_name, error_msg)
        
        # Docker 引擎
        else:
            docker_url = engine.get_docker_url()
            tls_config = None
            if engine.tls_enabled:
                tls_config = engine.get_tls_config()
            
            client = docker.DockerClient(base_url=docker_url, tls=tls_config, timeout=300)
            
            try:
                # 从镜像仓库拉取
                logger.info(f"开始拉取镜像: {docker_image.full_name}")
                image = client.images.pull(docker_image.full_name)
                logger.info(f"成功拉取镜像: {docker_image.full_name}")
                
                # 更新镜像信息
                docker_image.is_pulled = True
                docker_image.image_id = image.id
                docker_image.image_size = image.attrs.get('Size', 0)
                docker_image.last_pulled = timezone.now()
                docker_image.save(update_fields=['is_pulled', 'image_id', 'image_size', 'last_pulled'])
                
                response.data = {
                    'image_name': docker_image.full_name,
                    'image_id': image.id,
                    'image_size': docker_image.image_size,
                    'status': 'success',
                    'message': f'成功拉取镜像: {docker_image.full_name}'
                }
                
            except docker.errors.ImageNotFound:
                error_msg = f"镜像 {docker_image.full_name} 不存在"
                response.error = error_msg
                logger.error(f"镜像不存在: {docker_image.full_name}")
                _send_image_pull_failure_notification(docker_image.full_name, error_msg)
            except docker.errors.APIError as e:
                error_msg = f"Docker API 错误: {str(e)}"
                response.error = error_msg
                logger.error(f"拉取镜像 {docker_image.full_name} 时发生 API 错误: {str(e)}")
                _send_image_pull_failure_notification(docker_image.full_name, error_msg)
            except Exception as e:
                error_msg = f"拉取镜像失败: {str(e)}"
                response.error = error_msg
                logger.error(f"拉取镜像 {docker_image.full_name} 时发生错误: {str(e)}")
                _send_image_pull_failure_notification(docker_image.full_name, error_msg)
            finally:
                client.close()
            
    except Exception as e:
        response.error = f"异步拉取镜像时发生错误: {str(e)}"
        logger.error(f"异步拉取镜像任务异常: {str(e)}")
    
    return response.as_dict()


@shared_task
def pull_multiple_docker_images(image_ids):
    """
    批量异步拉取多个 Docker 镜像到所有激活的引擎
    
    Args:
        image_ids: DockerImage 模型 ID 列表
    
    Returns:
        dict: 任务响应数据
    """
    response = TaskResponse()
    
    try:
        from container.models import DockerImage
        
        # 获取所有激活的引擎
        engines = DockerEngine.objects.filter(is_active=True)
        if not engines.exists():
            response.error = "没有可用的容器引擎"
            logger.error("批量拉取镜像失败: 没有可用的容器引擎")
            return response.as_dict()
        
        logger.info(f"将镜像拉取到 {engines.count()} 个激活的引擎")
        
        full_success_count = 0    # 所有引擎都成功
        partial_success_count = 0  # 部分引擎成功
        failed_count = 0           # 所有引擎都失败
        results = []
        
        # 遍历所有镜像
        for image_id in image_ids:
            try:
                docker_image = DockerImage.objects.get(id=image_id)
                image_success_engines = []
                image_failed_engines = []
                
                # 遍历所有激活的引擎
                for engine in engines:
                    try:
                        # K8s 引擎：创建临时 Job 预拉取镜像
                        if engine.engine_type == 'KUBERNETES':
                            try:
                                logger.info(f"[{engine.name}] 开始K8s引擎拉取镜像: {docker_image.full_name}")
                                from container.k8s_service import K8sService
                                
                                k8s_service = K8sService(engine=engine)
                                logger.info(f"[{engine.name}] K8s服务已初始化")
                                
                                # 创建一个临时 Job 来拉取镜像（K8s 名称只允许小写字母、数字、- 和 .）
                                import re
                                safe_name = re.sub(r'[^a-z0-9\-.]', '-', docker_image.name.lower())
                                safe_name = re.sub(r'-+', '-', safe_name).strip('-.')  # 合并连续的 - 并移除首尾的 - 或 .
                                job_name = f"pre-pull-{safe_name}-{int(timezone.now().timestamp())}"[:63].rstrip('-.')
                                logger.info(f"[{engine.name}] Job名称: {job_name}")
                                
                                from kubernetes import client as k8s_client
                                # 使用K8sService中已配置的客户端
                                batch_api = k8s_client.BatchV1Api(api_client=k8s_service.core_api.api_client)
                                logger.info(f"[{engine.name}] Batch API已初始化")
                                
                                # 创建 Job 配置（只执行一次，不重试）
                                job = k8s_client.V1Job(
                                    metadata=k8s_client.V1ObjectMeta(name=job_name[:63]),
                                    spec=k8s_client.V1JobSpec(
                                        ttl_seconds_after_finished=300,  # 完成或失败后5分钟自动删除（给检查留足够时间）
                                        active_deadline_seconds=120,  # 最长运行2分钟（镜像拉取超时则直接失败）
                                        template=k8s_client.V1PodTemplateSpec(
                                            spec=k8s_client.V1PodSpec(
                                                restart_policy='Never',  # Pod 失败后不重启
                                                containers=[
                                                    k8s_client.V1Container(
                                                        name='pre-pull',
                                                        image=docker_image.full_name,
                                                        command=['sh', '-c', 'echo "Image pulled successfully"'],
                                                        image_pull_policy='Always'  # 总是尝试拉取镜像
                                                    )
                                                ]
                                            )
                                        ),
                                        backoff_limit=0  # Job 失败后不创建新的 Pod，只执行一次
                                    )
                                )
                                
                                # 创建 Job
                                namespace = engine.namespace or 'ctf-challenges'
                                logger.info(f"[{engine.name}] 准备创建Job到命名空间: {namespace}")
                                
                                created_job = batch_api.create_namespaced_job(
                                    namespace=namespace,
                                    body=job
                                )
                                logger.info(f"✓ [{engine.name}] K8s Job 已创建: {job_name} (命名空间: {namespace})")
                                
                                # 等待 Job 完成（最多等待120秒）
                                import time
                                max_wait_time = 120
                                check_interval = 3
                                elapsed_time = 0
                                job_succeeded = False
                                job_failed = False
                                failure_reason = ""
                                
                                while elapsed_time < max_wait_time:
                                    try:
                                        job_status = batch_api.read_namespaced_job_status(
                                            name=job_name,
                                            namespace=namespace
                                        )
                                        
                                        # 检查 Job 是否成功
                                        if job_status.status.succeeded and job_status.status.succeeded > 0:
                                            job_succeeded = True
                                            logger.info(f"✓ [{engine.name}] K8s Job 成功: {job_name}")
                                            break
                                        
                                        # 检查 Job 是否失败
                                        if job_status.status.failed and job_status.status.failed > 0:
                                            job_failed = True
                                            # 获取失败原因
                                            try:
                                                core_api = k8s_client.CoreV1Api()
                                                pods = core_api.list_namespaced_pod(
                                                    namespace=namespace,
                                                    label_selector=f"job-name={job_name}"
                                                )
                                                if pods.items:
                                                    pod = pods.items[0]
                                                    if pod.status.container_statuses:
                                                        container_status = pod.status.container_statuses[0]
                                                        if container_status.state.waiting:
                                                            failure_reason = f"{container_status.state.waiting.reason}: {container_status.state.waiting.message}"
                                                        elif container_status.state.terminated:
                                                            failure_reason = f"{container_status.state.terminated.reason}: {container_status.state.terminated.message}"
                                            except Exception as pod_err:
                                                logger.debug(f"获取Pod失败原因出错: {pod_err}")
                                            
                                            logger.error(f" [{engine.name}] K8s Job 失败: {job_name} - {failure_reason}")
                                            break
                                        
                                        # 继续等待
                                        time.sleep(check_interval)
                                        elapsed_time += check_interval
                                        
                                    except Exception as status_err:
                                        logger.warning(f"[{engine.name}] 检查Job状态失败: {status_err}")
                                        break
                                
                                # 根据结果设置状态
                                if job_succeeded:
                                    image_success_engines.append(f"{engine.name} (K8s)")
                                    logger.info(f"✓ [{engine.name}] 成功拉取镜像: {docker_image.full_name}")
                                else:
                                    error_reason = failure_reason if job_failed else f"超时（{max_wait_time}秒）"
                                    image_failed_engines.append(f"{engine.name} (K8s): {error_reason}")
                                    logger.error(f" [{engine.name}] 拉取镜像失败: {docker_image.full_name} - {error_reason}")
                                
                            except Exception as k8s_error:
                                image_failed_engines.append(f"{engine.name} (K8s): {str(k8s_error)}")
                                logger.error(f"[{engine.name}] K8s引擎拉取镜像失败: {str(k8s_error)}")
                        
                        # Docker 引擎：使用 Docker API 拉取或导入
                        else:
                            docker_url = engine.get_docker_url()
                            tls_config = None
                            if engine.tls_enabled:
                                tls_config = engine.get_tls_config()
                            
                            client = docker.DockerClient(base_url=docker_url, tls=tls_config, timeout=300)
                            
                            try:
                                # 从镜像仓库拉取
                                logger.info(f"[{engine.name}] 开始拉取镜像: {docker_image.full_name}")
                                image = client.images.pull(docker_image.full_name)
                                logger.info(f"[{engine.name}] 成功拉取镜像: {docker_image.full_name}")
                                
                                # 记录成功
                                image_success_engines.append(engine.name)
                                
                                # 更新镜像基本信息（使用最后一个成功的引擎的信息）
                                docker_image.image_id = image.id
                                docker_image.image_size = image.attrs.get('Size', 0)
                                
                            finally:
                                client.close()
                            
                    except Exception as e:
                        image_failed_engines.append(f"{engine.name}: {str(e)}")
                        logger.error(f"[{engine.name}] 拉取镜像 {docker_image.full_name} 失败: {str(e)}")
                
                # 更新数据库状态
                # 只有所有引擎都成功拉取，才标记为已拉取
                all_engines_success = (len(image_success_engines) == engines.count())
                
                if image_success_engines:
                    # 至少有一个引擎成功
                    docker_image.is_pulled = all_engines_success  # 所有引擎都成功才为 True
                    docker_image.last_pulled = timezone.now()
                    docker_image.save(update_fields=['is_pulled', 'image_id', 'image_size', 'last_pulled'])
                    
                    if all_engines_success:
                        # 所有引擎都成功
                        full_success_count += 1
                        results.append({
                            'image_name': docker_image.full_name,
                            'status': 'success',
                            'message': f'成功拉取到所有引擎 ({len(image_success_engines)}/{engines.count()})',
                            'success_engines': image_success_engines,
                            'failed_engines': image_failed_engines
                        })
                    else:
                        # 部分引擎成功
                        partial_success_count += 1
                        results.append({
                            'image_name': docker_image.full_name,
                            'status': 'partial',
                            'message': f'部分引擎拉取成功 ({len(image_success_engines)}/{engines.count()} 个引擎)',
                            'success_engines': image_success_engines,
                            'failed_engines': image_failed_engines
                        })
                else:
                    # 所有引擎都失败
                    failed_count += 1
                    results.append({
                        'image_name': docker_image.full_name,
                        'status': 'error',
                        'message': '所有引擎拉取失败',
                        'failed_engines': image_failed_engines
                    })
                
            except DockerImage.DoesNotExist:
                failed_count += 1
                results.append({
                    'image_id': image_id,
                    'status': 'error',
                    'message': f'镜像 ID {image_id} 不存在'
                })
                logger.warning(f"镜像 ID {image_id} 不存在")
                
            except Exception as e:
                failed_count += 1
                image_name = docker_image.full_name if 'docker_image' in locals() else f'ID:{image_id}'
                results.append({
                    'image_name': image_name,
                    'status': 'error',
                    'message': str(e)
                })
                logger.error(f"拉取镜像 {image_name} 失败: {str(e)}")
        
        response.data = {
            'total': len(image_ids),
            'full_success': full_success_count,      # 完全成功（所有引擎）
            'partial_success': partial_success_count, # 部分成功
            'failed': failed_count,                   # 完全失败
            'total_engines': engines.count(),
            'results': results,
            'message': f'拉取完成: 完全成功 {full_success_count} 个，部分成功 {partial_success_count} 个，失败 {failed_count} 个（共 {engines.count()} 个引擎）'
        }
        
        
        
        # 如果有失败的镜像，发送通知
        if failed_count > 0:
            failed_images = [r for r in results if r['status'] == 'error']
            _send_batch_pull_failure_notification(full_success_count + partial_success_count, failed_count, failed_images)
            
    except Exception as e:
        response.error = f"批量拉取镜像时发生错误: {str(e)}"
        logger.error(f"批量拉取镜像任务异常: {str(e)}")
    
    return response.as_dict()



@shared_task
def batch_check_images_status():
    """
    批量异步检查所有镜像的引擎状态（用于页面加载时自动刷新）
    
    Returns:
        dict: 任务响应数据
    """
    from django.core.cache import cache
    from datetime import datetime
    import docker
    
    response = TaskResponse()
    
    try:
        from container.models import DockerImage, DockerEngine
        
        # 获取所有激活的引擎
        engines = DockerEngine.objects.filter(is_active=True).order_by('name')
        
        if not engines.exists():
            response.error = "没有可用的容器引擎"
            logger.warning("批量检查镜像状态: 没有可用的容器引擎")
            return response.as_dict()
        
        # 获取所有激活的镜像（不限制数量，因为现在按引擎检查，效率很高）
        images = DockerImage.objects.filter(is_active=True)
        
        if not images.exists():
            response.data = {
                'message': '没有需要检查的镜像',
                'checked_count': 0
            }
            return response.as_dict()
        
    
        
        checked_count = 0
        updated_count = 0
        cache_time = datetime.now().strftime('%m-%d %H:%M:%S')
        
        # 优化：按引擎遍历，而不是按镜像遍历（减少API调用）
        # 构建镜像名称到镜像对象的映射
        image_dict = {img.full_name: img for img in images}
        
        # 遍历所有引擎，获取每个引擎的镜像列表
        for engine in engines:
            # K8s 引擎：检查节点上的镜像
            if engine.engine_type == 'KUBERNETES':
                try:
                    from container.k8s_service import K8sService
                    from kubernetes import client as k8s_client
                    
                    k8s_service = K8sService(engine=engine)
                    
                    core_api = k8s_client.CoreV1Api()
                    
                    # 获取所有节点
                    nodes = core_api.list_node()
                    
                    # 收集所有节点上的镜像
                    all_k8s_images = set()
                    for node in nodes.items:
                        # 获取节点上的镜像列表
                        if node.status and node.status.images:
                            for image_info in node.status.images:
                                # image_info.names 包含所有镜像的标签
                                if image_info.names:
                                    all_k8s_images.update(image_info.names)
                    
                    # 构建镜像字典
                    images_dict = {}
                    for image_name in image_dict.keys():
                        # 检查镜像是否在任何节点上
                        if image_name in all_k8s_images:
                            images_dict[image_name] = {
                                'status': 'pulled',
                                'color': 'green',
                                'icon': '✓'
                            }
                        else:
                            images_dict[image_name] = {
                                'status': 'not_pulled',
                                'color': 'gray',
                                'icon': '✗'
                            }
                    
                    cache_key = f'docker_engine_{engine.id}_images'
                    cache_data = {
                        'images': images_dict,
                        'cache_time': cache_time,
                        'engine_type': 'k8s'
                    }
                    cache.set(cache_key, cache_data, timeout=3600)
                
                    
                except Exception as k8s_error:
                    logger.error(f"获取 K8s 引擎 {engine.name} 的镜像列表失败: {str(k8s_error)}")
                    # K8s 失败，缓存错误状态
                    cache_key = f'docker_engine_{engine.id}_images'
                    cache.set(cache_key, {
                        'images': {},
                        'cache_time': cache_time,
                        'error': str(k8s_error)
                    }, timeout=3600)
                
                continue
            
            # Docker 引擎：实际检查镜像状态
            try:
                docker_url = engine.get_docker_url()
                tls_config = engine.get_tls_config() if engine.needs_tls else None
                client = docker.DockerClient(base_url=docker_url, tls=tls_config, timeout=5)
                
                try:
                    # 获取该引擎的所有镜像
                    all_images = client.images.list()
                    
                    # 构建镜像字典（引擎上所有镜像的状态）
                    images_dict = {}
                    for img in all_images:
                        for tag in img.tags:
                            images_dict[tag] = {
                                'status': 'pulled',
                                'color': 'green',
                                'icon': '✓'
                            }
                    
                    # 标记我们关注的镜像中不存在的
                    for image_name in image_dict.keys():
                        if image_name not in images_dict:
                            images_dict[image_name] = {
                                'status': 'not_pulled',
                                'color': 'gray',
                                'icon': '✗'
                            }
                    
                    # 缓存该引擎的所有镜像状态
                    cache_key = f'docker_engine_{engine.id}_images'
                    cache_data = {
                        'images': images_dict,
                        'cache_time': cache_time
                    }
                    cache.set(cache_key, cache_data, timeout=3600)
                    
                
                    
                except Exception as img_error:
                    logger.error(f"获取 Docker 引擎 {engine.name} 的镜像列表失败: {str(img_error)}")
                    # 引擎失败，缓存错误状态
                    cache_key = f'docker_engine_{engine.id}_images'
                    cache.set(cache_key, {
                        'images': {},
                        'cache_time': cache_time,
                        'error': str(img_error)
                    }, timeout=3600)
                    
                finally:
                    client.close()
                    
            except Exception as e:
                logger.error(f"连接 Docker 引擎 {engine.name} 失败: {str(e)}")
                # 连接失败也写入空缓存，避免显示"未检查"
                cache_key = f'docker_engine_{engine.id}_images'
                cache.set(cache_key, {
                    'images': {},
                    'cache_time': cache_time
                }, timeout=300)
                continue
        
        # 从缓存中读取所有引擎的状态，更新镜像的 is_pulled 字段
        for image in images:
            try:
                engine_statuses = []
                has_any_pulled = False
                
                for engine in engines:
                    cache_key = f'docker_engine_{engine.id}_images'
                    cached_data = cache.get(cache_key)
                    
                    if cached_data and 'images' in cached_data:
                        images_dict = cached_data['images']
                        image_status = images_dict.get(image.full_name)
                        
                        if image_status:
                            engine_statuses.append({
                                'name': engine.name,
                                'status': image_status['status'],
                                'color': image_status['color'],
                                'icon': image_status['icon']
                            })
                            if image_status['status'] == 'pulled':
                                has_any_pulled = True
                        else:
                            # 该镜像在此引擎上未找到
                            engine_statuses.append({
                                'name': engine.name,
                                'status': 'not_pulled',
                                'color': 'gray',
                                'icon': '✗'
                            })
                    else:
                        # 缓存不存在或获取失败
                        engine_statuses.append({
                            'name': engine.name,
                            'status': 'error',
                            'color': 'orange',
                            'icon': '?'
                        })
                
                # 统计拉取情况
                pulled_count = sum(1 for s in engine_statuses if s['status'] == 'pulled')
                not_pulled_count = sum(1 for s in engine_statuses if s['status'] == 'not_pulled')
                checkable_count = pulled_count + not_pulled_count
                
                # 更新数据库状态（所有引擎都已拉取才标记为 True）
                all_pulled = (checkable_count > 0 and pulled_count == checkable_count)
                
                # 记录状态变化用于日志
                status_changed = (image.is_pulled != all_pulled)
                
                if status_changed:
                    image.is_pulled = all_pulled
                    if all_pulled:
                        # 全部拉取：更新 is_pulled 和 last_pulled
                        image.last_pulled = timezone.now()
                        image.save(update_fields=['is_pulled', 'last_pulled'])
                        
                    else:
                        # 未全部拉取：只更新 is_pulled
                        image.save(update_fields=['is_pulled'])
                    
                
                checked_count += 1
                if status_changed:
                    updated_count += 1
                
            except Exception as e:
                logger.error(f"检查镜像 {image.full_name} 状态失败: {str(e)}")
                continue
        
        response.data = {
            'message': f'批量检查完成',
            'checked_count': checked_count,
            'updated_count': updated_count,
            'total_images': images.count(),
            'total_engines': engines.count()
        }
        
        logger.info(f"批量检查镜像状态完成: 检查了 {checked_count} 个镜像")
        
    except Exception as e:
        response.error = f"批量检查镜像状态时发生错误: {str(e)}"
        logger.error(f"批量检查镜像状态任务异常: {str(e)}")
    
    return response.as_dict()


# ==================== 综合排行榜任务 ====================

@shared_task(bind=True, max_retries=3)
def save_combined_leaderboard_to_db(self, competition_id, competition_type, leaderboard_data, is_final=False):
    """
    将综合排行榜数据异步写入数据库
    
    流程：
    1. 用户访问触发计算，数据立即写入 Redis
    2. 该异步任务在后台将数据写入数据库
    3. 保证用户体验（快速响应）和数据持久化（后台写入）
    
    Args:
        competition_id: 比赛ID
        competition_type: 比赛类型（individual 或 team）
        leaderboard_data: 排行榜数据列表
        is_final: 是否为最终数据
        
    Returns:
        dict: 写入结果
    """
    response = TaskResponse()
    
    try:
        from competition.models import Competition, CombinedLeaderboard
        from django.db import transaction
        from decimal import Decimal
        import uuid
        
        logger.info(f'[数据库写入] 开始: competition_id={competition_id}, type={competition_type}, count={len(leaderboard_data)}')
        
        # 获取比赛对象
        try:
            competition = Competition.objects.get(id=competition_id)
        except Competition.DoesNotExist:
            logger.error(f'[数据库写入] 比赛不存在: competition_id={competition_id}')
            response.error = f'比赛不存在: competition_id={competition_id}'
            return response.as_dict()
        
        # 使用事务保证原子性
        try:
            with transaction.atomic():
                # 步骤1：删除旧数据
                if competition_type == 'team':
                    old_count = CombinedLeaderboard.objects.filter(
                        competition=competition,
                        team__isnull=False
                    ).delete()[0]
                    
                else:
                    old_count = CombinedLeaderboard.objects.filter(
                        competition=competition,
                        user__isnull=False
                    ).delete()[0]
                
                
                # 步骤2：批量创建新记录
                records_to_create = []
                for data in leaderboard_data:
                    if competition_type == 'team':
                        record = CombinedLeaderboard(
                            competition=competition,
                            team_id=data['team_id'],
                            ctf_score=Decimal(str(data['ctf_score'])),
                            quiz_score=Decimal(str(data['quiz_score'])),
                            combined_score=Decimal(str(data['combined_score'])),
                            rank=data['rank'],
                            ctf_rank=data.get('ctf_rank', 0),
                            quiz_rank=0,
                            is_final=is_final
                        )
                    else:
                        record = CombinedLeaderboard(
                            competition=competition,
                            user_id=data['user_id'],
                            ctf_score=Decimal(str(data['ctf_score'])),
                            quiz_score=Decimal(str(data['quiz_score'])),
                            combined_score=Decimal(str(data['combined_score'])),
                            rank=data['rank'],
                            ctf_rank=data.get('ctf_rank', 0),
                            quiz_rank=0,
                            is_final=is_final
                        )
                    records_to_create.append(record)
                
                # 批量插入
                if records_to_create:
                    CombinedLeaderboard.objects.bulk_create(
                        records_to_create,
                        batch_size=500,
                        ignore_conflicts=False
                    )
                
                response.data = {
                    'success': True,
                    'competition_id': competition_id,
                    'competition_type': competition_type,
                    'records_count': len(records_to_create)
                }
                
        except Exception as e:
            logger.error(f'[数据库写入]  失败: {e}', exc_info=True)
            response.error = f'数据库写入失败: {str(e)}'
            
            # 重试机制
            try:
                self.retry(countdown=30 * (self.request.retries + 1))
            except self.MaxRetriesExceededError:
                logger.error(f'[数据库写入] 达到最大重试次数: competition_id={competition_id}')
        
    except Exception as e:
        logger.error(f'[数据库写入] 任务执行失败: competition_id={competition_id}, error={e}', exc_info=True)
        response.error = f'任务执行失败: {str(e)}'
    
    return response.as_dict()



@shared_task
def auto_calculate_ended_competitions():
    """
    自动计算最近结束的比赛综合排行榜（定时任务）
    
    执行频率：每小时执行一次（在 Celery Beat 中配置）
    处理范围：最近24小时内结束且关联了知识竞赛的比赛
    
    说明：
    - 比赛结束后，首次用户访问会触发计算
    - 该定时任务用于提前计算，避免首次访问等待
    - 只计算还没有缓存数据的比赛
    """
    response = TaskResponse()
    
    try:
        from competition.models import Competition, CombinedLeaderboard
        from competition.utils_optimized import CombinedLeaderboardCalculator
        from django.core.cache import cache
        
        now = timezone.now()
        one_day_ago = now - timedelta(days=1)
        
        # 查找最近24小时内结束且关联了知识竞赛的比赛
        ended_competitions = Competition.objects.filter(
            end_time__gte=one_day_ago,
            end_time__lt=now,
            related_quiz__isnull=False
        )
        
        total_count = ended_competitions.count()
        
        if total_count == 0:
            logger.info('[自动计算] 没有需要计算的结束比赛')
            response.data = {
                'message': '没有需要计算的结束比赛',
                'total': 0
            }
            return response.as_dict()
        
        logger.info(f'[自动计算] 开始检查 {total_count} 场最近结束的比赛')
        
        success_count = 0
        skip_count = 0
        failed_count = 0
        results = []
        
        for competition in ended_competitions:
            try:
                # 检查是否已有缓存数据
                cache_key = f'combined_leaderboard_{competition.competition_type}_{competition.id}_all'
                if cache.get(cache_key):
                    skip_count += 1
                    logger.info(f'[自动计算] 跳过（已有缓存）: {competition.title}')
                    continue
                
                # 检查数据库是否已有数据
                db_count = CombinedLeaderboard.objects.filter(competition=competition).count()
                if db_count > 0:
                    skip_count += 1
                    logger.info(f'[自动计算] 跳过（数据库已有数据）: {competition.title}')
                    continue
                
                # 触发计算
                
                calculator = CombinedLeaderboardCalculator(competition, competition.related_quiz)
                result = calculator.calculate_leaderboard_with_lock(force=True)
                
                if result.get('success'):
                    success_count += 1
                    results.append({
                        'competition_id': competition.id,
                        'competition_name': competition.title,
                        'status': 'success',
                        'count': result.get('total_count', 0)
                    })
                    
                else:
                    failed_count += 1
                    results.append({
                        'competition_id': competition.id,
                        'competition_name': competition.title,
                        'status': 'failed',
                        'error': result.get('message')
                    })
                    logger.error(f'[自动计算]  计算失败: {competition.title}, error={result.get("message")}')
                
            except Exception as e:
                failed_count += 1
                results.append({
                    'competition_id': competition.id,
                    'competition_name': competition.title,
                    'status': 'error',
                    'error': str(e)
                })
                logger.error(f'[自动计算]  异常: {competition.title}, error={e}', exc_info=True)
        
        response.data = {
            'message': f'自动计算完成：成功 {success_count} 场，跳过 {skip_count} 场，失败 {failed_count} 场',
            'total': total_count,
            'success': success_count,
            'skipped': skip_count,
            'failed': failed_count,
            'results': results
        }
        
        
    except Exception as e:
        error_msg = f'自动计算任务执行失败: {str(e)}'
        logger.error(f'[自动计算] {error_msg}', exc_info=True)
        response.error = error_msg
    
    return response.as_dict()


# 已删除 refresh_active_competitions 任务
# 原因：综合排行榜只在比赛结束后计算，比赛进行中不需要刷新



@shared_task
def send_unread_notifications_email():
    """
    [定时发送未读通知邮件给用户]
    定时发送未读通知邮件给用户
    
    功能：
    - 检查邮箱功能是否启用
    - 查询有未读通知的用户（系统通知和评论通知）
    - 汇总每个用户的未读通知
    - 发送简约的邮件通知
    
    执行频率：建议每天执行1-2次（在 Celery Beat 中配置）
    
    Returns:
        dict: 任务执行结果
    """
    from django.contrib.auth import get_user_model
    from django.core.mail import send_mail
    from django.conf import settings
    from public.models import SiteSettings
    from public.utils import site_full_url
    from comment.models import SystemNotification, Notification
    from datetime import datetime
    import re
    from html import escape as html_escape
    
    response = TaskResponse()
    User = get_user_model()
    
    # 辅助函数：检查邮箱是否已验证
    def is_email_verified(user):
        """检查用户邮箱是否已验证"""
        if not user.email:
            return False
        try:
            from allauth.account.models import EmailAddress
            email_obj = EmailAddress.objects.filter(
                user=user,
                email=user.email,
                verified=True
            ).exists()
            return email_obj
        except Exception as e:
            # 如果没有使用 allauth 或出现其他错误，默认返回 True（兼容模式）
            logger.warning(f'[邮件通知] 无法检查邮箱验证状态: {str(e)}')
            return True
    
    try:
        # 1. 获取站点配置
        try:
            site_settings = SiteSettings.objects.first()
        except Exception:
            site_settings = None
        
        # 2. 检查邮箱功能是否启用
        if not site_settings or not site_settings.email_enabled:
            logger.info('[邮件通知] 邮箱功能未启用，跳过发送')
            response.data = {
                'message': '邮箱功能未启用',
                'status': 'skipped'
            }
            return response.as_dict()
        
        # 3. 配置邮箱设置（临时覆盖 Django settings）
        if site_settings.email_host:
            settings.EMAIL_HOST = site_settings.email_host
            settings.EMAIL_PORT = site_settings.email_port
            # 从 email_host_user 中提取纯邮箱地址
            email_host_user = site_settings.email_host_user
            # 如果已经是格式化的（包含 < 和 >），提取出纯邮箱地址
            if '<' in email_host_user and '>' in email_host_user:
                # 提取 <email@example.com> 中的 email@example.com
                pure_email = email_host_user.split('<')[1].split('>')[0].strip()
            else:
                pure_email = email_host_user.strip()
            
            settings.EMAIL_HOST_USER = pure_email
            settings.EMAIL_HOST_PASSWORD = site_settings.email_host_password
            settings.EMAIL_USE_SSL = site_settings.email_use_ssl
            settings.EMAIL_USE_TLS = not site_settings.email_use_ssl
            settings.DEFAULT_FROM_EMAIL = pure_email
        
        # 构建发件人格式
        if site_settings:
            # 检查 email_host_user 是否已经是格式化的
            if '<' in site_settings.email_host_user and '>' in site_settings.email_host_user:
                # 已经是格式化的，直接使用
                from_email = site_settings.email_host_user
                logger.info(f'[邮件通知] 使用已格式化的发件人: {from_email}')
            else:
                # 纯邮箱地址，需要格式化
                from_email = f"{site_settings.email_from} <{site_settings.email_host_user}>"
                logger.info(f'[邮件通知] 格式化发件人: {from_email}')
        else:
            from_email = settings.DEFAULT_FROM_EMAIL
        

        

        
        # 4. 查询所有未读的系统通知和评论通知
        system_notifications = SystemNotification.objects.filter(
            is_read=False
        ).prefetch_related('get_p')
        
        comment_notifications = Notification.objects.filter(
            is_read=False
        ).select_related('create_p', 'get_p')
        
        if not system_notifications.exists() and not comment_notifications.exists():
            logger.info('[邮件通知] 没有未读通知，跳过发送')
            response.data = {
                'message': '没有未读通知',
                'status': 'success',
                'sent_count': 0
            }
            return response.as_dict()
        
        # 5. 按用户汇总未读通知
        user_notifications = {}
        
        # 汇总系统通知
        for notification in system_notifications:
            for user in notification.get_p.all():
                # 只给有邮箱、已激活且邮箱已验证的用户发送
                if user.email and user.is_active and is_email_verified(user):
                    if user.id not in user_notifications:
                        user_notifications[user.id] = {
                            'user': user,
                            'system': [],
                            'comment': []
                        }
                    user_notifications[user.id]['system'].append(notification)
        
        # 汇总评论通知
        for notification in comment_notifications:
            user = notification.get_p
            # 只给有邮箱、已激活且邮箱已验证的用户发送
            if user.email and user.is_active and is_email_verified(user):
                if user.id not in user_notifications:
                    user_notifications[user.id] = {
                        'user': user,
                        'system': [],
                        'comment': []
                    }
                user_notifications[user.id]['comment'].append(notification)
        
        if not user_notifications:
            logger.info('[邮件通知] 没有需要发送邮件的用户（无邮箱、未激活或邮箱未验证）')
            response.data = {
                'message': '没有需要发送邮件的用户（无邮箱、未激活或邮箱未验证）',
                'status': 'success',
                'sent_count': 0
            }
            return response.as_dict()
        
        
        # 6. 发送邮件
        success_count = 0
        failed_count = 0
        failed_users = []
        
        for user_id, data in user_notifications.items():
            user = data['user']
            system_notifs = data['system']
            comment_notifs = data['comment']
            total_count = len(system_notifs) + len(comment_notifs)
            
            try:
                # 辅助函数：移除HTML标签
                def strip_html(text):
                    if not text:
                        return ''
                    # 移除HTML标签
                    text = re.sub(r'<[^>]+>', '', text)
                    # 移除多余空白
                    text = re.sub(r'\s+', ' ', text)
                    return text.strip()
                
                # 构建邮件正文
                subject = f'您有 {total_count} 条未读通知 - {site_settings.site_name}'
                
                # 构建通知列表HTML
                notifications_html = ''
                
                # 系统通知
                if system_notifs:
                    notifications_html += '<div style="margin: 20px 0;"><strong style="color: #1890ff;">系统通知</strong></div>'
                    for notif in system_notifs[:5]:  # 最多显示5条
                        content_text = strip_html(notif.content)[:80]
                        notifications_html += f'''
                        <div style="background: #fff; margin: 10px 0; padding: 12px; border-left: 3px solid #1890ff;">
                            <div style="font-weight: 500; color: #333; margin-bottom: 4px;">{html_escape(notif.title)}</div>
                            <div style="color: #666; font-size: 13px; margin-bottom: 4px;">{html_escape(content_text)}...</div>
                            <div style="color: #999; font-size: 12px;">{notif.create_date.strftime('%m-%d %H:%M')}</div>
                        </div>
                        '''
                    if len(system_notifs) > 5:
                        notifications_html += f'<div style="color: #999; font-size: 13px; margin: 5px 0;">还有 {len(system_notifs) - 5} 条系统通知...</div>'
                
                # 评论通知
                if comment_notifs:
                    notifications_html += '<div style="margin: 20px 0;"><strong style="color: #52c41a;">评论通知</strong></div>'
                    for notif in comment_notifs[:5]:  # 最多显示5条
                        try:
                            comment_content = strip_html(notif.comment.content)[:60] if notif.comment else '评论内容'
                            notifications_html += f'''
                            <div style="background: #fff; margin: 10px 0; padding: 12px; border-left: 3px solid #52c41a;">
                                <div style="font-weight: 500; color: #333; margin-bottom: 4px;">{html_escape(notif.create_p.username)} 评论了您</div>
                                <div style="color: #666; font-size: 13px; margin-bottom: 4px;">{html_escape(comment_content)}...</div>
                                <div style="color: #999; font-size: 12px;">{notif.create_date.strftime('%m-%d %H:%M')}</div>
                            </div>
                            '''
                        except:
                            continue
                    if len(comment_notifs) > 5:
                        notifications_html += f'<div style="color: #999; font-size: 13px; margin: 5px 0;">还有 {len(comment_notifs) - 5} 条评论通知...</div>'
                
                # HTML邮件内容（简约版）
                html_message = f'''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
</head>
<body style="margin: 0; padding: 0; font-family: Arial, sans-serif; background: #f5f5f5;">
    <div style="max-width: 600px; margin: 20px auto; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
        <div style="background: #1890ff; color: #fff; padding: 20px; text-align: center;">
            <h2 style="margin: 0; font-size: 18px;">📬 未读通知提醒</h2>
        </div>
        <div style="padding: 20px;">
            <p style="color: #333; margin: 0 0 15px 0;">您好，<strong>{html_escape(user.username)}</strong>！</p>
            <p style="color: #666; margin: 0 0 20px 0;">您有 <strong style="color: #1890ff;">{total_count}</strong> 条未读通知：</p>
            {notifications_html}
            <div style="text-align: center; margin: 25px 0 10px 0;">
                <a href="{site_full_url()}/comment/notification/no-read/" 
                   style="display: inline-block; padding: 10px 24px; background: #1890ff; color: #fff; text-decoration: none; border-radius: 4px; font-size: 14px;">
                    查看所有通知
                </a>
            </div>
        </div>
        <div style="background: #f5f5f5; padding: 15px; text-align: center; color: #999; font-size: 12px;">
            <p style="margin: 0;">此邮件由系统自动发送，请勿回复</p>
        </div>
    </div>
</body>
</html>
                '''
                
                # 纯文本版本（备用）
                plain_text = f'''
您好，{user.username}！

您有 {total_count} 条未读通知：
'''
                if system_notifs:
                    plain_text += f'\n【系统通知】（{len(system_notifs)} 条）\n'
                    for notif in system_notifs[:5]:
                        content_text = strip_html(notif.content)[:60]
                        plain_text += f'• {notif.title}\n  {content_text}...\n  {notif.create_date.strftime("%m-%d %H:%M")}\n\n'
                
                if comment_notifs:
                    plain_text += f'\n【评论通知】（{len(comment_notifs)} 条）\n'
                    for notif in comment_notifs[:5]:
                        try:
                            comment_content = strip_html(notif.comment.content)[:40] if notif.comment else '评论内容'
                            plain_text += f'• {notif.create_p.username} 评论了您\n  {comment_content}...\n  {notif.create_date.strftime("%m-%d %H:%M")}\n\n'
                        except:
                            continue

                plain_text += '\n请登录系统查看完整通知。\n'
                
                # 发送邮件
                send_mail(
                    subject=subject,
                    message=plain_text,
                    from_email=from_email,
                    recipient_list=[user.email],
                    html_message=html_message,
                    fail_silently=False
                )
                
                success_count += 1
            
                
            except Exception as e:
                failed_count += 1
                failed_users.append({
                    'username': user.username,
                    'email': user.email,
                    'error': str(e)
                })
                logger.error(f'[邮件通知] 发送失败: {user.username} ({user.email}), 错误: {str(e)}')
        
        # 7. 返回结果
        response.data = {
            'message': f'邮件发送完成：成功 {success_count} 封，失败 {failed_count} 封',
            'status': 'completed',
            'total_users': len(user_notifications),
            'success_count': success_count,
            'failed_count': failed_count,
            'failed_users': failed_users[:5],
            'total_system_notifications': sum(len(d['system']) for d in user_notifications.values()),
            'total_comment_notifications': sum(len(d['comment']) for d in user_notifications.values())
        }
        
        
    except Exception as e:
        error_msg = f'发送未读通知邮件失败: {str(e)}'
        logger.error(f'[邮件通知] {error_msg}', exc_info=True)
        response.error = error_msg
    
    return response.as_dict()



@shared_task
def sync_wiki_knowledge_base():
    """
    [定时同步Wiki文章到AI知识库]
    定时同步 Wiki 知识库到 AI 系统
    
    功能：
    - 自动同步所有已发布的文章到 AI 知识库
    - 智能检测内容变化（通过哈希）
    - 只更新有变化的文章，提高效率
    - 记录详细日志
    
    执行频率：建议每小时或每天执行一次（在 Celery Beat 中配置）
    
    Returns:
        dict: 同步结果
    """
    response = TaskResponse()
    
    try:
        from blog.models import Article
        from snowai.models import WikiKnowledgeBase
        from django.db import transaction
        import hashlib
        
        logger.info('[Wiki同步] 开始定时同步 Wiki 知识库...')
        
        # 获取所有已发布的文章
        articles = Article.objects.filter(is_publish=True).select_related('category')
        total = articles.count()
        
        if total == 0:
            logger.info('[Wiki同步] 没有需要同步的文章')
            response.data = {
                'message': '没有需要同步的文章',
                'status': 'success',
                'total': 0
            }
            return response.as_dict()
        
        logger.info(f'[Wiki同步] 共找到 {total} 篇已发布文章')
        
        synced = 0
        updated = 0
        skipped = 0
        failed = 0
        failed_articles = []
        
        for article in articles:
            try:
                with transaction.atomic():
                    # 提取文章内容（移除 HTML 标签）
                    content = strip_tags(article.body)
                    
                    # 生成内容哈希
                    content_hash = hashlib.md5(content.encode()).hexdigest()
                    
                    # 生成摘要
                    max_length = 200
                    summary = content[:max_length] + '...' if len(content) > max_length else content
                    
                    # 提取标签
                    try:
                        tags = [tag.name for tag in article.tags.all()]
                    except:
                        tags = []
                    
                    # 检查是否已存在
                    kb_article, created = WikiKnowledgeBase.objects.get_or_create(
                        article_id=article.id,
                        defaults={
                            'title': article.title,
                            'content': content,
                            'summary': summary,
                            'category': article.category.name if article.category else '',
                            'tags': tags,
                            'url': site_full_url() + article.get_absolute_url(),
                            'content_hash': content_hash,
                            'is_indexed': True
                        }
                    )
                    
                    if created:
                        synced += 1
                        logger.info(f'[Wiki同步] ✓ 新增: {article.title}')
                    else:
                        # 检查内容是否变化
                        if kb_article.content_hash != content_hash:
                            kb_article.title = article.title
                            kb_article.content = content
                            kb_article.summary = summary
                            kb_article.category = article.category.name if article.category else ''
                            kb_article.tags = tags
                            kb_article.url = site_full_url() + article.get_absolute_url()
                            kb_article.content_hash = content_hash
                            kb_article.is_indexed = True
                            kb_article.save()
                            updated += 1
                            logger.info(f'[Wiki同步] ↻ 更新: {article.title}')
                        else:
                            skipped += 1
            
            except Exception as e:
                failed += 1
                failed_articles.append({
                    'title': article.title,
                    'id': article.id,
                    'error': str(e)
                })
                logger.error(f'[Wiki同步] ✗ 错误: {article.title} - {str(e)}')
        
        response.data = {
            'message': f'同步完成：新增 {synced} 篇，更新 {updated} 篇，跳过 {skipped} 篇，失败 {failed} 篇',
            'status': 'completed',
            'total': total,
            'synced': synced,
            'updated': updated,
            'skipped': skipped,
            'failed': failed,
            'failed_articles': failed_articles[:5]  # 最多显示5个失败的
        }
        
        logger.info(
            f'[Wiki同步] 同步完成: '
            f'总数={total}, 新增={synced}, 更新={updated}, 跳过={skipped}, 失败={failed}'
        )
        
    except Exception as e:
        error_msg = f'Wiki知识库同步失败: {str(e)}'
        logger.error(f'[Wiki同步] {error_msg}', exc_info=True)
        response.error = error_msg
    
    return response.as_dict()



@shared_task
def sync_external_knowledge_sources(source_id=None):
    """
    [定时同步外部知识源到AI知识库]
    定时同步外部知识源（Sitemap、RSS等）到 AI 知识库
    
    功能：
    - 自动从外部网站抓取内容
    - 支持 Sitemap、RSS 等多种来源
    - 智能检测内容变化
    - 只更新有改动的页面
    - 记录详细日志
    
    Args:
        source_id: 指定要同步的知识源 ID，None 则同步所有启用的知识源
    
    执行频率：建议每天执行一次（在 Celery Beat 中配置）
    
    Returns:
        dict: 同步结果
    """
    response = TaskResponse()
    
    try:
        from snowai.models import ExternalKnowledgeSource, WikiKnowledgeBase
        from bs4 import BeautifulSoup
        import xml.etree.ElementTree as ET
        import requests
        import hashlib
        
        logger.info('[外部知识源] 开始同步外部知识源...')
        
        # 获取要同步的知识源
        if source_id:
            sources = ExternalKnowledgeSource.objects.filter(id=source_id)
        else:
            sources = ExternalKnowledgeSource.objects.filter(
                is_active=True,
                auto_sync=True
            )
        
        if not sources.exists():
            logger.info('[外部知识源] 没有需要同步的知识源')
            response.data = {
                'message': '没有需要同步的知识源',
                'status': 'success',
                'total': 0
            }
            return response.as_dict()
        
        logger.info(f'[外部知识源] 共找到 {sources.count()} 个知识源')
        
        total_sources = sources.count()
        success_sources = 0
        failed_sources = 0
        total_synced = 0
        total_updated = 0
        total_failed = 0
        results = []
        
        for source in sources:
            try:
                logger.info(f'[外部知识源] 处理: {source.name} ({source.get_source_type_display()})')
                
                if source.source_type in ['sitemap', 'sitemap_markdown']:
                    synced, updated, failed = process_sitemap_source(source)
                elif source.source_type == 'markdown_list':
                    synced, updated, failed = process_markdown_list_source(source)
                elif source.source_type == 'rss':
                    synced, updated, failed = process_rss_source(source)
                else:
                    logger.warning(f'[外部知识源] 暂不支持的类型: {source.source_type}')
                    continue
                
                # 更新统计信息
                source.synced_pages = WikiKnowledgeBase.objects.filter(
                    external_source=source
                ).count()
                source.last_sync_time = timezone.now()
                source.last_sync_status = f'成功: 新增{synced}, 更新{updated}, 失败{failed}'
                source.save()
                
                success_sources += 1
                total_synced += synced
                total_updated += updated
                total_failed += failed
                
                results.append({
                    'source': source.name,
                    'status': 'success',
                    'synced': synced,
                    'updated': updated,
                    'failed': failed
                })
                
                logger.info(
                    f'[外部知识源] ✓ {source.name}: '
                    f'新增 {synced}, 更新 {updated}, 失败 {failed}'
                )
                
            except Exception as e:
                failed_sources += 1
                error_msg = str(e)
                
                source.last_sync_status = f'失败: {error_msg[:100]}'
                source.last_sync_time = timezone.now()
                source.save()
                
                results.append({
                    'source': source.name,
                    'status': 'error',
                    'error': error_msg
                })
                
                logger.error(f'[外部知识源] ✗ {source.name}: {error_msg}', exc_info=True)
        
        response.data = {
            'message': f'同步完成：成功 {success_sources} 个，失败 {failed_sources} 个',
            'status': 'completed',
            'total_sources': total_sources,
            'success_sources': success_sources,
            'failed_sources': failed_sources,
            'total_synced': total_synced,
            'total_updated': total_updated,
            'total_failed': total_failed,
            'results': results
        }
        
        logger.info(
            f'[外部知识源] 同步完成: '
            f'知识源={success_sources}/{total_sources}, '
            f'页面: 新增={total_synced}, 更新={total_updated}, 失败={total_failed}'
        )
        
    except Exception as e:
        error_msg = f'外部知识源同步失败: {str(e)}'
        logger.error(f'[外部知识源] {error_msg}', exc_info=True)
        response.error = error_msg
    
    return response.as_dict()


def process_sitemap_source(source):
    """处理 Sitemap 类型的知识源"""
    from snowai.models import WikiKnowledgeBase
    from bs4 import BeautifulSoup
    import xml.etree.ElementTree as ET
    import requests
    import hashlib
    import time
    import re
    
    # 获取 Sitemap
    response = requests.get(source.url, timeout=30, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    response.raise_for_status()
    
    # 解析 Sitemap XML
    root = ET.fromstring(response.content)
    namespaces = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
    urls = []
    
    # 解析标准 sitemap
    for url_elem in root.findall('.//ns:url', namespaces):
        loc = url_elem.find('ns:loc', namespaces)
        if loc is not None and loc.text:
            urls.append(loc.text)
    
    # 如果没有找到，尝试解析 sitemap index
    if not urls:
        for sitemap_elem in root.findall('.//ns:sitemap', namespaces):
            loc = sitemap_elem.find('ns:loc', namespaces)
            if loc is not None and loc.text:
                # 递归处理子 sitemap
                child_response = requests.get(loc.text, timeout=30, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                })
                child_root = ET.fromstring(child_response.content)
                for url_elem in child_root.findall('.//ns:url', namespaces):
                    child_loc = url_elem.find('ns:loc', namespaces)
                    if child_loc is not None and child_loc.text:
                        urls.append(child_loc.text)
    
    if not urls:
        raise Exception('Sitemap 中没有找到任何 URL')
    
    # 更新总页面数
    source.total_pages = len(urls)
    source.save()
    
    logger.info(f'[外部知识源] 找到 {len(urls)} 个页面，使用{"Markdown源文件" if source.use_raw_markdown else "HTML解析"}模式')
    
    # 抓取每个页面
    synced = 0
    updated = 0
    failed = 0
    
    for i, url in enumerate(urls, 1):
        try:
            # 检查是否已存在
            existing = WikiKnowledgeBase.objects.filter(
                source_type='external',
                url=url
            ).first()
            
            # 如果配置了使用原始 Markdown，直接获取 MD 文件
            if source.use_raw_markdown and source.markdown_url_pattern:
                title, content = fetch_markdown_content(url, source)
            else:
                # 原有的 HTML 解析逻辑
                title, content = fetch_html_content(url, source)
            
            if not title or not content:
                logger.warning(f'[外部知识源] 页面内容为空，跳过: {url}')
                failed += 1
                continue
            
            # 生成内容哈希
            content_hash = hashlib.md5(content.encode()).hexdigest()
            
            # 生成摘要（从Markdown内容中提取纯文本）
            summary = generate_summary(content)
            
            # 如果已存在，检查是否需要更新
            if existing:
                if existing.content_hash == content_hash:
                    # 内容没有变化，跳过
                    continue
                
                # 更新
                existing.title = title
                existing.content = content
                existing.summary = summary
                existing.content_hash = content_hash
                existing.is_indexed = True  # 标记为已索引（关键词搜索无需额外索引）
                existing.save()
                updated += 1
                logger.debug(f'[外部知识源] 更新: {title}')
            else:
                # 新增
                WikiKnowledgeBase.objects.create(
                    source_type='external',
                    external_source=source,
                    title=title,
                    content=content,
                    summary=summary,
                    url=url,
                    content_hash=content_hash,
                    is_indexed=True  # 标记为已索引（关键词搜索无需额外索引）
                )
                synced += 1
                logger.debug(f'[外部知识源] 新增: {title}')
            
            # 避免请求过快
            if i % 10 == 0:
                logger.info(f'[外部知识源] 进度: {i}/{len(urls)}')
                time.sleep(2)
            else:
                time.sleep(0.5)
            
        except Exception as e:
            logger.error(f'[外部知识源] 抓取页面失败: {url}, 错误: {str(e)}')
            failed += 1
    
    return synced, updated, failed


def fetch_markdown_content(page_url, source):
    """
    获取原始 Markdown 内容
    
    Args:
        page_url: 页面 URL
        source: 知识源对象
    
    Returns:
        tuple: (title, content)
    """
    import requests
    import re
    
    # 构建 Markdown 文件 URL
    # 支持的模式：
    # - {url}/raw  -> 将页面URL后加 /raw
    # - {url}.md   -> 将页面URL后加 .md
    # - 自定义模式
    markdown_url = source.markdown_url_pattern.replace('{url}', page_url)
    
    logger.debug(f'[外部知识源] 获取Markdown: {markdown_url}')
    
    # 获取 Markdown 内容
    response = requests.get(markdown_url, timeout=30, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    response.raise_for_status()
    
    # 确保使用正确的编码
    response.encoding = response.apparent_encoding or 'utf-8'
    markdown_content = response.text
    
    # 从 Markdown 内容中提取标题
    title = extract_title_from_markdown(markdown_content, page_url)
    
    return title, markdown_content


def fetch_html_content(page_url, source):
    """
    从 HTML 页面提取内容并转换为 Markdown 格式
    
    Args:
        page_url: 页面 URL
        source: 知识源对象
    
    Returns:
        tuple: (title, content)
    """
    import requests
    from bs4 import BeautifulSoup
    
    # 抓取页面内容
    page_response = requests.get(page_url, timeout=30, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    page_response.raise_for_status()
    
    # 解析 HTML
    soup = BeautifulSoup(page_response.content, 'html.parser')
    
    # 提取标题
    title = ''
    if source.title_selector:
        title_elem = soup.select_one(source.title_selector)
        if title_elem:
            title = title_elem.get_text(strip=True)
    
    if not title:
        title_tag = soup.find('title')
        if title_tag:
            title = title_tag.get_text(strip=True)
        else:
            title = page_url.split('/')[-1] or 'Untitled'
    
    # 提取内容
    content = ''
    content_elem = None
    
    if source.content_selector:
        content_elem = soup.select_one(source.content_selector)
        if content_elem:
            # 移除排除的元素
            if source.exclude_selectors:
                for selector in source.exclude_selectors.strip().split('\n'):
                    selector = selector.strip()
                    if selector:
                        for elem in content_elem.select(selector):
                            elem.decompose()
    
    if not content_elem:
        # 备选方案：获取 body 内容
        content_elem = soup.find('body')
        if content_elem:
            # 移除不需要的标签
            for script in content_elem(['script', 'style', 'nav', 'header', 'footer']):
                script.decompose()
    
    # 转换为 Markdown 格式（保留代码块等格式）
    if content_elem:
        content = html_to_markdown(content_elem)
    
    return title, content


def extract_title_from_markdown(markdown_content, fallback_url=''):
    """
    从 Markdown 内容中提取标题
    
    Args:
        markdown_content: Markdown 文本
        fallback_url: 备用URL（提取文件名作为标题）
    
    Returns:
        str: 标题
    """
    import re
    
    lines = markdown_content.split('\n')
    
    # 优先查找 YAML Front Matter 中的 title
    if lines and lines[0].strip() == '---':
        in_front_matter = True
        for line in lines[1:]:
            if line.strip() == '---':
                break
            if line.strip().startswith('title:'):
                title = line.split('title:', 1)[1].strip()
                # 移除可能的引号
                title = title.strip('"\'')
                if title:
                    return title
    
    # 查找第一个 # 标题
    for line in lines:
        line = line.strip()
        if line.startswith('# '):
            title = line.lstrip('#').strip()
            if title:
                return title
    
    # 备用方案：从 URL 提取文件名
    if fallback_url:
        filename = fallback_url.rstrip('/').split('/')[-1]
        # 移除可能的文件扩展名和特殊字符
        title = re.sub(r'\.(html?|md|markdown)$', '', filename, flags=re.IGNORECASE)
        title = title.replace('-', ' ').replace('_', ' ')
        if title:
            return title
    
    return 'Untitled'


def generate_summary(content, max_length=200):
    """
    生成内容摘要
    
    Args:
        content: 原始内容（可能是Markdown）
        max_length: 最大长度
    
    Returns:
        str: 摘要
    """
    import re
    
    # 移除 Markdown 语法
    # 移除代码块
    text = re.sub(r'```[\s\S]*?```', '', content)
    # 移除行内代码
    text = re.sub(r'`[^`]+`', '', text)
    # 移除链接
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    # 移除图片
    text = re.sub(r'!\[([^\]]*)\]\([^\)]+\)', '', text)
    # 移除标题标记
    text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)
    # 移除加粗和斜体
    text = re.sub(r'\*\*([^\*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^\*]+)\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    text = re.sub(r'_([^_]+)_', r'\1', text)
    # 移除列表标记
    text = re.sub(r'^[\*\-\+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)
    # 移除多余的空白
    text = re.sub(r'\n\s*\n', '\n', text)
    text = text.strip()
    
    # 截取指定长度
    if len(text) > max_length:
        return text[:max_length] + '...'
    return text


def html_to_markdown(soup_or_html):
    """
    将 HTML 转换为 Markdown 格式
    保留代码块、列表、标题等结构
    
    Args:
        soup_or_html: BeautifulSoup 对象或 HTML 字符串
    
    Returns:
        str: Markdown 格式的文本
    """
    from bs4 import BeautifulSoup, NavigableString
    import re
    
    if isinstance(soup_or_html, str):
        soup = BeautifulSoup(soup_or_html, 'html.parser')
    else:
        soup = soup_or_html
    
    def process_element(element, depth=0):
        """递归处理元素"""
        if isinstance(element, NavigableString):
            text = str(element).strip()
            # 保留必要的空格
            if text:
                return text
            return ''
        
        tag_name = element.name
        result = []
        
        # 处理不同的 HTML 标签
        if tag_name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            # 标题
            level = int(tag_name[1])
            text = element.get_text(strip=True)
            result.append('\n' + '#' * level + ' ' + text + '\n')
            
        elif tag_name == 'pre':
            # 代码块
            code = element.find('code')
            if code:
                # 尝试获取语言类型
                lang = ''
                if code.get('class'):
                    for cls in code.get('class', []):
                        if cls.startswith('language-'):
                            lang = cls.replace('language-', '')
                            break
                code_text = code.get_text()
                result.append(f'\n```{lang}\n{code_text}\n```\n')
            else:
                code_text = element.get_text()
                result.append(f'\n```\n{code_text}\n```\n')
                
        elif tag_name == 'code' and element.parent.name != 'pre':
            # 行内代码
            code_text = element.get_text()
            result.append(f'`{code_text}`')
            
        elif tag_name == 'blockquote':
            # 引用
            lines = element.get_text(strip=True).split('\n')
            quoted = '\n'.join('> ' + line for line in lines)
            result.append('\n' + quoted + '\n')
            
        elif tag_name in ['ul', 'ol']:
            # 列表
            result.append('\n')
            for i, li in enumerate(element.find_all('li', recursive=False), 1):
                if tag_name == 'ul':
                    prefix = '- '
                else:
                    prefix = f'{i}. '
                text = process_element(li, depth + 1).strip()
                result.append(prefix + text + '\n')
            result.append('\n')
            
        elif tag_name == 'li' and element.parent.name not in ['ul', 'ol']:
            # 单独的 li（不在 ul/ol 中）
            for child in element.children:
                result.append(process_element(child, depth))
                
        elif tag_name == 'a':
            # 链接
            text = element.get_text(strip=True)
            href = element.get('href', '')
            if href:
                result.append(f'[{text}]({href})')
            else:
                result.append(text)
                
        elif tag_name == 'img':
            # 图片
            alt = element.get('alt', '')
            src = element.get('src', '')
            result.append(f'![{alt}]({src})')
            
        elif tag_name in ['strong', 'b']:
            # 加粗
            text = element.get_text(strip=True)
            result.append(f'**{text}**')
            
        elif tag_name in ['em', 'i']:
            # 斜体
            text = element.get_text(strip=True)
            result.append(f'*{text}*')
            
        elif tag_name == 'hr':
            # 分隔线
            result.append('\n---\n')
            
        elif tag_name in ['p', 'div', 'section', 'article']:
            # 段落和块级元素
            for child in element.children:
                result.append(process_element(child, depth))
            result.append('\n\n')
            
        elif tag_name == 'br':
            # 换行
            result.append('\n')
            
        elif tag_name in ['table']:
            # 表格 - 简化处理，转为文本
            result.append('\n' + element.get_text(separator=' | ', strip=True) + '\n')
            
        else:
            # 其他标签，递归处理子元素
            for child in element.children:
                result.append(process_element(child, depth))
        
        return ''.join(result)
    
    # 处理整个文档
    markdown = process_element(soup)
    
    # 清理多余的空行
    markdown = re.sub(r'\n\s*\n\s*\n+', '\n\n', markdown)
    markdown = markdown.strip()
    
    return markdown


def process_markdown_list_source(source):
    """
    处理 Markdown 文件列表类型的知识源
    URL 应该指向一个包含 Markdown 文件链接列表的页面
    """
    from snowai.models import WikiKnowledgeBase
    from bs4 import BeautifulSoup
    import requests
    import time
    
    logger.info(f'[外部知识源] 获取Markdown文件列表: {source.url}')
    
    # 获取列表页面
    response = requests.get(source.url, timeout=30, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    response.raise_for_status()
    
    # 解析HTML，提取所有.md链接
    soup = BeautifulSoup(response.content, 'html.parser')
    md_links = []
    
    for link in soup.find_all('a', href=True):
        href = link['href']
        if href.endswith('.md') or href.endswith('.markdown'):
            # 处理相对链接
            if href.startswith('http'):
                md_links.append(href)
            else:
                # 构建完整URL
                from urllib.parse import urljoin
                full_url = urljoin(source.url, href)
                md_links.append(full_url)
    
    if not md_links:
        raise Exception('页面中没有找到任何 Markdown 文件链接')
    
    logger.info(f'[外部知识源] 找到 {len(md_links)} 个Markdown文件')
    
    # 更新总页面数
    source.total_pages = len(md_links)
    source.save()
    
    synced = 0
    updated = 0
    failed = 0
    
    for i, md_url in enumerate(md_links, 1):
        try:
            # 检查是否已存在
            existing = WikiKnowledgeBase.objects.filter(
                source_type='external',
                url=md_url
            ).first()
            
            # 获取Markdown内容
            logger.debug(f'[外部知识源] 获取: {md_url}')
            md_response = requests.get(md_url, timeout=30, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            md_response.raise_for_status()
            md_response.encoding = md_response.apparent_encoding or 'utf-8'
            markdown_content = md_response.text
            
            # 提取标题
            title = extract_title_from_markdown(markdown_content, md_url)
            
            if not title or not markdown_content:
                logger.warning(f'[外部知识源] 内容为空，跳过: {md_url}')
                failed += 1
                continue
            
            # 生成内容哈希
            import hashlib
            content_hash = hashlib.md5(markdown_content.encode()).hexdigest()
            
            # 生成摘要
            summary = generate_summary(markdown_content)
            
            # 如果已存在，检查是否需要更新
            if existing:
                if existing.content_hash == content_hash:
                    continue
                
                # 更新
                existing.title = title
                existing.content = markdown_content
                existing.summary = summary
                existing.content_hash = content_hash
                existing.is_indexed = True  # 标记为已索引
                existing.save()
                updated += 1
                logger.debug(f'[外部知识源] 更新: {title}')
            else:
                # 新增
                WikiKnowledgeBase.objects.create(
                    source_type='external',
                    external_source=source,
                    title=title,
                    content=markdown_content,
                    summary=summary,
                    url=md_url,
                    content_hash=content_hash,
                    is_indexed=True  # 标记为已索引
                )
                synced += 1
                logger.debug(f'[外部知识源] 新增: {title}')
            
            # 避免请求过快
            if i % 10 == 0:
                logger.info(f'[外部知识源] 进度: {i}/{len(md_links)}')
                time.sleep(2)
            else:
                time.sleep(0.5)
                
        except Exception as e:
            logger.error(f'[外部知识源] 处理Markdown文件失败: {md_url}, 错误: {str(e)}')
            failed += 1
    
    return synced, updated, failed


def process_rss_source(source):
    """
    处理 RSS 类型的知识源
    支持 RSS 2.0 和 Atom 格式
    """
    from snowai.models import WikiKnowledgeBase
    from bs4 import BeautifulSoup
    import xml.etree.ElementTree as ET
    import requests
    import hashlib
    import time
    
    logger.info(f'[外部知识源] 获取 RSS Feed: {source.url}')
    
    # 获取 RSS Feed
    response = requests.get(source.url, timeout=30, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    response.raise_for_status()
    response.encoding = response.apparent_encoding or 'utf-8'
    
    # 解析 XML
    root = ET.fromstring(response.content)
    
    # 检测 RSS 类型
    items = []
    
    # RSS 2.0 格式
    if root.tag == 'rss' or root.find('channel') is not None:
        channel = root.find('channel')
        if channel is not None:
            for item in channel.findall('item'):
                title_elem = item.find('title')
                link_elem = item.find('link')
                desc_elem = item.find('description')
                
                if title_elem is not None and link_elem is not None:
                    items.append({
                        'title': title_elem.text or '',
                        'link': link_elem.text or '',
                        'description': desc_elem.text if desc_elem is not None else ''
                    })
    
    # Atom 格式
    elif root.tag.endswith('feed'):
        namespaces = {'atom': 'http://www.w3.org/2005/Atom'}
        for entry in root.findall('atom:entry', namespaces):
            title_elem = entry.find('atom:title', namespaces)
            link_elem = entry.find('atom:link[@rel="alternate"]', namespaces)
            if link_elem is None:
                link_elem = entry.find('atom:link', namespaces)
            content_elem = entry.find('atom:content', namespaces)
            summary_elem = entry.find('atom:summary', namespaces)
            
            if title_elem is not None and link_elem is not None:
                link = link_elem.get('href', '')
                description = ''
                if content_elem is not None:
                    description = content_elem.text or ''
                elif summary_elem is not None:
                    description = summary_elem.text or ''
                
                items.append({
                    'title': title_elem.text or '',
                    'link': link,
                    'description': description
                })
    
    if not items:
        raise Exception('RSS Feed 中没有找到任何条目')
    
    logger.info(f'[外部知识源] 找到 {len(items)} 个条目')
    
    # 更新总页面数
    source.total_pages = len(items)
    source.save()
    
    synced = 0
    updated = 0
    failed = 0
    
    for i, item in enumerate(items, 1):
        try:
            article_url = item['link']
            article_title = item['title']
            
            # 检查是否已存在
            existing = WikiKnowledgeBase.objects.filter(
                source_type='external',
                url=article_url
            ).first()
            
            # 获取文章内容
            # 如果配置了 Markdown 源文件模式，尝试获取 MD
            if source.use_raw_markdown and source.markdown_url_pattern:
                try:
                    title, content = fetch_markdown_content(article_url, source)
                except Exception as e:
                    logger.warning(f'[外部知识源] 无法获取 Markdown，降级为 HTML 解析: {str(e)}')
                    # 降级处理：从 description 或页面获取内容
                    title, content = fetch_content_from_rss_item(item, article_url, source)
            else:
                # 直接从 RSS 的 description 或抓取页面
                title, content = fetch_content_from_rss_item(item, article_url, source)
            
            if not title or not content:
                logger.warning(f'[外部知识源] 内容为空，跳过: {article_url}')
                failed += 1
                continue
            
            # 生成内容哈希
            content_hash = hashlib.md5(content.encode()).hexdigest()
            
            # 生成摘要
            summary = generate_summary(content)
            
            # 如果已存在，检查是否需要更新
            if existing:
                if existing.content_hash == content_hash:
                    continue
                
                # 更新
                existing.title = title
                existing.content = content
                existing.summary = summary
                existing.content_hash = content_hash
                existing.is_indexed = True  # 标记为已索引
                existing.save()
                updated += 1
                logger.debug(f'[外部知识源] 更新: {title}')
            else:
                # 新增
                WikiKnowledgeBase.objects.create(
                    source_type='external',
                    external_source=source,
                    title=title,
                    content=content,
                    summary=summary,
                    url=article_url,
                    content_hash=content_hash,
                    is_indexed=True  # 标记为已索引
                )
                synced += 1
                logger.debug(f'[外部知识源] 新增: {title}')
            
            # 避免请求过快
            if i % 10 == 0:
                logger.info(f'[外部知识源] 进度: {i}/{len(items)}')
                time.sleep(2)
            else:
                time.sleep(0.5)
                
        except Exception as e:
            logger.error(f'[外部知识源] 处理 RSS 条目失败: {item.get("link", "")}, 错误: {str(e)}')
            failed += 1
    
    return synced, updated, failed


def fetch_content_from_rss_item(item, article_url, source):
    """
    从 RSS 条目获取内容
    
    优先级：
    1. 如果 description 中有足够的 HTML 内容，转换为 Markdown
    2. 否则抓取文章页面
    
    Args:
        item: RSS 条目 dict
        article_url: 文章 URL
        source: 知识源对象
    
    Returns:
        tuple: (title, content)
    """
    import requests
    from bs4 import BeautifulSoup
    
    title = item['title']
    description = item.get('description', '')
    
    # 如果 description 包含 HTML 且长度足够，转换为 Markdown
    if description and len(description) > 200:
        soup = BeautifulSoup(description, 'html.parser')
        
        # 移除脚本和样式
        for script in soup(['script', 'style']):
            script.decompose()
        
        # 转换为 Markdown 格式（保留代码块等格式）
        markdown_content = html_to_markdown(soup)
        
        # 如果提取的内容足够长，就使用它
        if len(markdown_content.strip()) > 100:
            return title, markdown_content
    
    # description 内容不够，抓取完整页面
    logger.debug(f'[外部知识源] RSS description 内容不足，抓取完整页面: {article_url}')
    
    try:
        page_title, page_content = fetch_html_content(article_url, source)
        # 优先使用页面标题，如果没有才用 RSS 标题
        return page_title or title, page_content
    except Exception as e:
        logger.warning(f'[外部知识源] 抓取页面失败，使用 description: {str(e)}')
        # 降级：使用 RSS 的 description
        if description:
            soup = BeautifulSoup(description, 'html.parser')
            markdown_content = html_to_markdown(soup)
            return title, markdown_content or description
        
        raise Exception('无法获取文章内容')



@shared_task
def sync_ctf_writeups():
    """
    [定时同步CTF公开题解到AI知识库]
    定时同步 CTF 靶场公开题解到 AI 知识库
    
    功能：
    - 自动同步所有公开的 CTF 题解（writeup_is_public=True）
    - 只同步免费题解，付费题解不导入
    - 智能检测内容变化（通过哈希）
    - 只更新有变化的题解，提高效率
    - 记录详细日志
    
    执行频率：建议每小时或每天执行一次（在 Celery Beat 中配置）
    
    Returns:
        dict: 同步结果
    """
    response = TaskResponse()
    
    try:
        from practice.models import PC_Challenge
        from snowai.models import WikiKnowledgeBase
        from django.db import transaction
        import hashlib
        
        logger.info('[CTF题解同步] 开始定时同步 CTF 公开题解...')
        
        # 获取所有公开的题解
        # 条件：1. 题目激活 2. 题解公开 3. 题解内容不为空
        challenges = PC_Challenge.objects.filter(
            is_active=True,
            writeup_is_public=True,
            hint__isnull=False
        ).exclude(hint='')
        
        total = challenges.count()
        
        if total == 0:
            logger.info('[CTF题解同步] 没有需要同步的公开题解')
            response.data = {
                'message': '没有需要同步的公开题解',
                'status': 'success',
                'total': 0
            }
            return response.as_dict()
        
        logger.info(f'[CTF题解同步] 共找到 {total} 个公开题解')
        
        synced = 0
        updated = 0
        skipped = 0
        failed = 0
        failed_challenges = []
        
        for challenge in challenges:
            try:
                with transaction.atomic():
                    # 提取题解内容（已经是 Markdown 格式）
                    content = challenge.hint
                    
                    # 生成内容哈希
                    content_hash = hashlib.md5(content.encode()).hexdigest()
                    
                    # 生成标题（包含题目类型和难度信息）
                    title = f"[靶场题目] {challenge.title} ({challenge.get_category_display()} - {challenge.get_difficulty_display()})"
                    
                    # 生成摘要
                    max_length = 200
                    # 从 markdown 内容生成摘要
                    summary = strip_tags(content)[:max_length] + '...' if len(strip_tags(content)) > max_length else strip_tags(content)
                    
                    # 提取标签
                    try:
                        tags = [tag.name for tag in challenge.tags.all()]
                    except:
                        tags = []
                    
                    # 添加题目分类和难度作为标签
                    tags.extend([challenge.category, challenge.difficulty])
                    
                    # 生成完整 URL
                    full_url = site_full_url() + challenge.get_absolute_url()
                    
                    # 检查是否已存在
                    kb_challenge, created = WikiKnowledgeBase.objects.get_or_create(
                        ctf_challenge_uuid=challenge.uuid,
                        defaults={
                            'source_type': 'ctf_writeup',
                            'title': title,
                            'content': content,
                            'summary': summary,
                            'category': f'{challenge.category}',
                            'tags': tags,
                            'url': full_url,
                            'content_hash': content_hash,
                            'is_indexed': True
                        }
                    )
                    
                    if created:
                        synced += 1
                        logger.info(f'[CTF题解同步] ✓ 新增: {challenge.title}')
                    else:
                        # 检查内容是否变化
                        if kb_challenge.content_hash != content_hash:
                            kb_challenge.title = title
                            kb_challenge.content = content
                            kb_challenge.summary = summary
                            kb_challenge.category = f'{challenge.category}'
                            kb_challenge.tags = tags
                            kb_challenge.url = full_url
                            kb_challenge.content_hash = content_hash
                            kb_challenge.is_indexed = True
                            kb_challenge.save()
                            updated += 1
                            logger.info(f'[CTF题解同步] ↻ 更新: {challenge.title}')
                        else:
                            skipped += 1
            
            except Exception as e:
                failed += 1
                failed_challenges.append({
                    'title': challenge.title,
                    'uuid': str(challenge.uuid),
                    'error': str(e)
                })
                logger.error(f'[CTF题解同步] ✗ 错误: {challenge.title} - {str(e)}')
        
        response.data = {
            'message': f'同步完成：新增 {synced} 个，更新 {updated} 个，跳过 {skipped} 个，失败 {failed} 个',
            'status': 'completed',
            'total': total,
            'synced': synced,
            'updated': updated,
            'skipped': skipped,
            'failed': failed,
            'failed_challenges': failed_challenges[:5]  # 最多显示5个失败的
        }
        
        logger.info(
            f'[CTF题解同步] 同步完成: '
            f'总数={total}, 新增={synced}, 更新={updated}, 跳过={skipped}, 失败={failed}'
        )
        
    except Exception as e:
        error_msg = f'CTF题解同步失败: {str(e)}'
        logger.error(f'[CTF题解同步] {error_msg}', exc_info=True)
        response.error = error_msg
    
    return response.as_dict()



@shared_task
def cleanup_k8s_stale_pods():
    """
    清理 K8s 中的僵尸 Pod（定时任务，每 5 分钟执行）
    
    功能：
    1. 清理 Failed 状态的 Pod
    2. 清理超时 Pending（>5分钟）的 Pod
    3. 清理 Unknown 状态的 Pod
    4. 释放 requests 资源配额
    """
    from container.models import DockerEngine
    from container.k8s_service import K8sService
    from datetime import timedelta
    
    total_cleaned = 0
    errors = []
    
    # 获取所有 K8s 引擎
    k8s_engines = DockerEngine.objects.filter(
        engine_type='KUBERNETES',
        is_active=True
    )
    
    logger.info(f"开始清理 K8s 僵尸 Pod，共 {k8s_engines.count()} 个集群")
    
    for engine in k8s_engines:
        try:
            # 创建 K8s 服务实例
            k8s_service = K8sService(engine)
            
            # 获取所有问题 Pod
            namespace = k8s_service.namespace
            
            # 1. 清理 Failed Pod
            failed_pods = k8s_service.core_api.list_namespaced_pod(
                namespace=namespace,
                field_selector='status.phase=Failed'
            )
            
            # 2. 清理超时 Pending Pod
            pending_pods = k8s_service.core_api.list_namespaced_pod(
                namespace=namespace,
                field_selector='status.phase=Pending'
            )
            
            # 过滤超时的 Pending Pod（>5分钟）
            timeout_threshold = timezone.now() - timedelta(minutes=5)
            timed_out_pending = [
                pod for pod in pending_pods.items
                if pod.metadata.creation_timestamp and 
                   pod.metadata.creation_timestamp < timeout_threshold
            ]
            
            # 3. 清理 Unknown Pod
            unknown_pods = k8s_service.core_api.list_namespaced_pod(
                namespace=namespace,
                field_selector='status.phase=Unknown'
            )
            
            # 合并要清理的 Pod
            pods_to_clean = (
                list(failed_pods.items) + 
                timed_out_pending + 
                list(unknown_pods.items)
            )
            
            if pods_to_clean:
                logger.info(
                    f"集群 {engine.name} 发现 {len(pods_to_clean)} 个僵尸 Pod: "
                    f"Failed={len(failed_pods.items)}, "
                    f"Pending超时={len(timed_out_pending)}, "
                    f"Unknown={len(unknown_pods.items)}"
                )
                
                # 批量删除
                for pod in pods_to_clean:
                    try:
                        pod_name = pod.metadata.name
                        k8s_service.core_api.delete_namespaced_pod(
                            name=pod_name,
                            namespace=namespace,
                            grace_period_seconds=0  # 立即删除
                        )
                        
                        # 同时删除关联的 Service
                        service_name = f"{pod_name}-svc"
                        try:
                            k8s_service.core_api.delete_namespaced_service(
                                name=service_name,
                                namespace=namespace
                            )
                        except:
                            pass  # Service 可能不存在
                        
                        total_cleaned += 1
                        logger.debug(f"清理僵尸 Pod: {pod_name}")
                        
                    except Exception as e:
                        error_msg = f"清理 Pod {pod_name} 失败: {str(e)}"
                        errors.append(error_msg)
                        logger.warning(error_msg)
                
                logger.info(f"集群 {engine.name} 清理完成")
            else:
                logger.debug(f"集群 {engine.name} 无需清理")
                
        except Exception as e:
            error_msg = f"清理集群 {engine.name} 失败: {str(e)}"
            errors.append(error_msg)
            logger.error(error_msg, exc_info=True)
    
    result = {
        'total_cleaned': total_cleaned,
        'clusters_checked': k8s_engines.count(),
        'errors': errors
    }
    
    logger.info(f"K8s 僵尸 Pod 清理完成: {result}")
    return result



@shared_task
def optimize_k8s_resources():
    """
    K8s 资源优化任务（定时任务，每 10 分钟执行）
    
    功能：
    1. 清理客户端连接池（释放内存）
    2. 清理过期缓存
    3. 统计资源使用情况
    """
    from container.k8s_client_pool import K8sClientPool
    
    result = {
        'pool_status_before': K8sClientPool.get_pool_status(),
        'actions': []
    }
    
    # 记录连接池状态
    logger.info(f"K8s 连接池状态: {result['pool_status_before']}")
    
    # 如果连接池过大（>10），考虑清理
    if result['pool_status_before']['pool_size'] > 10:
        logger.warning(f"K8s 连接池过大，建议检查是否有配置变更")
        result['actions'].append('pool_size_warning')
    
    # 清理 Django 缓存中的过期键（可选）
    # cache.delete_pattern('k8s:*')  # 需要 Redis 支持
    
    result['pool_status_after'] = K8sClientPool.get_pool_status()
    
    logger.info(f"K8s 资源优化完成: {result}")
    return result


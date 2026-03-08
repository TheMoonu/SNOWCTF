"""
容器创建资源预占管理器（重构版）
使用加权令牌桶算法，基于实际资源消耗的智能限流
"""
import time
from django.core.cache import cache
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


class ResourceReservationManager:
    """
    资源预占管理器 - 基于加权令牌桶的智能限流
    
    核心优化：
    1. 废弃全局资源预占 - 改用K8s原生调度检查（Dry Run）
    2. 加权令牌桶 - 大容器消耗更多令牌，小容器快速通过
    3. 短超时（30秒）- 快速释放失败请求的配额
    4. 分级限流 - 按资源大小分档，避免大容器挤占小容器
    """
    
    # Redis Keys
    TOKEN_BUCKET_KEY = 'container_token_bucket'        # 令牌桶当前令牌数
    TOKEN_LAST_REFILL_KEY = 'container_token_last_refill'  # 上次补充令牌时间
    RESERVATION_PREFIX = 'resource_reservation_'       # 预占记录前缀（用于超时清理）
    
    # 令牌桶配置（支持环境变量）
    from container.models import ContainerEngineConfig
    config = ContainerEngineConfig.get_config()
    MAX_TOKENS = config.token_bucket_max  # 最大令牌数（可支撑200个小容器或20个大容器）
    REFILL_RATE = config.token_bucket_refill_rate  # 每秒补充20个令牌
    
    # 预占超时时间（秒）- 缩短到30秒，快速释放
    RESERVATION_TIMEOUT = config.k8s_node_reservation_timeout
    
    @classmethod
    def try_reserve(cls, memory_mb, cpu_cores, max_memory_mb=None, max_cpu_cores=None, reserve_key=None):
        """
        尝试获取令牌（基于加权令牌桶算法）
        
        核心改进：
        1. 不再预占全局资源（避免与K8s调度脱节）
        2. 使用加权令牌桶：大容器消耗更多令牌
        3. 令牌自动补充：每秒补充10个令牌
        
        Args:
            memory_mb: 需要的内存（MB）
            cpu_cores: 需要的CPU核心数
            max_memory_mb: 【废弃】保留参数以兼容旧代码
            max_cpu_cores: 【废弃】保留参数以兼容旧代码
            reserve_key: 预占标识（用于超时清理）
            
        Returns:
            tuple: (success: bool, reserve_key: str, error_msg: str)
        """
        if not reserve_key:
            reserve_key = f"{int(time.time() * 1000)}_{id(object())}"
        
        cache_client = cache.client.get_client()
        
        try:
            # 0. 自动健康检查和修复
            current_tokens = cache_client.get(cls.TOKEN_BUCKET_KEY)
            
            if current_tokens is None:
                # 情况1：令牌桶不存在，自动初始化
                cache_client.set(cls.TOKEN_BUCKET_KEY, float(cls.MAX_TOKENS))
                cache_client.set(cls.TOKEN_LAST_REFILL_KEY, time.time())
                logger.info(f" 令牌桶初始化: {cls.MAX_TOKENS}个令牌，补充速率={cls.REFILL_RATE}/秒")
            else:
                current_tokens = float(current_tokens)
                # 情况2：令牌数为负数（异常），自动修复
                if current_tokens < -10:
                    cache_client.set(cls.TOKEN_BUCKET_KEY, float(cls.MAX_TOKENS))
                    cache_client.set(cls.TOKEN_LAST_REFILL_KEY, time.time())
                    logger.warning(f" 令牌桶异常修复: 从{current_tokens:.2f}重置为{cls.MAX_TOKENS}")
            
            # 1. 计算需要的令牌数（加权）
            # 小容器（<512MB）= 1令牌，中容器（512MB-2GB）= 3令牌，大容器（>2GB）= 5令牌
            if memory_mb < 512 and cpu_cores < 1:
                tokens_needed = 1  # 小容器
            elif memory_mb < 2048 and cpu_cores < 2:
                tokens_needed = 3  # 中容器
            else:
                tokens_needed = 5  # 大容器
            
            # 2. 补充令牌（基于时间流逝，使用浮点数精确计算）
            current_time = time.time()
            last_refill_time = float(cache_client.get(cls.TOKEN_LAST_REFILL_KEY) or current_time)
            time_elapsed = current_time - last_refill_time
            
            # 计算应补充的令牌数（使用浮点数，避免高并发时丢失小数部分）
            tokens_to_add = time_elapsed * cls.REFILL_RATE
            if tokens_to_add > 0.01:  # 至少补充0.01个令牌才更新（避免频繁写Redis）
                current_tokens = float(cache_client.get(cls.TOKEN_BUCKET_KEY) or cls.MAX_TOKENS)
                new_tokens = min(current_tokens + tokens_to_add, float(cls.MAX_TOKENS))
                cache_client.set(cls.TOKEN_BUCKET_KEY, new_tokens)
                cache_client.set(cls.TOKEN_LAST_REFILL_KEY, current_time)
                
                logger.debug(
                    f"令牌补充: +{tokens_to_add:.2f} → {new_tokens:.2f}/{cls.MAX_TOKENS} "
                    f"(间隔{time_elapsed:.3f}秒)"
                )
            
            # 3. 尝试消耗令牌（原子操作）
            remaining_tokens = cache_client.incrbyfloat(cls.TOKEN_BUCKET_KEY, -tokens_needed)
            
            # 4. 检查是否成功
            if remaining_tokens < 0:
                # 令牌不足，回滚
                cache_client.incrbyfloat(cls.TOKEN_BUCKET_KEY, tokens_needed)
                
                current_tokens = max(0, remaining_tokens + tokens_needed)
                wait_time = int((tokens_needed - current_tokens) / cls.REFILL_RATE)
                
                return False, None, (
                    f"系统当前负载过高，请等待约{wait_time}秒后重试 "
                    f"(令牌不足: 需要{tokens_needed}个，剩余{current_tokens:.0f}个)"
                )
            
            # 5. 成功，记录预占信息（用于超时清理）
            reservation_info = {
                'tokens': tokens_needed,
                'memory_mb': memory_mb,
                'cpu_cores': cpu_cores,
                'timestamp': current_time
            }
            cache.set(
                f"{cls.RESERVATION_PREFIX}{reserve_key}",
                reservation_info,
                timeout=cls.RESERVATION_TIMEOUT
            )
            
            logger.debug(
                f"令牌获取成功: 消耗{tokens_needed}个令牌 "
                f"(memory={memory_mb}MB, cpu={cpu_cores:.2f}核), "
                f"剩余令牌={remaining_tokens:.0f}/{cls.MAX_TOKENS}"
            )
            
            return True, reserve_key, ""
            
        except Exception as e:
            logger.error(f"令牌获取异常: {e}", exc_info=True)
            return False, None, f"系统繁忙，请稍后重试"
    
    @classmethod
    def release(cls, reserve_key):
        """
        释放令牌（归还到令牌桶）
        
        Args:
            reserve_key: 预占标识
        """
        if not reserve_key:
            return
        
        reservation_key = f"{cls.RESERVATION_PREFIX}{reserve_key}"
        reservation_info = cache.get(reservation_key)
        
        if not reservation_info:
            logger.debug(f"预占记录不存在或已过期: {reserve_key}")
            return
        
        tokens = reservation_info.get('tokens', 0)
        
        cache_client = cache.client.get_client()
        
        try:
            # 归还令牌（但不超过最大值，使用浮点数）
            current_tokens = float(cache_client.get(cls.TOKEN_BUCKET_KEY) or 0)
            new_tokens = min(current_tokens + tokens, float(cls.MAX_TOKENS))
            cache_client.set(cls.TOKEN_BUCKET_KEY, new_tokens)
            
            # 删除预占记录
            cache.delete(reservation_key)
            
            logger.debug(f"令牌归还成功: +{tokens}个令牌, 当前={new_tokens:.0f}/{cls.MAX_TOKENS}")
            
        except Exception as e:
            logger.error(f"令牌归还异常: {e}", exc_info=True)
    
    @classmethod
    def get_reserved_resources(cls):
        """
        获取当前令牌桶状态
        
        Returns:
            dict: {
                'available_tokens': float,  # 当前可用令牌
                'max_tokens': int,          # 最大令牌数
                'usage_percent': float      # 使用率
            }
        """
        cache_client = cache.client.get_client()
        
        try:
            current_tokens = float(cache_client.get(cls.TOKEN_BUCKET_KEY) or cls.MAX_TOKENS)
            usage_percent = ((cls.MAX_TOKENS - current_tokens) / cls.MAX_TOKENS) * 100
            
            return {
                'available_tokens': current_tokens,
                'max_tokens': cls.MAX_TOKENS,
                'usage_percent': usage_percent,
                'refill_rate_per_sec': cls.REFILL_RATE
            }
        except Exception as e:
            logger.error(f"获取令牌桶状态失败: {e}", exc_info=True)
            return {
                'available_tokens': 0,
                'max_tokens': cls.MAX_TOKENS,
                'usage_percent': 100
            }
    
    @classmethod
    def clear_expired_reservations(cls):
        """
        清理过期的预占记录（定时任务调用）
        
        由于预占记录有TTL，Redis会自动过期，这里主要是防止资源泄漏
        """
        # 获取所有预占记录
        cache_client = cache.client.get_client()
        pattern = f"{cls.RESERVATION_PREFIX}*"
        
        try:
            keys = cache_client.keys(pattern)
            expired_count = 0
            
            for key in keys:
                key_str = key.decode() if isinstance(key, bytes) else key
                reserve_key = key_str.replace(cls.RESERVATION_PREFIX, '')
                
                reservation_info = cache.get(key_str)
                if not reservation_info:
                    continue
                
                # 检查是否超过超时时间
                timestamp = reservation_info.get('timestamp', 0)
                if time.time() - timestamp > cls.RESERVATION_TIMEOUT:
                    cls.release(reserve_key)
                    expired_count += 1
            
            if expired_count > 0:
                logger.info(f"清理了 {expired_count} 个过期的资源预占")
            
            return expired_count
            
        except Exception as e:
            logger.error(f"清理过期预占失败: {e}", exc_info=True)
            return 0
    
    @classmethod
    def reset_all_reservations(cls):
        """
        重置令牌桶（紧急情况使用）
        """
        cache_client = cache.client.get_client()
        
        try:
            # 重置令牌桶为满状态（使用浮点数）
            cache_client.set(cls.TOKEN_BUCKET_KEY, float(cls.MAX_TOKENS))
            cache_client.set(cls.TOKEN_LAST_REFILL_KEY, time.time())
            
            # 清除所有预占记录
            pattern = f"{cls.RESERVATION_PREFIX}*"
            keys = cache_client.keys(pattern)
            if keys:
                cache_client.delete(*keys)
            
            logger.warning("已重置令牌桶（紧急操作）")
            
        except Exception as e:
            logger.error(f"重置令牌桶失败: {e}", exc_info=True)


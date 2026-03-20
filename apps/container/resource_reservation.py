
import time
from django.core.cache import cache
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


class ResourceReservationManager:
    

    TOKEN_BUCKET_KEY = 'container_token_bucket' 
    TOKEN_LAST_REFILL_KEY = 'container_token_last_refill'  
    RESERVATION_PREFIX = 'resource_reservation_'      
    
    from container.models import ContainerEngineConfig
    config = ContainerEngineConfig.get_config()
    MAX_TOKENS = config.token_bucket_max 
    REFILL_RATE = config.token_bucket_refill_rate  
    
    RESERVATION_TIMEOUT = config.k8s_node_reservation_timeout
    
    @classmethod
    def _refill_tokens(cls, cache_client):
        """
        用 Lua 脚本原子地完成"按时间补充令牌"操作，避免并发竞态。

        Lua 脚本在 Redis 单线程内执行，整个 GET-计算-SET 过程不会被其他客户端打断。

        Returns:
            float: 补充后的当前令牌数（已 clamp 到 MAX_TOKENS）
        """
        lua_script = """
        local token_key    = KEYS[1]
        local refill_key   = KEYS[2]
        local max_tokens   = tonumber(ARGV[1])
        local refill_rate  = tonumber(ARGV[2])
        local now          = tonumber(ARGV[3])

        local current = tonumber(redis.call('GET', token_key))
        local last    = tonumber(redis.call('GET', refill_key))

        -- 初始化（键不存在）
        if current == nil then
            redis.call('SET', token_key,   max_tokens)
            redis.call('SET', refill_key,  now)
            return max_tokens
        end

        -- 异常修复：令牌数低于 -MAX_TOKENS 视为数据损坏
        if current < -max_tokens then
            redis.call('SET', token_key,  max_tokens)
            redis.call('SET', refill_key, now)
            return max_tokens
        end

        if last == nil then last = now end

        local elapsed   = now - last
        local to_add    = elapsed * refill_rate
        if to_add > 0.01 then
            local new_val = math.min(current + to_add, max_tokens)
            redis.call('SET', token_key,  new_val)
            redis.call('SET', refill_key, now)
            return new_val
        end

        return current
        """
        current_time = time.time()
        result = cache_client.eval(
            lua_script, 2,
            cls.TOKEN_BUCKET_KEY, cls.TOKEN_LAST_REFILL_KEY,
            float(cls.MAX_TOKENS), float(cls.REFILL_RATE), current_time
        )
        return float(result)

    @classmethod
    def try_reserve(cls, memory_mb, cpu_cores, max_memory_mb=None, max_cpu_cores=None, reserve_key=None):

        if not reserve_key:
            reserve_key = f"{int(time.time() * 1000)}_{id(object())}"
        
        cache_client = cache.client.get_client()
        
        try:
            if memory_mb < 512 and cpu_cores < 1:
                tokens_needed = 1 
            elif memory_mb < 2048 and cpu_cores < 2:
                tokens_needed = 3 
            else:
                tokens_needed = 5
            
            after_refill = cls._refill_tokens(cache_client)
            logger.debug(f"令牌补充后: {after_refill:.2f}/{cls.MAX_TOKENS}")
            
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
            
            
            current_time = time.time()
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
            lua_script = """
            local token_key  = KEYS[1]
            local max_tokens = tonumber(ARGV[1])
            local to_add     = tonumber(ARGV[2])
            local current = tonumber(redis.call('GET', token_key)) or 0
            local new_val = math.min(current + to_add, max_tokens)
            redis.call('SET', token_key, new_val)
            return new_val
            """
            new_tokens = float(cache_client.eval(
                lua_script, 1,
                cls.TOKEN_BUCKET_KEY,
                float(cls.MAX_TOKENS), float(tokens)
            ))
            
            
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


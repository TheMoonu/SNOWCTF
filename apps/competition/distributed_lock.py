"""
分布式锁实现
支持Redis和数据库两种方式，确保高并发场景下的数据一致性
"""

import time
import logging
from contextlib import contextmanager
from django.core.cache import cache
from django.db import transaction, connection
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger('apps.competition')


class DistributedLock:
    """分布式锁基类"""
    
    def __init__(self, lock_key, timeout=300, retry_times=3, retry_delay=1):
        """
        初始化锁
        
        Args:
            lock_key: 锁的唯一标识
            timeout: 锁超时时间（秒）
            retry_times: 获取锁失败后的重试次数
            retry_delay: 重试间隔（秒）
        """
        self.lock_key = lock_key
        self.timeout = timeout
        self.retry_times = retry_times
        self.retry_delay = retry_delay
        self.acquired = False
    
    def acquire(self):
        """获取锁（子类实现）"""
        raise NotImplementedError
    
    def release(self):
        """释放锁（子类实现）"""
        raise NotImplementedError
    
    @contextmanager
    def __call__(self):
        """上下文管理器"""
        try:
            if self.acquire():
                yield True
            else:
                yield False
        finally:
            if self.acquired:
                self.release()


class CacheLock(DistributedLock):
    """基于Django Cache的分布式锁（支持Redis、Memcached等）"""
    
    def __init__(self, lock_key, timeout=300, retry_times=3, retry_delay=1):
        super().__init__(lock_key, timeout, retry_times, retry_delay)
        self.lock_value = f"{time.time()}_{id(self)}"  # 唯一标识
    
    def acquire(self):
        """获取锁"""
        for attempt in range(self.retry_times):
            # 使用add确保原子性（只有key不存在时才能设置成功）
            success = cache.add(
                self.lock_key,
                self.lock_value,
                timeout=self.timeout
            )
            
            if success:
                self.acquired = True
                return True
            
            # 检查是否是死锁（锁持有者已经崩溃）
            # 注意：这个检查不是原子的，但可以降低死锁风险
            if attempt < self.retry_times - 1:
                logger.warning(f'获取锁失败，等待重试: {self.lock_key} (尝试 {attempt + 1}/{self.retry_times})')
                time.sleep(self.retry_delay)
        
        logger.error(f'获取锁失败，已达到最大重试次数: {self.lock_key}')
        return False
    
    def release(self):
        """释放锁"""
        if self.acquired:
            # 使用delete_many确保删除
            cache.delete(self.lock_key)
            self.acquired = False
    
    def extend(self, extra_time=60):
        """延长锁的有效期"""
        if self.acquired:
            cache.touch(self.lock_key, self.timeout + extra_time)
            logger.debug(f'延长锁有效期: {self.lock_key} (+{extra_time}秒)')


class DatabaseLock(DistributedLock):
    """基于数据库的分布式锁（用于缓存不可用的情况）"""
    
    def acquire(self):
        """使用数据库行级锁"""
        from competition.models import Competition
        
        try:
            # 提取competition_id
            competition_id = self._extract_competition_id()
            if not competition_id:
                logger.error(f'无法从锁键提取竞赛ID: {self.lock_key}')
                return False
            
            for attempt in range(self.retry_times):
                try:
                    # 使用select_for_update获取行级锁
                    with transaction.atomic():
                        competition = Competition.objects.select_for_update(
                            nowait=False  # 等待锁释放
                        ).get(id=competition_id)
                        
                        # 检查是否有其他进程正在计算
                        if hasattr(competition, '_lock_holder') and competition._lock_holder:
                            lock_time = getattr(competition, '_lock_time', None)
                            if lock_time and timezone.now() - lock_time < timedelta(seconds=self.timeout):
                                logger.warning(f'数据库锁已被占用: {self.lock_key}')
                                if attempt < self.retry_times - 1:
                                    time.sleep(self.retry_delay)
                                    continue
                                return False
                        
                        # 标记锁持有者
                        competition._lock_holder = self.lock_value
                        competition._lock_time = timezone.now()
                        # 注意：这些属性不会保存到数据库，只在内存中
                        
                        self.acquired = True
                        self.competition = competition
                        logger.info(f'成功获取数据库锁: {self.lock_key}')
                        return True
                        
                except Competition.DoesNotExist:
                    logger.error(f'竞赛不存在: competition_id={competition_id}')
                    return False
                except Exception as e:
                    logger.error(f'获取数据库锁失败: {e}')
                    if attempt < self.retry_times - 1:
                        time.sleep(self.retry_delay)
                    
            return False
            
        except Exception as e:
            logger.error(f'数据库锁错误: {e}', exc_info=True)
            return False
    
    def release(self):
        """释放数据库锁"""
        if self.acquired:
            # 数据库锁会在事务结束时自动释放
            self.acquired = False
            logger.info(f'释放数据库锁: {self.lock_key}')
    
    def _extract_competition_id(self):
        """从锁键中提取竞赛ID"""
        # 假设锁键格式为: combined_leaderboard_lock_{competition_id}
        try:
            parts = self.lock_key.split('_')
            return int(parts[-1])
        except (ValueError, IndexError):
            return None


class HybridLock(DistributedLock):
    """混合锁：优先使用Cache，失败时降级到数据库锁"""
    
    def __init__(self, lock_key, timeout=300, retry_times=3, retry_delay=1):
        super().__init__(lock_key, timeout, retry_times, retry_delay)
        self.cache_lock = CacheLock(lock_key, timeout, retry_times, retry_delay)
        self.db_lock = None
        self.lock_type = None
    
    def acquire(self):
        """优先尝试Cache锁，失败则使用数据库锁"""
        # 首先尝试Cache锁
        if self.cache_lock.acquire():
            self.acquired = True
            self.lock_type = 'cache'
            logger.info(f'使用Cache锁: {self.lock_key}')
            return True
        
        # Cache锁失败，尝试数据库锁
        logger.warning(f'Cache锁获取失败，尝试数据库锁: {self.lock_key}')
        self.db_lock = DatabaseLock(self.lock_key, self.timeout, retry_times=1, retry_delay=1)
        
        if self.db_lock.acquire():
            self.acquired = True
            self.lock_type = 'database'
            logger.info(f'使用数据库锁: {self.lock_key}')
            return True
        
        logger.error(f'所有锁机制均失败: {self.lock_key}')
        return False
    
    def release(self):
        """释放锁"""
        if self.acquired:
            if self.lock_type == 'cache':
                self.cache_lock.release()
            elif self.lock_type == 'database' and self.db_lock:
                self.db_lock.release()
            
            self.acquired = False
            self.lock_type = None


def get_leaderboard_lock(competition_id, timeout=300):
    """
    获取综合排行榜计算锁
    
    Args:
        competition_id: 竞赛ID
        timeout: 锁超时时间（秒），默认5分钟
    
    Returns:
        DistributedLock实例
    """
    lock_key = f'combined_leaderboard_lock_{competition_id}'
    
    # 使用混合锁策略
    return HybridLock(lock_key, timeout=timeout, retry_times=3, retry_delay=2)


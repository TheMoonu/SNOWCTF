# -*- coding:utf-8 -*-
import os
from celery import Celery
from celery.signals import task_prerun, task_postrun, worker_process_init

# 设置环境变量
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'secsnow.settings')

# 实例化
app = Celery('secsnow')

# namespace='CELERY'作用是允许你在Django配置文件中对Celery进行配置
# 但所有Celery配置项必须以CELERY开头，防止冲突
app.config_from_object('django.conf:settings', namespace='CELERY')

# 自动从Django的已注册app中发现任务
app.autodiscover_tasks()


# ==================== 数据库连接管理 ====================
# 解决高并发下的 "connection already closed" 问题

@worker_process_init.connect
def init_worker_process(**kwargs):
    """
    Worker 进程初始化时关闭所有数据库连接
    这样每个进程会创建自己的连接池
    """
    from django.db import connections
    for conn in connections.all():
        conn.close()


@task_prerun.connect
def task_prerun_handler(sender=None, task_id=None, task=None, args=None, kwargs=None, **extra):
    """
    任务执行前关闭旧的数据库连接
    确保使用新鲜的连接
    """
    from django.db import connections
    from django.db.utils import OperationalError
    
    for conn in connections.all():
        try:
            # 关闭可能已经失效的连接
            conn.close_if_unusable_or_obsolete()
        except OperationalError:
            # 连接已失效，强制关闭
            conn.close()


@task_postrun.connect
def task_postrun_handler(sender=None, task_id=None, task=None, args=None, kwargs=None, retval=None, state=None, **extra):
    """
    任务执行后关闭数据库连接
    避免连接泄露和超时
    """
    from django.db import connections
    
    for conn in connections.all():
        # 关闭连接，归还给连接池
        conn.close()

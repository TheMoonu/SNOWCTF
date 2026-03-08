# -*- coding: utf-8 -*-
"""
性能监控中间件
用于诊断慢请求问题
"""

import time
import logging
from django.db import connection
from django.utils.deprecation import MiddlewareMixin

# 使用apps.competition作为logger名称，匹配settings.py中的配置
logger = logging.getLogger('apps.competition')


class PerformanceMonitoringMiddleware(MiddlewareMixin):
    """
    性能监控中间件
    
    功能：
    - 记录请求处理时间
    - 记录数据库查询次数和时间
    - 对慢请求发出警告
    """
    
    def process_request(self, request):
        """请求开始时记录时间"""
        request._start_time = time.time()
        request._db_queries_before = len(connection.queries)
    
    def process_response(self, request, response):
        """请求结束时计算性能指标"""
        # 计算总耗时
        if hasattr(request, '_start_time'):
            elapsed = time.time() - request._start_time
            
            # 计算数据库查询
            db_queries_after = len(connection.queries)
            db_queries_count = db_queries_after - getattr(request, '_db_queries_before', 0)
            
            # 计算数据库查询总时间
            db_time = 0
            if hasattr(request, '_db_queries_before'):
                for query in connection.queries[request._db_queries_before:]:
                    db_time += float(query['time'])
            
            # 对慢请求记录日志
            if elapsed > 2.0:  # 超过2秒
                logger.warning(
                    f"SLOW REQUEST: {request.method} {request.path} | "
                    f"Total: {elapsed:.3f}s | DB: {db_time:.3f}s ({db_queries_count} queries) | "
                    f"User: {getattr(request.user, 'username', 'anonymous')}"
                )
            elif elapsed > 1.0:  # 超过1秒
                logger.info(
                    f"Slow request: {request.method} {request.path} | "
                    f"Total: {elapsed:.3f}s | DB: {db_time:.3f}s ({db_queries_count} queries)"
                )
            
            # 添加响应头（用于前端监控）
            response['X-Response-Time'] = f"{elapsed:.3f}s"
            response['X-DB-Queries'] = str(db_queries_count)
            response['X-DB-Time'] = f"{db_time:.3f}s"
        
        return response


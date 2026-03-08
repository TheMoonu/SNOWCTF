# -*- coding: utf-8 -*-
"""
数据库连接管理中间件 - 防止连接泄漏

这个中间件确保每个请求结束后都正确清理数据库连接，
防止在高并发场景下出现 "too many clients already" 错误。
"""

import logging
from django.db import close_old_connections, connection
from django.utils.deprecation import MiddlewareMixin

# 使用apps.competition作为logger名称，匹配settings.py中的配置
logger = logging.getLogger('apps.competition')


class DatabaseConnectionMiddleware(MiddlewareMixin):
    """
    数据库连接管理中间件
    
    功能：
    1. 在请求开始时清理陈旧的数据库连接
    2. 在请求结束后确保连接被正确关闭或返回连接池
    3. 在异常情况下也能正确清理连接
    
    这个中间件对于使用连接池（CONN_MAX_AGE > 0）的 Django 应用特别重要。
    """
    
    def process_request(self, request):
        """
        在请求开始时清理陈旧连接
        
        Django 的 close_old_connections() 会：
        - 关闭超过 CONN_MAX_AGE 的连接
        - 关闭不可用的连接
        """
        close_old_connections()
        return None
    
    def process_response(self, request, response):
        """
        在请求结束后清理连接
        
        确保：
        1. 不可用的连接被关闭
        2. 过期的连接被关闭
        3. 连接被正确标记为可重用
        """
        try:
            # 检查连接状态并清理
            if connection.connection is not None:
                # 如果连接不在事务中，检查其可用性
                if not connection.in_atomic_block:
                    connection.close_if_unusable_or_obsolete()
            
            # 清理陈旧连接
            close_old_connections()
            
        except Exception as e:
            # 记录错误但不影响响应
            logger.warning(f"Error in database connection cleanup: {str(e)}")
        
        return response
    
    def process_exception(self, request, exception):
        """
        在异常发生时也要清理连接
        
        这对于防止连接泄漏非常重要，因为异常可能导致
        事务中断或连接状态不一致。
        """
        try:
            # 关闭不可用的连接
            if connection.connection is not None:
                try:
                    # 如果在事务中，回滚
                    if connection.in_atomic_block:
                        connection.needs_rollback = True
                    
                    # 关闭不可用或过期的连接
                    connection.close_if_unusable_or_obsolete()
                except Exception as conn_err:
                    logger.error(f"Error closing connection after exception: {str(conn_err)}")
            
            # 清理陈旧连接
            close_old_connections()
            
        except Exception as e:
            # 记录错误但不影响异常处理
            logger.error(f"Error in exception connection cleanup: {str(e)}")
        
        # 返回 None 让 Django 继续正常的异常处理
        return None


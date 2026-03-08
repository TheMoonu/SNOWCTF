# -*- coding: utf-8 -*-
"""
管理后台自定义视图
"""
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.csrf import csrf_protect
from oauth.models import Ouser
import logging

# 使用apps.oauth作为logger名称，匹配settings.py中的配置
logger = logging.getLogger('apps.oauth')


@staff_member_required
@require_POST
@csrf_protect
def reset_user_password(request):
    """
    重置用户密码（AJAX接口）
    
    只有管理员可以访问
    """
    # 获取客户端IP
    def get_client_ip(request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip
    
    client_ip = get_client_ip(request)
    admin_user = request.user.username
    
    try:
        user_id = request.POST.get('user_id')
        new_password = request.POST.get('new_password')
        
        # 参数验证
        if not user_id or not new_password:
            logger.warning(
                f"🔐 密码重置失败 | 管理员: {admin_user} | IP: {client_ip} | "
                f"原因: 缺少必要参数 | user_id={user_id}",
                extra={'request': request}
            )
            return JsonResponse({
                'success': False,
                'error': '缺少必要参数'
            }, status=400)
        
        # 验证密码长度
        if len(new_password) < 8:
            logger.warning(
                f"🔐 密码重置失败 | 管理员: {admin_user} | IP: {client_ip} | "
                f"user_id={user_id} | 原因: 密码长度不足（{len(new_password)}位）",
                extra={'request': request}
            )
            return JsonResponse({
                'success': False,
                'error': '密码长度至少8位'
            }, status=400)
        
        # 获取用户
        try:
            user = Ouser.objects.get(pk=user_id)
        except Ouser.DoesNotExist:
            logger.warning(
                f"🔐 密码重置失败 | 管理员: {admin_user} | IP: {client_ip} | "
                f"user_id={user_id} | 原因: 用户不存在",
                extra={'request': request}
            )
            return JsonResponse({
                'success': False,
                'error': '用户不存在'
            }, status=404)
        
        # 修改密码
        old_password_hash = user.password[:20]  # 记录部分旧密码哈希
        user.set_password(new_password)
        user.save(update_fields=['password'])
        new_password_hash = user.password[:20]
        
        # 详细日志（传递request对象以记录IP和路径）
        logger.info(
            f"✅ 密码重置成功 | "
            f"管理员: {admin_user}(ID:{request.user.id}) | "
            f"目标用户: {user.username}(ID:{user.id}) | "
            f"IP: {client_ip}",
            extra={'request': request}
        )
        
        return JsonResponse({
            'success': True,
            'message': f'用户 {user.username} 的密码已修改'
        })
        
    except Exception as e:
        logger.error(
            f"❌ 密码重置异常 | "
            f"管理员: {admin_user} | "
            f"IP: {client_ip} | "
            f"错误: {str(e)}",
            exc_info=True,
            extra={'request': request}
        )
        return JsonResponse({
            'success': False,
            'error': f'服务器错误: {str(e)}'
        }, status=500)


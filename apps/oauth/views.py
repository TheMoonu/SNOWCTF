from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from .forms import ProfileForm
from django.contrib import messages
from django.shortcuts import render, get_object_or_404
from oauth.models import Ouser
from django.http import JsonResponse
import os
import random
import string
from django.utils import timezone
from datetime import timedelta
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from competition.models import Team,Registration
from comment.models import SystemNotification
from public.utils import clear_user_teams_cache
from django.utils.html import escape


# Create your views here.

@login_required
def profile_view(request):
    """个人资料页面 - 重定向到公共 profile 页面"""
    return redirect('public:profileViews', user_uuid=request.user.uuid)


@login_required
def change_profile_view(request):
    """修改个人资料"""
    import logging
    logger = logging.getLogger('apps.oauth')
    
    if request.method == 'POST':
        # 保存旧数据用于对比
        old_real_name = request.user.real_name
        old_phones = request.user.phones
        old_profile = request.user.profile
        old_department = request.user.department
        old_student_id = request.user.student_id
        old_avatar_url = request.user.avatar.url if request.user.avatar else None
        old_avatar_name = request.user.avatar.name if request.user.avatar else None
        
        # 检查是否上传了新头像
        has_new_avatar = 'avatar' in request.FILES and request.FILES['avatar']
        
        # 创建表单
        if not has_new_avatar:
            form = ProfileForm(request.POST, instance=request.user)
        else:
            form = ProfileForm(request.POST, request.FILES, instance=request.user)
        
        if form.is_valid():
            logger.info("表单验证通过")
            logger.info(f"表单清洗后的数据: {form.cleaned_data}")
            
            # 文件验证（如果上传了新头像）
            if has_new_avatar:
                avatar_file = request.FILES['avatar']
                
                # 验证文件类型
                allowed_types = ['image/jpeg', 'image/jpg', 'image/png', 'image/gif', 'image/webp']
                if avatar_file.content_type not in allowed_types:
                    form.add_error('avatar', '不支持的文件格式')
                    messages.error(request, '不支持的文件格式')
                    return render(request, 'oauth/change_profile.html', {'form': form})
                
                # 验证文件大小（5MB）
                max_size = 5 * 1024 * 1024
                if avatar_file.size > max_size:
                    form.add_error('avatar', f'文件过大: {avatar_file.size / (1024 * 1024):.2f}MB')
                    messages.error(request, '文件大小超过 5MB')
                    return render(request, 'oauth/change_profile.html', {'form': form})
                
                # 验证文件不能为空
                if avatar_file.size == 0:
                    form.add_error('avatar', '文件不能为空')
                    messages.error(request, '文件不能为空')
                    return render(request, 'oauth/change_profile.html', {'form': form})
            
            # 保存数据
            try:
                # 使用表单的 save 方法（会自动处理加密字段）
                user = form.save(commit=True)
                
                
                # 验证保存结果
                user.refresh_from_db()
    
                
                # 删除旧头像（兼容本地存储和对象存储）
                if has_new_avatar and old_avatar_name and old_avatar_url:
                    if 'default' not in old_avatar_url.lower():
                        try:
                            # 使用 Django 的存储后端删除（兼容本地和对象存储）
                            from django.core.files.storage import default_storage
                            if default_storage.exists(old_avatar_name):
                                default_storage.delete(old_avatar_name)
                                logger.info(f"已删除旧头像: {old_avatar_name}")
                        except Exception as e:
                            logger.warning(f"删除旧头像失败: {e}")
                
                messages.success(request, '个人信息更新成功！')
                logger.info("重定向到个人资料页面")
                return redirect('public:profileViews', user_uuid=request.user.uuid)
            
            except Exception as e:
                logger.error(f"保存失败: {e}", exc_info=True)
                messages.error(request, f'保存失败：{str(e)}')
                return render(request, 'oauth/change_profile.html', {'form': form})
        else:
            # 表单验证失败
            logger.warning(f"表单验证失败: {form.errors}")
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f'{field}: {error}')
            messages.error(request, '请检查输入信息')
    else:
        # GET 请求 - 需要手动设置加密字段的初始值
        initial_data = {
            'real_name': request.user.real_name,
            'phones': request.user.phones,
            'department': request.user.department,
            'student_id': request.user.student_id,
        }
        form = ProfileForm(instance=request.user, initial=initial_data)
    
    return render(request, 'oauth/change_profile.html', {'form': form})


@login_required
def dissolve_team(request, team_id):
    team = get_object_or_404(Team, id=team_id)
    if team.leader != request.user:
        return JsonResponse({'success': False, 'message': '只有队长可以解散队伍'})
    
    # 获取比赛和团队成员
    competition = team.competition
    
    # 检查比赛状态：比赛进行中或已结束时不允许解散队伍
    if competition:
        if competition.is_running():
            return JsonResponse({'success': False, 'message': '比赛正在进行中，无法解散队伍'})
        if competition.is_ended():
            return JsonResponse({'success': False, 'message': '比赛已结束，无法解散队伍'})
    
    team_members = list(team.members.all())
    
    # 清除所有成员的团队报名记录
    if competition:
        for member in team_members:
            # 删除与该团队相关的报名记录
            Registration.objects.filter(
                competition=competition,
                user=member,
                registration_type='team',
                team_name=team
            ).delete()
            
            # 清除用户缓存
            clear_user_teams_cache(member.id)
    
    # 解散团队
    team.delete()
    return JsonResponse({'success': True, 'message': '团队已解散，相关比赛报名已取消'})

@login_required
def leave_team(request, team_id):
    team = get_object_or_404(Team, id=team_id)
    if team.leader == request.user:
        return JsonResponse({'success': False, 'message': '队长不能退出队伍'})
    
    # 获取比赛
    competition = team.competition

    if competition:
        if competition.is_running():
            return JsonResponse({'success': False, 'message': '比赛正在进行中，无法退出队伍'})
        if competition.is_ended():
            return JsonResponse({'success': False, 'message': '比赛已结束，无法退出队伍'})
    
    # 删除用户的团队报名记录
    if competition:
        Registration.objects.filter(
            competition=competition,
            user=request.user,
            registration_type='team',
            team_name=team
        ).delete()
    
    # 清除用户缓存
    clear_user_teams_cache(request.user.id)
    
    # 用户退出团队
    team.members.remove(request.user)
    
    return JsonResponse({'success': True, 'message': '已退出团队，相关比赛报名已取消'})


@login_required
@require_POST
def follow_toggle(request):
    """
    关注/取消关注的切换接口
    """
    user_id = request.POST.get('user_id')
    try:
        # 获取要关注/取消关注的用户
        target_user = get_object_or_404(Ouser, id=user_id)
        user = request.user
        
        # 不能关注自己
        if user == target_user:
            return JsonResponse({
                'status': 'error',
                'message': '不能关注自己'
            }, status=400)
        
        # 切换关注状态
        if user.is_following(target_user):
            user.unfollow(target_user)
            is_following = False
            message = '取消关注成功'
        else:
            user.follow(target_user)
            is_following = True
            message = '关注成功'
            notification = SystemNotification.objects.create(
                title='新的关注者',
                content=f'用户  <a href="/profile/{user.uuid}" class="text-primary">{escape(user.username)}</a>  关注了你'
            )
            # 添加接收者
            notification.get_p.add(target_user)

        
        
        # 返回最新的关注状态和统计数据
        return JsonResponse({
            'status': 'success',
            'message': message,
            'data': {
                'is_following': is_following,
                'followers_count': target_user.followers_count,
                'following_count': target_user.following_count
            }
        })
        
    except Exception as e:
        print(e)
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)

@login_required
@require_POST
def generate_invite_code(request):
    """生成邀请码接口"""
    user = request.user
    
    try:
        # 如果已有未过期的邀请码，直接返回
        if user.invite_code and user.is_invite_code_valid:
            return JsonResponse({
                'status': 'success',
                'message': '您已有有效邀请码',
                'data': {
                    'invite_code': user.invite_code,
                    'expires_at': user.invite_code_expires.strftime('%Y-%m-%d %H:%M:%S')
                }
            })
        
        # 生成新的邀请码
        while True:
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            if not Ouser.objects.filter(invite_code=code).exists():
                user.invite_code = code
                user.invite_code_expires = timezone.now() + timedelta(days=1)  # 24小时后过期
                user.save(update_fields=['invite_code', 'invite_code_expires'])
                break
        
        return JsonResponse({
            'status': 'success',
            'message': '邀请码生成成功（24小时内有效）',
            'data': {
                'invite_code': code,
                'expires_at': user.invite_code_expires.strftime('%Y-%m-%d %H:%M:%S')
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)



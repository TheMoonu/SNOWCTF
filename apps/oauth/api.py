from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404
from django.contrib.auth import get_user_model
from comment.models import SystemNotification
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
# 使用 get_user_model() 获取当前项目的用户模型
from django.utils.html import escape
User = get_user_model()

@login_required
def get_following(request, user_id):
    """获取用户关注的人列表，支持分页"""
    user = get_object_or_404(User, id=user_id)
    following_users = user.following.all()
    
    # 获取分页参数
    page = request.GET.get('page', 1)
    page_size = request.GET.get('page_size', 10)
    
    # 创建分页器
    paginator = Paginator(following_users, page_size)
    
    try:
        # 获取当前页的数据
        current_page = paginator.page(page)
    except PageNotAnInteger:
        current_page = paginator.page(1)
    except EmptyPage:
        current_page = paginator.page(paginator.num_pages)
    
    # 构建结果
    result = []
    for followed_user in current_page:
        result.append({
            'id': followed_user.id,
            'uuid': followed_user.uuid,
            'profile': followed_user.profile,
            'username': followed_user.username,
            'avatar': followed_user.avatar.url if hasattr(followed_user, 'avatar') and followed_user.avatar else None,
            'bio': followed_user.bio if hasattr(followed_user, 'bio') else '',
            'is_staff': followed_user.is_staff,
            'is_following': request.user.is_authenticated and request.user.following.filter(id=followed_user.id).exists()
        })
    
    # 返回分页信息
    return JsonResponse({
        'results': result,
        'count': paginator.count,
        'next': current_page.has_next(),
        'previous': current_page.has_previous(),
        'total_pages': paginator.num_pages,
        'current_page': current_page.number
    })

@login_required
def get_followers(request, user_id):
    """获取用户的粉丝列表，支持分页"""
    user = get_object_or_404(User, id=user_id)
    followers = user.followers.all()
    
    # 获取分页参数
    page = request.GET.get('page', 1)
    page_size = request.GET.get('page_size', 10)
    
    # 创建分页器
    paginator = Paginator(followers, page_size)
    
    try:
        # 获取当前页的数据
        current_page = paginator.page(page)
    except PageNotAnInteger:
        current_page = paginator.page(1)
    except EmptyPage:
        current_page = paginator.page(paginator.num_pages)
    
    # 构建结果
    result = []
    for follower in current_page:
        result.append({
            'id': follower.id,
            'uuid': follower.uuid,
            'profile': follower.profile,
            'username': follower.username,
            'avatar': follower.avatar.url if hasattr(follower, 'avatar') and follower.avatar else None,
            'bio': follower.bio if hasattr(follower, 'bio') else '',
            'is_staff': follower.is_staff,
            'is_following': request.user.is_authenticated and request.user.following.filter(id=follower.id).exists()
        })
    
    # 返回分页信息
    return JsonResponse({
        'results': result,
        'count': paginator.count,
        'next': current_page.has_next(),
        'previous': current_page.has_previous(),
        'total_pages': paginator.num_pages,
        'current_page': current_page.number
    })

@login_required
@require_POST
def toggle_follow(request, user_id):
    """关注或取消关注用户"""
    if request.user.id == int(user_id):
        return JsonResponse({'success': False, 'message': '不能关注自己'}, status=400)
    
    target_user = get_object_or_404(User, id=user_id)
    action = request.POST.get('action')
    
    if action == 'follow':
        request.user.following.add(target_user)
        
        # 添加通知功能 - 当用户关注某人时发送通知
        try:
            notification = SystemNotification.objects.create(
                title='新的关注者',
                content=f'用户 <a href="/accounts/profile/{request.user.id}/" class="text-primary">{escape(request.user.username)}</a> 关注了你'
            )
            # 添加接收者
            notification.get_p.add(target_user)
        except Exception as e:
            # 记录错误但不影响关注功能
            print(f"创建关注通知时出错: {str(e)}")
        
        return JsonResponse({'success': True, 'message': f'成功关注 {target_user.username}'})
    
    elif action == 'unfollow':
        request.user.following.remove(target_user)
        return JsonResponse({'success': True, 'message': f'成功取消关注 {target_user.username}'})
    
    else:
        return JsonResponse({'success': False, 'message': '无效的操作'}, status=400)
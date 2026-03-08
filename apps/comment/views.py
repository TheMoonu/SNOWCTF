from django.shortcuts import render
from blog.models import Article
from practice.models import PC_Challenge
from comment.models import ArticleComment, ChallengeComment, Notification, SystemNotification
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from datetime import datetime
from django.shortcuts import get_object_or_404
from comment.utils import sanitize_content
from django.contrib import messages
from django.core.cache import cache
from comment.ip_db.ip2region import searchProvince
user_model = settings.AUTH_USER_MODEL


@login_required
@require_POST
def AddCommentView(request):
    
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.method == "POST":
        
        data = request.POST
        new_user = request.user
        new_content = data.get('content')
        new_content = sanitize_content(new_content)
        article_id = data.get('article_id')
        rep_id = data.get('rep_id')
        the_article = Article.objects.get(id=article_id)
        if not new_content or new_content.strip() == '':
                messages.error(request, '评论内容不能为空！')
                return JsonResponse({'msg': '评论失败！'})
        
        # 获取用户真实IP地址
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        user_ip = x_forwarded_for.split(',')[0].strip() if x_forwarded_for else request.META.get('REMOTE_ADDR', '')
        
        # 解析IP归属地（省份/国家）
        ip_location = searchProvince(user_ip)
        
        # 评论冷却时间检查
        cache_key = f'comment_cooldown_{new_user.id}'
        
        # 如果缓存键存在，说明用户在短时间内已经评论过
        if cache.get(cache_key):
            # 获取剩余冷却时间
            ttl = cache.ttl(cache_key)  # 获取键的剩余生存时间（秒）
            if ttl > 0:
                return JsonResponse({'msg': f'评论太频繁，请等待 {ttl} 秒后再试！'})
            
        if len(new_content) > 1048:
            return JsonResponse({'msg': '你的评论字数超过1048，无法保存。'})

        if not rep_id:
            new_comment = ArticleComment(author=new_user, content=new_content, belong=the_article, ip_address=ip_location,
                                         parent=None,
                                         rep_to=None)
        else:
            new_rep_to = ArticleComment.objects.get(id=rep_id)
            new_parent = new_rep_to.parent if new_rep_to.parent else new_rep_to
            new_comment = ArticleComment(author=new_user, content=new_content, belong=the_article, ip_address=ip_location,
                                         parent=new_parent,
                                         rep_to=new_rep_to)
        new_comment.save()
        cache.set(cache_key, True, 40)
        new_point = '#com-' + str(new_comment.id)
        messages.success(request, "评论提交成功！")
        return JsonResponse({'msg': '评论提交成功！', 'new_point': new_point})
    return JsonResponse({'msg': '评论失败！'})


@login_required
def NotificationView(request, is_read=None):
    """展示提示消息列表（带分页）"""
    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
    
    user = request.user
    now_date = datetime.now()
    
    # 获取通知列表
    if is_read == 'true':
        # 获取所有已读通知
        notifications = []
        notifications.extend(user.notification_get.filter(is_read=True))
        notifications.extend(user.systemnotification_recipient.filter(is_read=True))
    elif is_read == 'false':
        # 获取所有未读通知
        notifications = []
        notifications.extend(user.notification_get.filter(is_read=False))
        notifications.extend(user.systemnotification_recipient.filter(is_read=False))
    else:
        # 获取所有通知
        notifications = []
        notifications.extend(user.notification_get.all())
        notifications.extend(user.systemnotification_recipient.all())
    
    # 按照 create_date 字段进行排序
    notifications = sorted(notifications, key=lambda x: x.create_date, reverse=True)
    
    # 分页设置：每页显示20条
    paginator = Paginator(notifications, 20)
    page = request.GET.get('page', 1)
    
    try:
        notifications_page = paginator.page(page)
    except PageNotAnInteger:
        # 如果页码不是整数，显示第一页
        notifications_page = paginator.page(1)
    except EmptyPage:
        # 如果页码超出范围，显示最后一页
        notifications_page = paginator.page(paginator.num_pages)
    
    return render(request, 'comment/notification.html',
                  context={
                      'is_read': is_read,
                      'now_date': now_date,
                      'notifications': notifications_page,
                      'paginator': paginator
                  })


@login_required
@require_POST
def mark_to_read(request):
    """将一个消息标记为已读"""
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.method == "POST":
        data = request.POST
        user = request.user
        _id = data.get('id')
        _tag = data.get('tag')
        if _tag == 'comment':
            info = get_object_or_404(Notification, get_p=user, id=_id)
        elif _tag == 'system':
            info = get_object_or_404(SystemNotification, get_p=user, id=_id)
        else:
            return JsonResponse({'msg': 'bad tag', 'code': 1})
        info.mark_to_read()
        return JsonResponse({'msg': 'mark success', 'code': 0})
    return JsonResponse({'msg': 'miss', 'code': 1})


@login_required
@require_POST
def mark_to_delete(request):
    """将一个消息删除"""
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.method == "POST":
        data = request.POST
        user = request.user
        _id = data.get('id')
        _tag = data.get('tag')
        if _tag == 'comment':
            info = get_object_or_404(Notification, get_p=user, id=_id)
        elif _tag == 'system':
            info = get_object_or_404(SystemNotification, get_p=user, id=_id)
        else:
            return JsonResponse({'msg': 'bad tag', 'code': 1})
        info.delete()
        return JsonResponse({'msg': 'delete success', 'code': 0})
    return JsonResponse({'msg': 'miss', 'code': 1})

@login_required
@require_POST
def AddChallengeCommentView(request):
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.method == "POST":
        try:
            data = request.POST
            new_user = request.user
            new_content = data.get('content')
            new_content = sanitize_content(new_content)
            Challenge_uuid = data.get('challenge_id')
            rep_id = data.get('rep_id')
            the_Challenge =  PC_Challenge.objects.get(uuid=Challenge_uuid)

            # 获取用户真实IP地址
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            user_ip = x_forwarded_for.split(',')[0].strip() if x_forwarded_for else request.META.get('REMOTE_ADDR', '')
            
            # 解析IP归属地（省份/国家）
            ip_location = searchProvince(user_ip)

            if not new_content or new_content.strip() == '':
                return JsonResponse({'msg': '评论失败！'})
        
            # 评论冷却时间检查
            cache_key = f'comment_cooldown_{new_user.id}'
            
            # 如果缓存键存在，说明用户在短时间内已经评论过
            if cache.get(cache_key):
                # 获取剩余冷却时间
                ttl = cache.ttl(cache_key)  # 获取键的剩余生存时间（秒）
                if ttl > 0:
                    return JsonResponse({'msg': f'评论太频繁，请等待 {ttl} 秒后再试！'})
            
            if len(new_content) > 1048:
                return JsonResponse({'msg': '你的评论字数超过1048，无法保存。'})

            if not rep_id:
                new_comment = ChallengeComment(author=new_user, content=new_content, belong=the_Challenge, ip_address=ip_location,
                                            parent=None,
                                            rep_to=None)
            else:
                new_rep_to = ChallengeComment.objects.get(id=rep_id)
                new_parent = new_rep_to.parent if new_rep_to.parent else new_rep_to
                new_comment = ChallengeComment(author=new_user, content=new_content, belong=the_Challenge, ip_address=ip_location,
                                            parent=new_parent,
                                            rep_to=new_rep_to)
            new_comment.save()
            cache.set(cache_key, True, 40)
            new_point = '#com-' + str(new_comment.id)
            messages.success(request, "评论提交成功！")
            return JsonResponse({'msg': '评论提交成功！', 'new_point': new_point})
        except Exception as e:
            print(e)
    return JsonResponse({'msg': '评论失败！'})
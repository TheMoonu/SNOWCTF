from django.shortcuts import render
from django.http import (
    Http404,
    HttpResponseForbidden,
    JsonResponse,
    HttpResponseBadRequest
)
from itertools import chain
from operator import attrgetter
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from oauth.models import Ouser
from blog.models import Article
from practice.models import PC_Challenge
from public.models import CTFUser
from comment.models import ArticleComment, ChallengeComment, SystemNotification
from django.core.paginator import Paginator
from public.utils import site_full_url
from django.utils import timezone
from django.core.cache import cache

from django.db import transaction
from django.core.exceptions import ValidationError
from public.models import RewardItem, ExchangeRecord
from django.contrib import messages
from django.shortcuts import redirect
from django.utils.html import escape
from django.contrib.auth import get_user_model
import logging
from django.urls import reverse
from competition.models import Competition, Team, ScoreTeam, ScoreUser
from container.models import StaticFile, DockerImage
from django.db.models import Count, Q
import json
from datetime import datetime
from django.contrib.auth import get_user_model
User = get_user_model()



# Create your views here.
def indexViews(request):
    # 系统完整性检查（深度嵌入）
   
    context = {
        'hide_footer': True,
    }
    return render(request, 'public/index.html', context)

def licenseViews(request):
    return render(request, 'public/license.html')


@login_required
def get_exchange_captcha(request):
    """获取兑奖验证码"""
    from public.utils import generate_captcha, generate_captcha_image
    import uuid
    
    captcha_text = generate_captcha()
    captcha_image = generate_captcha_image(captcha_text)
    captcha_key = str(uuid.uuid4())
    
    # 存储到 Redis，5分钟过期
    cache.set(f'exchange_captcha_{captcha_key}', captcha_text, 300)
    
    return JsonResponse({
        'success': True,
        'captcha_key': captcha_key,
        'captcha_image': captcha_image
    })


@login_required
def exchange_reward(request):
    """处理兑换请求"""
    if request.method != 'POST':
        messages.error(request, '无效的请求方法')
        return JsonResponse({'status': 'error'})
    
    reward_id = request.POST.get('reward_id')
    contact = request.POST.get('contact')
    captcha_key = request.POST.get('captcha_key')
    captcha_input = request.POST.get('captcha_input')
    
    if not all([reward_id, contact, captcha_key, captcha_input]):
        messages.error(request, '请填写完整信息')
        return JsonResponse({'status': 'error'})
    if len(contact) > 100:
        messages.error(request, '联系方式长度不能超过100个字符')
        return JsonResponse({'status': 'error'})
    
    # 验证验证码
    cache_key = f'exchange_captcha_{captcha_key}'
    cached_captcha = cache.get(cache_key)
    
    if not cached_captcha:
        messages.error(request, '验证码已过期，请重新获取')
        return JsonResponse({'status': 'error', 'message': '验证码已过期'})
    
    if cached_captcha.upper() != captcha_input.upper():
        messages.error(request, '验证码错误')
        return JsonResponse({'status': 'error', 'message': '验证码错误'})
    
    # 验证通过后删除验证码
    cache.delete(cache_key)
    
    # 添加防抖缓存机制，防止重复点击
    cache_key = f'exchange_debounce_{request.user.id}_{reward_id}'
    if cache.get(cache_key):
        messages.warning(request, '操作过于频繁，请稍后再试')
        return JsonResponse({'status': 'error', 'message': '操作过于频繁，请稍后再试'})
    
    # 设置防抖缓存，有效期5秒
    cache.set(cache_key, True, 10)
    
    try:
        with transaction.atomic():
            ctf_user = request.user.ctfuser
            if ExchangeRecord.objects.filter(
                user=request.user,
                is_processed=False
            ).exists():
                messages.warning(request, '您有未处理的相同奖品兑换记录，请等待处理完成后再次兑换')
                return JsonResponse({'status': 'error'})
            # 获取奖品信息
            reward = RewardItem.objects.select_for_update().get(
                id=reward_id,
                is_active=True
            )
            # 获取用户CTF信息
            # 检查库存
            if reward.stock < 1:  # 确保库存至少为1
                messages.error(request, '奖品库存不足')
                return JsonResponse({'status': 'error'})
            
            # 检查用户金币
            if ctf_user.coins < reward.coins:
                messages.error(request, '金币不足')
                return JsonResponse({'status': 'error'})
            # 创建兑换记录
            exchange_record = ExchangeRecord.objects.create(
                user=request.user,
                reward=reward,
                contact=escape(contact)  # 转义联系方式
            )
            # 扣除用户金币
            ctf_user.coins -= reward.coins
            # 保存更改
            ctf_user.save()
            # 创建系统通知，使用安全的字符串格式化
            notification = SystemNotification.objects.create(
                title='奖品兑换成功',
                content=f'''
                    <p>您已成功兑换奖品：{escape(reward.name)}，数量：1件，消耗金币：{reward.coins}</p>
                    <p>联系方式：{escape(contact)}</p>
                    <p>我们会尽快处理您的兑换请求，若有疑问，请联系管理员。</p>
                '''
            )
            notification.get_p.add(request.user)
            # 给管理员发送通知
            admin_notification = SystemNotification.objects.create(
                title='新的奖品兑换请求',
                content=f'''
                    <p>用户 {escape(request.user.username)} 兑换了奖品</p>
                    <p>奖品名称：{escape(reward.name)}</p>
                    <p>联系方式：{escape(contact)}</p>
                    <p>请及时处理该兑换请求。</p>
                '''
            )
            # 添加所有超级管理员为通知接收者
            superusers = User.objects.filter(is_superuser=True)
            admin_notification.get_p.add(*superusers)
            
            messages.success(request, '兑换成功！我们会尽快处理您的兑换请求。')
            return JsonResponse({'status': 'success'})
            
    except RewardItem.DoesNotExist:
        messages.error(request, '奖品不存在')
    except CTFUser.DoesNotExist:
        messages.error(request, '用户信息不存在')
    except ValidationError as e:
        messages.error(request, str(e))
    except Exception as e:
        
        messages.error(request, '兑换失败，请稍后重试')
    
    # 如果执行到这里，说明出现了错误，删除防抖缓存，允许用户重试
    cache.delete(cache_key)
    return JsonResponse({'status': 'error'})

# 辅助函数：将datetime对象转换为字符串
def datetime_handler(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

def get_visible_problems(author, viewer):
    """获取对特定用户可见的题目列表"""
    # 缓存键：用户ID + 查看者ID
    cache_key = f'visible_problems_{author.id}_{viewer.id if viewer else "anonymous"}'
    cached_data = cache.get(cache_key)
    
    if cached_data is not None:
        return cached_data
    
    base_query = PC_Challenge.objects.filter(author=author)
    
    # 如果是管理员或作者本人，可以看到所有题目
    if viewer and (viewer.is_staff or viewer == author):
        problems = base_query
    else:
        # 其他用户只能看到激活的题目
        problems = base_query.filter(is_active=True)
    
    # 提前处理所有数据，避免在模板中访问关系管理器
    result = []
    for problem in problems.order_by('-created_at'):
        # 将tags转换为列表，避免使用关系管理器
        tags_list = [{'name': tag.name} for tag in problem.tags.all()] if hasattr(problem, 'tags') else []
        
        result.append({
            'title': problem.title,
            'url': problem.get_absolute_url(),
            'tags': tags_list,  # 预先获取所有标签
            'solves': problem.solves,
            'create_time': problem.created_at,
            'category': problem.category,
            'description': problem.description,
            'is_active': problem.is_active,
            'allocated_coins': problem.allocated_coins
        })
    
    # 缓存结果，有效期30分钟
    cache.set(cache_key, result, 60 * 30)
    return result

def get_user_content(user, content_type='articles', is_self=True, viewer=None):
    """获取用户的特定类型内容"""
    # 缓存键：用户ID + 内容类型 + 是否自己 + 查看者ID
    cache_key = f'user_content_{user.id}_{content_type}_{is_self}_{viewer.id if viewer else "anonymous"}'
    cached_data = cache.get(cache_key)
    
    if cached_data is not None:
        return cached_data
    
    # 评论相关的内容
    if content_type == 'comments' and is_self:
        # 使用select_related减少数据库查询
        article_comments = ArticleComment.objects.filter(author=user).select_related('belong')
        challenge_comments = ChallengeComment.objects.filter(author=user).select_related('belong')
        
        article_comments_list = [{
            'content': comment.content_to_markdown(),
            'create_date': comment.create_date,
            'target_title': comment.belong.title,
            'target_type': '文章',
            'target_id': comment.belong.id,
            'target_time': comment.belong.create_date,
            'url': comment.belong.get_absolute_url(),
        } for comment in article_comments]
        
        challenge_comments_list = [{
            'content': comment.content_to_markdown(),
            'create_date': comment.create_date,
            'target_title': comment.belong.title,
            'target_type': '题目',
            'target_id': comment.belong.id,
            'target_time': comment.belong.created_at,
            'url': comment.belong.get_absolute_url(),
        } for comment in challenge_comments]
        
        result = sorted(
            chain(article_comments_list, challenge_comments_list),
            key=lambda x: x['create_date'],
            reverse=True
        )
        
        # 缓存结果，有效期15分钟（评论更新较频繁）
        cache.set(cache_key, result, 60 * 15)
        return result
    
    # 其他类型的内容
    queries = {
        'problems': get_visible_problems(user, viewer),
        'competitions': get_user_competitions(user, viewer),
        'resources': get_user_resources(user, is_self,viewer),
        'teams': get_user_teams(user, viewer),
        'collects': get_user_favorites(user, viewer)
    }
    
    result = queries.get(content_type, [])

    # 缓存结果，有效期30分钟
    cache.set(cache_key, result, 60 * 30)
    return result






def get_user_favorites(author, viewer=None):
    """
    返回 author 的收藏（包括题目和岗位），按 viewer 的权限过滤
    如果 author 省略，就默认等于 viewer（看自己的列表）
    """


    cache_key = f'favorites_{author.id}_{viewer.id}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    ctf_user, _ = CTFUser.objects.get_or_create(user=author)
    
    # 获取收藏的题目
    challenges_qs = ctf_user.collect_challenges.all()
    # 权限：只有作者本人或管理员可看「未激活」收藏
    if not (viewer == author or author.is_staff):
        challenges_qs = challenges_qs.filter(is_active=True)
    challenges_qs = challenges_qs.prefetch_related('tags').order_by('-created_at')
    
    challenges_result = [
        {
            'type': 'challenge',  # 标记类型
            'title': p.title,
            'url': p.get_absolute_url(),
            'tags': [{'name': t.name} for t in p.tags.all()],
            'solves': p.solves,
            'create_time': p.created_at,
            'category': p.category,
            'description': p.description,
            'is_active': p.is_active,
            'allocated_coins': p.allocated_coins,
        }
        for p in challenges_qs
    ]
    
    # 获取收藏的岗位
    from recruit.models import Job
    jobs_qs = ctf_user.collect_jobs.filter(is_published=True, expire_at__gte=timezone.now())
    jobs_qs = jobs_qs.prefetch_related('page_keywords', 'companyname', 'cityname').order_by('-created_at')
    
    jobs_result = [
        {
            'type': 'job',  # 标记类型
            'title': job.title,
            'url': job.get_absolute_url(),
            'tags': [{'name': t.name} for t in job.page_keywords.all()],
            'salary_desc': job.salary_desc,
            'create_time': job.created_at,
            'track': job.get_track_display(),
            'recruitment_type': job.get_RecruitmentType_display(),
            'description': job.description,
            'companies': [{'name': c.company_name} for c in job.companyname.all()],
            'cities': [{'name': c.name} for c in job.cityname.all()],
            'views': job.views,
        }
        for job in jobs_qs
    ]
    
    # 合并并按时间排序
    result = sorted(
        challenges_result + jobs_result,
        key=lambda x: x['create_time'],
        reverse=True
    )
    
    cache.set(cache_key, result, 60*10)
    return result

def get_user_competitions(user, viewer):
    """获取用户参与的比赛"""
    # 缓存键
    cache_key = f'user_competitions_{user.id}_{viewer.id if viewer else "anonymous"}'
    cached_data = cache.get(cache_key)
    
    if cached_data is not None:
        return cached_data
    
    # 优化查询：使用prefetch_related减少数据库查询
    created_competitions = Competition.objects.filter(author=user).select_related('author')
    
    # 使用单次查询获取用户参与的比赛
    user_teams = Team.objects.filter(members=user).select_related('competition', 'leader')
    participated_competitions = [team.competition for team in user_teams]
    
    # 合并并去重
    all_competitions = list(created_competitions)
    for comp in participated_competitions:
        if comp not in all_competitions:
            all_competitions.append(comp)
    
    result = [{
        'title': comp.title,
        'url': reverse('competition:competition_detail', kwargs={'slug': comp.slug}),
        'description': comp.description,
        'create_time': comp.start_time,
        'start_time': comp.start_time,
        'end_time': comp.end_time,
        'status': comp.get_status_display(),
        'is_author': comp.author == user,
        'competition_type': '个人赛' if comp.competition_type == 'individual' else '团体赛'
    } for comp in all_competitions]
    
    # 缓存结果，有效期30分钟
    cache.set(cache_key, result, 60*10)
    return result

def get_user_resources(user, is_self, viewer):
    """获取用户的资源"""
    # 缓存键
    cache_key = f'user_resources_{user.id}_{viewer.id if viewer else "anonymous"}'
    cached_data = cache.get(cache_key)
    
    if cached_data is not None:
        return cached_data
    
    # 优化查询：使用select_related减少数据库查询
    if is_self:
        static_files = StaticFile.objects.filter(author=user).select_related('author')
        docker_images = DockerImage.objects.filter(author=user).select_related('author')

    
        resources = []
        
        # 添加静态文件资源
        for sf in static_files:
            try:
                # 安全地获取文件大小
                if hasattr(sf, 'file') and sf.file:
                    try:
                        file_size = sf.file.size
                    except (FileNotFoundError, OSError):
                        # 如果文件不存在，使用数据库中存储的大小
                        file_size = sf.file_size
                else:
                    file_size = 0
                
                resources.append({
                    'title': sf.name,
                    'url': reverse('container:static_file_list'),
                    'type': '文件',
                    'create_time': sf.upload_time,
                    'size': file_size,
                    'description': sf.description or '无描述'
                })
            except Exception as e:
                # 记录错误但不中断处理
                logger = logging.getLogger('apps.public')
                logger.error(f"处理静态文件资源时出错: {str(e)}")
                continue
        
        # 添加 Docker Compose 资源
        for di in docker_images:
            try:
                resources.append({
                    'title': di.name,
                    'url': reverse('container:docker_image_list'),
                    'type': '镜像',
                    'create_time': di.created_at,
                    'size': 0,
                })
            except Exception as e:
                # 记录错误但不中断处理
                logger = logging.getLogger('apps.public')
                logger.error(f"处理Docker Compose资源时出错: {str(e)}")
                continue
        
        # 按创建时间排序
        result = sorted(resources, key=lambda x: x['create_time'], reverse=True)
        
        # 缓存结果，有效期30分钟
        cache.set(cache_key, result, 60*10)
        return result
    else:
        return []

def get_user_teams(user, viewer):
    """获取用户创建或加入的队伍"""
    # 缓存键
    cache_key = f'user_teams_{user.id}_{viewer.id if viewer else "anonymous"}'
    cached_data = cache.get(cache_key)
    
    if cached_data is not None:
        return cached_data
    
    # 优化查询：使用select_related和prefetch_related减少数据库查询
    created_teams = Team.objects.filter(leader=user).select_related('competition', 'leader').prefetch_related('members')
    joined_teams = Team.objects.filter(members=user).exclude(leader=user).select_related('competition', 'leader').prefetch_related('members')
    
    teams = []
    
    # 添加一个脱敏处理函数
    def mask_username(username):
        """对用户名进行脱敏处理"""
        if len(username) <= 5:
            # 用户名较短时，只显示前两个字符
            return username[0:2] + '***'
        else:
            # 用户名较长时，显示前3个和后2个字符
            return username[0:2] + '***' + username[-2:]

    # 添加创建的队伍
    for team in created_teams:
        # 获取队伍成员信息（脱敏处理）
        members = []
        for member in team.members.all():
            members.append({
                'username': member.username,
                'is_leader': member == team.leader
            })
       
        teams.append({
            'id': team.id,
            'title': team.name,
            'team_code': team.team_code,
            'url': reverse('competition:competition_detail', kwargs={'slug': team.competition.slug}),
            'competition': team.competition.title,
            'create_time': team.created_at,
            'role': '队长',
            'member_count': team.members.count(),
            'max_members': team.member_count,
            'description': f'{team.competition.title} 的参赛队伍',
            'members': members
        })

    # 添加加入的队伍
    for team in joined_teams:
        # 获取队伍成员信息（脱敏处理）
        members = []
        for member in team.members.all():
            members.append({
                'username': member.username,
                'is_leader': member == team.leader
            })
        
        teams.append({
            'id': team.id,
            'title': team.name,
            'team_code': team.team_code,
            'url': reverse('competition:competition_detail', kwargs={'slug': team.competition.slug}),
            'competition': team.competition.title,
            'create_time': team.created_at,
            'role': '队员',
            'member_count': team.members.count(),
            'max_members': team.member_count,
            'description': f'{team.competition.title} 的参赛队伍',
            'members': members
        })
    
    # 按创建时间排序
    result = sorted(teams, key=lambda x: x['create_time'], reverse=True)
    
    # 缓存结果，有效期15分钟（队伍信息可能变化较频繁）
    cache.set(cache_key, result, 60*10)
    return result

@login_required
def profileViews(request, user_uuid):
    user = get_object_or_404(Ouser, uuid=user_uuid)
    is_self = request.user.uuid == user_uuid
    
    # 记录日志
    logger = logging.getLogger('apps.public')
    
    
    # 定义内容类型（带授权模块信息）
    if is_self:
        content_types = {
            'problems': {'name': '我的题目', 'module': 'practice'},
            'collects': {'name': '我的收藏', 'module': 'practice'},  # 不需要特定模块
            'comments': {'name': '评论', 'module': None},  # 不需要特定模块
            'competitions': {'name': '我的比赛', 'module': 'competition'},
            'resources': {'name': '我的资源', 'module': 'container'},  # 不需要特定模块
            'teams': {'name': '我的队伍', 'module': 'competition'}
        }
    else:
        content_types = {
            'problems': {'name': '他的题目', 'module': 'practice'},
            'collects': {'name': '他的收藏', 'module': 'practice'},
            'comments': {'name': '评论', 'module': None},
            'competitions': {'name': '他的比赛', 'module': 'competition'},
            'resources': {'name': '他的资源', 'module': 'container'},
            'teams': {'name': '他的队伍', 'module': 'competition'}
        }
    
    # 获取当前内容类型
    content_type = request.GET.get('type', 'articles')
    if content_type not in content_types:
        content_type = 'articles'  # 默认显示文章
    
    # 获取内容，传入当前查看者
    content_items = get_user_content(user, content_type, is_self, viewer=request.user)
    
    # 分页相关
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 10))
    if per_page not in [10, 20, 50]:
        per_page = 10

    paginator = Paginator(content_items, per_page)

    try:
        current_page = paginator.page(page)
    except:
        current_page = paginator.page(1)
        
    # 获取用户排名和统计信息（使用缓存）
    user_rank, num_problems = get_user_stats(user, is_self, viewer=request.user)
    
    context = {
        'profile_user': user,
        'is_self': is_self,
        'content_type': content_type,
        'content_types': content_types,
        'content_items': current_page.object_list,
        'total_count': paginator.count,
        'current_page': current_page.number,
        'has_previous': current_page.has_previous(),
        'has_next': current_page.has_next(),
        'per_page': per_page, 
        'user_rank': user_rank,
        'num_problems': num_problems,
    }
    
    return render(request, 'public/profile.html', context)

def get_user_stats(user, is_self=True, viewer=None):
    """获取用户排名和统计信息（带缓存）"""
    # 缓存键
    cache_key = f'user_stats_{user.id}_{is_self}_{viewer.id if viewer else "anonymous"}'
    cached_data = cache.get(cache_key)
    
    if cached_data is not None:
        return cached_data
    
    user_rank = CTFUser.objects.filter(user=user).first()
    
    
    # 根据查看者的权限显示题目数量
    if viewer and (viewer.is_staff or viewer == user):
        num_problems = PC_Challenge.objects.filter(author=user).count()
    else:
        num_problems = PC_Challenge.objects.filter(author=user, is_active=True).count()
    
    # 获取比赛数量（优化查询）
   
    
    result = (user_rank, num_problems)
    
    # 缓存结果，有效期1小时
    cache.set(cache_key, result, 60*10)
    return result

@login_required
def reward_list(request):
 
    rewards = RewardItem.objects.filter(is_active=True)
    try:
        ctf_user = request.user.ctfuser
        user_coins = ctf_user.coins
    except CTFUser.DoesNotExist:
        user_coins = 0
    
    context = {
        'rewards': rewards,
        'user_coins': user_coins,
        'hide_footer': True,
    }
    return render(request, 'public/reward.html', context)
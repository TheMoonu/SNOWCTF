from django.shortcuts import render

# Create your views here.
# jobs/views.py
import markdown
import time
from django.utils import timezone
from django.db.models import Count, Q
from django.views import generic
from django.views.generic import ListView
from recruit.models import Job
from django.conf import settings
from django.core.cache import cache
from django.template import Template, Context
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.template import loader
from django.shortcuts import get_object_or_404, render, reverse, redirect
from django.utils.decorators import method_decorator
from django.utils.text import slugify
from django.contrib.auth.decorators import login_required
from public.models import CTFUser

from public.utils import (check_request_headers,
                            CustomHtmlFormatter,
                    )
from markdown.extensions.codehilite import CodeHiliteExtension
from markdown.extensions.toc import TocExtension 

from utils.markdown_ext import (
    DelExtension,
    IconExtension,
    AlertExtension,
    CodeItemExtension,
    CodeGroupExtension
)


def make_markdown():
    md = markdown.Markdown(extensions=[
        'markdown.extensions.extra',
        'markdown_checklist.extension',
        CodeHiliteExtension(pygments_formatter=CustomHtmlFormatter),
        TocExtension(slugify=slugify),
        DelExtension(),
        IconExtension(),
        AlertExtension(),
        CodeItemExtension(),
        CodeGroupExtension()
    ])
    return md


class JobListView(ListView):
    model = Job
    template_name = 'recruit/job_list.html'
    context_object_name = 'job_list'
    paginate_by = 20
    def get_paginate_by(self, queryset):
        # 如果是 AJAX 请求（加载更多），返回 6
        # 如果是首次加载，返回 12
        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return 6  # 每次加载6个，保证每列能加载2个
        return 12  # 首次加载12个，每列4个

    def get_queryset(self):
        qs = Job.active.all()               # 未过期 + 已发布
        track = self.request.GET.get('track')
        city = self.request.GET.get('city')
        types = self.request.GET.get('type')
        q = self.request.GET.get('q')

        # 排序功能
        sort = self.request.GET.get('sort')
        if sort == 'views':
            qs = qs.order_by('-views')
        elif sort == 'salary':
            # 按薪资排序（使用最高薪资或最低薪资，空值排在最后）
            qs = qs.order_by('-salary_max', '-salary_min')
        else:
            # 默认按创建时间排序
            qs = qs.order_by('-created_at')

        # 筛选条件
        if track:
            qs = qs.filter(track=track)
        if city:
            qs = qs.filter(cityname__name=city)
        if types:
            qs = qs.filter(RecruitmentType=types)
        if q:
            # 跨 3 张表模糊匹配
            qs = qs.filter(
                Q(title__icontains=q) |
                Q(page_keywords__name__icontains=q) |   # Tag 名称
                Q(companyname__company_name__icontains=q)  # Company 名称
            ).distinct()          # 多对多可能产生重复行，加 distinct 去重
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['now'] = timezone.now()
        # 获取职位总数
        ctx['total_Job'] = self.get_queryset().count()
        # 方向下拉
        ctx['job_track_choices'] = Job.SECURITY_TRACK_CHOICES
        ctx['RecruitmentType'] = Job.Recruitment_Type
        # 城市下拉 - 修复查询逻辑
        from recruit.models import City
        ctx['cities'] = City.objects.filter(
            job__is_published=True,
            job__expire_at__gte=timezone.now()
        ).distinct().order_by('name').values_list('name', flat=True)
        
        # 添加分页范围
        paginator = ctx['paginator']
        page_obj = ctx['page_obj']
        ctx['page_range'] = self.get_page_range(paginator, page_obj)
        
        # 示例：如果需要隐藏页脚，取消下面这行的注释
        ctx['hide_footer'] = True
        
        return ctx
    
    def get_page_range(self, paginator, page_obj, on_each_side=2, on_ends=1):
        """生成分页范围，包含省略号"""
        page_range = []
        number = page_obj.number
        total_pages = paginator.num_pages
        
        # 左侧处理
        if number > on_each_side + on_ends + 1:
            for i in range(1, on_ends + 1):
                page_range.append(i)
            page_range.append("...")
            for i in range(number - on_each_side, number):
                page_range.append(i)
        else:
            for i in range(1, number):
                page_range.append(i)
        
        # 当前页
        page_range.append(number)
        
        # 右侧处理
        if number < total_pages - on_each_side - on_ends:
            for i in range(number + 1, number + on_each_side + 1):
                page_range.append(i)
            page_range.append("...")
            for i in range(total_pages - on_ends + 1, total_pages + 1):
                page_range.append(i)
        else:
            for i in range(number + 1, total_pages + 1):
                page_range.append(i)
        
        return page_range


class JobDetailView(generic.DetailView):
    model = Job
    context_object_name = 'jobdetail'


    def get_queryset(self):
        # 普通用户只能看已发布的职位，超级管理员可以看到所有职位
        queryset = super().get_queryset()
        # 超级管理员访问所有职位（包括未发布的）
        if self.request.user.is_authenticated and self.request.user.is_superuser:
            return queryset
        # 其他用户只能访问已发布的职位
        return queryset.filter(is_published=True)


    def get_object(self, queryset=None):
        obj = super().get_object()
        # 设置浏览量增加时间判断，同一职位两次浏览超过半小时才重新统计阅览量，超级管理员浏览忽略
        u = self.request.user
        if check_request_headers(self.request.headers):  # 请求头校验通过才计算阅读量
            ses = self.request.session
            the_key = self.context_object_name + ':read:{}'.format(obj.id)
            is_read_time = ses.get(the_key)
            if u.is_superuser:
                pass
            else:
                if not is_read_time:
                    obj.update_views()
                    ses[the_key] = time.time()
                else:
                    now_time = time.time()
                    t = now_time - is_read_time
                    if t > 60 * 30:
                        obj.update_views()
                        ses[the_key] = time.time()

        ud = obj.updated_at.strftime("%Y%m%d%H%M%S")
        md_key = self.context_object_name + ':markdown:{}:{}'.format(obj.id, ud)
        cache_md = cache.get(md_key)
        if cache_md and settings.DEBUG is False:
            obj.description, obj.requirements = cache_md
        else:
            md = make_markdown()
            obj.description = md.convert(obj.description)
            obj.requirements = md.convert(obj.requirements)
            cache.set(md_key, (obj.description, obj.requirements), 3600 * 24 * 7)
        return obj


class JobdetailView(JobDetailView):
    template_name = 'recruit/jobdetail.html'

    def get(self, request, *args, **kwargs):
        # 获取实例
        instance = self.get_object()
        return super().get(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # 如果用户已登录，检查是否收藏
        if self.request.user.is_authenticated:
            ctf_user, _ = CTFUser.objects.get_or_create(user=self.request.user)
            context['is_collected'] = ctf_user.collect_jobs.filter(slug=self.object.slug).exists()
        else:
            context['is_collected'] = False
        return context


# 城市详情页
class CityDetailView(generic.DetailView):
    model = None  # 延迟导入避免循环
    template_name = 'recruit/city_detail.html'
    context_object_name = 'city'
    slug_field = 'slug'
    paginate_by = 12  # 每页显示12个职位
    
    def get_model(self):
        from recruit.models import City
        return City
    
    def get_queryset(self):
        return self.get_model().objects.all()
    
    def get_context_data(self, **kwargs):
        from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
        from django.core.cache import cache
        
        context = super().get_context_data(**kwargs)
        city = self.object
        page = self.request.GET.get('page', 1)
        
        # 缓存键：包含城市slug和页码
        cache_key = f'city_jobs:{city.slug}:page:{page}'
        cached_data = cache.get(cache_key)
        
        if cached_data:
            # 使用缓存数据
            context.update(cached_data)
        else:
            # 获取该城市的所有活跃职位
            jobs_list = Job.active.filter(cityname=city).order_by('-created_at')
            total_jobs = jobs_list.count()
            
            # 分页
            paginator = Paginator(jobs_list, self.paginate_by)
            
            try:
                jobs = paginator.page(page)
            except PageNotAnInteger:
                jobs = paginator.page(1)
            except EmptyPage:
                jobs = paginator.page(paginator.num_pages)
            
            page_range = self.get_page_range(paginator, jobs)
            
            # 准备缓存数据
            cache_data = {
                'jobs': jobs,
                'page_obj': jobs,
                'paginator': paginator,
                'page_range': page_range,
                'total_jobs': total_jobs,
            }
            
            # 缓存10分钟（600秒）
            cache.set(cache_key, cache_data, 600)
            
            context.update(cache_data)
        
        return context
    
    def get_page_range(self, paginator, page_obj, on_each_side=2, on_ends=1):
        """生成分页范围，包含省略号"""
        page_range = []
        number = page_obj.number
        total_pages = paginator.num_pages
        
        # 左侧处理
        if number > on_each_side + on_ends + 1:
            for i in range(1, on_ends + 1):
                page_range.append(i)
            page_range.append("...")
            for i in range(number - on_each_side, number):
                page_range.append(i)
        else:
            for i in range(1, number):
                page_range.append(i)
        
        # 当前页
        page_range.append(number)
        
        # 右侧处理
        if number < total_pages - on_each_side - on_ends:
            for i in range(number + 1, number + on_each_side + 1):
                page_range.append(i)
            page_range.append("...")
            for i in range(total_pages - on_ends + 1, total_pages + 1):
                page_range.append(i)
        else:
            for i in range(number + 1, total_pages + 1):
                page_range.append(i)
        
        return page_range


# 公司详情页
class CompanyDetailView(generic.DetailView):
    model = None  # 延迟导入避免循环
    template_name = 'recruit/company_detail.html'
    context_object_name = 'company'
    slug_field = 'slug'
    paginate_by = 12  
    
    def get_model(self):
        from recruit.models import Company
        return Company
    
    def get_queryset(self):
        return self.get_model().objects.all()
    
    def get_context_data(self, **kwargs):
        from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
        from django.core.cache import cache
        
        context = super().get_context_data(**kwargs)
        company = self.object
        page = self.request.GET.get('page', 1)
        
        # 缓存键：包含公司slug和页码
        cache_key = f'company_jobs:{company.slug}:page:{page}'
        cached_data = cache.get(cache_key)
        
        if cached_data:
            # 使用缓存数据
            context.update(cached_data)
        else:
            # 获取该公司的所有活跃职位
            jobs_list = Job.active.filter(companyname=company).order_by('-created_at')
            total_jobs = jobs_list.count()
            
            # 分页
            paginator = Paginator(jobs_list, self.paginate_by)
            
            try:
                jobs = paginator.page(page)
            except PageNotAnInteger:
                jobs = paginator.page(1)
            except EmptyPage:
                jobs = paginator.page(paginator.num_pages)
            
            page_range = self.get_page_range(paginator, jobs)
            
            # 准备缓存数据
            cache_data = {
                'jobs': jobs,
                'page_obj': jobs,
                'paginator': paginator,
                'page_range': page_range,
                'total_jobs': total_jobs,
            }
            
            # 缓存10分钟（600秒）
            cache.set(cache_key, cache_data, 600)
            
            context.update(cache_data)
        
        return context
    
    def get_page_range(self, paginator, page_obj, on_each_side=2, on_ends=1):
        """生成分页范围，包含省略号"""
        page_range = []
        number = page_obj.number
        total_pages = paginator.num_pages
        
        # 左侧处理
        if number > on_each_side + on_ends + 1:
            for i in range(1, on_ends + 1):
                page_range.append(i)
            page_range.append("...")
            for i in range(number - on_each_side, number):
                page_range.append(i)
        else:
            for i in range(1, number):
                page_range.append(i)
        
        # 当前页
        page_range.append(number)
        
        # 右侧处理
        if number < total_pages - on_each_side - on_ends:
            for i in range(number + 1, number + on_each_side + 1):
                page_range.append(i)
            page_range.append("...")
            for i in range(total_pages - on_ends + 1, total_pages + 1):
                page_range.append(i)
        else:
            for i in range(number + 1, total_pages + 1):
                page_range.append(i)
        
        return page_range


# 收藏岗位功能
@login_required
def toggle_job_collect(request, slug):
    """收藏/取消收藏岗位"""
    job = get_object_or_404(Job, slug=slug)
    ctf_user, created = CTFUser.objects.get_or_create(user=request.user)

    if job in ctf_user.collect_jobs.all():
        ctf_user.collect_jobs.remove(job)
        return JsonResponse({"message": "已取消收藏", "collected": False})
    else:
        ctf_user.collect_jobs.add(job)
        return JsonResponse({"message": "收藏成功", "collected": True})
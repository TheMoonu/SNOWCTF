import time

import markdown
from django.conf import settings
from django.core.cache import cache
from django.template import Template, Context
from django.db.models import Count, Q
from django.http import (
    Http404,
    HttpResponseForbidden,
    JsonResponse,
    HttpResponseBadRequest
)
from django.shortcuts import get_object_or_404, render, reverse, redirect
from django.utils.decorators import method_decorator
from django.utils.text import slugify
from django.views import generic
from django.views.decorators.http import require_http_methods
from haystack.generic_views import SearchView  # 导入搜索视图
from haystack.query import SearchQuerySet
from markdown.extensions.codehilite import CodeHiliteExtension
from markdown.extensions.toc import TocExtension 
from django.views import generic
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.template import loader
from django.template.loader import render_to_string# 锚点的拓展

from blog.models import Article, Tag, Category, Timeline, Silian, AboutBlog, FriendLink, Subject,vulnerabilitywiki,PrivacyBlog
from blog.utils import (site_full_url,
                    CustomHtmlFormatter,
                    ApiResponse,
                    ErrorApiResponse,
                    add_views,
                    check_request_headers)
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


# Create your views here.

def test_page_view(request):
    return render(request, 'blog/test.html')

def index(request):
    """Wiki 首页 - 显示 AI 对话和搜索"""
    subjects = Subject.objects.all().order_by('sort_order')
    recent_articles = Article.objects.filter(is_publish=True).order_by('-create_date')[:10]
    
    context = {
        'subjects': subjects,
        'recent_articles': recent_articles,
        'hide_footer': True,
    }
    return render(request, 'blog/wiki_index.html', context)

class ArchiveView(generic.ListView):
    model = Article
    template_name = 'blog/archive.html'
    context_object_name = 'articles'
    paginate_by = 200
    paginate_orphans = 50

    def get_queryset(self, **kwargs):
        queryset = super().get_queryset()
        return queryset.filter(is_publish=True)




class IndexView(generic.ListView):
    model = Article
    template_name = 'blog/blogindex.html'
    context_object_name = 'articles'
    paginate_orphans = getattr(settings, 'BASE_ORPHANS', 0)

    def get_paginate_by(self, queryset):
        # 如果是 AJAX 请求（加载更多），返回 6
        # 如果是首次加载，返回 12
        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return 6  # 每次加载6个，保证每列能加载2个
        return 12  # 首次加载12个，每列4个

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # 获取文章总数
        context['total_articles'] = self.get_queryset().count()
        if hasattr(self, 'paginator'):
            context['paginator'] = self.paginator
        context['hide_footer'] = True
        return context

    def get_ordering(self):
        sort = self.request.GET.get('sort')
        if sort == 'views':
            return '-views', '-update_date', '-id'
        return '-is_top', '-create_date'

    def get_queryset(self):
        queryset = super().get_queryset().filter(is_publish=True)
        sort = self.request.GET.get('sort')
        if sort == 'comment':
            queryset = queryset.annotate(com=Count('article_comments')).order_by('-com', '-views')
        else:
            queryset = queryset.order_by(*self.get_ordering())
        return queryset

    def get(self, request, *args, **kwargs):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            try:
                page = int(request.GET.get('page', 1))
                articles = self.get_queryset()
                total_count = articles.count()
                
                # 计算偏移量和限制
                per_page = 12  # 每页6篇文章
                start = (page - 1) * per_page
                end = min(start + per_page, total_count)
                
                # 检查是否还有更多文章
                has_next = end < total_count
                
                # 获取当前页的文章
                current_articles = articles[start:end]
                
                # 打印调试信息
                template = Template('{% load blog_tags %}{% load_article_summary articles user %}')
                context = Context({
                    'articles': current_articles,
                    'user': request.user,
                })
                
                html_content = template.render(context)
                
                return JsonResponse({
                    'html': html_content,
                    'has_next': has_next,
                    'total': total_count,
                    'current_page': page,
                    'loaded': end,
                    'remaining': total_count - end
                })
            except Exception as e:
                print(f"Error loading more articles: {e}")
                return JsonResponse({
                    'html': '', 
                    'has_next': False, 
                    'error': str(e)
                })
                
        return super().get(request, *args, **kwargs)







""" class IndexView(generic.ListView):
    model = Article
    template_name = 'blog/blogindex.html'
    context_object_name = 'articles'
    paginate_orphans = getattr(settings, 'BASE_ORPHANS', 0)

    

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['total_articles'] = self.get_queryset().count()
        if hasattr(self, 'paginator'):
            context['paginator'] = self.paginator
        return context

    def get_ordering(self):
        sort = self.request.GET.get('sort')
        if sort == 'views':
            return '-views', '-update_date', '-id'
        return '-is_top', '-create_date'

    def get_queryset(self):
        queryset = super().get_queryset().filter(is_publish=True)
        sort = self.request.GET.get('sort')
        if sort == 'comment':
            queryset = queryset.annotate(com=Count('article_comments')).order_by('-com', '-views')
        else:
            queryset = queryset.order_by(*self.get_ordering())
        return queryset

    def get(self, request, *args, **kwargs):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            try:
                page = int(request.GET.get('page', 1))
                articles = self.get_queryset()
                total_count = articles.count()

                # 使用 Paginator 进行分页
                
                paginator = Paginator(articles,6)
                current_page = paginator.get_page(page)


                # 获取当前页的文章
                current_articles = current_page.object_list

                # 判断是否有更多文章
                has_next = current_page.has_next()

                # 渲染文章列表
                template = Template('{% load blog_tags %}{% load_article_summary articles user %}')
                context = Context({
                    'articles': current_articles,
                    'user': request.user
                })

                html_content = template.render(context)

                return JsonResponse({
                    'html': html_content,
                    'has_next': has_next,
                    'total': total_count,
                    'current_page': page,
                    'loaded': len(current_articles),
                    'remaining': total_count - len(current_articles) - (page * 6)
                })
            except Exception as e:
                print(f"Error loading more articles: {e}")
                return JsonResponse({
                    'html': '', 
                    'has_next': False, 
                    'error': str(e)
                })

        return super().get(request, *args, **kwargs) """



class BaseDetailView(generic.DetailView):
    model = Article
    context_object_name = 'article'

    def get_queryset(self):
        # 普通用户只能看发布的文章，作者和管理员可以看到未发布的
        queryset = super().get_queryset()
        # 非登录用户可以访问全部发布的文章
        if not self.request.user.is_authenticated:
            return queryset.filter(is_publish=True, is_memberShow = False)
        # 超级管理员访问所有
        if self.request.user.is_superuser:
            return queryset
        #会员访问用户访问出版且是会员类文章
        if self.request.user.is_member:
            return queryset.filter(is_publish=True)
        # 登录用户访问所有发布和自己的未发布
        return queryset.filter(is_publish=True, is_memberShow = False)

    def get_object(self, queryset=None):
        obj = super().get_object()
        # 设置浏览量增加时间判断,同一篇文章两次浏览超过半小时才重新统计阅览量,作者浏览忽略
        u = self.request.user
        if check_request_headers(self.request.headers):  # 请求头校验通过才计算阅读量
            ses = self.request.session
            the_key = self.context_object_name + ':read:{}'.format(obj.id)
            is_read_time = ses.get(the_key)
            if u == obj.author or u.is_superuser:
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
        # 获取文章更新的时间，判断是否从缓存中取文章的markdown,可以避免每次都转换
        ud = obj.update_date.strftime("%Y%m%d%H%M%S")
        md_key = self.context_object_name + ':markdown:{}:{}'.format(obj.id, ud)
        cache_md = cache.get(md_key)
        if cache_md and settings.DEBUG is False:
            obj.body, obj.toc = cache_md
        else:
            md = make_markdown()
            obj.body = md.convert(obj.body)
            obj.toc = md.toc
            cache.set(md_key, (obj.body, obj.toc), 3600 * 24 * 7)
        return obj


class DetailView(BaseDetailView):
    template_name = 'blog/detail.html'

    def get(self, request, *args, **kwargs):
        # 获取实例
        instance = self.get_object()
        # 如果有主题，则跳转到主题格式的文章详情页
        if instance.topic:
            redirect_url = reverse('blog:subject_detail', kwargs={'slug': instance.slug})
            return redirect(redirect_url)
        # 如果不满足条件，则继续处理视图逻辑
        return super().get(request, *args, **kwargs)


class SubjectDetailView(BaseDetailView):
    """
    专题文章视图
    """
    template_name = 'blog/subjectDetail.html'

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(topic__isnull=False)




class CategoryView(generic.ListView):
    model = Article
    template_name = 'blog/category.html'
    context_object_name = 'articles'
    paginate_orphans = getattr(settings, 'BASE_ORPHANS', 0)

    def get_paginate_by(self, queryset):
        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return 6
        return 12

    def get_ordering(self):
        sort = self.request.GET.get('sort')
        if sort == 'views':
            return '-views', '-update_date', '-id'
        return '-create_date'

    def get_queryset(self, **kwargs):
        queryset = super(CategoryView, self).get_queryset()
        cate = get_object_or_404(Category, slug=self.kwargs.get('slug'))
        if self.request.user.is_superuser or self.request.user.is_member:
            return queryset.filter(category=cate, is_publish=True)
        else: 
            return queryset.filter(category=cate, is_publish=True, is_memberShow=False)

    def get_context_data(self, **kwargs):
        context_data = super(CategoryView, self).get_context_data()
        cate = get_object_or_404(Category, slug=self.kwargs.get('slug'))
        context_data['search_tag'] = '文章分类'
        context_data['search_instance'] = cate
        return context_data

    def get(self, request, *args, **kwargs):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            try:
                page = int(request.GET.get('page', 1))
                articles = self.get_queryset()
                total_count = articles.count()
                
                per_page = 6
                start = (page - 1) * per_page
                end = min(start + per_page, total_count)
                
                has_next = end < total_count
                current_articles = articles[start:end]
                
                template = Template('{% load blog_tags %}{% load_article_summary articles user %}')
                context = Context({
                    'articles': current_articles,
                    'user': request.user
                })
                
                html_content = template.render(context)
                
                return JsonResponse({
                    'html': html_content,
                    'has_next': has_next,
                    'total': total_count,
                    'current_page': page,
                    'loaded': end,
                    'remaining': total_count - end
                })
            except Exception as e:
                print(f"Error loading more articles: {e}")
                return JsonResponse({
                    'html': '', 
                    'has_next': False, 
                    'error': str(e)
                })
                
        return super().get(request, *args, **kwargs)

class TagView(generic.ListView):
    model = Article
    template_name = 'blog/tag.html'
    context_object_name = 'articles'
    paginate_orphans = getattr(settings, 'BASE_ORPHANS', 0)

    def get_paginate_by(self, queryset):
        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return 6
        return 12

    def get_ordering(self):
        sort = self.request.GET.get('sort')
        if sort == 'views':
            return '-views', '-update_date', '-id'
        return '-create_date'

    def get_queryset(self, **kwargs):
        queryset = super(TagView, self).get_queryset()
        tag = get_object_or_404(Tag, slug=self.kwargs.get('slug'))
        
        if self.request.user.is_superuser or self.request.user.is_member:
            return queryset.filter(tags=tag, is_publish=True)
        else: 
            return queryset.filter(tags=tag, is_publish=True, is_memberShow=False)

    def get_context_data(self, **kwargs):
        context_data = super(TagView, self).get_context_data()
        tag = get_object_or_404(Tag, slug=self.kwargs.get('slug'))
        context_data['search_tag'] = '文章标签'
        context_data['search_instance'] = tag
        return context_data

    def get(self, request, *args, **kwargs):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            try:
                page = int(request.GET.get('page', 1))
                articles = self.get_queryset()
                total_count = articles.count()
                
                per_page = 6
                start = (page - 1) * per_page
                end = min(start + per_page, total_count)
                
                has_next = end < total_count
                current_articles = articles[start:end]
                
                template = Template('{% load blog_tags %}{% load_article_summary articles user %}')
                context = Context({
                    'articles': current_articles,
                    'user': request.user
                })
                
                html_content = template.render(context)
                
                return JsonResponse({
                    'html': html_content,
                    'has_next': has_next,
                    'total': total_count,
                    'current_page': page,
                    'loaded': end,
                    'remaining': total_count - end
                })
            except Exception as e:
                print(f"Error loading more articles: {e}")
                return JsonResponse({
                    'html': '', 
                    'has_next': False, 
                    'error': str(e)
                })
                
        return super().get(request, *args, **kwargs)


@add_views('blog:about', 'About页面')
def AboutView(request):
    obj = AboutBlog.objects.first()
    if obj:
        ud = obj.update_date.strftime("%Y%m%d%H%M%S")
        md_key = 'about:markdown:{}:{}'.format(obj.id, ud)
        cache_md = cache.get(md_key)
        if cache_md and settings.DEBUG is False:
            body = cache_md
        else:
            body = obj.body_to_markdown()
            cache.set(md_key, body, 3600 * 24 * 15)
    else:
        repo_url = 'https://www.secsnow.cn'
        body = '<li>网站地址：<a href="{}">{}</a></li>'.format(repo_url, repo_url)
    return render(request, 'blog/about.html', context={'body': body})

@add_views('blog:privacy', '隐私保护页面')
def PrivacyView(request):
    obj =  PrivacyBlog.objects.first()
    if obj:
        ud = obj.update_date.strftime("%Y%m%d%H%M%S")
        md_key = 'privacy:markdown:{}:{}'.format(obj.id, ud)
        cache_md = cache.get(md_key)
        if cache_md and settings.DEBUG is False:
            body = cache_md
        else:
            body = obj.body_to_markdown()
            cache.set(md_key, body, 3600 * 24 * 15)
    else:
        
        body = ''
    return render(request, 'blog/privacy.html', context={'body': body})



@method_decorator(add_views('blog:timeline', '时间线'), name='get')
class TimelineView(generic.ListView):
    model = Timeline
    template_name = 'blog/timeline.html'
    context_object_name = 'timeline_list'

    def get_ordering(self):
        return '-update_date',


class SilianView(generic.ListView):
    model = Silian
    template_name = 'blog/silian.xml'
    context_object_name = 'badurls'


@method_decorator(add_views('blog:friend', '友链'), name='get')
class FriendLinkView(generic.ListView):
    model = FriendLink
    template_name = 'blog/friend.html'
    context_object_name = 'friend_list'

    def get_queryset(self):
        queryset = super(FriendLinkView, self).get_queryset()
        return queryset.filter(is_show=True, is_active=True)


# 重写搜索视图，可以增加一些额外的参数，且可以重新定义名称
""" class MySearchView(SearchView):
    template_name = 'search/blog/search.html'
    context_object_name = 'search_list'
    paginate_by = getattr(settings, 'BASE_PAGE_BY', None)
    paginate_orphans = getattr(settings, 'BASE_ORPHANS', 0)
    queryset = SearchQuerySet().order_by('-views').filter(is_publish=True) """

# apps/blog/views.py


class MySearchView(SearchView):
    template_name = 'search/blog/search.html'
    context_object_name = 'search_list'
    paginate_by = 12  # 首次加载12个结果
    
    def get_queryset(self):
        """根据类型筛选搜索结果，优化排序"""
        search_type = self.request.GET.get('type', 'article')  # 默认为文章
        query = self.request.GET.get('q', '').strip()
        
        # 如果没有查询词或查询词太短，返回空结果
        if not query or len(query) < 2:
            return SearchQuerySet().none()
        
        sqs = SearchQuerySet()
        
        # 根据类型过滤
        if search_type == 'article':
            # 只搜索文章 - 使用更精准的排序：先按相关度，再按浏览量
            sqs = sqs.models(Article).auto_query(query).filter(is_publish=True)
            if not (self.request.user.is_superuser or self.request.user.is_member):
                sqs = sqs.filter(is_memberShow=False)
        elif search_type == 'challenge':
            # 只搜索靶场题目
            from practice.models import PC_Challenge
            sqs = sqs.models(PC_Challenge).auto_query(query).filter(is_active=True)
        elif search_type == 'job':
            # 只搜索岗位
            from recruit.models import Job
            sqs = sqs.models(Job).auto_query(query).filter(is_published=True)
        
        return sqs
    
    def get_context_data(self, **kwargs):
        """添加类型统计和过滤信息"""
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get('q', '').strip()
        
        # 确保 query 在 context 中
        context['query'] = query
        
        if query and len(query) >= 2:
            from practice.models import PC_Challenge
            from recruit.models import Job
            
            # 统计各类型的结果数量
            context['article_count'] = SearchQuerySet().models(Article).auto_query(query).filter(is_publish=True).count()
            context['challenge_count'] = SearchQuerySet().models(PC_Challenge).auto_query(query).filter(is_active=True).count()
            context['job_count'] = SearchQuerySet().models(Job).auto_query(query).filter(is_published=True).count()
            context['total_count'] = context['article_count'] + context['challenge_count'] + context['job_count']
        else:
            context['article_count'] = 0
            context['challenge_count'] = 0
            context['job_count'] = 0
            context['total_count'] = 0
        
        context['search_type'] = self.request.GET.get('type', 'article')
        
        return context

    def get(self, request, *args, **kwargs):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            try:
                query = request.GET.get('q', '').strip()
                # 验证查询长度（至少2个字符）
                if query and len(query) >= 2:
                    page = int(request.GET.get('page', 1))
                    
                    results = self.get_queryset().auto_query(query)
                    total_count = results.count()
                elif query and len(query) < 2:
                    # 查询太短，返回空结果
                    return JsonResponse({
                        'html': '<div class="col-12"><div class="alert alert-warning text-center">请输入至少2个字符进行搜索</div></div>',
                        'has_next': False,
                        'total': 0,
                        'current_page': 1,
                        'loaded': 0,
                        'remaining': 0
                    })
                else:
                    # 空查询
                    return JsonResponse({
                        'html': '',
                        'has_next': False,
                        'total': 0
                    })
                
                # 计算分页（只在查询有效时执行）
                per_page = 6  # AJAX请求时每次加载6个
                start = (page - 1) * per_page
                end = start + per_page
                
                current_results = results[start:end]
                
                html_content = render_to_string(
                    'search/blog/search_result_list.html',
                    {
                        'search_list': current_results,
                        'query': query,
                        'request': request
                    },
                    request
                )
                return JsonResponse({
                    'html': html_content,
                    'has_next': end < total_count,
                    'total': total_count,
                    'current_page': page,
                    'loaded': end,
                    'remaining': total_count - end
                })

                
                    
            except Exception as e:
                return JsonResponse({
                    'html': '', 
                    'has_next': False, 
                    'error': str(e)
                })
                
        return super().get(request, *args, **kwargs) # 首次加载使用默认值


class WikiSearchView(SearchView):
    """Wiki 专题文章搜索视图"""
    template_name = 'search/blog/wiki_search.html'
    context_object_name = 'search_list'
    paginate_by = 12
    
    def _highlight_text(self, text, query):
        """在文本中高亮显示查询关键词"""
        import re
        if not text or not query:
            return text
        
        # 分割查询词（支持多个关键词）
        keywords = query.strip().split()
        highlighted_text = text
        
        for keyword in keywords:
            if len(keyword) < 2:  # 跳过太短的词
                continue
            # 使用正则表达式进行大小写不敏感的替换
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
            highlighted_text = pattern.sub(
                lambda m: f'<mark class="search-highlight">{m.group(0)}</mark>',
                highlighted_text
            )
        
        return highlighted_text
    
    def get_queryset(self):
        """只搜索已发布的 wiki 文章（有 subject 关联的文章）"""
        query = self.request.GET.get('q', '').strip()
        
        # 如果没有查询词或查询词太短，返回空结果
        if not query or len(query) < 2:
            return SearchQuerySet().none()
        
        # 只搜索有专题的文章（通过 topic 关联），且已发布
        sqs = SearchQuerySet().models(Article).auto_query(query).filter(
            is_publish=True
        ).exclude(topic=None)  # 排除没有主题（专题）的文章
        
        # 如果不是超管或会员，过滤会员专属内容
        if not (self.request.user.is_superuser or self.request.user.is_member):
            sqs = sqs.filter(is_memberShow=False)
        
        return sqs
    
    def get_context_data(self, **kwargs):
        """添加查询信息"""
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get('q', '').strip()
        context['query'] = query
        
        if query and len(query) >= 2:
            # 统计 wiki 文章结果数量
            context['total_count'] = self.get_queryset().count()
        else:
            context['total_count'] = 0
        
        return context
    
    def get(self, request, *args, **kwargs):
        """处理 AJAX 请求，返回 JSON 格式的搜索结果"""
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            try:
                query = request.GET.get('q', '').strip()
                
                # 验证查询长度
                if not query or len(query) < 2:
                    return JsonResponse({
                        'success': False,
                        'message': '请输入至少2个字符进行搜索' if query else '请输入搜索关键词',
                        'results': [],
                        'total': 0
                    })
                
                # 获取搜索结果
                results = self.get_queryset()
                total_count = results.count()
                
                # 获取分页参数
                page = int(request.GET.get('page', 1))
                per_page = int(request.GET.get('per_page', 10))
                start = (page - 1) * per_page
                end = start + per_page
                
                current_results = results[start:end]
                
                # 构建结果列表，包含高亮内容
                articles_data = []
                for result in current_results:
                    article = result.object
                    if article:
                        # 通过 topic 获取 subject
                        subject = article.topic.subject if article.topic else None
                        
                        # 高亮处理：简单的关键词标记
                        title_highlighted = self._highlight_text(article.title, query)
                        summary_highlighted = self._highlight_text(
                            article.summary[:150] if article.summary else '', 
                            query
                        )
                        
                        articles_data.append({
                            'title': article.title,
                            'title_highlighted': title_highlighted,
                            'url': article.get_absolute_url(),
                            'subject': subject.name if subject else '',
                            'subject_url': subject.get_absolute_url() if subject else '',
                            'summary': article.summary[:100] if article.summary else '',
                            'summary_highlighted': summary_highlighted,
                            'views': article.views,
                            'create_date': article.create_date.strftime('%Y-%m-%d'),
                        })
                
                return JsonResponse({
                    'success': True,
                    'results': articles_data,
                    'total': total_count,
                    'page': page,
                    'per_page': per_page,
                    'has_next': end < total_count,
                    'loaded': min(end, total_count),
                    'remaining': max(0, total_count - end)
                })
                
            except Exception as e:
                return JsonResponse({
                    'success': False,
                    'message': f'搜索出错: {str(e)}',
                    'results': [],
                    'total': 0
                })
        
        # 非 AJAX 请求，返回正常页面
        return super().get(request, *args, **kwargs)


# 重写漏洞搜索视图，可以增加一些额外的参数，且可以重新定义名称

    

def robots(request):
    site_url = site_full_url()
    return render(request, 'robots.txt', context={'site_url': site_url}, content_type='text/plain')


class DetailEditView(generic.DetailView):
    """
    文章编辑视图
    """
    model = Article
    template_name = 'blog/articleEdit.html'
    context_object_name = 'article'

    def get_object(self, queryset=None):
        obj = super(DetailEditView, self).get_object()
        # 非作者及超管无权访问
        if not self.request.user.is_superuser and obj.author != self.request.user:
            raise Http404('Invalid request.')
        return obj


@require_http_methods(["POST"])
def update_article(request):
    """更新文章，仅管理员和作者可以更新"""
    if request.method == 'POST' and request.headers.get('x-requested-with') == 'XMLHttpRequest':
        article_slug = request.POST.get('article_slug')
        article_body = request.POST.get('article_body')
        article_img_link = request.POST.get('article_img_link')
        change_img_link_flag = request.POST.get('change_img_link_flag')

        try:
            article = Article.objects.get(slug=article_slug)
            # 检查当前用户是否是作者
            if not request.user.is_superuser and article.author != request.user:
                return HttpResponseForbidden("You don't have permission to update this article.")

            # 更新article模型的数据
            article.body = article_body
            if change_img_link_flag == 'true':
                article.img_link = article_img_link  # 更新封面图地址
            article.save()  # 这里不要设置更新的字段，不然会导致其他要在save更新的字段不更新

            callback = article.get_absolute_url()
            response_data = {'message': 'Success', 'data': {'callback': callback}, 'code': 0}
            return JsonResponse(response_data)
        except Article.DoesNotExist:
            return HttpResponseBadRequest("Article not found.")
    return HttpResponseBadRequest("Invalid request.")


def friend_add(request):
    """
    申请友链
    @param request:
    @return:
    """
    if request.method == "POST" and request.headers.get('x-requested-with') == 'XMLHttpRequest':
        data = request.POST
        name = data.get('name')
        description = data.get('description')
        link = data.get('link')

        try:
            friend = FriendLink.objects.create(name=name,
                                               description=description,
                                               link=link,
                                               is_active=False,
                                               is_show=True,
                                               )
            resp = ApiResponse()
            resp.data = {'id': friend.id}
            return resp.as_json_response()
        except Exception as e:
            resp = ErrorApiResponse()
            resp.error = str(e)
            return resp.as_json_response()

    return render(request, 'blog/friendAdd.html')


# 专题详情页
class SubjectPageDetailView(generic.DetailView):
    model = Subject
    template_name = 'blog/subject.html'
    context_object_name = 'subject'
    paginate_by = 100
    paginate_orphans = 50


# 专题列表页
class SubjectListView(generic.ListView):
    model = Subject
    template_name = 'blog/subjectIndex.html'
    context_object_name = 'subjects'
    paginate_by = 100
    paginate_orphans = 0


# dashboard页面，仅管理员可以访问，其他用户不能访问
def dashboard(request):
    if request.user.is_staff:
        return render(request, 'blog/dashboard.html')
    return render(request, '403.html')


# feed hub
def feed_hub(request):
    return render(request, 'blog/feedhub.html')


""" class SesDbListView(generic.ListView):
    model = Subject
    template_name = 'blog/secdbIndex.html'
    context_object_name = 'subjects'
    paginate_by = 200
    paginate_orphans = 50 """

class vulnerability_articles_view(generic.ListView):
    model = Article
    template_name = 'blog/vulnerabilitywiki.html'
    context_object_name = 'articles'



    
    



    
    

from django.shortcuts import render
from django.http import JsonResponse
from django.utils.html import mark_safe
from django.core.cache import cache
from blog.utils import add_views
from .apis.bd_push import push_urls, get_urls
from .apis.useragent import get_user_agent
from .apis.docker_search import DockerSearch
from .apis.word_cloud import jieba_word_cloud
from tool.utils import IMAGE_LIST
from django.contrib.auth.decorators import login_required
from .decorators import check_tool_permission
import re
import markdown
from public.utils import site_full_url


# Create your views here.


def Toolview(request):
    context = {
        'hide_footer': True,
    }
    return render(request, 'tool/tool.html', context)


# 百度主动推送
@check_tool_permission('tool:baidu_push')
@add_views('tool:baidu_push', '百度主动推送')
def BD_pushview(request):
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.method == "POST":
        data = request.POST
        url = data.get('url')
        urls = data.get('url_list')
        info = push_urls(url, urls)
        return JsonResponse({'msg': info})
    return render(request, 'tool/bd_push.html')


# 百度主动推送升级版，提取sitemap链接推送
@check_tool_permission('tool:baidu_push_site')
@add_views('tool:baidu_push_site', 'Sitemap主动推送')
def BD_pushview_site(request):
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.method == "POST":
        data = request.POST
        url = data.get('url')
        map_url = data.get('map_url')
        urls = get_urls(map_url)
        if urls == 'miss':
            info = "{'error':404,'message':'sitemap地址请求超时，请检查链接地址！'}"
        elif urls == '':
            info = "{'error':400,'message':'sitemap页面没有提取到有效链接，sitemap格式不规范。'}"
        else:
            info = push_urls(url, urls)
        return JsonResponse({'msg': info})
    return render(request, 'tool/bd_push_site.html')


# 在线正则表达式
@check_tool_permission('tool:regex')
@add_views('tool:regex', '在线正则表达式')
def regexview(request):
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.method == "POST":
        data = request.POST
        texts = data.get('texts')
        regex = data.get('r')
        key = data.get('key')
        lis = re.findall(r'{}'.format(regex), texts)
        num = len(lis)
        if key == 'url' and num:
            script_tag = '''<script>$(".re-result p").children("a").attr({target:"_blank",rel:"noopener noreferrer"});</script>'''
            result = '<br>'.join(['[{}]({})'.format(i, i) for i in lis])
        else:
            script_tag = ''
            info = '\n'.join(lis)
            result = "匹配到&nbsp;{}&nbsp;个结果：\n".format(
                num) + "```\n" + info + "\n```"
        result = markdown.markdown(result,
                                   extensions=[
                                       'markdown.extensions.extra',
                                       'markdown.extensions.codehilite',
                                   ])
        return JsonResponse({
            'result': mark_safe(result + script_tag),
            'num': num
        })
    return render(request, 'tool/regex.html')


# 生成请求头
@check_tool_permission('tool:useragent')
# @add_views('tool:useragent', 'User-Agent生成器')
def useragent_view(request):
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.method == "POST":
        data = request.POST
        d_lis = data.get('d_lis')
        os_lis = data.get('os_lis')
        n_lis = data.get('n_lis')
        d = d_lis.split(',') if len(d_lis) > 0 else None
        os = os_lis.split(',') if len(os_lis) > 0 else None
        n = n_lis.split(',') if len(n_lis) > 0 else None
        result = get_user_agent(os=os, navigator=n, device_type=d)
        return JsonResponse({'result': result})
    return render(request, 'tool/useragent.html')


# HTML特殊字符对照表
@check_tool_permission('tool:html_characters')
@add_views('tool:html_characters', 'HTML查询表')
def html_characters(request):
    return render(request, 'tool/characters.html')


# docker镜像查询
@check_tool_permission('tool:docker_search')
@add_views('tool:docker_search', 'Docker镜像查询')
def docker_search_view(request):
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.method == "POST":
        data = request.POST
        name = data.get('name')
        # 只有名称在常用镜像列表中的搜索才使用缓存，可以避免对名称的过滤
        if name in IMAGE_LIST:
            cache_key = 'tool:docker_search:' + name
            cache_value = cache.get(cache_key)
            if cache_value:
                res = cache_value
            else:
                ds = DockerSearch(name)
                res = ds.main()
                total = res.get('total')
                if total and total >= 20:
                    # 将查询到超过20条镜像信息的资源缓存一天
                    cache.set(cache_key, res, 60 * 60 * 24)
        else:
            ds = DockerSearch(name)
            res = ds.main()
        return JsonResponse(res, status=res['status'])
    return render(request, 'tool/docker_search.html')


@check_tool_permission('tool:markdown_editor')
@add_views('tool:markdown_editor', 'Markdown编辑器')
def editor_view(request):
    return render(request, 'tool/editor.html')


# 词云图
@check_tool_permission('tool:word_cloud')
@add_views('tool:word_cloud', '词云图')
def word_cloud(request):
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' and request.method == "POST":
        data = request.POST
        text = data.get('text')
        stop_text = data.get('stop_text')
        res = jieba_word_cloud(text, stop_text)
        return JsonResponse(res)
    return render(request, 'tool/word_cloud.html')


@check_tool_permission('tool:json2go')
@add_views('tool:json2go', 'JSON转Go工具')
def json2go(request):
    return render(request, 'tool/json2go.html')


# 个人所得税年度汇算
@check_tool_permission('tool:tax')
@add_views('tool:tax', '综合所得年度汇算')
def tax(request):
    return render(request, 'tool/tax.html')


# ip地址查询
@check_tool_permission('tool:ip')
@add_views('tool:ip', 'IP地址查询')
def query_ip(request):
    """
    备用接口https://ip-api.com/docs/api:json
    """
    if request.META.get('HTTP_X_FORWARDED_FOR'):
        ip = request.META.get('HTTP_X_FORWARDED_FOR')
    else:
        ip = ''
    return render(request, 'tool/query_ip.html', context={'ip': ip})


# 文件上传工具（仅管理员+对象存储）
@check_tool_permission('tool:file_upload')
@login_required
@add_views('tool:file_upload', '文件上传工具')
def file_upload_view(request):
    """拖拽式文件上传工具"""
    from django.conf import settings
    from django.contrib.admin.views.decorators import staff_member_required
    from django.core.files.storage import default_storage
    from django.utils import timezone
    from django.contrib import messages
    import os
    
    # 检查是否启用对象存储
    use_object_storage = getattr(settings, 'USE_OBJECT_STORAGE', False)
    
    # 检查用户权限
    if not request.user.is_staff:
        messages.error(request, '此工具仅限管理员使用，暂时无法访问')
        return render(request, 'tool/tool.html', {
            'use_object_storage': use_object_storage
        })
    
    if not use_object_storage:
        messages.error(request, '此工具需要启用对象存储功能，暂时无法访问')
        return render(request, 'tool/tool.html', {
            'use_object_storage': use_object_storage
        })
    
    # 检查对象存储配置是否正确
    endpoint_url = getattr(settings, 'AWS_S3_ENDPOINT_URL', '')
    storage_public_url = os.getenv('SNOW_STORAGE_PUBLIC_URL', '')
    
    # 检测是否使用内部地址
   
    
    # 处理文件上传
    if request.method == 'POST' and request.FILES.get('file'):
        try:
            uploaded_file = request.FILES['file']
            
            # 生成文件路径（使用时间戳）
            timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
            ext = os.path.splitext(uploaded_file.name)[1].lower()
            filename = f"{timestamp}_{uploaded_file.name}"
            date_path = timezone.now().strftime('%Y/%m')
            file_path = os.path.join('images', 'tool', date_path, filename)
            
            # 保存文件到对象存储
            saved_path = default_storage.save(file_path, uploaded_file)
            
            # 获取文件URL
            file_url = default_storage.url(saved_path)
            
            # 如果是相对路径，构建完整的 URL
            if file_url.startswith('/'):
                # 方法1：使用 request.build_absolute_uri() 构建完整 URL
                file_url = site_full_url() + file_url
            
            # 判断是否为图片
            image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp']
            is_image = ext in image_extensions
            
            # 生成Markdown格式链接
            if is_image:
                markdown_link = f"![{uploaded_file.name}]({file_url})"
            else:
                markdown_link = f"[{uploaded_file.name}]({file_url})"
            
            return JsonResponse({
                'success': True,
                'file_name': uploaded_file.name,
                'file_url': file_url,
                'file_size': uploaded_file.size,
                'is_image': is_image,
                'markdown_link': markdown_link,
                'html_link': f'<a href="{file_url}" target="_blank">{uploaded_file.name}</a>' if not is_image else f'<img src="{file_url}" alt="{uploaded_file.name}">',
            })
            
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'上传失败'
            }, status=500)
    
    return render(request, 'tool/file_upload.html', {
        'use_object_storage': use_object_storage
    })

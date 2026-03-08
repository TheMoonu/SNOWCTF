import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.views.generic import CreateView, ListView, DetailView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from django.http import JsonResponse, HttpResponseForbidden
from django.db import transaction
from django.views.decorators.http import require_POST, require_http_methods
from django.contrib.auth.decorators import login_required
import os
from django.conf import settings
from django.core.files.storage import default_storage
import shutil
from pathlib import Path
from comment.models import SystemNotification
from container.models import StaticFile, DockerImage, DockerEngine
from .forms import StaticFileForm, DockerImageForm
from public.utils import create_captcha_for_registration
from django.utils.html import escape
from django.contrib.auth import get_user_model
from django.utils import timezone



User = get_user_model()
# 获取日志记录器
logger = logging.getLogger('apps.container')

class StaticFileCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = StaticFile
    form_class = StaticFileForm
    template_name = 'container/static_file_create.html'
    success_url = reverse_lazy('container:static_file_list')
    
    def test_func(self):
        # 检查用户是否有权限上传静态文件
        return self.request.user.is_staff or self.request.user.is_superuser or self.request.user.is_member
    
    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            
            messages.warning(self.request, '请先登录后再上传静态文件')
            return redirect('account_login')
        
        logger.warning(f'用户 {self.request.user.username} 尝试访问静态文件上传页面但权限不足', 
                      extra={'request': self.request})
        messages.error(self.request, '您没有权限上传静态文件')
        return redirect('container:static_file_list')
    
    def get_initial(self):
        # 生成验证码并添加到表单初始数据中
        initial = super().get_initial()
        captcha_data = create_captcha_for_registration()
        initial['captcha_key'] = captcha_data['captcha_key']
        self.captcha_image = captcha_data['captcha_image']
        return initial
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # 将验证码图片添加到上下文中
        context['captcha_image'] = getattr(self, 'captcha_image', None)
        return context
    
    def form_valid(self, form):
        try:
            with transaction.atomic():
                # 设置作者
                form.instance.author = self.request.user
                
                # 保存静态文件
                success, error_message = form.instance.save()
                if not success:
                    messages.error(self.request, error_message)
                    return self.form_invalid(form)
                
                # 记录成功日志
                file_size = form.instance.file.size if hasattr(form.instance, 'file') and form.instance.file else 0
                logger.info(f'用户 {self.request.user.username} 成功上传静态文件 "{form.instance.name}" (ID: {form.instance.id}, 大小: {file_size} 字节)',
                           extra={'request': self.request})
                
                if not (self.request.user and (self.request.user.is_superuser or self.request.user.is_staff)):
                    admin_notification = SystemNotification.objects.create(
                        title='配置文件审核通知',
                        content=f'''
                            <p>用户 {escape(self.request.user.username)} 成功上传静态文件 "{escape(form.instance.name)}"</p>
                            <p>请及时审核。</p>
                        '''
                    )
                    # 添加所有超级管理员为通知接收者
                    superusers = User.objects.filter(is_superuser=True)
                    admin_notification.get_p.add(*superusers)
                messages.success(self.request, f'静态文件上传成功，审核成功后可使用')
                
                # 如果是AJAX请求，返回JSON响应
                if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({
                        'success': True,
                        'message': '静态文件上传成功',
                        'redirect': self.get_success_url()
                    })
                
                return super().form_valid(form)
        except Exception as e:
            logger.error(f'用户 {self.request.user.username} 上传静态文件时发生错误: {str(e)}')
            messages.error(self.request, '上传文件时发生错误，请稍后重试')
            return self.form_invalid(form)
    
    def form_invalid(self, form):
        # 生成新的验证码
        captcha_data = create_captcha_for_registration()
        form.initial['captcha_key'] = captcha_data['captcha_key']
        self.captcha_image = captcha_data['captcha_image']
        
        # 记录表单验证失败日志
        errors = {field: error_list[0] for field, error_list in form.errors.items()}
        logger.warning(f'用户 {self.request.user.username} 上传静态文件表单验证失败: {errors}',
                      extra={'request': self.request})
        
        #messages.error(self.request, '表单验证失败，请检查您的输入')
        
        # 如果是AJAX请求，返回JSON响应
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': False,
                'message': '表单验证失败',
                'errors': errors,
                'captcha_key': captcha_data['captcha_key'],
                'captcha_image': captcha_data['captcha_image']
            }, status=400)
        
        return super().form_invalid(form)


    



class DockerImageCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    """Docker镜像配置创建视图"""
    model = DockerImage
    form_class = DockerImageForm
    template_name = 'container/docker_image_create.html'
    success_url = reverse_lazy('container:docker_image_list')
    
    def test_func(self):
        # 检查用户是否有权限创建Docker镜像配置
        return self.request.user.is_staff or self.request.user.is_superuser or self.request.user.is_member
    
    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            logger.warning(f'未登录用户尝试访问Docker镜像创建页面', extra={'request': self.request})
            messages.warning(self.request, '请先登录后再创建Docker镜像配置')
            return redirect('account_login')
        
        logger.warning(f'用户 {self.request.user.username} 尝试访问Docker镜像创建页面但权限不足', 
                      extra={'request': self.request})
        messages.error(self.request, '您没有权限创建Docker镜像配置')
        return redirect('container:docker_image_list')
    
    def get_initial(self):
        # 生成验证码并添加到表单初始数据中
        initial = super().get_initial()
        captcha_data = create_captcha_for_registration()
        initial['captcha_key'] = captcha_data['captcha_key']
        self.captcha_image = captcha_data['captcha_image']
        return initial
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # 将验证码图片添加到上下文中
        context['captcha_image'] = getattr(self, 'captcha_image', None)
        return context
    
    def form_valid(self, form):
        try:
            with transaction.atomic():
                # 设置作者
                form.instance.author = self.request.user
                
                # 保存Docker镜像配置
                response = super().form_valid(form)
                
                # 记录成功日志
                logger.info(f'用户 {self.request.user.username} 成功创建Docker镜像配置 "{form.instance.name}:{form.instance.tag}" (ID: {form.instance.id})',
                           extra={'request': self.request})
                
                # 如果不是管理员，发送审核通知
                if not (self.request.user and (self.request.user.is_superuser or self.request.user.is_staff)):
                    admin_notification = SystemNotification.objects.create(
                        title='镜像配置审核通知',
                        content=f'''
                            <p>用户 {escape(self.request.user.username)} 创建了Docker镜像配置 "{escape(form.instance.name)}:{escape(form.instance.tag)}"</p>
                            <p>请及时审核。</p>
                        '''
                    )
                    # 添加所有超级管理员为通知接收者
                    superusers = User.objects.filter(is_superuser=True)
                    admin_notification.get_p.add(*superusers)
                    messages.success(self.request, f'镜像配置创建成功，审核成功后可使用')
                else:
                    messages.success(self.request, f'Docker镜像配置创建成功')
                
                # 如果是AJAX请求，返回JSON响应
                if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({
                        'success': True,
                        'message': 'Docker镜像配置创建成功',
                        'redirect': self.get_success_url()
                    })
                
                return response
        except Exception as e:
            logger.error(f'用户 {self.request.user.username} 创建Docker镜像配置时发生错误: {str(e)}')
            messages.error(self.request, f'创建时发生错误: {str(e)}')
            return self.form_invalid(form)
    
    def form_invalid(self, form):
        # 生成新的验证码
        captcha_data = create_captcha_for_registration()
        form.initial['captcha_key'] = captcha_data['captcha_key']
        self.captcha_image = captcha_data['captcha_image']
        
        # 记录表单验证失败日志
        errors = {field: error_list[0] for field, error_list in form.errors.items()}
        logger.warning(f'用户 {self.request.user.username} 创建Docker镜像配置表单验证失败: {errors}',
                      extra={'request': self.request})
        
        # 如果是AJAX请求，返回JSON响应
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': False,
                'message': '表单验证失败',
                'errors': errors,
                'captcha_key': captcha_data['captcha_key'],
                'captcha_image': captcha_data['captcha_image']
            }, status=400)
        
        return super().form_invalid(form)


class DockerImageListView(LoginRequiredMixin, ListView):
    """Docker镜像配置列表视图"""
    model = DockerImage
    template_name = 'container/docker_image_list.html'
    context_object_name = 'docker_images'
    paginate_by = 10
    
    def get_queryset(self):
        queryset = super().get_queryset()
        # 如果不是管理员，只显示自己创建的配置
        if not (self.request.user.is_staff or self.request.user.is_superuser):
            queryset = queryset.filter(author=self.request.user)
        
        return queryset.order_by('-created_at')


class DockerImageUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    """Docker镜像配置更新视图"""
    model = DockerImage
    form_class = DockerImageForm
    template_name = 'container/docker_image_update.html'
    success_url = reverse_lazy('container:docker_image_list')
    
    def test_func(self):
        # 检查用户是否有权限修改Docker镜像配置
        docker_image = self.get_object()
        
        # 禁止修改已审核通过的配置
        if docker_image.review_status == 'APPROVED' and not self.request.user.is_superuser:
            return False
        
        has_permission = (self.request.user.is_staff or 
                self.request.user.is_superuser or 
                docker_image.author == self.request.user or self.request.user.is_member)
        
        if not has_permission:
            logger.warning(f'用户 {self.request.user.username} 尝试修改Docker镜像配置 "{docker_image.name}" (ID: {docker_image.id}) 但权限不足',
                          extra={'request': self.request})
        
        return has_permission
    
    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            messages.warning(self.request, '请先登录后再修改Docker镜像配置')
            return redirect('account_login')
        
        messages.warning(self.request, '审核已通过无法修改或者无权限修改')
        return redirect('container:docker_image_list')
    
    def get_initial(self):
        initial = super().get_initial()
        # 生成验证码
        captcha_data = create_captcha_for_registration()
        initial['captcha_key'] = captcha_data['captcha_key']
        self.captcha_image = captcha_data['captcha_image']
        return initial
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # 将验证码图片添加到上下文中
        context['captcha_image'] = getattr(self, 'captcha_image', None)
        return context
    
    def get(self, request, *args, **kwargs):
        # 记录访问日志
        self.object = self.get_object()
        logger.info(f'用户 {request.user.username} 访问Docker镜像配置 "{self.object.name}" (ID: {self.object.id}) 的修改页面',
                   extra={'request': request})
        
        return super().get(request, *args, **kwargs)
    
    def form_valid(self, form):
        try:
            with transaction.atomic():
                # 获取原始数据，用于记录变更
                original_name = self.object.name
                
                # 保存Docker镜像配置
                response = super().form_valid(form)
                
                # 记录成功日志
                logger.info(f'用户 {self.request.user.username} 成功修改Docker镜像配置 "{original_name}" (ID: {self.object.id})',
                           extra={'request': self.request})
                
                messages.success(self.request, f'Docker镜像配置 "{form.instance.name}" 修改成功')
                
                # 如果是AJAX请求，返回JSON响应
                if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({
                        'success': True,
                        'message': 'Docker镜像配置修改成功',
                        'redirect': self.get_success_url()
                    })
                
                return response
        except Exception as e:
            logger.error(f'用户 {self.request.user.username} 修改Docker镜像配置时发生错误: {str(e)}')
            messages.error(self.request, f'修改时发生错误: {str(e)}')
            return self.form_invalid(form)
    
    def form_invalid(self, form):
        # 生成新的验证码
        captcha_data = create_captcha_for_registration()
        form.initial['captcha_key'] = captcha_data['captcha_key']
        self.captcha_image = captcha_data['captcha_image']
        
        # 记录表单验证失败日志
        errors = {field: error_list[0] for field, error_list in form.errors.items()}
        logger.warning(f'用户 {self.request.user.username} 修改Docker镜像配置表单验证失败: {errors}',
                      extra={'request': self.request})
        
        # 如果是AJAX请求，返回JSON响应
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': False,
                'message': '表单验证失败',
                'errors': errors,
                'captcha_key': captcha_data['captcha_key'],
                'captcha_image': captcha_data['captcha_image']
            }, status=400)
        
        return super().form_invalid(form)


@require_POST
@login_required
def docker_image_delete(request, pk):
    """删除Docker镜像配置"""
    try:
        docker_image = DockerImage.objects.get(pk=pk)
        
        # 权限检查
        if not (request.user.is_staff or request.user.is_superuser or docker_image.author == request.user):
            logger.warning(f'用户 {request.user.username} 尝试删除Docker镜像配置 "{docker_image.name}" (ID: {docker_image.id}) 但权限不足',
                          extra={'request': request})
            messages.error(request, '您没有权限删除此镜像配置')
            return redirect('container:docker_image_list')
        
        # 删除配置
        image_name = docker_image.name
        docker_image.delete()
        
        logger.info(f'用户 {request.user.username} 成功删除Docker镜像配置 "{image_name}" (ID: {pk})',
                   extra={'request': request})
        
        messages.success(request, f'Docker镜像配置 "{image_name}" 已删除')
        
        # 如果是AJAX请求，返回JSON响应
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'message': 'Docker镜像配置删除成功'
            })
        
        return redirect('container:docker_image_list')
    except DockerImage.DoesNotExist:
        logger.error(f'用户 {request.user.username} 尝试删除不存在的Docker镜像配置 (ID: {pk})',
                    extra={'request': request})
        messages.error(request, 'Docker镜像配置不存在')
        return redirect('container:docker_image_list')
    except Exception as e:
        logger.error(f'删除Docker镜像配置时发生错误: {str(e)}', exc_info=True)
        messages.error(request, f'删除时发生错误: {str(e)}')
        return redirect('container:docker_image_list')


class StaticFileListView(LoginRequiredMixin, ListView):
    model = StaticFile
    template_name = 'container/static_file_list.html'
    context_object_name = 'static_files'
    paginate_by = 10
    
    def get_queryset(self):
        queryset = super().get_queryset()
        # 如果不是管理员，只显示自己上传的文件
        if not (self.request.user.is_staff or self.request.user.is_superuser):
            queryset = queryset.filter(author=self.request.user)
        
        # 记录访问日志
        
        return queryset.order_by('-upload_time')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # 检查文件是否存在，并更新文件大小
        for static_file in context['static_files']:
            if static_file.file:
                try:
                    # 尝试访问文件，如果不存在则更新数据库记录
                    static_file.file.size
                except (FileNotFoundError, OSError):
                    # 记录文件不存在的日志
                    logger.warning(f'文件不存在: {static_file.file.name}', 
                                  extra={'request': self.request})
        
        return context




class StaticFileUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = StaticFile
    form_class = StaticFileForm
    template_name = 'container/static_file_update.html'
    success_url = reverse_lazy('container:static_file_list')
    
    def test_func(self):
        # 检查用户是否有权限修改静态文件
        static_file = self.get_object()
        
        # 禁止修改已审核通过的静态文件
        if static_file.review_status == 'APPROVED':
            logger.warning(f'用户 {self.request.user.username} 尝试修改已审核通过的静态文件 "{static_file.name}" (ID: {static_file.id})',
                          extra={'request': self.request})
            messages.error(self.request, '已审核通过的文件不允许修改')
            return False
        
        has_permission = (self.request.user.is_staff or 
                self.request.user.is_superuser or 
                static_file.author == self.request.user or self.request.user.is_member)
        
        if not has_permission:
            logger.warning(f'用户 {self.request.user.username} 尝试修改静态文件 "{static_file.name}" (ID: {static_file.id}) 但权限不足',
                          extra={'request': self.request})
        
        return has_permission

def is_safe_path(path, base_dir):
    """检查路径是否安全（在允许的目录范围内）"""
    try:
        # 将路径转换为绝对路径
        path = os.path.abspath(path)
        base_dir = os.path.abspath(base_dir)
        
        # 记录路径信息用于调试
        logger.debug(f"检查路径安全性: 路径={path}, 基础目录={base_dir}")
        
        # 检查路径是否以基础目录开头
        # 同时检查是否以/opt/secsnow/media/开头（实际存储路径）
        return path.startswith(base_dir) or path.startswith('/media/challenge_files')
    except (ValueError, AttributeError) as e:
        logger.error(f"路径安全检查出错: {str(e)}")
        return False



@login_required
@require_POST
def static_file_delete(request, pk):
    """删除静态文件"""
    static_file = get_object_or_404(StaticFile, pk=pk)
    
    # 记录操作开始日志
    logger.info(f'用户 {request.user.username} 尝试删除静态文件 "{static_file.name}" (ID: {pk})', 
                extra={'request': request})
    
    # 检查权限：只有作者或管理员可以删除
    if not (request.user.is_staff or request.user.is_superuser or static_file.author == request.user or request.user.is_member):
        logger.warning(f'用户 {request.user.username} 尝试删除静态文件 "{static_file.name}" (ID: {pk}) 但权限不足',
                      extra={'request': request})
        
        messages.error(request, '您没有权限删除此静态文件')
        return redirect('container:static_file_list')

    practice_tasks = static_file.practice_tasks_static_files.all()
    if practice_tasks.exists():
        practice_names = ", ".join([f'"{task.title}"' for task in practice_tasks[:3]])
        message = f'无法删除：此配置正在被题目 {practice_names} 使用'
        if practice_tasks.count() > 3:
            message += f' 等{practice_tasks.count()}个题目'
        messages.error(request, message)
        return redirect('container:static_file_list')

    
    com_tasks = static_file.com_tasks_static_files.all()
    if com_tasks.exists():
        practice_names = ", ".join([f'"{task.title}"' for task in com_tasks[:3]])
        message = f'无法删除：此配置正在被竞赛题目 {practice_names} 使用'
        if com_tasks.count() > 3:
            message += f' 等{com_tasks.count()}个题目'
        messages.error(request, message)
        return redirect('container:static_file_list')
    
    try:
        # 记录文件名称和路径用于消息提示和删除
        file_name = static_file.name
        file_author = static_file.author.username if static_file.author else "未知用户"
        file_size = static_file.file_size  # 使用模型中存储的文件大小
        
        # 删除物理文件
        if static_file.file:
            try:
                # 获取文件相对路径
                file_relative_name = static_file.file.name
                
                # 记录文件信息
            
                # 尝试获取文件路径
                try:
                    file_path = static_file.file.path
                    # 删除文件
                    if os.path.exists(file_path) and os.path.isfile(file_path):
                        os.remove(file_path)
                        logger.info(f'成功删除服务器上的文件: {file_path}', 
                                   extra={'request': request})
                    else:
                        logger.warning(f'文件不存在或不是文件: {file_path}', 
                                      extra={'request': request})
                        
                        # 尝试使用storage API删除
                        if default_storage.exists(file_relative_name):
                            default_storage.delete(file_relative_name)
                            logger.info(f'通过storage接口成功删除文件: {file_relative_name}', 
                                       extra={'request': request})
                except Exception as e:
                    logger.warning(f'通过path属性删除文件失败: {str(e)}', 
                                  extra={'request': request})
                    
                    # 尝试使用storage API删除
                    if default_storage.exists(file_relative_name):
                        default_storage.delete(file_relative_name)
                        logger.info(f'通过storage接口成功删除文件: {file_relative_name}', 
                                   extra={'request': request})
                    else:
                        logger.warning(f'文件不存在: {file_relative_name}', 
                                      extra={'request': request})
            except Exception as e:
                # 使用exc_info=True而不是在extra中包含它
                logger.warning(f'删除服务器上的文件失败: 错误: {str(e)}', 
                              extra={'request': request}, exc_info=True)
        else:
            logger.warning(f'文件字段为空', extra={'request': request})
        
        # 删除数据库记录
        static_file.delete()
        
        # 记录成功日志
        logger.info(f'用户 {request.user.username} 成功删除静态文件 "{file_name}" (作者: {file_author}, 大小: {file_size} 字节)',
                   extra={'request': request})
        
        # 添加成功消息
        messages.success(request, f'静态文件 "{file_name}" 已成功删除')
        
        # 检查是否是AJAX请求
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'message': f'静态文件 "{file_name}" 已成功删除'
            })
        
        # 普通表单提交，重定向到列表页
        return redirect('container:static_file_list')
    except Exception as e:
        # 使用exc_info=True而不是在extra中包含它
        logger.error(f'用户 {request.user.username} 删除静态文件 "{static_file.name}" (ID: {pk}) 时发生错误: {str(e)}',
                    extra={'request': request}, exc_info=True)
        
        # 添加通用错误消息
        messages.error(request, '删除失败，请联系管理员')
        
        # 检查是否是AJAX请求
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': False,
                'message': '删除失败，请联系管理员'
            }, status=500)
        
        # 普通表单提交，重定向到列表页
        return redirect('container:static_file_list')


@require_http_methods(["GET", "POST"])
def refresh_captcha(request):
    """刷新验证码"""
    try:
        captcha_data = create_captcha_for_registration()
        return JsonResponse({
            'success': True,
            'captcha_key': captcha_data['captcha_key'],
            'captcha_image': captcha_data['captcha_image']
        })
    except Exception as e:
        logger.error(f'刷新验证码失败: {str(e)}', exc_info=True)
        return JsonResponse({
            'success': False,
            'message': '刷新验证码失败'
        }, status=500)


# ==================== Docker 引擎健康监控 ====================

@login_required
def docker_health_dashboard(request):
    """
    Docker 引擎健康监控仪表板页面
    
    权限：管理员或 staff
    """
    # 权限检查
    if not (request.user.is_staff or request.user.is_superuser):
        messages.error(request, '权限不足，仅管理员可访问')
        return redirect('home')
    
    return render(request, 'container/engine_health_dashboard.html')


@login_required
@require_http_methods(["GET"])
def docker_engine_health_check(request, engine_id):
    """
    检查单个容器引擎的健康状态（支持 Docker 和 K8s）
    
    权限：仅管理员或 staff
    性能：添加请求频率限制和缓存（1小时）
    支持：force=true 强制刷新
    """
    from django.db import close_old_connections
    from django.core.cache import cache
    from django.views.decorators.csrf import csrf_exempt
    import time
    
    try:
        # 严格权限检查
        if not (request.user.is_staff or request.user.is_superuser):
            logger.warning(f"未授权访问健康检查: user={request.user.username}, engine_id={engine_id}")
            return JsonResponse({
                'success': False,
                'error': '权限不足，仅管理员可访问'
            }, status=403)
        
        # 检查是否强制刷新
        force_refresh = request.GET.get('force', '').lower() == 'true'
        
        # 请求频率限制：每个用户每分钟最多检查同一引擎30次（管理员页面，限制较宽松）
        rate_limit_key = f"health_check_rate:{request.user.id}:{engine_id}"
        check_count = cache.get(rate_limit_key, 0)
        
        if check_count >= 30:
            logger.warning(f"单引擎检查频率限制: user={request.user.username}, engine={engine_id}")
            return JsonResponse({
                'success': False,
                'error': '请求过于频繁，请稍后再试'
            }, status=429)
        
        cache.set(rate_limit_key, check_count + 1, 60)  # 1分钟过期
        
        # 参数验证
        try:
            engine_id = int(engine_id)
            if engine_id <= 0:
                raise ValueError("Invalid engine_id")
        except (ValueError, TypeError):
            return JsonResponse({
                'success': False,
                'error': '无效的引擎ID'
            }, status=400)
        
        # 获取引擎（使用 select_related 优化查询）
        try:
            engine = DockerEngine.objects.select_related().get(id=engine_id, is_active=True)
        except DockerEngine.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': '引擎不存在或已禁用'
            }, status=404)
        
        # 检查是否有缓存的结果（1小时内）
        cache_key = f"engine_health:{engine_id}"
        
        # 强制刷新时清除缓存
        if force_refresh:
            cache.delete(cache_key)
            # 同时清除总览缓存
            cache.delete('all_engines_health')
            logger.info(f"强制刷新引擎健康检查: {engine.name} by {request.user.username}")
        else:
            cached_result = cache.get(cache_key)
            if cached_result and (time.time() - cached_result.get('check_time', 0)) < 3600:
                logger.debug(f"使用缓存的健康检查结果: {engine.name}")
                cached_result['cached'] = True
                return JsonResponse(cached_result)
        
        # 执行健康检查（带超时）
        logger.info(f"🔍 执行健康检查: {engine.name} by {request.user.username}")
        
        start_time = time.time()
        result = engine.check_health(timeout=360)
        elapsed = time.time() - start_time
        
        response_data = {
            'success': True,
            'engine_id': engine.id,
            'engine_name': engine.name,
            'status': result['status'],
            'details': result['details'],
            'error': result['error'],
            'summary': engine.get_health_summary(),
            'check_time': time.time(),
            'elapsed_time': round(elapsed, 2),
            'cached': False
        }
        
        # 缓存结果（1小时）
        cache.set(cache_key, response_data, 3600)
        
        # 清除总览缓存，确保数据同步
        cache.delete('all_engines_health')
        logger.info(f"💾 健康检查结果已缓存: {engine.name}, 耗时: {elapsed:.2f}s")
        
        return JsonResponse(response_data)
        
    except Exception as e:
        logger.error(f"健康检查失败: engine_id={engine_id}, user={request.user.username}, error={str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': '服务器内部错误'
        }, status=500)
    finally:
        close_old_connections()


@login_required
@require_http_methods(["GET"])
def docker_engines_health_status(request):
    """
    获取所有容器引擎的健康状态概览（支持 Docker 和 K8s）
    
    权限：仅管理员或 staff
    性能：添加缓存和查询优化（1小时缓存）
    支持：force=true 强制刷新
    """
    from django.db import close_old_connections
    from django.core.cache import cache
    import time
    
    try:
        # 严格权限检查
        if not (request.user.is_staff or request.user.is_superuser):
            logger.warning(f"未授权访问引擎状态列表: user={request.user.username}")
            return JsonResponse({
                'success': False,
                'error': '权限不足，仅管理员可访问'
            }, status=403)
        
        # 检查是否强制刷新
        force_refresh = request.GET.get('force', '').lower() == 'true'
        
        # 请求频率限制：每个用户每分钟最多请求100次（管理员页面，宽松限制）
        rate_limit_key = f"health_status_rate:{request.user.id}"
        request_count = cache.get(rate_limit_key, 0)
        
        if request_count >= 100:
            logger.warning(f"频率限制触发: user={request.user.username}, count={request_count}")
            return JsonResponse({
                'success': False,
                'error': '请求过于频繁，请稍后再试'
            }, status=429)
        
        cache.set(rate_limit_key, request_count + 1, 60)
        
        # 检查缓存（1小时内）
        cache_key = f"all_engines_health"
        
        # 强制刷新时清除缓存
        if force_refresh:
            cache.delete(cache_key)
            logger.info(f"🔄 强制刷新所有引擎健康状态 by {request.user.username}")
        else:
            cached_data = cache.get(cache_key)
            if cached_data:
                logger.debug(f"✅ 使用缓存的引擎状态列表")
                cached_data['cached'] = True
                return JsonResponse(cached_data)
        
        # 优化查询：只获取需要的字段
        engines = DockerEngine.objects.filter(is_active=True).only(
            'id', 'name', 'host_type', 'health_status', 'last_health_check',
            'running_containers', 'total_containers', 'response_time',
            'cpu_usage', 'memory_usage', 'disk_usage', 'health_check_error',
            'engine_type', 'namespace'
        )
        
        health_data = []
        for engine in engines:
            health_data.append(engine.get_health_summary())
        
        # 统计总体状况
        total = len(health_data)
        healthy = sum(1 for e in health_data if e['status'] == 'HEALTHY')
        warning = sum(1 for e in health_data if e['status'] == 'WARNING')
        critical = sum(1 for e in health_data if e['status'] == 'CRITICAL')
        offline = sum(1 for e in health_data if e['status'] == 'OFFLINE')
        
        response_data = {
            'success': True,
            'timestamp': timezone.now().isoformat(),
            'summary': {
                'total': total,
                'healthy': healthy,
                'warning': warning,
                'critical': critical,
                'offline': offline,
            },
            'engines': health_data,
            'cached': False
        }
        
        # 缓存结果（1小时）- 所有用户共享缓存，提高性能
        cache.set(cache_key, response_data, 3600)
        logger.info(f"💾 引擎状态列表已缓存, 总数: {total}")
        
        return JsonResponse(response_data)
        
    except Exception as e:
        logger.error(f"获取健康状态失败: user={request.user.username}, error={str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': '服务器内部错误'
        }, status=500)
    finally:
        close_old_connections()


@login_required
@require_http_methods(["POST"])
def docker_engines_check_all(request):
    """
    批量检查所有 Docker 引擎健康状态
    
    权限：仅超级管理员
    性能：添加防重复提交和超时控制
    """
    from django.db import close_old_connections
    from django.core.cache import cache
    import time
    
    try:
        # 严格权限检查（仅超级管理员）
        if not request.user.is_superuser:
            logger.warning(f"非超级管理员尝试批量检查: user={request.user.username}")
            return JsonResponse({
                'success': False,
                'error': '权限不足，仅超级管理员可执行批量检查'
            }, status=403)
        
        # 防止重复提交：同一用户5分钟内只能执行一次批量检查
        batch_check_key = f"batch_health_check:{request.user.id}"
        
        if cache.get(batch_check_key):
            return JsonResponse({
                'success': False,
                'error': '批量检查正在进行中，请稍后再试'
            }, status=429)
        
        # 设置锁定标记（5分钟）
        cache.set(batch_check_key, True, 300)
        
        try:
            logger.info(f"批量健康检查启动: user={request.user.username}")
            start_time = time.time()
            
            # 执行批量检查
            results = DockerEngine.check_all_health()
            
            elapsed = time.time() - start_time
            
            logger.info(
                f"批量健康检查完成: user={request.user.username}, "
                f"耗时={elapsed:.2f}s, 总数={results['total']}, "
                f"健康={results['healthy']}, 离线={results['offline']}"
            )
            
            return JsonResponse({
                'success': True,
                'timestamp': timezone.now().isoformat(),
                'results': results,
                'elapsed_time': round(elapsed, 2)
            })
            
        finally:
            # 清除锁定标记
            cache.delete(batch_check_key)
        
    except Exception as e:
        logger.error(
            f"批量健康检查失败: user={request.user.username}, error={str(e)}", 
            exc_info=True
        )
        return JsonResponse({
            'success': False,
            'error': '服务器内部错误'
        }, status=500)
    finally:
        close_old_connections()


@require_http_methods(["POST"])
@login_required
def refresh_image_status(request):
    """
    异步刷新单个镜像在所有引擎上的状态
    
    POST /container/api/v1/docker-image/refresh-status/
    参数: image_id
    """
    import docker
    from django.core.cache import cache
    from datetime import datetime
    
    # 检查权限（仅管理员可用）
    if not request.user.is_staff:
        return JsonResponse({'success': False, 'error': '权限不足'}, status=403)
    
    try:
        image_id = request.POST.get('image_id')
        if not image_id:
            return JsonResponse({'success': False, 'error': '缺少 image_id 参数'}, status=400)
        
        # 获取镜像对象
        try:
            docker_image = DockerImage.objects.get(id=image_id)
        except DockerImage.DoesNotExist:
            return JsonResponse({'success': False, 'error': '镜像不存在'}, status=404)
        
        # 清理该镜像的缓存（强制重新检查）
        cache_key = f'docker_image_{image_id}_status'
        cache.delete(cache_key)
        #logger.info(f"已清理镜像 {docker_image.full_name} 的缓存")
        
        # 获取所有激活的容器引擎
        engines = DockerEngine.objects.filter(is_active=True).order_by('engine_type', 'name')
        
        if not engines.exists():
            return JsonResponse({
                'success': False,
                'error': '没有可用的容器引擎'
            }, status=400)
        
        # 检查所有引擎上的镜像状态
        engine_statuses = []
        pulled_count = 0
        cache_time = datetime.now().strftime('%m-%d %H:%M:%S')
        
        for engine in engines:
            engine_name_display = f"{engine.name} (K8s)" if engine.engine_type == 'KUBERNETES' else engine.name
            
            if engine.engine_type == 'KUBERNETES':
                # K8s 引擎检查
                try:
                    from container.k8s_service import K8sService
                    from kubernetes import client as k8s_client
                    
                    service = K8sService(engine=engine)
                    # 使用K8sService中已配置的API client
                    core_api = k8s_client.CoreV1Api(api_client=service.core_api.api_client)
                    batch_api = k8s_client.BatchV1Api(api_client=service.core_api.api_client)
                    namespace = engine.namespace or 'ctf-challenges'
                    
                    # 最佳方案：创建测试 Pod，使用 imagePullPolicy=Never
                    # 如果镜像存在 -> Pod 启动成功；不存在 -> ErrImageNeverPull
                    image_found = False
                    check_method = None
                    
                    try:
                        import re
                        import time
                        
                        safe_name = re.sub(r'[^a-z0-9\-.]', '-', docker_image.name.lower())
                        safe_name = re.sub(r'-+', '-', safe_name).strip('-.')
                        test_pod_name = f"img-check-{safe_name}-{int(time.time())}"[-63:].strip('-.')
                        
                        # 创建测试 Pod
                        test_pod = k8s_client.V1Pod(
                            metadata=k8s_client.V1ObjectMeta(
                                name=test_pod_name,
                                labels={'app': 'image-checker', 'temp': 'true'}
                            ),
                            spec=k8s_client.V1PodSpec(
                                restart_policy='Never',
                                containers=[
                                    k8s_client.V1Container(
                                        name='checker',
                                        image=docker_image.full_name,
                                        command=['sh', '-c', 'exit 0'],
                                        image_pull_policy='Never'  # 关键：不拉取，只检查本地
                                    )
                                ]
                            )
                        )
                        
                        logger.info(f"[{engine.name}] 创建测试Pod检查镜像: {test_pod_name}")
                        core_api.create_namespaced_pod(namespace=namespace, body=test_pod)
                        
                        # 等待 Pod 状态（最多10秒）
                        for _ in range(10):
                            time.sleep(1)
                            pod_status = core_api.read_namespaced_pod_status(test_pod_name, namespace)
                            
                            # 检查容器状态
                            if pod_status.status.container_statuses:
                                container_status = pod_status.status.container_statuses[0]
                                
                                # 镜像存在：容器正在运行或已完成
                                if container_status.state.running or container_status.state.terminated:
                                    if container_status.state.terminated and container_status.state.terminated.exit_code == 0:
                                        image_found = True
                                        check_method = "测试Pod"
                                        logger.info(f"✓ [{engine.name}] 镜像存在（测试Pod成功）")
                                        break
                                    elif container_status.state.running:
                                        image_found = True
                                        check_method = "测试Pod"
                                        logger.info(f"✓ [{engine.name}] 镜像存在（测试Pod运行中）")
                                        break
                                
                                # 镜像不存在：ErrImageNeverPull
                                if container_status.state.waiting:
                                    reason = container_status.state.waiting.reason
                                    if reason in ['ErrImageNeverPull', 'ImagePullBackOff', 'ErrImagePull']:
                                        logger.info(f"✗ [{engine.name}] 镜像不存在: {reason}")
                                        break
                            
                            # Pod 失败
                            if pod_status.status.phase == 'Failed':
                                logger.info(f"✗ [{engine.name}] 测试Pod失败，镜像可能不存在")
                                break
                        
                        # 删除测试 Pod
                        try:
                            core_api.delete_namespaced_pod(
                                test_pod_name, 
                                namespace,
                                body=k8s_client.V1DeleteOptions(grace_period_seconds=0)
                            )
                            logger.info(f"[{engine.name}] 已删除测试Pod: {test_pod_name}")
                        except Exception as del_err:
                            logger.debug(f"删除测试Pod失败: {del_err}")
                            
                    except Exception as pod_check_err:
                        logger.warning(f"[{engine.name}] 测试Pod检查失败: {pod_check_err}")
                    
                    # 兜底方案：检查节点镜像列表
                    if not image_found:
                        logger.info(f"[{engine.name}] 使用兜底方案：检查节点镜像列表")
                        nodes = core_api.list_node()
                        found_on_nodes = []
                        target_image = docker_image.full_name
                        
                        for node in nodes.items:
                            node_name = node.metadata.name
                            
                            for img in node.status.images or []:
                                match = False
                                matched_name = None
                                
                                for img_name in (img.names or []):
                                    clean_name = img_name.split('@')[0].strip()
                                    
                                    # 匹配逻辑（支持多种格式）
                                    # 1. 精确匹配
                                    if clean_name == target_image:
                                        match = True
                                        matched_name = clean_name
                                    # 2. 移除registry前缀匹配
                                    elif '/' in clean_name:
                                        parts = clean_name.split('/')
                                        if parts[-1] == target_image:
                                            match = True
                                            matched_name = clean_name
                                        # 3. library/xxx 格式匹配
                                        elif target_image.count('/') == 0 and len(parts) >= 2:
                                            if f"{parts[-2]}/{parts[-1]}" == f"library/{target_image}":
                                                match = True
                                                matched_name = clean_name
                                        # 4. 比较后两段（registry/name:tag vs name:tag）
                                        elif len(parts) >= 2 and target_image.count('/') >= 1:
                                            target_parts = target_image.split('/')
                                            # 比较 name:tag 部分
                                            if len(parts) >= 2 and len(target_parts) >= 2:
                                                if f"{parts[-2]}/{parts[-1]}" == f"{target_parts[-2]}/{target_parts[-1]}":
                                                    match = True
                                                    matched_name = clean_name
                                    # 5. 反向匹配：docker_image.full_name可能包含registry
                                    if not match and '/' in target_image:
                                        image_parts = target_image.split('/')
                                        # 比较最后的name:tag部分
                                        if clean_name.endswith(image_parts[-1]):
                                            match = True
                                            matched_name = clean_name
                                    
                                    if match:
                                        image_found = True
                                        found_on_nodes.append(node_name)
                                        logger.info(f"✓ [{engine.name}] 在节点 {node_name} 找到镜像: '{matched_name}'")
                                        break
                                
                                if match:
                                    break
                            
                            if image_found:
                                break
                    
          
                        
                    
                    if image_found:
                        # 根据检查方式显示不同的标签
                        if check_method == "测试Pod":
                            node_info = " (已验证)"
                        elif 'found_on_nodes' in locals() and found_on_nodes:
                            nodes_count = len(core_api.list_node().items) if 'nodes' not in locals() else len(nodes.items)
                            node_info = f" ({len(found_on_nodes)}/{nodes_count} 节点)" if len(found_on_nodes) < nodes_count else ""
                        else:
                            node_info = ""
                        
                        engine_statuses.append({
                            'name': engine_name_display,
                            'status': 'pulled',
                            'color': 'green',
                            'icon': '✓',
                            'note': node_info if node_info else None
                        })
                        pulled_count += 1
                    else:
                        engine_statuses.append({
                            'name': engine_name_display,
                            'status': 'not_pulled',
                            'color': 'gray',
                            'icon': '✗'
                        })
                    
                except Exception as e:
                    logger.error(f"检查 K8s 引擎 {engine.name} 镜像状态失败: {str(e)}")
                    engine_statuses.append({
                        'name': engine_name_display,
                        'status': 'error',
                        'color': 'red',
                        'icon': '⚠',
                        'note': str(e)[:50]
                    })
            else:
                # Docker 引擎检查
                client = None
                try:
                    docker_url = engine.get_docker_url()
                    tls_config = engine.get_tls_config() if engine.needs_tls else None
                    client = docker.DockerClient(base_url=docker_url, tls=tls_config, timeout=5)
                    
                    # 检查镜像是否存在
                    try:
                        client.images.get(docker_image.full_name)
                        engine_statuses.append({
                            'name': engine_name_display,
                            'status': 'pulled',
                            'color': 'green',
                            'icon': '✓'
                        })
                        pulled_count += 1
                    except docker.errors.ImageNotFound:
                        engine_statuses.append({
                            'name': engine_name_display,
                            'status': 'not_pulled',
                            'color': 'gray',
                            'icon': '✗'
                        })
                    except Exception as img_error:
                        logger.error(f"获取 Docker 引擎 {engine.name} 镜像失败: {str(img_error)}")
                        engine_statuses.append({
                            'name': engine_name_display,
                            'status': 'error',
                            'color': 'red',
                            'icon': '⚠',
                            'note': str(img_error)[:50]
                        })
                        
                except Exception as e:
                    logger.error(f"连接 Docker 引擎 {engine.name} 失败: {str(e)}")
                    engine_statuses.append({
                        'name': engine_name_display,
                        'status': 'error',
                        'color': 'red',
                        'icon': '⚠',
                        'note': str(e)[:50]
                    })
                finally:
                    if client:
                        client.close()
        
        # 统计状态
        not_pulled_count = sum(1 for s in engine_statuses if s['status'] == 'not_pulled')
        error_count = sum(1 for s in engine_statuses if s['status'] == 'error')
        total_count = len(engine_statuses)
        
        # 更新数据库状态（只要有一个引擎拉取了就标记为已拉取）
        should_be_pulled = (pulled_count > 0)
        if docker_image.is_pulled != should_be_pulled:
            docker_image.is_pulled = should_be_pulled
            if should_be_pulled:
                docker_image.last_pulled = timezone.now()
            docker_image.save(update_fields=['is_pulled', 'last_pulled'])
        
        # 缓存该镜像的状态（10分钟）
        cache.set(f'docker_image_{image_id}_status', {
            'engine_statuses': engine_statuses,
            'cache_time': cache_time
        }, timeout=600)
        
        # 构建刷新按钮
        refresh_button = (
            f'<button type="button" class="btn-refresh-image-status" '
            f'data-image-id="{image_id}" '
            f'style="padding: 2px 8px; font-size: 11px; cursor: pointer; '
            f'background: linear-gradient(to bottom, #e3f4ff 0%, #cfe9ff 100%); '
            f'color: #205067; border: 1px solid #b4d5e6; border-radius: 4px; '
            f'font-weight: 500; transition: all 0.2s ease; margin-left: 8px;">'
            f'🔄 刷新</button>'
        )
        
        # 构建总览状态
        if pulled_count == total_count:
            status_text = f'<strong style="color: green;">✓ 已拉取 ({pulled_count}/{total_count})</strong>'
        elif pulled_count > 0:
            status_text = f'<strong style="color: orange;">⚠ 部分拉取 ({pulled_count}/{total_count})</strong>'
        elif error_count > 0:
            status_text = f'<strong style="color: red;">✗ 检查失败 ({error_count}/{total_count})</strong>'
        else:
            status_text = f'<strong style="color: gray;">✗ 未拉取 (0/{total_count})</strong>'
        
        overview = (
            f'<div style="display: flex; align-items: center; margin-bottom: 5px;">'
            f'{status_text}'
            f'{refresh_button}'
            f'</div>'
        )
        
        # 构建完整 HTML
        html_parts = [overview]
        
        # 引擎详情
        for status in engine_statuses:
            note = status.get('note')
            if note:
                html_parts.append(
                    f'<div style="margin: 1px 0; padding-left: 8px;">'
                    f'<small style="color: {status["color"]};">{status["icon"]} {status["name"]}<span style="color: #666;"> {note}</span></small>'
                    f'</div>'
                )
            else:
                html_parts.append(
                    f'<div style="margin: 1px 0; padding-left: 8px;">'
                    f'<small style="color: {status["color"]};">{status["icon"]} {status["name"]}</small>'
                    f'</div>'
                )
        
        # 更新时间
        html_parts.append(
            f'<div style="margin-top: 3px;">'
            f'<small style="color: #999;">更新: {cache_time}</small>'
            f'</div>'
        )
        
        return JsonResponse({
            'success': True,
            'html': ''.join(html_parts),
            'pulled_count': pulled_count,
            'total_count': total_count
        })
        
    except Exception as e:
        logger.error(f"刷新镜像状态失败: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'刷新失败: {str(e)}'
        }, status=500)


# ==================== K8s 安全监控 ====================

@login_required
@require_http_methods(["GET"])
def security_dashboard(request):
    """
    安全监控仪表板页面
    
    权限：仅管理员
    """
    if not (request.user.is_staff or request.user.is_superuser):
        messages.error(request, '权限不足，仅管理员可访问')
        return redirect('home')
    
    return render(request, 'container/security_dashboard.html')


def _get_cached_or_compute(cache_key, compute_func, timeout=3600):
    """
    通用缓存辅助函数
    
    Args:
        cache_key: 缓存键
        compute_func: 计算函数（返回数据）
        timeout: 缓存过期时间（秒，默认1小时）
    
    Returns:
        tuple: (数据, 是否来自缓存)
    """
    from django.core.cache import cache
    
    # 尝试从缓存获取
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        logger.debug(f"✅ 缓存命中: {cache_key}")
        # 返回缓存数据，标记为来自缓存
        return cached_data, True
    
    # 缓存未命中，计算新数据
    logger.info(f"🔄 缓存未命中，重新计算: {cache_key}")
    try:
        data = compute_func()
        # 存入缓存
        cache.set(cache_key, data, timeout)
        logger.info(f"💾 数据已缓存: {cache_key}, 过期时间: {timeout}秒")
        # 返回新数据，标记为非缓存
        return data, False
    except Exception as e:
        logger.error(f"❌ 计算数据失败 ({cache_key}): {str(e)}")
        raise


@login_required
@require_http_methods(["GET"])
def security_status(request):
    """
    获取安全状态概览（带缓存）
    
    返回：
    - 网络策略状态
    - 安全等级
    - 警告信息
    
    缓存：1小时（仅手动刷新时清理）
    """
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'success': False, 'error': '权限不足'}, status=403)
    
    # 强制刷新？
    force_refresh = request.GET.get('force', '').lower() == 'true'
    
    try:
        from .container_service_factory import ContainerServiceFactory
        from .security_monitor import SecurityMonitor
        from django.core.cache import cache
        
        cache_key = 'security:status:all_engines'
        
        # 强制刷新则清除缓存
        if force_refresh:
            cache.delete(cache_key)
            logger.info("强制刷新安全状态缓存")
        
        def compute_status():
            # 获取所有 K8s 引擎
            k8s_engines = DockerEngine.objects.filter(
                engine_type='KUBERNETES',
                is_active=True
            )
            
            if not k8s_engines.exists():
                return {
                    'success': True,
                    'engines': [],
                    'message': '没有配置 K8s 引擎',
                    'cached': False
                }
            
            results = []
            for engine in k8s_engines:
                try:
                    # 先检查数据库配置
                    if not engine.enable_network_policy:
                        # 如果后台未启用网络策略，直接返回禁用状态
                        # 但仍然获取 K8s 基本信息
                        k8s_info = engine.get_health_summary().get('k8s_info', {})
                        
                        results.append({
                            'engine_id': engine.id,
                            'engine_name': engine.name,
                            'namespace': engine.namespace,
                            'k8s_info': k8s_info,  # 添加 K8s 信息
                            'status': {
                                'network_policies_count': 0,
                                'has_egress_deny': False,
                                'has_dns_allow': False,
                                'security_level': 'DISABLED',
                                'policies': [],
                                'warnings': ['⚠️ 网络策略未启用（后台配置已禁用）'],
                                'config_disabled': True
                            }
                        })
                        continue
                    
                    # 创建服务实例
                    service = ContainerServiceFactory.create_service(engine)
                    
                    # 创建安全监控器
                    monitor = SecurityMonitor(
                        core_api=service.core_api,
                        networking_api=service.networking_api,
                        namespace=engine.namespace
                    )
                    
                    # 获取安全状态（从K8s实际查询）
                    status = monitor.check_security_status()
                    status['config_disabled'] = False
                    
                    # 获取 K8s 基本信息
                    k8s_info = engine.get_health_summary().get('k8s_info', {})
                    
                    results.append({
                        'engine_id': engine.id,
                        'engine_name': engine.name,
                        'namespace': engine.namespace,
                        'k8s_info': k8s_info,  # 添加 K8s 信息
                        'status': status
                    })
                    
                except Exception as e:
                    logger.error(f"获取引擎 {engine.name} 安全状态失败: {str(e)}")
                    results.append({
                        'engine_id': engine.id,
                        'engine_name': engine.name,
                        'namespace': engine.namespace,
                        'error': str(e)
                    })
            
            return {
                'success': True,
                'engines': results,
                'cached': False
            }
        
        # 使用缓存（1小时）
        data, is_cached = _get_cached_or_compute(cache_key, compute_status, timeout=3600)
        
        # 添加时间戳和缓存标记
        response_data = data.copy()
        response_data['timestamp'] = timezone.now().isoformat()
        response_data['cached'] = is_cached
        
        return JsonResponse(response_data)
        
    except Exception as e:
        logger.error(f"获取安全状态失败: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_http_methods(["GET"])
def security_monitor_pods(request, engine_id):
    """
    监控指定引擎的所有 Pod（带缓存）
    
    Args:
        engine_id: 引擎 ID
        
    返回：
    - Pod 列表
    - 可疑活动
    - 告警信息
    
    缓存：1小时（仅手动刷新时清理）
    """
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'success': False, 'error': '权限不足'}, status=403)
    
    # 强制刷新？
    force_refresh = request.GET.get('force', '').lower() == 'true'
    
    try:
        engine = DockerEngine.objects.get(id=engine_id, engine_type='KUBERNETES')
        
        from .container_service_factory import ContainerServiceFactory
        from .security_monitor import SecurityMonitor
        from django.core.cache import cache
        
        cache_key = f'security:pods:engine_{engine_id}'
        
        # 强制刷新则清除缓存
        if force_refresh:
            cache.delete(cache_key)
            logger.info(f"强制刷新引擎 {engine.name} Pod 监控缓存")
        
        def compute_pods():
            service = ContainerServiceFactory.create_service(engine)
            monitor = SecurityMonitor(
                core_api=service.core_api,
                networking_api=service.networking_api,
                namespace=engine.namespace
            )
            
            # 监控所有 Pod
            results = monitor.monitor_all_pods()
            
            return {
                'success': True,
                'engine_name': engine.name,
                'namespace': engine.namespace,
                'data': results,
                'cached': False
            }
        
        # 使用缓存（1小时）
        data, is_cached = _get_cached_or_compute(cache_key, compute_pods, timeout=3600)
        
        # 添加时间戳和缓存标记
        response_data = data.copy()
        response_data['timestamp'] = timezone.now().isoformat()
        response_data['cached'] = is_cached
        
        return JsonResponse(response_data)
        
    except DockerEngine.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': '引擎不存在'
        }, status=404)
    except Exception as e:
        logger.error(f"监控 Pod 失败: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_http_methods(["GET"])
def security_events(request, engine_id):
    """
    获取安全事件日志（带缓存）
    
    Args:
        engine_id: 引擎 ID
        hours: 查询最近多少小时（默认 24）
    
    缓存：1小时（仅手动刷新时清理）
    """
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'success': False, 'error': '权限不足'}, status=403)
    
    # 强制刷新？
    force_refresh = request.GET.get('force', '').lower() == 'true'
    
    try:
        hours = int(request.GET.get('hours', 24))
        engine = DockerEngine.objects.get(id=engine_id, engine_type='KUBERNETES')
        
        from .container_service_factory import ContainerServiceFactory
        from .security_monitor import SecurityMonitor
        from django.core.cache import cache
        
        cache_key = f'security:events:engine_{engine_id}_hours_{hours}'
        
        # 强制刷新则清除缓存
        if force_refresh:
            cache.delete(cache_key)
            logger.info(f"强制刷新引擎 {engine.name} 安全事件缓存")
        
        def compute_events():
            service = ContainerServiceFactory.create_service(engine)
            monitor = SecurityMonitor(
                core_api=service.core_api,
                networking_api=service.networking_api,
                namespace=engine.namespace
            )
            
            # 获取事件
            events = monitor.get_security_events(hours=hours)
            
            return {
                'success': True,
                'engine_name': engine.name,
                'namespace': engine.namespace,
                'hours': hours,
                'events': events,
                'cached': False
            }
        
        # 使用缓存（1小时）
        data, is_cached = _get_cached_or_compute(cache_key, compute_events, timeout=3600)
        
        # 添加时间戳和缓存标记
        response_data = data.copy()
        response_data['timestamp'] = timezone.now().isoformat()
        response_data['cached'] = is_cached
        
        return JsonResponse(response_data)
        
    except DockerEngine.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': '引擎不存在'
        }, status=404)
    except Exception as e:
        logger.error(f"获取安全事件失败: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_http_methods(["GET"])
def security_pod_details(request, engine_id, pod_name):
    """
    获取单个 Pod 的详细安全信息（带缓存）
    
    Args:
        engine_id: 引擎 ID
        pod_name: Pod 名称
        
    返回：
    - 网络连接
    - 流量统计
    - 可疑活动检测
    
    缓存：1小时（仅手动刷新时清理）
    """
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'success': False, 'error': '权限不足'}, status=403)
    
    # 强制刷新？
    force_refresh = request.GET.get('force', '').lower() == 'true'
    
    try:
        engine = DockerEngine.objects.get(id=engine_id, engine_type='KUBERNETES')
        
        from .container_service_factory import ContainerServiceFactory
        from .security_monitor import SecurityMonitor
        from django.core.cache import cache
        
        cache_key = f'security:pod_details:engine_{engine_id}_pod_{pod_name}'
        
        # 强制刷新则清除缓存
        if force_refresh:
            cache.delete(cache_key)
            logger.info(f"强制刷新 Pod {pod_name} 详情缓存")
        
        def compute_details():
            service = ContainerServiceFactory.create_service(engine)
            monitor = SecurityMonitor(
                core_api=service.core_api,
                networking_api=service.networking_api,
                namespace=engine.namespace
            )
            
            # 检测可疑活动
            detection = monitor.detect_suspicious_activity(pod_name)
            
            return {
                'success': True,
                'pod_name': pod_name,
                'data': detection,
                'cached': False
            }
        
        # 使用缓存（1小时）
        data, is_cached = _get_cached_or_compute(cache_key, compute_details, timeout=3600)
        
        # 添加时间戳和缓存标记
        response_data = data.copy()
        response_data['timestamp'] = timezone.now().isoformat()
        response_data['cached'] = is_cached
        
        return JsonResponse(response_data)
        
    except DockerEngine.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': '引擎不存在'
        }, status=404)
    except Exception as e:
        logger.error(f"获取 Pod 详情失败: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_http_methods(["GET"])
def security_pod_connections(request, engine_id, pod_name):
    """
    实时查看 Pod 的所有网络连接（带缓存）
    
    Args:
        engine_id: 引擎 ID
        pod_name: Pod 名称
    
    缓存：1小时（仅手动刷新时清理）
    """
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'success': False, 'error': '权限不足'}, status=403)
    
    # 强制刷新？
    force_refresh = request.GET.get('force', '').lower() == 'true'
    
    try:
        engine = DockerEngine.objects.get(id=engine_id, engine_type='KUBERNETES')
        
        from .container_service_factory import ContainerServiceFactory
        from kubernetes.stream import stream
        from django.core.cache import cache
        
        cache_key = f'security:pod_connections:engine_{engine_id}_pod_{pod_name}'
        
        # 强制刷新则清除缓存
        if force_refresh:
            cache.delete(cache_key)
        
        def compute_connections():
            service = ContainerServiceFactory.create_service(engine)
            
            # 获取所有连接（包括 LISTEN 状态）
            commands = {
                'established': 'ss -tn state established 2>/dev/null || echo "no-ss"',
                'all': 'ss -tn 2>/dev/null || netstat -tn 2>/dev/null || echo "no-tools"',
                'listen': 'ss -tln 2>/dev/null || netstat -tln 2>/dev/null || echo "no-tools"'
            }
            
            results = {}
            
            for name, cmd in commands.items():
                try:
                    resp = stream(
                        service.core_api.connect_get_namespaced_pod_exec,
                        name=pod_name,
                        namespace=engine.namespace,
                        command=['/bin/sh', '-c', cmd],
                        stderr=True,
                        stdin=False,
                        stdout=True,
                        tty=False,
                        _preload_content=False
                    )
                    
                    output = ""
                    while resp.is_open():
                        resp.update(timeout=1)
                        if resp.peek_stdout():
                            output += resp.read_stdout()
                        if not resp.is_open():
                            break
                    
                    resp.close()
                    results[name] = output
                    
                except Exception as e:
                    results[name] = f"Error: {str(e)}"
            
            return {
                'success': True,
                'pod_name': pod_name,
                'connections': results,
                'cached': False
            }
        
        # 使用缓存（1小时）
        data, is_cached = _get_cached_or_compute(cache_key, compute_connections, timeout=3600)
        
        # 添加时间戳和缓存标记
        response_data = data.copy()
        response_data['timestamp'] = timezone.now().isoformat()
        response_data['cached'] = is_cached
        
        return JsonResponse(response_data)
        
    except DockerEngine.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': '引擎不存在'
        }, status=404)
    except Exception as e:
        logger.error(f"获取 Pod 连接失败: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_http_methods(["POST"])
def security_clear_cache(request):
    """
    清除所有安全监控缓存
    
    用途：管理员手动清除缓存，或在配置变更后使用
    """
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'success': False, 'error': '权限不足'}, status=403)
    
    try:
        from django.core.cache import cache
        
        # 清除所有 security: 开头的缓存
        cache_keys = cache.keys('security:*') if hasattr(cache, 'keys') else []
        
        cleared_count = 0
        if cache_keys:
            # Redis backend 支持批量删除
            for key in cache_keys:
                cache.delete(key)
                cleared_count += 1
        else:
            # 如果不支持 keys()，则清除已知的缓存
            known_patterns = [
                'security:status:all_engines',
                'security:pods:engine_*',
                'security:events:engine_*',
                'security:pod_details:engine_*',
                'security:resource_stats:engine_*'
            ]
            logger.warning("缓存后端不支持 keys() 方法，仅清除已知缓存模式")
            # 这里可以遍历所有引擎ID来删除特定缓存
            engines = DockerEngine.objects.filter(engine_type='KUBERNETES', is_active=True)
            for engine in engines:
                cache.delete(f'security:pods:engine_{engine.id}')
                cache.delete(f'security:resource_stats:engine_{engine.id}')
                cleared_count += 2
            cache.delete('security:status:all_engines')
            cleared_count += 1
        
        logger.info(f"管理员 {request.user.username} 清除了 {cleared_count} 个安全监控缓存")
        
        return JsonResponse({
            'success': True,
            'message': f'已清除 {cleared_count} 个缓存',
            'cleared_count': cleared_count,
            'timestamp': timezone.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"清除缓存失败: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_http_methods(["GET"])
def security_resource_stats(request, engine_id):
    """
    获取资源使用统计（带缓存）
    
    Args:
        engine_id: 引擎 ID
    
    缓存：1小时（仅手动刷新时清理）
    """
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'success': False, 'error': '权限不足'}, status=403)
    
    # 强制刷新？
    force_refresh = request.GET.get('force', '').lower() == 'true'
    
    try:
        engine = DockerEngine.objects.get(id=engine_id, engine_type='KUBERNETES')
        
        from .container_service_factory import ContainerServiceFactory
        from .security_monitor import SecurityMonitor
        from django.core.cache import cache
        
        cache_key = f'security:resource_stats:engine_{engine_id}'
        
        # 强制刷新则清除缓存
        if force_refresh:
            cache.delete(cache_key)
            logger.info(f"强制刷新引擎 {engine.name} 资源统计缓存")
        
        def compute_stats():
            service = ContainerServiceFactory.create_service(engine)
            monitor = SecurityMonitor(
                core_api=service.core_api,
                networking_api=service.networking_api,
                namespace=engine.namespace
            )
            
            # 获取资源统计
            stats = monitor.get_resource_usage_stats()
            
            return {
                'success': True,
                'engine_name': engine.name,
                'namespace': engine.namespace,
                'stats': stats,
                'cached': False
            }
        
        # 使用缓存（1小时）
        data, is_cached = _get_cached_or_compute(cache_key, compute_stats, timeout=3600)
        
        # 添加时间戳和缓存标记
        response_data = data.copy()
        response_data['timestamp'] = timezone.now().isoformat()
        response_data['cached'] = is_cached
        
        return JsonResponse(response_data)
        
    except DockerEngine.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': '引擎不存在'
        }, status=404)
    except Exception as e:
        logger.error(f"获取资源统计失败: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_http_methods(["GET"])
def secure_file_download(request, file_id, token):
    """
    安全文件下载视图
    
    功能：
    1. 验证下载令牌（防止URL被滥用）
    2. 检查频率限制（防止暴力下载）
    3. 验证用户权限
    4. 提供文件下载
    """
    from django.http import FileResponse, HttpResponse
    from .download_security import (
        DownloadTokenGenerator, 
        DownloadRateLimiter,
        get_client_ip
    )
    
    try:
        # 1. 获取静态文件对象
        static_file = get_object_or_404(StaticFile, id=file_id)
        
        # 2. 验证令牌
        token_generator = DownloadTokenGenerator()
        is_valid, error_msg = token_generator.verify_token(
            token, 
            file_id, 
            request.user.id
        )
        
        if not is_valid:
            logger.warning(
                f"下载令牌验证失败: 用户={request.user.username}, "
                f"文件={static_file.name}, 原因={error_msg}"
            )
            messages.error(request, error_msg)
            return HttpResponseForbidden(error_msg)
        
        # 3. 检查文件状态
        if static_file.review_status != 'APPROVED':
            logger.warning(
                f"尝试下载未审核文件: 用户={request.user.username}, "
                f"文件={static_file.name}"
            )
            messages.error(request, "该文件未通过审核，暂时无法下载")
            return HttpResponseForbidden("文件未通过审核")
        
        # 4. 检查频率限制
        client_ip = get_client_ip(request)
        can_download, rate_error_msg, remaining_time = DownloadRateLimiter.check_rate_limit(
            request.user.id,
            file_id,
            client_ip
        )
        
        if not can_download:
            logger.warning(
                f"下载频率限制: 用户={request.user.username}, "
                f"文件={static_file.name}, IP={client_ip}, "
                f"原因={rate_error_msg}"
            )
            messages.warning(request, rate_error_msg)
            return JsonResponse({
                'error': rate_error_msg,
                'remaining_time': remaining_time
            }, status=429)
        
        # 5. 检查文件是否存在
        if not static_file.file or not default_storage.exists(static_file.file.name):
            logger.error(
                f"文件不存在: 文件ID={file_id}, "
                f"路径={static_file.file.name if static_file.file else 'None'}"
            )
            messages.error(request, "文件不存在")
            return HttpResponseForbidden("文件不存在")
        
        # 6. 记录下载次数
        DownloadRateLimiter.record_download(
            request.user.id,
            file_id,
            client_ip
        )
        
        # 7. 更新下载计数（原子操作）
        from django.db.models import F
        StaticFile.objects.filter(id=file_id).update(
            download_count=F('download_count') + 1
        )
        
        # 8. 返回文件（兼容不同的存储后端）
        try:
            # 尝试使用 storage.open() 打开文件（兼容云存储）
            file_handle = static_file.file.open('rb')
            
            # 获取文件名（从 file.name 中提取）
            filename = os.path.basename(static_file.file.name)
            
            # 创建文件响应
            response = FileResponse(
                file_handle,
                as_attachment=True,
                filename=filename
            )
            
            # 设置正确的 Content-Type
            import mimetypes
            content_type, _ = mimetypes.guess_type(filename)
            if content_type:
                response['Content-Type'] = content_type
            
            logger.info(
                f"✅ 文件下载成功: 用户={request.user.username}, "
                f"文件={static_file.name}, IP={client_ip}"
            )
            
            return response
            
        except Exception as file_error:
            logger.error(
                f"打开文件失败: 文件={static_file.name}, "
                f"存储路径={static_file.file.name}, "
                f"错误={str(file_error)}"
            )
            raise
        
    except StaticFile.DoesNotExist:
        logger.warning(f"文件不存在: 文件ID={file_id}")
        messages.error(request, "文件不存在")
        return HttpResponseForbidden("文件不存在")
    
    except Exception as e:
        logger.error(
            f"文件下载失败: 用户={request.user.username}, "
            f"文件ID={file_id}, 错误={str(e)}",
            exc_info=True
        )
        messages.error(request, "文件下载失败，请稍后再试")
        return HttpResponseForbidden("下载失败")
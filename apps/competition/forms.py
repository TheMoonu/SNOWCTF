from django import forms
import re
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.utils import timezone
from competition.models import Registration
from django.core.cache import cache
from django.conf import settings
from competition.models import Competition,Challenge
from django.views.generic import CreateView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404
from competition.models import Challenge
from container.models import StaticFile
from container.models import DockerImage

class TeamSelectionForm(forms.Form):
    """团队赛报名表单"""
    TEAM_CHOICES = [
        ('create', '创建新队伍'),
        ('join', '加入现有队伍')
    ]
    
    team_action = forms.ChoiceField(
        choices=TEAM_CHOICES,
        widget=forms.RadioSelect,
        label='队伍选择',
        required=True
    )
    
    team_name = forms.CharField(
        max_length=255,
        required=True,
        label='队伍名称',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入队伍名称'
        }),
        help_text='请输入队伍名称'
    )

    team_code = forms.CharField(
        max_length=6,
        required=False,
        label='队伍认证码',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入6位认证码'
        }),
        help_text='加入队伍时需要填写，认证码可从队长个人信息-我的队伍模块获取'
    )
    
    invitation_code = forms.CharField(
        max_length=255,
        required=True,
        label='报名码',
        widget=forms.TextInput(attrs={
            'class': 'form-control', 
            'placeholder': '请输入报名码'
        }),
        help_text='内部赛需要填写报名码'
    )
    
    def __init__(self, *args, **kwargs):
        self.competition = kwargs.pop('competition', None)
        super().__init__(*args, **kwargs)
        
        # 如果不是内部赛，移除报名码字段
        if self.competition and self.competition.visibility_type != Competition.INTERNAL:
            self.fields.pop('invitation_code', None)

    def clean_team_name(self):
        """验证队伍名称"""
        team_name = self.cleaned_data.get('team_name', '').strip()
        
        if not team_name:
            raise ValidationError('请输入队伍名称')
        
        if len(team_name) < 2 or len(team_name) > 20:
            raise ValidationError('队伍名称长度必须在2-20个字符之间')
            
        if not re.match(r'^[\u4e00-\u9fa5a-zA-Z0-9_]+$', team_name):
            raise ValidationError('队伍名称只能包含中文、英文、数字和下划线')
            
        # 防止XSS和SQL注入
        dangerous_chars = ['<', '>', '&', '"', "'", ';', '--', '/*', '*/']
        if any(char in team_name for char in dangerous_chars):
            raise ValidationError('队伍名称包含非法字符')
                
        return team_name

    def clean_team_code(self):
        """验证队伍认证码"""
        team_code = self.cleaned_data.get('team_code', '').strip()
        team_action = self.cleaned_data.get('team_action')

        if team_action == 'join':
            if not team_code:
                raise ValidationError('加入队伍时必须填写队伍认证码')
            
            if len(team_code) != 6:
                raise ValidationError('队伍认证码长度为6位')
                
            if not re.match(r'^[\u4e00-\u9fa5a-zA-Z0-9_]+$', team_code):
                raise ValidationError('队伍认证码只能包含中文、英文、数字和下划线')
                
            # 防止XSS和SQL注入
            dangerous_chars = ['<', '>', '&', '"', "'", ';', '--', '/*', '*/']
            if any(char in team_code for char in dangerous_chars):
                raise ValidationError('队伍认证码包含非法字符')
        
        return team_code
    
    def clean_invitation_code(self):
        """验证报名码"""
        invitation_code = self.cleaned_data.get('invitation_code', '').strip()
        
        if not invitation_code:
            return invitation_code
            
        if not re.match(r'^[0-9A-Za-z]+$', invitation_code):
            raise ValidationError('报名码只能包含数字和字母')
            
        return invitation_code

class RegistrationConfirmForm(forms.Form):
    """个人赛报名表单"""

    invitation_code = forms.CharField(
        max_length=255,
        required=False,
        label='报名码',
        widget=forms.TextInput(attrs={
            'class': 'form-control', 
            'placeholder': '请输入报名码'
        }),
        help_text='内部赛需要填写报名码'
    )
    
    def __init__(self, *args, **kwargs):
        self.competition = kwargs.pop('competition', None)
        super().__init__(*args, **kwargs)
        
        # 如果不是内部赛，移除报名码字段
        if self.competition and self.competition.visibility_type != Competition.INTERNAL:
            self.fields.pop('invitation_code', None)

    def clean_invitation_code(self):
        """验证报名码"""
        invitation_code = self.cleaned_data.get('invitation_code', '').strip()
        
        if not invitation_code:
            return invitation_code
            
        if not re.match(r'^[0-9A-Za-z]+$', invitation_code):
            raise ValidationError('报名码只能包含数字和字母')
            
        return invitation_code


# 保留旧的表单名称作为别名，便于向后兼容
PersonalInfoForm = RegistrationConfirmForm

class CaptchaForm(forms.Form):
    """验证码表单基类"""
    captcha_key = forms.CharField(widget=forms.HiddenInput())
    captcha = forms.CharField(
        label="验证码",
        max_length=10,
        widget=forms.TextInput(attrs={'class': 'comp-input', 'placeholder': '请输入验证码'}),
        help_text="请输入图片中显示的验证码"
    )
    
    def clean_captcha(self):
        captcha = self.cleaned_data.get('captcha', '').upper()
        captcha_key = self.cleaned_data.get('captcha_key', '')
        
        # 从缓存中获取验证码
        stored_captcha = cache.get(f'registration_captcha_{captcha_key}')
        
        # 验证码使用后立即删除，防止重复使用
        if stored_captcha:
            cache.delete(f'registration_captcha_{captcha_key}')
        
        if not stored_captcha or captcha != stored_captcha:
            raise forms.ValidationError("验证码错误或已过期，请手动重新获取")
        
        return captcha

class CompetitionForm(CaptchaForm, forms.ModelForm):
    class Meta:
        model = Competition
        fields = ['title', 'description', 'img_link', 'start_time', 'end_time', 'competition_type', 'team_max_members', 'slug', 'visibility_type', 'is_audit', 'theme','related_quiz','combined_score_ctf_weight','combined_score_top_percent','dashboard_template']
        widgets = {
            'start_time': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'end_time': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }
    
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
    
    def clean_title(self):
        """清理和验证标题"""
        title = self.cleaned_data.get('title', '').strip()
        
        if not title:
            raise forms.ValidationError('标题不能为空')
            
        # 限制标题长度为50个字符
        if len(title) > 100:
            raise forms.ValidationError('标题长度不能超过100个字符')
            
        # 过滤危险字符
        dangerous_chars = ['<', '>', '"', "'", ';', '&', '|', '`', '$', '#', '\\', '/']
        for char in dangerous_chars:
            if char in title:
                raise forms.ValidationError(f'标题包含非法字符: {char}')
                
        # 只允许中文、英文、数字和基本标点
        pattern = r'^[\u4e00-\u9fa5a-zA-Z0-9_\-\s.,!?]+$'
        if not re.match(pattern, title):
            raise forms.ValidationError('标题只能包含中文、英文、数字和基本标点符号')
            
        return title
    
    def clean_description(self):
        """清理和验证描述"""
        description = self.cleaned_data.get('description', '').strip()
        
        if not description:
            raise forms.ValidationError('描述不能为空')
            
        # 限制描述长度为5000个字符
        if len(description) > 1000:
            raise forms.ValidationError('描述长度不能超过1000个字符')
            
        # 过滤危险字符
        dangerous_chars = ['<script', 'javascript:', 'onload=', 'onerror=', 'onclick=']
        for char in dangerous_chars:
            if char.lower() in description.lower():
                raise forms.ValidationError(f'描述包含非法字符或脚本: {char}')
                
        return description
    
    def clean_slug(self):
        slug = self.cleaned_data.get('slug')
        if slug and not slug.isalnum():
            raise forms.ValidationError('路由只能包含字母和数字')
        return slug
    
    def clean(self):
        cleaned_data = super().clean()
        start_time = cleaned_data.get('start_time')
        end_time = cleaned_data.get('end_time')
        competition_type = cleaned_data.get('competition_type')
        team_max_members = cleaned_data.get('team_max_members')
        related_quiz = cleaned_data.get('related_quiz')
        ctf_weight = cleaned_data.get('combined_score_ctf_weight')
        top_percent = cleaned_data.get('combined_score_top_percent')
        
        # 验证时间
        if start_time and end_time and start_time >= end_time:
            self.add_error('end_time', '结束时间必须晚于开始时间')
        
        # 验证团队赛的队伍人数设置
        if competition_type == 'team':
            if team_max_members is None:
                self.add_error('team_max_members', '团队赛必须设置队伍最大人数')
            elif team_max_members < 2 or team_max_members > 4:
                self.add_error('team_max_members', '队伍最大人数必须在2-4人之间')
        
        # 如果关联了知识竞赛，验证权限和相关参数
        if related_quiz:
            # 验证知识竞赛是否已被关联
            try:
                # OneToOneField 的反向关系，如果没有关联会抛出 ObjectDoesNotExist
                existing_competition = related_quiz.related_competition
                if existing_competition is not None:
                    self.add_error('related_quiz', '该知识竞赛已被其他比赛关联')
            except ObjectDoesNotExist:
                # 未被关联，这是正常的
                pass
            
            # 验证非管理员只能关联自己创建的知识竞赛
            if self.user and not (self.user.is_staff or self.user.is_superuser):
                # 如果知识竞赛没有创建者（系统创建），或者创建者不是当前用户
                if related_quiz.creator is None or related_quiz.creator != self.user:
                    self.add_error('related_quiz', '您只能关联自己创建的知识竞赛')
            
            # 验证CTF权重是否设置
            if ctf_weight is None:
                self.add_error('combined_score_ctf_weight', '关联知识竞赛后必须设置CTF权重')
            elif ctf_weight < 0 or ctf_weight > 1:
                self.add_error('combined_score_ctf_weight', 'CTF权重必须在0-1之间')
            
            # 验证归一化基准百分比是否设置
            if top_percent is None:
                self.add_error('combined_score_top_percent', '关联知识竞赛后必须设置归一化基准百分比')
            elif top_percent < 1 or top_percent > 100:
                self.add_error('combined_score_top_percent', '归一化基准百分比必须在1-100之间')
        
        return cleaned_data

class ChallengeCreateForm(CaptchaForm, forms.ModelForm):
    class Meta:
        model = Challenge
        fields = ['title', 'description', 'category', 'difficulty', 'initial_points', 
                  'minimum_points', 'flag_type', 'flag_template',
                  'docker_image', 'network_topology_config', 'hint', 'tags', 'is_active', 'static_files', 'static_file_url']
        
        widgets = {
            'description': forms.Textarea(attrs={'class': 'comp-input comp-textarea', 'rows': 4}),
            'title': forms.TextInput(attrs={'class': 'comp-input'}),
            'category': forms.Select(attrs={'class': 'comp-input comp-select'}),
            'difficulty': forms.Select(attrs={'class': 'comp-input comp-select'}),
            'initial_points': forms.NumberInput(attrs={
                'class': 'comp-input',
                'min': '200',
                'max': '1000',
                'placeholder': '200-1000分'
            }),
            'minimum_points': forms.NumberInput(attrs={
                'class': 'comp-input',
                'min': '50',
                'placeholder': '最低50分'
            }),
            'flag_type': forms.Select(attrs={'class': 'comp-input comp-select'}),
            'flag_template': forms.TextInput(attrs={'class': 'comp-input'}),
            'docker_image': forms.Select(attrs={'class': 'comp-input comp-select'}),
            'network_topology_config': forms.Select(attrs={'class': 'comp-input comp-select'}),
            'hint': forms.Textarea(attrs={'class': 'comp-input comp-textarea', 'rows': 3}),
            'tags': forms.SelectMultiple(attrs={'class': 'comp-input'}),
            'static_files': forms.Select(attrs={'class': 'comp-input comp-select'}),
            'static_file_url': forms.URLInput(attrs={'class': 'comp-input'}),
        }
    
    def clean_initial_points(self):
        """验证初始分数"""
        initial_points = self.cleaned_data.get('initial_points')
        if initial_points < 200:
            raise ValidationError('初始分数不能低于200分')
        if initial_points > 1000:
            raise ValidationError('初始分数不能超过1000分')
        return initial_points
    
    def clean_minimum_points(self):
        """验证最低分数"""
        minimum_points = self.cleaned_data.get('minimum_points')
        if minimum_points < 50:
            raise ValidationError('最低分数不能低于50分')
        return minimum_points
    
    def clean(self):
        """验证初始分数和最低分数的关系，以及镜像配置的互斥性"""
        cleaned_data = super().clean()
        initial_points = cleaned_data.get('initial_points')
        minimum_points = cleaned_data.get('minimum_points')
        docker_image = cleaned_data.get('docker_image')
        network_topology_config = cleaned_data.get('network_topology_config')
        
        # 验证镜像配置互斥：docker_image 和 network_topology_config 只能选其一
        if docker_image and network_topology_config:
            raise ValidationError("单镜像和多场景题目不能同时设置，请只选择其中一个")
        
        if initial_points and minimum_points:
            if minimum_points >= initial_points:
                raise ValidationError({
                    'minimum_points': f'最低分数必须小于初始分数（当前初始分数：{initial_points}分）'
                })
            
            # 建议最低分数不低于初始分数的20%
            min_suggested = int(initial_points * 0.2)
            if minimum_points < min_suggested:
                raise ValidationError({
                    'minimum_points': f'建议最低分数不低于初始分数的20%（建议最低：{min_suggested}分）'
                })
        
        return cleaned_data
    def __init__(self, *args, **kwargs):
        # 获取当前用户
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        # 如果有用户信息，限制静态文件和Docker选项只显示用户创建的
        if self.user:
            # 限制静态文件选择器只显示用户上传的文件
            from container.models import StaticFile  # 假设这是静态文件的模型
            self.fields['static_files'].queryset = StaticFile.objects.filter(
                author=self.user,
                review_status='APPROVED'
            ).order_by('-upload_time')
            
            # 限制Docker镜像选择器只显示用户创建的镜像
            self.fields['docker_image'].queryset = DockerImage.objects.filter(
                author=self.user,
                review_status='APPROVED'
            ).order_by('-created_at')
            
            # 限制网络拓扑配置选择器只显示用户创建的配置
            from container.models import NetworkTopologyConfig
            self.fields['network_topology_config'].queryset = NetworkTopologyConfig.objects.filter(
                author=self.user
            ).order_by('-created_at')
            
            # 如果用户是管理员，也可以选择系统内置的选项
            if self.user.is_staff or self.user.is_superuser:
                from django.db.models import Q
                self.fields['static_files'].queryset = StaticFile.objects.all().order_by('-upload_time')
                self.fields['docker_image'].queryset = DockerImage.objects.all().order_by('-created_at')
                self.fields['network_topology_config'].queryset = NetworkTopologyConfig.objects.all().order_by('-created_at')
    def clean(self):
        cleaned_data = super().clean()
        flag_type = cleaned_data.get('flag_type')
        flag_template = cleaned_data.get('flag_template')
        initial_points = cleaned_data.get('initial_points') or 0
        minimum_points = cleaned_data.get('minimum_points') or 0
        static_file_url = cleaned_data.get('static_file_url')
        if static_file_url:
            if not re.match(r'^https?://', static_file_url):
                self.add_error('static_file_url', '静态文件URL必须以http://或https://开头')
            if not static_file_url.endswith('.zip') and not static_file_url.endswith('.rar') and not static_file_url.endswith('.7z') and not static_file_url.endswith('.tar') and not static_file_url.endswith('.gz'):
                self.add_error('static_file_url', '静态文件URL必须以.zip、.rar、.7z、.tar或.gz结尾')
            
        # 验证 Flag
        if flag_type == 'STATIC' and not flag_template:
            self.add_error('flag_template', '静态Flag类型必须提供Flag值')

        # 验证分数范围
        if initial_points < 200 or initial_points > 1000:
            self.add_error('initial_points', '初始分数必须是 100-1000')

        if minimum_points < 50:
            self.add_error('minimum_points', '最低分数必须大于等于 50')

        if minimum_points >= initial_points:
            self.add_error('minimum_points', '最低分数必须小于初始分数')

        return cleaned_data



class ChallengeForm(forms.ModelForm):
    """题目表单修改编辑"""
    class Meta:
        model = Challenge
        fields = [
            'title', 'description', 'category', 'difficulty',
            'static_files', 'static_file_url', 'docker_image', 'network_topology_config','flag_type', 'flag_template',
            'hint', 'tags', 'is_active'
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 5}),
            'hint': forms.Textarea(attrs={'rows': 3}),
            'tags': forms.SelectMultiple(attrs={'size': 5}),
        }
    
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        # 设置字段为非必填
        self.fields['static_files'].required = False
        self.fields['static_file_url'].required = False
        self.fields['docker_image'].required = False
        self.fields['network_topology_config'].required = False
        self.fields['hint'].required = False
        self.fields['tags'].required = False
        self.fields['flag_template'].required = False
        
        # 添加帮助文本
        self.fields['title'].help_text = '最多20个字符，支持中英文、数字和基本标点'
        self.fields['description'].help_text = '详细描述题目的背景、目标和提示等信息'
        self.fields['hint'].help_text = '可选，帮助解题的提示信息，支持Markdown格式'
        self.fields['tags'].help_text = 'Ctrl+点击添加多个标签'
        self.fields['is_active'].help_text = '激活后题目将对参赛者可见'

        # 如果有用户信息，限制静态文件和Docker选项只显示用户创建的
        if self.user:
            # 限制静态文件选择器只显示用户上传的文件
            from container.models import StaticFile  # 假设这是静态文件的模型
            self.fields['static_files'].queryset = StaticFile.objects.filter(
                author=self.user,
                review_status='APPROVED'
            ).order_by('-upload_time')
            
            # 限制Docker镜像选择器只显示用户创建的镜像
             # 假设这是Docker模型
            self.fields['docker_image'].queryset = DockerImage.objects.filter(
                author=self.user,
                review_status='APPROVED'
            ).order_by('-created_at')

            # 限制网络拓扑配置选择器只显示用户创建的配置
            from container.models import NetworkTopologyConfig
            self.fields['network_topology_config'].queryset = NetworkTopologyConfig.objects.filter(
                author=self.user
            ).order_by('-created_at')
            # 如果用户是管理员，也可以选择系统内置的选项
            if self.user.is_staff or self.user.is_superuser:
                from django.db.models import Q
                self.fields['static_files'].queryset = StaticFile.objects.all().order_by('-upload_time')
                
                self.fields['docker_image'].queryset = DockerImage.objects.all().order_by('-created_at')
    
    def clean(self):
        cleaned_data = super().clean()
        flag_type = cleaned_data.get('flag_type')
        flag_template = cleaned_data.get('flag_template')
        static_files = cleaned_data.get('static_files')
        docker_image = cleaned_data.get('docker_image')
        static_file_url = cleaned_data.get('static_file_url')
        network_topology_config = cleaned_data.get('network_topology_config')
        # 验证镜像配置互斥：docker_image 和 network_topology_config 只能选其一
        if docker_image and network_topology_config:
            self.add_error('network_topology_config', '单镜像和多场景题目不能同时设置，请只选择其中一个')
        if static_file_url:
            if not re.match(r'^https?://', static_file_url):
                self.add_error('static_file_url', '静态文件URL必须以http://或https://开头')
            if not static_file_url.endswith('.zip') and not static_file_url.endswith('.rar') and not static_file_url.endswith('.7z') and not static_file_url.endswith('.tar') and not static_file_url.endswith('.gz'):
                self.add_error('static_file_url', '静态文件URL必须以.zip、.rar、.7z、.tar或.gz结尾')
            
        # 验证Flag类型和Flag值
        if flag_type == 'STATIC' and not flag_template:
            self.add_error('flag_template', '静态Flag类型必须提供Flag值')

        # 验证部署类型
        
        return cleaned_data
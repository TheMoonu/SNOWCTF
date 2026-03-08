from django import forms
from practice.models import PC_Challenge,CTFUser
from django.core.exceptions import ValidationError
from django.core.cache import cache
import re
from container.models import DockerEngine,DockerImage,StaticFile
class DockerEngineForm(forms.ModelForm):
    class Meta:
        model = DockerEngine
        fields = ['name', 'host', 'port', 'tls_enabled', 'ca_cert', 'client_cert', 'client_key']
        widgets = {
            'ca_cert': forms.FileInput(),
            'client_cert': forms.FileInput(),
            'client_key': forms.FileInput(),
        }

class CaptchaForm(forms.Form):
    """验证码表单基类"""
    captcha_key = forms.CharField(widget=forms.HiddenInput())
    captcha = forms.CharField(
        label="验证码",
        max_length=10,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '请输入验证码'}),
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
            raise forms.ValidationError("验证码错误或已过期，请请点击验证码刷新按钮重新获取")
        
        return captcha

class ChallengeForm(CaptchaForm, forms.ModelForm):
    # 移除deployment_type字段，因为只保留一种部署方式
    
    docker_image = forms.ModelChoiceField(
        queryset=None,
        label='单镜像配置',
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    network_topology_config = forms.ModelChoiceField(
        queryset=None,
        label='多场景题目',
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    static_files = forms.ModelChoiceField(
        queryset=None,
        label='题目附件配置',
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    static_file_url = forms.URLField(
        label='附件URL',
        required=False,
        widget=forms.URLInput(attrs={'class': 'form-control'})
    )
    class Meta:
        model = PC_Challenge
        fields = [
            'title', 'description', 'category', 
            'difficulty', 'points', 'coins',
            'reward_coins', 'hint', 'writeup_is_public', 'writeup_cost', 
            'is_active', 'flag_type', 'flag_template', 'flag_count', 'flag_points', 'tags'
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'category': forms.Select(attrs={'class': 'form-control'}),
            'difficulty': forms.Select(attrs={'class': 'form-control'}),
            'flag_type': forms.Select(attrs={'class': 'form-control'}),
            'flag_template': forms.TextInput(attrs={'class': 'form-control'}),
            'flag_count': forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'max': '10'}),
            'flag_points': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '例如: [10, 20, 30, 40] 或留空自动分配'
            }),
            'points': forms.NumberInput(attrs={'class': 'form-control'}),
            'coins': forms.NumberInput(attrs={'class': 'form-control'}),
            'reward_coins': forms.NumberInput(attrs={'class': 'form-control'}),
            'hint': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'writeup_cost': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'tags': forms.SelectMultiple(attrs={'class': 'form-control'}),
        }
    
    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.user = user  # 保存为实例属性
        if user:
            from container.models import NetworkTopologyConfig
            if user.is_superuser:
                # 管理员可以看到所有配置
                self.fields['docker_image'].queryset = DockerImage.objects.all()
                self.fields['network_topology_config'].queryset = NetworkTopologyConfig.objects.all()
                self.fields['static_files'].queryset = StaticFile.objects.all()
            else:
                # 普通用户只能看到自己已审核通过的配置
                self.fields['docker_image'].queryset = DockerImage.objects.filter(
                    author=user,
                    review_status='APPROVED'
                )
                self.fields['network_topology_config'].queryset = NetworkTopologyConfig.objects.filter(
                    author=user
                )
                self.fields['static_files'].queryset = StaticFile.objects.filter(
                    author=user,
                    review_status='APPROVED'
                )
    
    def clean(self):
        """验证镜像配置的互斥性"""
        from django.core.exceptions import ValidationError
        cleaned_data = super().clean()
        docker_image = cleaned_data.get('docker_image')
        network_topology_config = cleaned_data.get('network_topology_config')
        static_file_url = cleaned_data.get('static_file_url')
        
        # 验证镜像配置互斥：docker_image 和 network_topology_config 只能选其一
        if docker_image and network_topology_config:
            raise ValidationError("单镜像和多场景题目不能同时设置，请只选择其中一个")
        if static_file_url:
            if not re.match(r'^https?://', static_file_url):
                self.add_error('static_file_url', '静态文件URL必须以http://或https://开头')
            if not static_file_url.endswith('.zip') and not static_file_url.endswith('.rar') and not static_file_url.endswith('.7z') and not static_file_url.endswith('.tar') and not static_file_url.endswith('.gz'):
                self.add_error('static_file_url', '静态文件URL必须以.zip、.rar、.7z、.tar或.gz结尾')
        return cleaned_data

class DockerImageForm(forms.ModelForm):
    class Meta:
        model = DockerImage
        fields = ['name', 'tag', 'registry', 'category', 'description',
            'flag_inject_method', 'flag_env_name', 'flag_script',
            'exposed_ports']
        
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)  # 从 kwargs 中获取并移除 user
        super().__init__(*args, **kwargs)



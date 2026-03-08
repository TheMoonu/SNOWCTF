from django import forms
from container.models import StaticFile, DockerImage, DockerEngine
from django.core.cache import cache

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

class StaticFileForm(CaptchaForm, forms.ModelForm):
    class Meta:
        model = StaticFile
        fields = ['name', 'file', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '输入文件名称'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': '输入文件描述（可选）'}),
            'file': forms.FileInput(attrs={'class': 'form-control-file'})
        }
        help_texts = {
            'name': '文件的名称，便于识别',
            'file': '上传压缩包文件，支持的格式：zip, rar, 7z, tar, gz',
            'description': '文件的详细描述，可以包含文件内容、用途等信息'
        }



class DockerImageForm(CaptchaForm, forms.ModelForm):
    """Docker镜像配置表单"""
    
    class Meta:
        model = DockerImage
        fields = [
            'name', 'tag', 'registry', 'category', 'description',
            'flag_inject_method', 'flag_env_name', 'flag_script',
            'exposed_ports', 'memory_limit', 'cpu_limit','entrance'
        ]
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '例如: nginx'
            }),
            'tag': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'latest'
            }),
            'registry': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'docker.io'
            }),
            'category': forms.Select(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': '镜像功能描述、适用场景等'
            }),
            'flag_inject_method': forms.Select(attrs={'class': 'form-control'}),
            'flag_env_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '例如: FLAG, CTF_FLAG, GZCTF_FLAG'
            }),
            'flag_script': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': '例如: sh /flag.sh {SNOW_FLAG}'
            }),
            'exposed_ports': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '80,3306'
            }),
            'memory_limit': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '256',
                'min': '64',
                'step': '1'
            }),
            'cpu_limit': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '0.5',
                'min': '0.1',
                'step': '0.1'
            }),
            'entrance': forms.Select(attrs={'class': 'form-control'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 动态设置字段是否必填
        self.fields['flag_env_name'].required = False
        self.fields['flag_script'].required = False
        self.fields['description'].required = False
        self.fields['memory_limit'].required = False
        self.fields['cpu_limit'].required = False
    
    def clean(self):
        cleaned_data = super().clean()
        flag_inject_method = cleaned_data.get('flag_inject_method')
        flag_env_name = cleaned_data.get('flag_env_name')
        flag_script = cleaned_data.get('flag_script')
        
        # 验证 Flag 注入方法的相关字段
        if flag_inject_method == 'CUSTOM_ENV' and not flag_env_name:
            self.add_error('flag_env_name', '选择自定义环境变量时，必须填写环境变量名')
        
        if flag_inject_method == 'SCRIPT' and not flag_script:
            self.add_error('flag_script', '选择脚本注入时,必须填写注入脚本')
        
        # 验证端口格式
        exposed_ports = cleaned_data.get('exposed_ports')
        if exposed_ports:
            try:
                ports = [p.strip() for p in exposed_ports.split(',')]
                for port in ports:
                    if not port.isdigit() or not (1 <= int(port) <= 65535):
                        self.add_error('exposed_ports', f'端口 {port} 无效，端口必须是1-65535之间的数字')
                        break
            except Exception:
                self.add_error('exposed_ports', '端口格式错误，多个端口请用逗号分隔')
        
        # 验证资源限制
        memory_limit = cleaned_data.get('memory_limit')
        if memory_limit is not None and memory_limit < 64:
            self.add_error('memory_limit', '内存限制不能小于64MB')
        
        cpu_limit = cleaned_data.get('cpu_limit')
        if cpu_limit is not None and cpu_limit < 0.1:
            self.add_error('cpu_limit', 'CPU限制不能小于0.1核')
        
        return cleaned_data


class DockerEngineAdminForm(forms.ModelForm):
    """容器引擎管理表单（支持 Docker 和 K8s）"""
    
    class Meta:
        model = DockerEngine
        fields = '__all__'
        widgets = {
            'host': forms.TextInput(attrs={
                'placeholder': 'IP 地址'
            }),
            'port': forms.NumberInput(attrs={
                'placeholder': '端口号'
            }),
            'domain': forms.TextInput(attrs={
                'placeholder': '可选'
            }),
            'kubeconfig_file': forms.FileInput(attrs={
                'accept': '.yaml,.yml,.conf',
                'class': 'form-control-file'
            }),
            'namespace': forms.TextInput(attrs={
                'placeholder': 'ctf-challenges'
            }),
            'mirror_registry': forms.TextInput(attrs={
                'placeholder': 'dockerproxy.com 或 docker.mirrors.sjtug.sjtu.edu.cn'
            }),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # 根据实例的引擎类型和主机类型动态设置字段的必填状态
        if self.instance and self.instance.pk:
            if self.instance.engine_type == 'KUBERNETES':
                # K8s 引擎：K8s 字段必填，Docker 字段不必填
                if 'kubeconfig_file' in self.fields:
                    self.fields['kubeconfig_file'].required = False
                if 'namespace' in self.fields:
                    self.fields['namespace'].required = False
                if 'port' in self.fields:
                    self.fields['port'].required = False
                if 'tls_enabled' in self.fields:
                    self.fields['tls_enabled'].required = False
                if 'domain' in self.fields:
                    self.fields['domain'].required = False
            elif self.instance.host_type == 'LOCAL':
                # Docker 本地模式
                if 'port' in self.fields:
                    self.fields['port'].required = False
                if 'tls_enabled' in self.fields:
                    self.fields['tls_enabled'].required = False
                if 'kubeconfig_file' in self.fields:
                    self.fields['kubeconfig_file'].required = False
                if 'namespace' in self.fields:
                    self.fields['namespace'].required = False
            else:
                # Docker 远程模式
                if 'port' in self.fields:
                    self.fields['port'].required = True
                if 'kubeconfig_file' in self.fields:
                    self.fields['kubeconfig_file'].required = False
                if 'namespace' in self.fields:
                    self.fields['namespace'].required = False
    
    def clean(self):
        cleaned_data = super().clean()
        engine_type = cleaned_data.get('engine_type')
        host_type = cleaned_data.get('host_type')
        port = cleaned_data.get('port')
        tls_enabled = cleaned_data.get('tls_enabled')
        ca_cert = cleaned_data.get('ca_cert')
        client_cert = cleaned_data.get('client_cert')
        client_key = cleaned_data.get('client_key')
        kubeconfig_file = cleaned_data.get('kubeconfig_file')
        namespace = cleaned_data.get('namespace')
        
        # K8s 引擎验证
        if engine_type == 'KUBERNETES':
            # K8s 不需要 port 和 TLS 配置
            cleaned_data['port'] = None
            cleaned_data['tls_enabled'] = False
            cleaned_data['ca_cert'] = None
            cleaned_data['client_cert'] = None
            cleaned_data['client_key'] = None
            
            # 验证 kubeconfig 文件（如果上传）
            if kubeconfig_file:
                try:
                    # 验证文件大小（不应超过 1MB）
                    if kubeconfig_file.size > 1024 * 1024:
                        self.add_error('kubeconfig_file', 'Kubeconfig 文件过大，不应超过 1MB')
                    else:
                        # 验证文件内容是否是有效的 YAML
                        import yaml
                        content = kubeconfig_file.read()
                        yaml.safe_load(content)
                        kubeconfig_file.seek(0)  # 重置文件指针
                except yaml.YAMLError as e:
                    self.add_error('kubeconfig_file', f'无效的 YAML 格式: {str(e)}')
                except Exception as e:
                    # 文件读取或其他错误，跳过验证
                    pass
            
            # 设置默认命名空间
            if not namespace:
                cleaned_data['namespace'] = 'ctf-challenges'
        
        # Docker 引擎验证
        else:
            # Docker 不需要 K8s 配置
            # 注意：不要直接设置为 None，这会导致编辑时清空已有文件
            # 让 save_model 方法处理字段清理
            if kubeconfig_file:
                cleaned_data['kubeconfig_file'] = None
            cleaned_data['namespace'] = ''
            
            # 验证：远程模式必须填写端口
            if host_type == 'REMOTE' and not port:
                self.add_error('port', '远程模式必须填写端口号！')
            
            # 验证：本地模式不应该填写端口
            if host_type == 'LOCAL' and port:
                cleaned_data['port'] = None  # 自动清空
            
            # 验证：如果启用TLS，必须上传所有证书
            if tls_enabled:
                # 对于新上传的文件或已存在的文件都需要验证
                has_ca = ca_cert or (self.instance.pk and self.instance.ca_cert)
                has_client_cert = client_cert or (self.instance.pk and self.instance.client_cert)
                has_client_key = client_key or (self.instance.pk and self.instance.client_key)
                
                if not has_ca:
                    self.add_error('ca_cert', '启用TLS时必须上传CA证书')
                if not has_client_cert:
                    self.add_error('client_cert', '启用TLS时必须上传客户端证书')
                if not has_client_key:
                    self.add_error('client_key', '启用TLS时必须上传客户端密钥')
            else:
                # 如果禁用TLS，清除证书字段
                if not ca_cert:
                    cleaned_data['ca_cert'] = None
                if not client_cert:
                    cleaned_data['client_cert'] = None
                if not client_key:
                    cleaned_data['client_key'] = None
            
            # 验证端口范围
            if port and (port < 1 or port > 65535):
                self.add_error('port', '端口号必须在1-65535之间')
        
        return cleaned_data
    
    class Media:
        js = ('container/js/admin_docker_engine_form.js',)
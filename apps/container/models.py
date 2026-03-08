from django.db import models, transaction
from django.urls import reverse
import uuid
from django.conf import settings
from django.contrib.auth.models import User
import math
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import FileExtensionValidator,RegexValidator
from docker.tls import TLSConfig
import time
from django.db.models import F, Count
import os
from django.utils import timezone
from datetime import datetime, timedelta
from django.contrib.auth import get_user_model
import logging
from django.core.cache import cache
from datetime import timedelta






MAX_DOWNLOAD_ATTEMPTS = 15  # 设置5分钟内允许的最大下载次数
LOCKOUT_TIME = 3600*24  # 设置封号时间（24小时）

# 使用apps.container作为logger名称，匹配settings.py中的配置
logger = logging.getLogger('apps.container')

def certificate_upload_path(instance, filename):
    # 使用时间戳生成唯一文件名
    timestamp = int(time.time())
    # 添加年月日的路径结构
    date_path = datetime.now().strftime('%Y/%m/%d')
    return f'certificates/{date_path}/{timestamp}_{filename}'


class ContainerEngineConfig(models.Model):
    """容器引擎全局配置 - 单例模式（只有一条记录）"""
    
    # ==================== 容器生命周期配置 ====================
    container_expiry_hours = models.FloatField(
        '容器过期时间（小时）',
        default=2.0,
        help_text='容器创建后的有效时间。）'
    )
    
    # ==================== 容器并发限制 ====================
    max_containers_per_user = models.IntegerField(
        '每用户最大容器数',
        default=1,
        help_text='单个用户同时运行的最大容器数。'
    )
    max_containers_per_challenge = models.IntegerField(
        '每题目最大容器数',
        default=100,
        help_text='单个题目同时运行的最大容器数，防止单题耗尽资源。推荐：50-100'
    )
    max_containers_per_team = models.IntegerField(
        '每队伍最大容器数',
        default=1,
        help_text='单个队伍同时运行的最大容器数'
    )
    max_concurrent_creates = models.IntegerField(
        '最大并发创建容器数',
        default=10,
        help_text='系统全局同时创建容器的最大数量。值越小越安全。可理解1秒可创建多少容器'
    )
    
    # ==================== 令牌桶限流配置 ====================
    token_bucket_max = models.IntegerField(
        '令牌桶最大令牌数',
        default=200,
        help_text='令牌桶限流的最大令牌数。小容器消耗1个，中容器3个，大容器5个。推荐：200'
    )
    token_bucket_refill_rate = models.IntegerField(
        '令牌桶补充速率（个/秒）',
        default=40,
        help_text='每秒补充的令牌数。值越大，系统吞吐量越高。推荐：40'
    )
    
    # ==================== Docker 引擎配置 ====================
    docker_pool_min_size = models.IntegerField(
        'Docker连接池最小连接数',
        default=10,
        help_text='Docker客户端连接池的最小连接数（预创建）。推荐：10'
    )
    docker_pool_max_size = models.IntegerField(
        'Docker连接池最大连接数',
        default=50,
        help_text='Docker客户端连接池的最大连接数。建议 ≥ Celery并发数。推荐：50'
    )
    docker_max_usage_threshold = models.FloatField(
        'Docker宿主机资源使用率阈值',
        default=0.80,
        help_text='Docker宿主机CPU/内存使用率超过此值时，拒绝创建新容器。0.85 = 85%'
    )
    docker_image_pull_timeout = models.IntegerField(
        'Docker镜像拉取超时时间（秒）',
        default=300,
        help_text='前台用户拉取镜像的超时时间。网络不好时适当增加。推荐：200-300'
    )
    
    # ==================== K8s 节点资源阈值 ====================
    k8s_node_memory_threshold = models.FloatField(
        'K8s节点内存使用率阈值',
        default=0.80,
        help_text='单个节点内存使用率超过此值时，不再分配新容器到该节点。0.80 = 80%'
    )
    k8s_node_cpu_threshold = models.FloatField(
        'K8s节点CPU使用率阈值',
        default=0.80,
        help_text='单个节点CPU使用率超过此值时，不再分配新容器到该节点。0.80 = 80%'
    )
    
    # ==================== K8s 集群资源阈值 ====================
    k8s_cluster_memory_threshold = models.FloatField(
        'K8s集群内存使用率阈值',
        default=0.80,
        help_text='整个集群内存使用率超过此值时，拒绝创建新容器。0.80 = 80%'
    )
    k8s_cluster_cpu_threshold = models.FloatField(
        'K8s集群CPU使用率阈值',
        default=0.80,
        help_text='整个集群CPU使用率超过此值时，拒绝创建新容器。0.80 = 80%'
    )
    k8s_max_usage_threshold = models.FloatField(
        'K8s集群资源使用率总阈值',
        default=0.80,
        help_text='K8s集群整体资源使用率阈值（通用检查）。0.90 = 90%'
    )
    
    # ==================== K8s 原子预占配置 ====================
    k8s_node_reservation_timeout = models.IntegerField(
        'K8s节点原子预占超时时间（秒）',
        default=30,
        help_text='节点资源原子预占的过期时间。Pod创建通常5-10秒完成。推荐：30'
    )
    k8s_node_cache_timeout = models.FloatField(
        'K8s节点资源缓存时间（秒）',
        default=0.5,
        help_text='节点资源信息的缓存时间。值越小数据越准确，但API压力越大。推荐：0.5'
    )
    
    # ==================== K8s 资源策略配置 ====================
    k8s_requests_ratio = models.FloatField(
        'K8s requests资源配比',
        default=0.5,
        help_text='K8s requests与limits的比例。1.0=绝对稳定，0.5=平衡（推荐），0.3=激进'
    )
    k8s_use_max_node_capacity = models.BooleanField(
        'K8s使用最大节点容量策略',
        default=True,
        help_text='True=保守（使用最大单节点容量，适合K3s/小集群），False=激进（使用集群总资源）'
    )
    
    # ==================== K8s API连接池配置 ====================
    k8s_connection_pool_maxsize = models.IntegerField(
        'K8s API连接池最大连接数',
        default=200,
        help_text='K8s API连接池的最大连接数，支持高并发请求。推荐：200'
    )
    k8s_connection_pool_block = models.BooleanField(
        'K8s连接池阻塞模式',
        default=False,
        help_text='True=阻塞等待连接，False=快速失败。推荐：False（快速失败）'
    )
    
    # ==================== 管理信息 ====================
    updated_at = models.DateTimeField('最后更新时间', auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='最后修改人'
    )
    
    class Meta:
        verbose_name = '资源配置'
        verbose_name_plural = '资源配置'
    
    def __str__(self):
        return f'容器资源配置（最后更新：{self.updated_at.strftime("%Y-%m-%d %H:%M")}）'
    
    def save(self, *args, **kwargs):
        # 单例模式：只允许一条记录
        self.pk = 1
        super().save(*args, **kwargs)
        
        # 保存后清除所有相关缓存
        cache.delete('container_config:singleton')
    
    @classmethod
    def get_config(cls):
        """获取配置单例（带缓存）"""
        cache_key = 'container_config:singleton'
        config = cache.get(cache_key)
        
        if config is None:
            config, created = cls.objects.get_or_create(pk=1)
            cache.set(cache_key, config, timeout=3600*24*7)
        
        return config
    
    @classmethod
    def initialize_defaults(cls):
        """初始化默认配置（第一次使用时调用）"""
        cls.objects.get_or_create(pk=1)


def certificate_upload_path(instance, filename):
    # 使用时间戳生成唯一文件名
    timestamp = int(time.time())
    # 添加年月日的路径结构
    date_path = datetime.now().strftime('%Y/%m/%d')
    return f'certificates/{date_path}/{timestamp}_{filename}'

class DockerEngine(models.Model):
    ENGINE_TYPE_CHOICES = [
        ('DOCKER', 'Docker'),
        ('KUBERNETES', 'Kubernetes (k3s/k8s)'),
    ]
    
    HOST_CHOICES = [
        ('LOCAL', '本地模式'),
        ('REMOTE', '远程模式'),
    ]
    
    # 引擎类型（新增）
    engine_type = models.CharField(
        "引擎类型",
        max_length=20,
        choices=ENGINE_TYPE_CHOICES,
        default='DOCKER',
        help_text="选择容器引擎类型：Docker 或 Kubernetes"
    )
    
    name = models.CharField("引擎名称", max_length=100)
    host_type = models.CharField("主机类型", max_length=6, choices=HOST_CHOICES, default='LOCAL')
    host = models.CharField("主机地址", max_length=200,  blank=True, null=True,default=None,help_text="本地模式下题目服务和系统服务都运行在同一台主机上所以填你系统的主机IP，远程模式下题目服务和系统服务运行在不同的主机上填题目服务的主机IP")
    port = models.IntegerField("端口", blank=True, null=True, default=None, help_text="本地模式不需要填写，远程模式需要填写,请注意检查题目服务是否开放此端口" )
    tls_enabled = models.BooleanField("启用TLS", default=False, help_text="本地模式不需启用TLS。远程题目服务启用条件：\n1、需要您有一台独立的linux主机；\n2、需要您在linux主机上安装docker开放相关防火墙和端口；\n3、需要开启docker远程加密访问（一键开启题目服务docker远程访问脚本在系统安装目录config_sh目录下，直接在题目服务器运行即可。注意开放相关端口。）；4、脚本同时也会生成客户端证书，把客户端证书导出来上传至下面平台对应位置就行" )
    domain = models.CharField("域名", max_length=255, default=None, blank=True, null=True)  
    ca_cert = models.FileField(
        "CA证书",
        upload_to=certificate_upload_path,
        validators=[FileExtensionValidator(allowed_extensions=['pem', 'crt'])],
        blank=True,
        null=True
    )
    client_cert = models.FileField(
        "客户端证书",
        upload_to=certificate_upload_path,
        validators=[FileExtensionValidator(allowed_extensions=['pem', 'crt'])],
        blank=True,
        null=True
    )
    client_key = models.FileField(
        "客户端密钥",
        upload_to=certificate_upload_path,
        validators=[FileExtensionValidator(allowed_extensions=['pem', 'key'])],
        blank=True,
        null=True
    )
    
    # ==================== K8s 专用配置 ====================
    kubeconfig_file = models.FileField(
        "Kubeconfig 配置文件",
        upload_to=certificate_upload_path,  # 复用证书的上传路径
        validators=[FileExtensionValidator(allowed_extensions=['yaml', 'yml', 'conf'])],
        blank=True,
        null=True,
        help_text="上传 Kubernetes 配置文件 (kubeconfig)。"
    )
    namespace = models.CharField(
        "K8s 命名空间",
        max_length=100,
        default='ctf-challenges',
        blank=True,
        help_text="Kubernetes 命名空间，用于隔离题目容器"
    )
    
    verify_ssl = models.BooleanField(
        "验证 SSL 证书",
        default=False,
        help_text="是否验证 K8s API Server 的 SSL 证书。内网环境建议关闭（使用 IP 访问时会失败）"
    )
    
    # ==================== K8s 安全配置 ====================
    SECURITY_LEVEL_CHOICES = [
        ('LOW', '低 - 宽松（适合普通题目）'),
        ('MEDIUM', '中 - 平衡（推荐）'),
        ('HIGH', '高 - 严格（高安全场景）'),
        ('CUSTOM', '自定义'),
    ]
    
    security_level = models.CharField(
        "安全级别",
        max_length=10,
        choices=SECURITY_LEVEL_CHOICES,
        default='MEDIUM',
        help_text="预设安全级别。选择'自定义'可手动配置详细安全策略"
    )
    
    # 容器安全配置
    allow_privileged = models.BooleanField(
        "允许特权模式",
        default=False,
        help_text="是否允许容器以特权模式运行。⚠️ 高风险，仅在必要时启用（如 Docker-in-Docker）"
    )
    
    allow_host_network = models.BooleanField(
        "允许宿主机网络",
        default=False,
        help_text="是否允许容器使用宿主机网络命名空间。⚠️ 高风险，会绕过 NetworkPolicy"
    )
    
    allow_host_pid = models.BooleanField(
        "允许宿主机 PID",
        default=False,
        help_text="是否允许容器访问宿主机进程。⚠️ 高风险"
    )
    
    allow_host_ipc = models.BooleanField(
        "允许宿主机 IPC",
        default=False,
        help_text="是否允许容器使用宿主机 IPC 命名空间。⚠️ 高风险"
    )
    
    enable_service_account = models.BooleanField(
        "启用 ServiceAccount",
        default=False,
        help_text="是否自动挂载 K8s ServiceAccount Token。⚠️ 可能被用于攻击 K8s API"
    )
    
    drop_capabilities = models.TextField(
        "移除的 Capabilities",
        default='NET_RAW,SYS_ADMIN,SYS_MODULE,SYS_PTRACE',
        blank=True,
        help_text="要移除的 Linux Capabilities，用逗号分隔。留空表示不移除任何 capabilities。注意：移除 SETUID/SETGID 会导致 nginx/apache 等应用无法切换用户而启动失败。"
    )
    
    enable_seccomp = models.BooleanField(
        "启用 Seccomp",
        default=True,
        help_text="启用 Seccomp 系统调用过滤，降低内核漏洞利用风险。推荐启用"
    )
    
    enable_network_policy = models.BooleanField(
        "启用网络策略",
        default=True,
        help_text="自动应用 NetworkPolicy，限制容器出站流量。⚠️ 关闭后容器可自由访问外网"
    )
    
    is_active = models.BooleanField("是否激活", default=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)
    
    # ==================== 健康监控字段 ====================
    HEALTH_STATUS_CHOICES = [
        ('HEALTHY', '健康'),
        ('WARNING', '警告'),
        ('CRITICAL', '严重'),
        ('OFFLINE', '离线'),
        ('UNKNOWN', '未知'),
    ]
    
    health_status = models.CharField(
        "健康状态", 
        max_length=10, 
        choices=HEALTH_STATUS_CHOICES, 
        default='UNKNOWN',
        help_text="引擎当前健康状态"
    )
    last_health_check = models.DateTimeField(
        "最后检查时间", 
        null=True, 
        blank=True,
        help_text="最后一次健康检查的时间"
    )
    health_check_error = models.TextField(
        "健康检查错误", 
        blank=True, 
        null=True,
        help_text="健康检查失败时的错误信息"
    )
    
    # 资源使用情况（JSON格式存储）
    cpu_usage = models.FloatField("CPU使用率(%)", null=True, blank=True, help_text="0-100")
    memory_usage = models.FloatField("内存使用率(%)", null=True, blank=True, help_text="0-100")
    disk_usage = models.FloatField("磁盘使用率(%)", null=True, blank=True, help_text="0-100")
    
    # 容器统计
    running_containers = models.IntegerField("运行中的容器数", default=0)
    total_containers = models.IntegerField("总容器数", default=0)
    
    # 性能指标
    response_time = models.FloatField(
        "响应时间(ms)", 
        null=True, 
        blank=True,
        help_text="最后一次健康检查的响应时间"
    )

    class Meta:
        verbose_name = "容器引擎"
        verbose_name_plural = "容器引擎"

    def get_docker_url(self):
        if self.host_type == 'LOCAL':
            return "unix:///var/run/docker.sock"
        else:
            return f"tcp://{self.host}:{self.port}"

    def __str__(self):
        engine_type_display = dict(self.ENGINE_TYPE_CHOICES).get(self.engine_type, self.engine_type)
        return f"{self.name} ({engine_type_display})"
    
    @property
    def url(self):
        protocol = "https" if self.needs_tls else "http"
        return f"{protocol}://{self.host}:{self.port}"

    @property
    def needs_tls(self):
        return self.host_type == 'REMOTE' and self.tls_enabled
    
    def get_cert_path(self, cert_field):
        """获取证书文件路径（兼容 MinIO 和本地存储）"""
        import tempfile
        from django.core.files.storage import default_storage
        
        if not cert_field or not cert_field.name:
            return None
        
        try:
            # 尝试获取本地路径（本地存储）
            if hasattr(cert_field, 'path'):
                return cert_field.path
        except NotImplementedError:
            # MinIO 存储：下载到临时文件
            pass
        
        # 从存储读取内容并保存到临时文件
        suffix = os.path.splitext(cert_field.name)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            with cert_field.open('rb') as f:
                tmp.write(f.read())
            return tmp.name

    @property
    def ca_cert_path(self):
        return self.get_cert_path(self.ca_cert)

    @property
    def client_cert_path(self):
        return self.get_cert_path(self.client_cert)

    @property
    def client_key_path(self):
        return self.get_cert_path(self.client_key)
    
    @property
    def kubeconfig_file_path(self):
        """获取 kubeconfig 文件路径"""
        return self.get_cert_path(self.kubeconfig_file)
    
    def get_tls_config(self):
        if self.tls_enabled:
            return TLSConfig(
                ca_cert=self.ca_cert_path,
                client_cert=(self.client_cert_path, self.client_key_path),
                verify=True
            )
        return None
    
    # ==================== 健康监控方法 ====================
    
    def check_health(self, timeout=10):
        """
        检查容器引擎健康状态（支持 Docker 和 K8s）
        
        Args:
            timeout: 连接超时时间（秒）
        
        Returns:
            dict: 健康检查结果
                {
                    'status': 'HEALTHY'|'WARNING'|'CRITICAL'|'OFFLINE',
                    'details': {...},
                    'error': str or None
                }
        """
        # 根据引擎类型选择检查方法
        if self.engine_type == 'KUBERNETES':
            return self._check_k8s_health(timeout)
        else:
            return self._check_docker_health(timeout)
    
    def _check_docker_health(self, timeout=10):
        """检查 Docker 引擎健康状态"""
        import docker
        from docker.errors import DockerException
        import time as time_module
        
        start_time = time_module.time()
        
        try:
            # 创建 Docker 客户端
            docker_url = self.get_docker_url()
            tls_config = self.get_tls_config()
            
            client = docker.DockerClient(
                base_url=docker_url,
                tls=tls_config,
                timeout=timeout
            )
            
            # 1. 检查连接性 - ping Docker daemon
            ping_result = client.ping()
            if not ping_result:
                raise DockerException("Docker daemon ping failed")
            
            # 2. 获取系统信息
            info = client.info()
            
            # 3. 获取容器统计
            containers = client.containers.list(all=True)
            running_containers = len([c for c in containers if c.status == 'running'])
            total_containers = len(containers)
            
            # 4. 计算响应时间
            response_time = (time_module.time() - start_time) * 1000  # 转换为毫秒
            
            # 5. 获取系统资源使用情况
            # Docker info 包含的系统信息
            total_memory = info.get('MemTotal', 0)
            
            # 计算 CPU 核心数
            cpu_count = info.get('NCPU', 0)
            
            # 尝试获取资源使用率（使用系统级别监控，更准确）
            try:
                # 本地模式：直接获取系统资源
                if self.host_type == 'LOCAL':
                    try:
                        import psutil
                        # 获取系统 CPU 使用率（1秒采样）
                        self.cpu_usage = round(psutil.cpu_percent(interval=1), 2)
                        
                        # 获取系统内存使用率
                        mem = psutil.virtual_memory()
                        self.memory_usage = round(mem.percent, 2)
                        
                        # 获取磁盘使用率（Docker 数据目录）
                        disk = psutil.disk_usage('/var/lib/docker' if os.path.exists('/var/lib/docker') else '/')
                        self.disk_usage = round(disk.percent, 2)
                    except ImportError:
                        logger.warning("psutil 未安装，使用容器统计方式")
                        raise  # 回退到容器统计方式
                else:
                    # 远程模式：使用容器平均值估算
                    raise Exception("使用容器统计方式")
                    
            except Exception:
                # 回退方案：通过容器统计估算
                try:
                    running_containers_objs = [c for c in containers if c.status == 'running']
                    
                    if running_containers_objs:
                        # 简化的容器统计方式
                        # CPU: 基于运行容器数估算
                        container_cpu_estimate = min(running_containers * 5, 100)  # 每个容器估算5%
                        self.cpu_usage = round(container_cpu_estimate, 2)
                        
                        # 内存：基于 Docker info 中的内存统计
                        if total_memory > 0:
                            # 估算：每个运行容器平均使用 256MB
                            estimated_used = running_containers * 256 * 1024 * 1024
                            self.memory_usage = round(min((estimated_used / total_memory) * 100, 95.0), 2)
                        else:
                            self.memory_usage = round(min(running_containers * 10, 80), 2)
                    else:
                        # 没有运行的容器
                        self.cpu_usage = round(5.0, 2)  # 基础系统占用
                        self.memory_usage = round(10.0, 2)  # 基础系统占用
                    
                    # Docker 磁盘使用率估算
                    image_count = len(client.images.list())
                    if image_count > 20:
                        self.disk_usage = min(60.0 + (image_count - 20) * 2, 95.0)
                    elif image_count > 10:
                        self.disk_usage = 40.0 + (image_count - 10) * 2
                    else:
                        self.disk_usage = 20.0 + image_count * 2
                        
                except Exception as e:
                    logger.warning(f"获取资源使用率失败: {str(e)}")
                    self.cpu_usage = None
                    self.memory_usage = None
                    self.disk_usage = None
            
            # 6. 判断健康状态
            health_status = 'HEALTHY'
            warnings = []
            
            # 响应时间检查
            if response_time > 15000:  # 10秒
                health_status = 'CRITICAL'
                warnings.append(f'响应时间过长: {response_time:.0f}ms')
            elif response_time > 10000:  # 10秒
                if health_status != 'CRITICAL':
                    health_status = 'WARNING'
                warnings.append(f'响应时间较慢: {response_time:.0f}ms')
            
            # CPU 使用率检查
            if self.cpu_usage is not None:
                if self.cpu_usage > 90:
                    health_status = 'CRITICAL'
                    warnings.append(f'CPU 使用率过高: {self.cpu_usage:.1f}%')
                elif self.cpu_usage > 80:
                    if health_status != 'CRITICAL':
                        health_status = 'WARNING'
                    warnings.append(f'CPU 使用率较高: {self.cpu_usage:.1f}%')
            
            # 内存使用率检查
            if self.memory_usage is not None:
                if self.memory_usage > 90:
                    health_status = 'CRITICAL'
                    warnings.append(f'内存使用率过高: {self.memory_usage:.1f}%')
                elif self.memory_usage > 80:
                    if health_status != 'CRITICAL':
                        health_status = 'WARNING'
                    warnings.append(f'内存使用率较高: {self.memory_usage:.1f}%')
            
            # 磁盘使用率检查
            if self.disk_usage is not None:
                if self.disk_usage > 90:
                    health_status = 'CRITICAL'
                    warnings.append(f'磁盘使用率过高: {self.disk_usage:.1f}%')
                elif self.disk_usage > 85:
                    if health_status != 'CRITICAL':
                        health_status = 'WARNING'
                    warnings.append(f'磁盘使用率较高: {self.disk_usage:.1f}%')
            
            # 容器数量检查
            if running_containers > 100:
                if health_status != 'CRITICAL':
                    health_status = 'WARNING'
                warnings.append(f'运行容器数较多: {running_containers}')
            
            # 构建详细信息
            details = {
                'docker_version': info.get('ServerVersion', 'Unknown'),
                'os': info.get('OperatingSystem', 'Unknown'),
                'kernel': info.get('KernelVersion', 'Unknown'),
                'cpu_count': cpu_count,
                'total_memory_gb': round(total_memory / (1024**3), 2) if total_memory else 0,
                'running_containers': running_containers,
                'total_containers': total_containers,
                'response_time_ms': round(response_time, 2),
                'warnings': warnings,
                'check_time': timezone.now().isoformat()
            }
            
            # 更新模型字段
            self.health_status = health_status
            self.last_health_check = timezone.now()
            self.health_check_error = None
            self.running_containers = running_containers
            self.total_containers = total_containers
            self.response_time = round(response_time, 2)
            
            self.save(update_fields=[
                'health_status', 'last_health_check', 'health_check_error',
                'running_containers', 'total_containers', 'response_time',
                'cpu_usage', 'memory_usage', 'disk_usage'
            ])
            
            client.close()
            
            logger.info(
                f"DockerEngine health check SUCCESS: {self.name} - "
                f"Status={health_status}, Response={response_time:.0f}ms, "
                f"Containers={running_containers}/{total_containers}"
            )
            
            return {
                'status': health_status,
                'details': details,
                'error': None
            }
            
        except DockerException as e:
            error_msg = f"Docker 连接失败: {str(e)}"
            logger.error(f"DockerEngine health check FAILED: {self.name} - {error_msg}")
            
            self.health_status = 'OFFLINE'
            self.last_health_check = timezone.now()
            self.health_check_error = error_msg
            self.save(update_fields=['health_status', 'last_health_check', 'health_check_error'])
            
            return {
                'status': 'OFFLINE',
                'details': {},
                'error': error_msg
            }
            
        except Exception as e:
            error_msg = f"健康检查异常: {str(e)}"
            logger.error(f"DockerEngine health check ERROR: {self.name} - {error_msg}")
            
            self.health_status = 'UNKNOWN'
            self.last_health_check = timezone.now()
            self.health_check_error = error_msg
            self.save(update_fields=['health_status', 'last_health_check', 'health_check_error'])
            
            return {
                'status': 'UNKNOWN',
                'details': {},
                'error': error_msg
            }
    
    def _check_k8s_health(self, timeout=10):
        """检查 K8s 引擎健康状态（支持资源监控）"""
        from kubernetes import client, config
        from kubernetes.client.rest import ApiException
        import time as time_module
        
        start_time = time_module.time()
        
        try:
            # 加载 kubeconfig
            if self.kubeconfig_file_path:
                config.load_kube_config(config_file=self.kubeconfig_file_path)
            else:
                config.load_incluster_config()
            
            # 配置 SSL 验证（必须在创建 API 客户端之前）
            if not self.verify_ssl:
                # 禁用 SSL 警告
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                # 获取并修改默认配置
                configuration = client.Configuration.get_default_copy()
                configuration.verify_ssl = False
                client.Configuration.set_default(configuration)
                logger.info("已禁用 K8s SSL 证书验证")
            
            # 创建 API 客户端（使用已配置的默认配置）
            core_api = client.CoreV1Api()
            version_api = client.VersionApi()
            
            # 1. 检查连接性 - 获取 API 版本
            version_info = version_api.get_code()
            
            # 2. 获取节点信息
            nodes = core_api.list_node(timeout_seconds=timeout)
            nodes_total = len(nodes.items)
            nodes_ready = sum(1 for node in nodes.items 
                             if any(condition.type == 'Ready' and condition.status == 'True' 
                                   for condition in node.status.conditions))
            
            #  Bug修复：如果所有节点都挂了，直接返回CRITICAL
            if nodes_total > 0 and nodes_ready == 0:
                error_msg = f"所有K8s节点都不可用: {nodes_total}个节点全部NotReady"
                logger.error(f"K8s健康检查失败: {self.name} - {error_msg}")
                
                self.health_status = 'CRITICAL'
                self.health_check_error = error_msg
                self.last_health_check = timezone.now()
                self.save(update_fields=['health_status', 'health_check_error', 'last_health_check'])
                
                return {
                    'status': 'CRITICAL',
                    'details': {
                        'nodes_total': nodes_total,
                        'nodes_ready': 0,
                        'error': error_msg
                    },
                    'error': error_msg
                }
            
            # 3. 获取 Pod 统计
            pods = core_api.list_namespaced_pod(
                namespace=self.namespace or 'default',
                timeout_seconds=timeout
            )
            pods_total = len(pods.items)
            pods_running = sum(1 for pod in pods.items if pod.status.phase == 'Running')
            
            # 初始化节点详细信息（用于前端展示）
            nodes_details = {}
            
            # 4. 尝试获取资源使用率（需要 metrics-server）
            try:
                logger.info(f"🔍 开始获取 K8s metrics 数据: {self.name}")
                
                #  Bug修复：添加超时保护
                metrics_timeout = min(timeout, 5)  # metrics获取最多5秒
                logger.info(f"  metrics 请求超时设置: {metrics_timeout}秒")
                
                # 使用更简单可靠的方式：直接通过 RESTClientObject 发送请求
                import json
                api_client = core_api.api_client
                
                # 构建请求路径
                path = '/apis/metrics.k8s.io/v1beta1/nodes'
                
                # 发送 GET 请求（使用 request 方法更可靠）
                response = api_client.request(
                    method='GET',
                    url=api_client.configuration.host + path,
                    headers={'Accept': 'application/json'},
                    _preload_content=False,
                    _request_timeout=metrics_timeout  #  使用明确的超时
                )
                
                # 读取响应体
                response_data = response.data.decode('utf-8')
                nodes_metrics_data = json.loads(response_data)
                
                logger.info(f" 成功解析 metrics 数据, 类型: {type(nodes_metrics_data)}")
                if isinstance(nodes_metrics_data, dict):
                    logger.info(f" 数据键: {list(nodes_metrics_data.keys())}")
                    logger.info(f"items 数量: {len(nodes_metrics_data.get('items', []))}")
                
                # 解析节点资源使用情况
                total_cpu_usage = 0
                total_cpu_capacity = 0
                total_memory_usage = 0
                total_memory_capacity = 0
                
                #  Bug修复：只统计Ready节点的容量
                for node in nodes.items:
                    node_name = node.metadata.name
                    
                    # 检查节点是否Ready
                    is_ready = any(
                        condition.type == 'Ready' and condition.status == 'True'
                        for condition in node.status.conditions
                    )
                    
                    # 获取节点容量
                    cpu_capacity = self._parse_k8s_quantity(node.status.capacity.get('cpu', '0'))
                    memory_capacity = self._parse_k8s_quantity(node.status.capacity.get('memory', '0Ki'))
                    
                    logger.debug(
                        f"  节点 {node_name} 容量: CPU={cpu_capacity} cores, "
                        f"内存={memory_capacity / (1024**3):.2f} GiB, Ready={is_ready}"
                    )
                    
                    #  修复：只有Ready节点才计入总容量
                    if is_ready:
                        total_cpu_capacity += cpu_capacity
                        total_memory_capacity += memory_capacity
                    else:
                        logger.warning(f"⚠️ 节点 {node_name} 未就绪，不计入总容量")
                    
                    # 保存节点容量信息（标记是否Ready）
                    nodes_details[node_name] = {
                        'cpu_capacity': cpu_capacity,
                        'memory_capacity': memory_capacity,
                        'cpu_usage': 0,
                        'memory_usage': 0,
                        'is_ready': is_ready,  #  添加Ready状态
                    }
                
                logger.info(
                    f"📊 总容量: CPU={total_cpu_capacity} cores, 内存={total_memory_capacity / (1024**3):.2f} GiB"
                )
                
                # 从 metrics 获取实际使用量
                if nodes_metrics_data and isinstance(nodes_metrics_data, dict) and 'items' in nodes_metrics_data:
                    logger.info(f"📊 metrics 包含 {len(nodes_metrics_data['items'])} 个节点数据")
                    for node_metric in nodes_metrics_data['items']:
                        node_name = node_metric.get('metadata', {}).get('name', 'unknown')
                        cpu_usage = self._parse_k8s_quantity(node_metric['usage'].get('cpu', '0'))
                        memory_usage = self._parse_k8s_quantity(node_metric['usage'].get('memory', '0Ki'))
                        
                        logger.debug(
                            f"  节点 {node_name} 使用: CPU={cpu_usage} cores, 内存={memory_usage / (1024**3):.2f} GiB"
                        )
                        
                        #  Bug修复：只有Ready节点的使用量才计入总使用量
                        if node_name in nodes_details and nodes_details[node_name].get('is_ready', True):
                            total_cpu_usage += cpu_usage
                            total_memory_usage += memory_usage
                            
                            # 保存节点实际使用量
                            nodes_details[node_name]['cpu_usage'] = cpu_usage
                            nodes_details[node_name]['memory_usage'] = memory_usage
                        else:
                            logger.warning(f"⚠️ 节点 {node_name} 未就绪或未找到，不计入总使用量")
                    
                    logger.info(
                        f"📊 总使用: CPU={total_cpu_usage} cores, 内存={total_memory_usage / (1024**3):.2f} GiB"
                    )
                else:
                    logger.warning(
                        f"⚠️ metrics 响应格式异常 - "
                        f"is_dict={isinstance(nodes_metrics_data, dict)}, "
                        f"has_items={'items' in nodes_metrics_data if isinstance(nodes_metrics_data, dict) else False}, "
                        f"keys={list(nodes_metrics_data.keys()) if isinstance(nodes_metrics_data, dict) else 'N/A'}"
                    )
                
                # 计算使用率
                if total_cpu_capacity > 0:
                    self.cpu_usage = round((total_cpu_usage / total_cpu_capacity) * 100, 2)
                    logger.info(f" CPU 使用率: {self.cpu_usage}% = {total_cpu_usage}/{total_cpu_capacity}")
                else:
                    self.cpu_usage = None
                    logger.warning(
                        f"[K8s] 没有Ready节点或CPU容量为0，无法计算使用率 "
                        f"(总节点={nodes_total}, Ready节点={nodes_ready})"
                    )
                
                if total_memory_capacity > 0:
                    self.memory_usage = round((total_memory_usage / total_memory_capacity) * 100, 2)
                    logger.info(f" 内存使用率: {self.memory_usage}% = {total_memory_usage}/{total_memory_capacity}")
                else:
                    self.memory_usage = None
                    logger.warning(
                        f"[K8s] 没有Ready节点或内存容量为0，无法计算使用率 "
                        f"(总节点={nodes_total}, Ready节点={nodes_ready})"
                    )
                
            except Exception as e:
                import traceback
                logger.warning(
                    f"❌ 获取 K8s metrics 失败（可能未安装 metrics-server）: {str(e)}\n"
                    f"详细错误:\n{traceback.format_exc()}"
                )
                self.cpu_usage = None
                self.memory_usage = None
            
            # 5. 计算响应时间
            response_time = (time_module.time() - start_time) * 1000
            
            # 6. 更新引擎状态
            self.running_containers = pods_running
            self.total_containers = pods_total
            self.response_time = round(response_time, 2)
            
            # 7. 判断健康状态（综合考虑节点、Pod、资源使用率）
            status = 'HEALTHY'
            warnings = []
            
            #  Bug修复：检查节点就绪状态（更严格）
            if nodes_ready == 0 and nodes_total > 0:
                # 所有节点都挂了
                status = 'CRITICAL'
                warnings.append(f'所有节点都未就绪: 0/{nodes_total}')
            elif nodes_ready < nodes_total * 0.5:
                # 超过50%节点挂了
                status = 'CRITICAL'
                warnings.append(f'超过一半节点未就绪: {nodes_ready}/{nodes_total}')
            elif nodes_ready < nodes_total:
                # 部分节点挂了
                status = 'WARNING'
                warnings.append(f'部分节点未就绪: {nodes_ready}/{nodes_total}')
            
            # 检查 Pod 运行率
            if pods_running < pods_total * 0.8:  # 80% 的 Pod 运行
                if status != 'CRITICAL':
                    status = 'WARNING'
                warnings.append(f'部分 Pod 未运行: {pods_running}/{pods_total}')
            
            # 检查 CPU 使用率
            if self.cpu_usage is not None:
                if self.cpu_usage > 90:
                    status = 'CRITICAL'
                    warnings.append(f'CPU 使用率过高: {self.cpu_usage:.1f}%')
                elif self.cpu_usage > 80:
                    if status != 'CRITICAL':
                        status = 'WARNING'
                    warnings.append(f'CPU 使用率较高: {self.cpu_usage:.1f}%')
            
            # 检查内存使用率
            if self.memory_usage is not None:
                if self.memory_usage > 90:
                    status = 'CRITICAL'
                    warnings.append(f'内存使用率过高: {self.memory_usage:.1f}%')
                elif self.memory_usage > 80:
                    if status != 'CRITICAL':
                        status = 'WARNING'
                    warnings.append(f'内存使用率较高: {self.memory_usage:.1f}%')
            
            # 生成状态描述
            if status == 'HEALTHY':
                status_display = '健康'
            elif status == 'WARNING':
                status_display = f'警告（{"; ".join(warnings)}）'
            else:  # CRITICAL
                status_display = f'严重（{"; ".join(warnings)}）'
            
            self.health_status = status
            self.health_check_error = None
            self.last_health_check = timezone.now()
            self.save(update_fields=[
                'health_status', 'running_containers', 'total_containers',
                'response_time', 'health_check_error', 'last_health_check',
                'cpu_usage', 'memory_usage'
            ])
            
            # 缓存 K8s 详细信息（5分钟）
            from django.core.cache import cache
            
            # 获取Redis预占信息（用于显示实时的预测使用量）
            nodes_with_pending = []
            try:
                from django.core.cache import caches
                redis_client = caches['default'].client.get_client()
                
                # 获取namespace（与资源预检保持一致）
                namespace = self.namespace or 'default'
                
                for node in nodes.items:
                    node_name = node.metadata.name
                    node_detail = nodes_details.get(node_name, {})
                    
                    # 从Redis获取pending资源（修复：使用正确的key格式）
                    pending_mem_key = f"k8s:node_pending_memory:{namespace}:{node_name}"
                    pending_cpu_key = f"k8s:node_pending_cpu:{namespace}:{node_name}"
                    
                    pending_memory_mb = float(redis_client.get(pending_mem_key) or 0)
                    pending_cpu_cores = float(redis_client.get(pending_cpu_key) or 0)
                    
                    # 计算预测使用量（当前使用量 + pending预占）
                    current_cpu = node_detail.get('cpu_usage', 0)
                    current_memory = node_detail.get('memory_usage', 0)
                    
                    predicted_cpu = current_cpu + pending_cpu_cores
                    predicted_memory = current_memory + (pending_memory_mb * 1024 * 1024)  # 转换为字节
                    
                    nodes_with_pending.append({
                        'name': node_name,
                        'ready': any(condition.type == 'Ready' and condition.status == 'True' 
                                    for condition in node.status.conditions),
                        # 当前实际使用量（metrics）
                        'cpu_usage': current_cpu,
                        'cpu_capacity': node_detail.get('cpu_capacity', 0),
                        'memory_usage': current_memory,
                        'memory_capacity': node_detail.get('memory_capacity', 0),
                        # 预测使用量（包含pending）
                        'cpu_predicted': predicted_cpu,
                        'memory_predicted': predicted_memory,
                        # pending资源
                        'cpu_pending': pending_cpu_cores,
                        'memory_pending': pending_memory_mb,
                    })
                    
            except Exception as e:
                logger.warning(f"获取Redis预占信息失败: {str(e)}，将使用基础信息")
                # 降级方案：只使用基础信息
                nodes_with_pending = [
                    {
                        'name': node.metadata.name,
                        'ready': any(condition.type == 'Ready' and condition.status == 'True' 
                                    for condition in node.status.conditions),
                        'cpu_usage': nodes_details.get(node.metadata.name, {}).get('cpu_usage', 0),
                        'cpu_capacity': nodes_details.get(node.metadata.name, {}).get('cpu_capacity', 0),
                        'memory_usage': nodes_details.get(node.metadata.name, {}).get('memory_usage', 0),
                        'memory_capacity': nodes_details.get(node.metadata.name, {}).get('memory_capacity', 0),
                        'cpu_predicted': nodes_details.get(node.metadata.name, {}).get('cpu_usage', 0),
                        'memory_predicted': nodes_details.get(node.metadata.name, {}).get('memory_usage', 0),
                        'cpu_pending': 0,
                        'memory_pending': 0,
                    }
                    for node in nodes.items
                ]
            
            k8s_details = {
                'k8s_version': f"{version_info.major}.{version_info.minor}",
                'nodes_total': nodes_total,
                'nodes_ready': nodes_ready,
                'pods_total': pods_total,
                'pods_running': pods_running,
                'namespace': self.namespace or 'default',
                'response_time': round(response_time, 2),
                'status_display': status_display,
                'warnings': warnings,
                'nodes': nodes_with_pending
            }
            cache.set(f'k8s_health_details_{self.id}', k8s_details, 300)  # 5分钟缓存
            
            return {
                'status': status,
                'details': k8s_details,
                'error': None
            }
            
        except ApiException as e:
            error_msg = f"K8s API 错误: {e.reason}"
            logger.error(f"K8s 健康检查失败: {self.name}, {error_msg}")
            
            self.health_status = 'OFFLINE'
            self.health_check_error = error_msg
            self.last_health_check = timezone.now()
            self.save(update_fields=['health_status', 'health_check_error', 'last_health_check'])
            
            # 清除缓存
            from django.core.cache import cache
            cache.delete(f'k8s_health_details_{self.id}')
            
            return {
                'status': 'OFFLINE',
                'details': {},
                'error': error_msg
            }
            
        except Exception as e:
            error_msg = f"连接失败: {str(e)}"
            logger.error(f"K8s 健康检查异常: {self.name}, {error_msg}", exc_info=True)
            
            self.health_status = 'UNKNOWN'
            self.health_check_error = error_msg
            self.last_health_check = timezone.now()
            self.save(update_fields=['health_status', 'health_check_error', 'last_health_check'])
            
            # 清除缓存
            from django.core.cache import cache
            cache.delete(f'k8s_health_details_{self.id}')
            
            return {
                'status': 'UNKNOWN',
                'details': {},
                'error': error_msg
            }
    
    def get_health_summary(self):
        """
        获取健康状态摘要（用于前端展示）
        
        Returns:
            dict: 健康状态摘要
        """
        # 检查是否需要刷新健康数据
        should_refresh = False
        if not self.last_health_check:
            should_refresh = True
        else:
            # 如果超过5分钟没有检查，标记为需要刷新
            time_diff = timezone.now() - self.last_health_check
            if time_diff > timedelta(minutes=5):
                should_refresh = True
        
        summary = {
            'id': self.id,
            'name': self.name,
            'engine_type': self.engine_type,
            'host_type': self.host_type,
            'status': self.health_status,
            'status_display': self.get_health_status_display(),
            'last_check': self.last_health_check.isoformat() if self.last_health_check else None,
            'last_check_display': self._format_time_ago(self.last_health_check) if self.last_health_check else '从未检查',
            'running_containers': self.running_containers,
            'total_containers': self.total_containers,
            'response_time': self.response_time,
            'error': self.health_check_error,
            'should_refresh': should_refresh,
            'is_active': self.is_active,
            # 资源使用率（直接暴露顶层字段，方便前端访问）
            'cpu_usage': self.cpu_usage,
            'memory_usage': self.memory_usage,
            'disk_usage': self.disk_usage,
            'resource_usage': {
                'cpu': self.cpu_usage,
                'memory': self.memory_usage,
                'disk': self.disk_usage,
            }
        }
        
        # K8s 引擎添加额外信息
        if self.engine_type == 'KUBERNETES':
            from django.core.cache import cache
            
            # 从缓存获取 K8s 详细信息
            k8s_details = cache.get(f'k8s_health_details_{self.id}')
            
            if k8s_details:
                summary['k8s_info'] = {
                    'namespace': k8s_details.get('namespace', self.namespace or 'default'),
                    'pods_total': k8s_details.get('pods_total', self.total_containers),
                    'pods_running': k8s_details.get('pods_running', self.running_containers),
                    'version': k8s_details.get('k8s_version', '未知'),
                    'nodes_total': k8s_details.get('nodes_total', 0),
                    'nodes_ready': k8s_details.get('nodes_ready', 0),
                    'nodes': k8s_details.get('nodes', []),
                    'warnings': k8s_details.get('warnings', [])  # 添加警告信息
                }
            else:
                # 缓存未命中，尝试快速获取 K8s 信息
                try:
                    from .container_service_factory import ContainerServiceFactory
                    service = ContainerServiceFactory.create_service(self)
                    
                    # 快速获取节点信息
                    nodes = service.core_api.list_node(_request_timeout=5)
                    nodes_data = [
                        {
                            'name': node.metadata.name,
                            'ready': any(condition.type == 'Ready' and condition.status == 'True'
                                        for condition in node.status.conditions)
                        }
                        for node in nodes.items
                    ]
                    
                    # 快速获取 Pod 信息
                    pods = service.core_api.list_namespaced_pod(
                        namespace=service.namespace,
                        _request_timeout=5
                    )
                    pods_running = sum(1 for pod in pods.items if pod.status.phase == 'Running')
                    
                    # 获取版本
                    try:
                        version_info = service.core_api.api_client.call_api(
                            '/version', 'GET', _return_http_data_only=True, _request_timeout=3
                        )
                        k8s_version = version_info.get('gitVersion', '未知')
                    except:
                        k8s_version = '未知'
                    
                    k8s_details = {
                        'namespace': service.namespace,
                        'pods_total': len(pods.items),
                        'pods_running': pods_running,
                        'k8s_version': k8s_version,
                        'nodes_total': len(nodes_data),
                        'nodes_ready': sum(1 for n in nodes_data if n['ready']),
                        'nodes': nodes_data
                    }
                    
                    # 设置缓存（较短时间）
                    cache.set(f'k8s_health_details_{self.id}', k8s_details, 60)  # 1分钟
                    
                    summary['k8s_info'] = {
                        'namespace': k8s_details.get('namespace', self.namespace or 'default'),
                        'pods_total': k8s_details.get('pods_total', self.total_containers),
                        'pods_running': k8s_details.get('pods_running', self.running_containers),
                        'version': k8s_details.get('k8s_version', '未知'),
                        'nodes_total': k8s_details.get('nodes_total', 0),
                        'nodes_ready': k8s_details.get('nodes_ready', 0),
                        'nodes': k8s_details.get('nodes', [])
                    }
                except Exception as e:
                    logger.warning(f"快速获取 K8s 信息失败: {str(e)}")
                    # 使用基本信息
                    summary['k8s_info'] = {
                        'namespace': self.namespace or 'default',
                        'pods_total': self.total_containers,
                        'pods_running': self.running_containers,
                        'version': '未知',
                        'nodes_total': 0,
                        'nodes_ready': 0,
                        'nodes': []
                    }
        
        return summary
    
    @staticmethod
    def _parse_k8s_quantity(quantity_str):
        """
        解析 K8s 资源单位（CPU 和内存）
        
        Args:
            quantity_str: K8s 资源字符串，例如：
                - CPU: "100m" (100毫核), "2" (2核), "1.5" (1.5核)
                - 内存: "128Mi", "1Gi", "1024Ki", "1000000000" (字节)
        
        Returns:
            float: 标准化的数值
                - CPU: 核心数（例如：100m -> 0.1, 2 -> 2.0）
                - 内存: 字节数
        """
        if not quantity_str or quantity_str == '0':
            return 0.0
        
        quantity_str = str(quantity_str).strip()
        
        # CPU 单位处理
        if quantity_str.endswith('m'):
            # 毫核（millicores）: 1000m = 1 core
            return float(quantity_str[:-1]) / 1000.0
        elif quantity_str.endswith('n'):
            # 纳核（nanocores）: 1000000000n = 1 core
            return float(quantity_str[:-1]) / 1000000000.0
        
        # 内存单位处理
        memory_units = {
            'Ki': 1024,                    # Kibibyte
            'Mi': 1024 ** 2,               # Mebibyte
            'Gi': 1024 ** 3,               # Gibibyte
            'Ti': 1024 ** 4,               # Tebibyte
            'Pi': 1024 ** 5,               # Pebibyte
            'K': 1000,                     # Kilobyte
            'M': 1000 ** 2,                # Megabyte
            'G': 1000 ** 3,                # Gigabyte
            'T': 1000 ** 4,                # Terabyte
            'P': 1000 ** 5,                # Petabyte
        }
        
        for unit, multiplier in memory_units.items():
            if quantity_str.endswith(unit):
                return float(quantity_str[:-len(unit)]) * multiplier
        
        # 纯数字（CPU 核心数或内存字节数）
        try:
            return float(quantity_str)
        except ValueError:
            logger.warning(f"无法解析 K8s 资源单位: {quantity_str}")
            return 0.0
    
    @staticmethod
    def _format_time_ago(dt):
        """格式化时间为 'X 分钟前' 的形式"""
        if not dt:
            return '未知'
        
        now = timezone.now()
        diff = now - dt
        
        seconds = diff.total_seconds()
        if seconds < 60:
            return f'{int(seconds)} 秒前'
        elif seconds < 3600:
            return f'{int(seconds / 60)} 分钟前'
        elif seconds < 86400:
            return f'{int(seconds / 3600)} 小时前'
        else:
            return f'{int(seconds / 86400)} 天前'
    
    @classmethod
    def check_all_health(cls):
        """
        检查所有激活的 Docker 引擎健康状态
        
        Returns:
            dict: 所有引擎的健康状态汇总
        """
        engines = cls.objects.filter(is_active=True)
        results = {
            'total': engines.count(),
            'healthy': 0,
            'warning': 0,
            'critical': 0,
            'offline': 0,
            'unknown': 0,
            'engines': []
        }
        
        for engine in engines:
            health = engine.check_health()
            status = health['status']
            
            # 统计各状态数量
            if status == 'HEALTHY':
                results['healthy'] += 1
            elif status == 'WARNING':
                results['warning'] += 1
            elif status == 'CRITICAL':
                results['critical'] += 1
            elif status == 'OFFLINE':
                results['offline'] += 1
            else:
                results['unknown'] += 1
            
            results['engines'].append({
                'id': engine.id,
                'name': engine.name,
                'status': status,
                'details': health['details'],
                'error': health['error']
            })
        
        logger.info(
            f"All DockerEngines health check completed: "
            f"Total={results['total']}, Healthy={results['healthy']}, "
            f"Warning={results['warning']}, Critical={results['critical']}, "
            f"Offline={results['offline']}"
        )
        
        return results
    
class UserContainer(models.Model):
    """
    用户容器记录（支持软删除和历史审计）
    """
    STATUS_CHOICES = [
        ('RUNNING', '运行中'),
        ('STOPPED', '已停止'),      # 正常过期停止
        ('DELETED', '已删除'),      # 用户手动删除
        ('FAILED', '创建失败'),     # 创建过程中失败
        ('EXPIRED', '已过期'),      # 自动清理
    ]
    
    CONTAINER_TYPE_CHOICES = [
        ('COMPETITION', '比赛容器'),
        ('PRACTICE', '练习容器'),
    ]
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="用户")
    challenge_uuid = models.CharField("题目ID", max_length=255, default=None, blank=True, null=True)
    docker_engine = models.ForeignKey(DockerEngine, on_delete=models.CASCADE, verbose_name="Docker引擎")
    container_id = models.CharField("容器ID", max_length=64)
    challenge_title = models.CharField("题目标题", max_length=255, default=None, blank=True, null=True)
    ip_address = models.GenericIPAddressField("IP地址", null=True, blank=True)
    domain = models.CharField("域名", max_length=255, default=None, blank=True, null=True) 
    port = models.TextField("端口", blank=True, null=True)
    
    # 拓扑场景支持
    topology_config = models.ForeignKey(
        'NetworkTopologyConfig',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="网络拓扑配置",
        related_name='user_containers',
        help_text="如果是拓扑场景，关联到具体的拓扑配置"
    )
    topology_data = models.JSONField(
        "拓扑容器数据",
        null=True,
        blank=True,
        help_text="存储拓扑场景中所有容器的详细信息（Pod名称、端口映射等）"
    )
    
    # 容器分类
    container_type = models.CharField(
        "容器类型", 
        max_length=20, 
        choices=CONTAINER_TYPE_CHOICES, 
        null=True, 
        blank=True,
        db_index=True,
        help_text="区分比赛容器和练习容器"
    )
    competition = models.ForeignKey(
        'competition.Competition',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="所属比赛",
        related_name='user_containers',
        help_text="如果是比赛容器，关联到具体比赛"
    )
    

    
    # 状态和时间管理
    status = models.CharField("容器状态", max_length=20, choices=STATUS_CHOICES, default='RUNNING', db_index=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    expires_at = models.DateTimeField("过期时间", db_index=True)
    deleted_at = models.DateTimeField("删除时间", null=True, blank=True)
    deleted_by = models.CharField("删除方式", max_length=50, null=True, blank=True, 
                                   help_text="USER=用户手动, AUTO=自动清理, ADMIN=管理员")
    
    def get_expiration_time(self):
        """获取容器过期时间（根据创建时间计算）"""
        if not self.created_at:
            return None
        lifecycle_duration = timedelta(hours=2)  # 可以根据实际情况调整生命周期
        return self.created_at + lifecycle_duration

    def is_expired(self):
        """检查容器是否过期"""
        expiration_time = self.get_expiration_time()
        if not expiration_time:
            return False
        return timezone.now() > expiration_time
    
    def is_running(self):
        """检查容器是否运行中"""
        return self.status == 'RUNNING'
    
    def mark_deleted(self, deleted_by='USER'):
        """标记为已删除（软删除）"""
        self.status = 'DELETED'
        self.deleted_at = timezone.now()
        self.deleted_by = deleted_by
        self.save(update_fields=['status', 'deleted_at', 'deleted_by'])
    
    def mark_expired(self):
        """标记为已过期"""
        self.status = 'EXPIRED'
        self.deleted_at = timezone.now()
        self.deleted_by = 'AUTO'
        self.save(update_fields=['status', 'deleted_at', 'deleted_by'])
    
    def get_lifetime_seconds(self):
        """获取容器实际运行时长（秒）"""
        if not self.created_at:
            return 0
        if self.deleted_at:
            return (self.deleted_at - self.created_at).total_seconds()
        return (timezone.now() - self.created_at).total_seconds()
    
    @classmethod
    def get_pre_expire_minutes(cls):
        """
        获取预过期提前时间（分钟）
        应该与定时任务执行频率保持一致
        """
        return 0  # 提前20分钟，与定时任务频率同步
    
    def get_pre_expire_time(self):
        """
        获取预过期时间（提前清理时间）
        
        例如：容器过期时间 02:00:00
        预过期时间 01:40:00（提前20分钟）
        定时任务每20分钟执行，会在 01:40:00-02:00:00 之间清理
        """
        if not self.expires_at:
            return None
        return self.expires_at - timedelta(minutes=self.get_pre_expire_minutes())
    
    def is_pre_expired(self):
        """检查容器是否到达预过期时间（可以被清理）"""
        pre_expire_time = self.get_pre_expire_time()
        if not pre_expire_time:
            return False
        return timezone.now() >= pre_expire_time
    
    def is_truly_expired(self):
        """检查容器是否真正过期"""
        if not self.expires_at:
            return False
        return timezone.now() > self.expires_at

    class Meta:
        verbose_name = "容器日志"
        verbose_name_plural = verbose_name
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['challenge_uuid', 'status']),
            models.Index(fields=['expires_at', 'status']),
            models.Index(fields=['container_type', 'status']),
            models.Index(fields=['competition', 'status']),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.challenge_title} - {self.container_id} ({self.get_status_display()})"


def file_upload_path(instance, filename):

    today = timezone.now().strftime('%Y/%m/%d')
    ext = filename.split('.')[-1]
    new_filename = f"{instance.id}_{timezone.now().strftime('%H%M%S')}.{ext}"
    return os.path.join('uploads', today, new_filename)

def challenge_file_upload_path(instance, filename):
    timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    return f'challenge_files/{instance.__class__.__name__.lower()}/{timestamp}_{filename}'

def check_brute_force_uploads(user):
    """检查用户是否有暴力上传行为，如果有则封禁账号"""
    if user.is_superuser:  # 管理员不受限制
        return False
        
    # 检查最近8分钟内的上传次数
    eight_minutes_ago = timezone.now() - timedelta(minutes=8)
    
    # 统计静态文件上传
    static_uploads = StaticFile.objects.filter(
        author=user, 
        upload_time__gte=eight_minutes_ago
    ).count()
    
    # 统计Docker Compose上传
    compose_uploads = DockerCompose.objects.filter(
        author=user, 
        created_at__gte=eight_minutes_ago,
        compose_type='FILE'
    ).count()
    
    total_uploads = static_uploads + compose_uploads
    
    # 如果8分钟内尝试上传超过2次，封禁账号
    if total_uploads >= 14:
        # 获取用户模型
        User = get_user_model()
        try:
            user_obj = User.objects.get(pk=user.pk)
            
            # 禁用账号
            user_obj.is_active = False
            user_obj.save()
            
            # 记录日志
            logger.warning(
                f"用户 {user.username} (ID: {user.pk}) 因暴力上传行为被系统自动封禁。"
                f"8分钟内尝试上传 {total_uploads} 次文件。",
                extra={
                    'user_id': user.pk,
                    'username': user.username,
                    'upload_count': total_uploads,
                    'static_uploads': static_uploads,
                    'compose_uploads': compose_uploads,
                    'time_window': '8分钟'
                }
            )
            
            return True
        except Exception as e:
            logger.error(f"封禁用户 {user.username} (ID: {user.pk}) 时发生错误: {str(e)}")
            return False
            
    return False


class StaticFile(models.Model):
    """静态文件模型"""
    REVIEW_STATUS_CHOICES = [
        ('PENDING', '待审核'),
        ('APPROVED', '已通过'),
        ('REJECTED', '已拒绝'),
    ]
    
    name = models.CharField("文件名称", max_length=255)
    file = models.FileField(
        upload_to=challenge_file_upload_path,
        validators=[FileExtensionValidator(allowed_extensions=['zip', 'rar', '7z', 'tar', 'gz'])],
        verbose_name="静态文件",
        help_text="支持的压缩包格式：zip, rar, 7z, tar, gz"
    )
    description = models.TextField("文件描述", blank=True, null=True)
    file_size = models.BigIntegerField("文件大小", default=0)  # 以字节为单位
    upload_time = models.DateTimeField("上传时间", auto_now_add=True)
    download_count = models.IntegerField("下载次数", default=0)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name="创建者",
        null=True,
        blank=True,
    )
    # 审核相关字段
    review_status = models.CharField(
        "审核状态",
        max_length=10,
        choices=REVIEW_STATUS_CHOICES,
        default='PENDING'
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        verbose_name="审核人",
        null=True,
        blank=True,
        related_name='reviewed_static_files'
    )
    review_time = models.DateTimeField("审核时间", null=True, blank=True)
    review_comment = models.TextField("审核备注", blank=True, null=True)
    
    class Meta:
        verbose_name = "静态资源"
        verbose_name_plural = "静态资源"
        
    def __str__(self):
        return f"文件名称：{self.name} [{self.author}] ({self.upload_time.strftime('%Y-%m-%d %H:%M')})"
    
    def get_file_url_one(self):

        if self.file and self.review_status == 'APPROVED':
            return self.file.url

        return None


    def get_file_url(self, user):
        """
        生成安全的文件下载URL
        
        Returns:
            str: 带签名令牌的下载URL
        """
        if not self.file or self.review_status != 'APPROVED':
            return None
        
        if not user or not user.is_authenticated:
            return None
        
        # 导入令牌生成器
        from .download_security import DownloadTokenGenerator
        from django.urls import reverse
        
        # 生成令牌
        token_generator = DownloadTokenGenerator()
        token = token_generator.generate_token(self.id, user.id)
        
        # 生成安全下载URL
        download_url = reverse('container:secure_download', kwargs={
            'file_id': self.id,
            'token': token
        })
        
        return download_url



    def save(self, *args, **kwargs):

        if self.author and (self.author.is_superuser or self.author.is_staff):
            self.review_status = 'APPROVED'
            self.reviewer = self.author
            self.review_time = timezone.now()
            self.review_comment = '管理员创建，自动审核通过'
        if self.file:
            # 检查文件上传时间间隔
            if not self.pk:  # 新上传的文件
                # 检查最近上传的文件
                last_upload = StaticFile.objects.filter(
                    author=self.author
                ).order_by('-upload_time').first()
                
                if last_upload:
                    time_diff = timezone.now() - last_upload.upload_time
                    if time_diff.total_seconds() < 30:  # 30秒内不能重复上传
                        return False, '文件上传过于频繁，请等待30秒后再试'

                # 检查是否有暴力上传行为
                if check_brute_force_uploads(self.author):
                    return False, '存在暴力上传行为，账号已被封禁。请联系管理员。'
            
            try:
                self.file_size = self.file.size
            except (FileNotFoundError, OSError):
                if not self.file_size:
                    self.file_size = 0
                    
            super().save(*args, **kwargs)
            return True, None
        return False, '未上传文件'



class DockerImage(models.Model):
    """Docker镜像管理模型 - 集中式镜像配置"""
    CATEGORY_CHOICES = [
        # 基础分类
        ('签到', '签到'),
        ('Web', 'Web'),
        ('Pwn', 'Pwn'),
        ('逆向', '逆向工程'),
        ('密码学', '密码学'),
        ('杂项', '杂项'),
        ('防火墙', '防火墙'),
        ('堡垒机', '堡垒机'),
        ('VPN', 'VPN'),
        ('负载均衡', '负载均衡'),
        ('CDN', 'CDN'),
        ('DNS', 'DNS'),
        ('代理', '代理'),
        ('网关', '网关'),
        ('路由器', '路由器'),
        ('交换机', '交换机'),
        
        # 取证分析
        ('数字取证', '数字取证'),
        ('内存取证', '内存取证'),
        ('磁盘取证', '磁盘取证'),
        ('流量分析', '流量分析'),
        ('日志分析', '日志分析'),
        
        # 安全领域
        ('移动安全', '移动安全'),
        ('Android', 'Android'),
        ('iOS', 'iOS'),
        ('物联网', '物联网'),
        ('区块链', '区块链'),
        ('智能合约', '智能合约'),
        
        # 高级技术
        ('云安全', '云安全'),
        ('容器安全', '容器安全'),
        ('AI安全', 'AI安全'),
        ('机器学习', '机器学习'),
        
        # 特殊技能
        ('开源情报', '开源情报'),
        ('隐写术', '隐写术'),
        ('编程', '编程'),
        ('硬件安全', '硬件安全'),
        ('无线电', '无线电'),
        
        # 实战类
        ('CVE复现', 'CVE复现'),
        ('渗透测试', '渗透测试'),
        ('红队', '红队'),
        ('蓝队', '蓝队'),
        ('AD域渗透', 'AD域渗透'),
        ('内网渗透', '内网渗透'),
        
        # 新兴方向
        ('Web3', 'Web3'),
        ('元宇宙', '元宇宙'),
        ('游戏安全', '游戏安全'),
        ('车联网', '车联网'),
        
        # 其他
        ('其他', '其他'),
    ]
    
    FLAG_INJECT_CHOICES = [
        ('INTERNAL', '标准环境变量(SNOW_FLAG)'),
        ('CUSTOM_ENV', '自定义环境变量'),
        ('SCRIPT', '脚本注入'),
        ('NONE', '无需注入'),
    ]
    
    REVIEW_STATUS_CHOICES = [
        ('PENDING', '待审核'),
        ('APPROVED', '已通过'),
        ('REJECTED', '已拒绝'),
    ]
    ENTRANCE_CHOICES = [
        ('WEB', 'HTTP (网页服务)'),
        ('HTTPS', 'HTTPS (安全网页)'),
        ('SSH', 'SSH (远程终端)'),
        ('RDP', 'RDP (远程桌面)'),
        ('VNC', 'VNC (远程桌面)'),
        ('NC', 'NC (Netcat)'),
        ('FTP', 'FTP (文件传输)'),
        ('MYSQL', 'MySQL (数据库)'),
        ('REDIS', 'Redis (缓存)'),
        ('MONGODB', 'MongoDB (数据库)'),
        ('POSTGRESQL', 'PostgreSQL (数据库)'),
    ]
    # 基本信息
    name = models.CharField("镜像名称", max_length=255, help_text="例如: nginx, ctf/web-challenge")
    tag = models.CharField("镜像标签", max_length=100, default="latest")
    registry = models.CharField(
        "镜像仓库", 
        max_length=255, 
        default="docker.io",
        help_text="镜像仓库地址。支持公共仓库(docker.io)、私有仓库(registry.example.com)或本地仓库(localhost)"
    )
    category = models.CharField(
        "镜像类型", 
        max_length=20, 
        choices=CATEGORY_CHOICES, 
        default='WEB'
    )
    description = models.TextField("镜像描述", blank=True, null=True)
    
    # Flag注入配置
    flag_inject_method = models.CharField(
        "动态Flag适配",
        max_length=20,
        choices=FLAG_INJECT_CHOICES,
        default='INTERNAL',
        help_text="INTERNAL: 镜像已内置动态Flag且使用SNOW_FLAG环境变量读取动态Flag (Docker/K8s通用); "
                  "CUSTOM_ENV: 镜像已内置动态Flag但使用自定义环境变量名读取动态Flag (Docker/K8s通用); "
                  "SCRIPT: 镜像无内置支持动态Flag，通过脚本注入动态Flag (Docker 通过 exec，K8s 通过 postStart 钩子); "
                  "NONE: 镜像不支持动态Flag，请使用静态Flag (Docker/K8s通用)"
    )
    flag_env_name = models.CharField(
        "Flag环境变量名",
        max_length=100,
        blank=True,
        null=True,
        default="",
        help_text="当选择'CUSTOM_ENV'时填写镜像中已内置的动态Flag环境变量名，例如: FLAG, CTF_FLAG, GZCTF_FLAG 等。平台会将 动态Flag 映射给该变量名"
    )
    flag_script = models.TextField(
        "Flag注入脚本",
        blank=True,
        null=True,
        help_text='当选择"脚本注入"时填写。例如: echo "$SNOW_FLAG" > /flag.txt。$SNOW_FLAG会被替换为实际动态Flag值。'
                  'Docker 引擎通过 exec 执行，K8s 引擎通过 postStart 生命周期钩子执行，两者完全兼容'
    )
    entrance = models.CharField(
        "入口类型",
        max_length=20,
        choices=ENTRANCE_CHOICES,
        default='WEB',
        help_text="入口类型，例如: WEB, SSH, RDP, NC"
    )
    
    # 端口配置
    exposed_ports = models.CharField(
        "暴露端口",
        max_length=255,
        default="80",
        help_text="多个端口用逗号分隔，例如: 80,3306"
    )
    
    # 资源限制
    memory_limit = models.IntegerField(
        "内存限制(MB)",
        default=256,
        blank=True,
        null=True,
        help_text='该镜像运行时的内存限制（MB）。默认值256MB。推荐：轻量级=256MB，中型=512MB，重型=1024MB+'
    )
    cpu_limit = models.FloatField(
        "CPU限制(核心数)",
        default=0.5,
        blank=True,
        null=True,
        help_text='该镜像运行时的CPU核心数限制。默认值0.5核。推荐：轻量级=0.5核，中型=1核，重型=2核+'
    )
    
    # 镜像状态（主要用于 Docker 引擎，K8s 引擎不适用）
    is_pulled = models.BooleanField(
        "是否已拉取", 
        default=False,
        help_text="Docker 引擎专用：镜像是否已拉取到本地。K8s 引擎在各节点独立拉取，此字段无意义"
    )
    image_id = models.CharField(
        "镜像ID", 
        max_length=100, 
        blank=True, 
        null=True,
        help_text="Docker 引擎专用：镜像的唯一标识符"
    )
    image_size = models.BigIntegerField(
        "镜像大小(字节)", 
        default=0,
        help_text="Docker 引擎专用：镜像文件大小"
    )
    last_pulled = models.DateTimeField(
        "最后拉取时间", 
        null=True, 
        blank=True,
        help_text="Docker 引擎专用：最后一次拉取镜像的时间"
    )
    
    # 安全审核
    review_status = models.CharField(
        "审核状态",
        max_length=10,
        choices=REVIEW_STATUS_CHOICES,
        default='PENDING'
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        verbose_name="审核人",
        null=True,
        blank=True,
        related_name='reviewed_docker_images'
    )
    review_time = models.DateTimeField("审核时间", null=True, blank=True)
    review_comment = models.TextField("审核备注", blank=True, null=True)
    
    # 其他信息
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name="创建者"
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)
    is_active = models.BooleanField("是否启用", default=True)
    
    class Meta:
        verbose_name = "镜像资源"
        verbose_name_plural = "镜像资源"
        # unique_together = ('registry', 'name', 'tag')  # 允许用户创建相同名称的镜像
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.full_name} [{self.category}]"
    
    @property
    def full_name(self):
        """
        完整的镜像名称
        
        自动处理常见的格式问题：
        - 如果 name 包含冒号，自动拆分
        """
        # 处理 name 中包含冒号的情况（如 php:5.6-apache）
        if ':' in self.name:
            # 拆分 name，只使用冒号前的部分
            base_name = self.name.split(':')[0]
            # 如果 tag 是默认的 latest，使用 name 中冒号后的部分
            if self.tag == 'latest':
                tag_from_name = self.name.split(':', 1)[1]
                if self.registry == "docker.io":
                    return f"{base_name}:{tag_from_name}"
                return f"{self.registry}/{base_name}:{tag_from_name}"
        
        # 正常情况
        if self.registry == "docker.io":
            return f"{self.name}:{self.tag}"
        return f"{self.registry}/{self.name}:{self.tag}"
    
    def get_ports_list(self):
        """
        获取端口列表（支持多种格式）
        
        支持格式：
        - 纯端口号: "80,443"
        - 带协议: "80/tcp,443/tcp"
        - 混合: "615/tcp,615/udp" (会去重，只返回 615)
        
        Returns:
            list: 纯端口号列表（字符串）
        """
        if not self.exposed_ports:
            return []
        
        ports_set = set()  # 使用集合去重
        for port in self.exposed_ports.split(','):
            port = port.strip()
            if not port:
                continue
            
            # 移除协议后缀 (/tcp, /udp)
            if '/' in port:
                port = port.split('/')[0].strip()
            
            # 验证是否为有效端口号
            try:
                port_num = int(port)
                if 1 <= port_num <= 65535:
                    ports_set.add(str(port_num))
            except ValueError:
                logger.warning(f"DockerImage 无效的端口配置: {port}")
                continue
        
        return sorted(list(ports_set), key=int)  # 按端口号排序返回
    
    def save(self, *args, **kwargs):
        # 自动修正镜像名称格式
        if ':' in self.name:
            logger.info(f"检测到镜像名称包含冒号: {self.name}，自动拆分")
            parts = self.name.split(':', 1)
            base_name = parts[0]
            tag_from_name = parts[1] if len(parts) > 1 else 'latest'
            
            # 如果 tag 是默认的 latest，使用 name 中的 tag
            if self.tag == 'latest':
                self.name = base_name
                self.tag = tag_from_name
                logger.info(f"已自动修正为: name={self.name}, tag={self.tag}")
            else:
                # 如果 tag 已经有自定义值，只清理 name
                self.name = base_name
                logger.info(f"已清理 name 为: {self.name}，保留 tag={self.tag}")
        
        # 如果是管理员创建的，自动设置为已审核
        if self.author and (self.author.is_superuser or self.author.is_staff):
            self.review_status = 'APPROVED'
            self.reviewer = self.author
            self.review_time = timezone.now()
            self.review_comment = '管理员创建，自动审核通过'
        
        super().save(*args, **kwargs)


class NetworkTopologyConfig(models.Model):
    """网络拓扑配置 - 可视化编排企业网络架构"""
    
    name = models.CharField("题目名称", max_length=255, unique=True)
    description = models.TextField("描述", blank=True, null=True)
    topology_data = models.JSONField("拓扑数据", blank=True, null=True, default=dict)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="创建者")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)
    is_active = models.BooleanField("是否启用", default=True)

    class Meta:
        verbose_name = "题目编排"
        verbose_name_plural = "题目编排"
        ordering = ['-created_at']

    def __str__(self):
        return self.name
    
    def get_max_resources(self):
        """
        获取拓扑中所有节点的总资源需求（所有节点同时运行）
        
        Returns:
            tuple: (total_memory_mb, total_cpu_cores)
        """
        total_memory = 0
        total_cpu = 0.0
        
        if not self.topology_data:
            # 空拓扑返回默认单节点资源
            return 512, 1.0
        
        # 支持多种数据格式
        elements = self.topology_data.get('elements')
        if elements:
            # 标准格式: { elements: { nodes: [...], edges: [...] } }
            nodes = elements.get('nodes', [])
        else:
            # 旧格式兼容: { nodes: [...], edges: [...] }
            nodes = self.topology_data.get('nodes', [])
        
        if not isinstance(nodes, list) or len(nodes) == 0:
            # 无有效节点，返回默认单节点资源
            return 512, 1.0
        
        for node_element in nodes:
            node_data = node_element.get('data', {})
            image_id = node_data.get('imageId')
            
            if image_id:
                try:
                    docker_image = DockerImage.objects.get(id=image_id)
                    node_memory = docker_image.memory_limit or 512
                    node_cpu = docker_image.cpu_limit or 1.0
                    total_memory += node_memory  # 累加内存
                    total_cpu += node_cpu  # 累加CPU
                except DockerImage.DoesNotExist:
                    # 镜像不存在时使用默认值
                    total_memory += 512
                    total_cpu += 1.0
            else:
                # 节点没有镜像ID时使用默认值
                total_memory += 512
                total_cpu += 1.0
        
        return total_memory, total_cpu
    
    def get_node_count(self):
        """获取拓扑中的节点数量"""
        if not self.topology_data:
            return 0
        
        # 支持多种数据格式
        # 格式1: { elements: { nodes: [...], edges: [...] } }  <- 标准格式
        # 格式2: { nodes: [...], edges: [...] }  <- 旧格式（兼容）
        
        elements = self.topology_data.get('elements')
        if elements:
            # 标准格式
            nodes = elements.get('nodes', [])
        else:
            # 旧格式兼容
            nodes = self.topology_data.get('nodes', [])
        
        return len(nodes) if isinstance(nodes, list) else 0



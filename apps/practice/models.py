from docker.tls import TLSConfig
from container.models import StaticFile, DockerImage,NetworkTopologyConfig
import uuid
import math
import time
import os
from django.db import models,transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.conf import settings
from django.apps import apps
from django.contrib.auth.models import User
from public.utils import sanitize_html,escape_xss
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import FileExtensionValidator,RegexValidator
from django.core.cache import cache
from public.models import CTFUser


# 核心验证 Manager（深度嵌入）






class PC_Challenge(models.Model):
    
   

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name="唯一标识符")
    CATEGORY_CHOICES = [
        # 基础分类
        ('签到', '签到'),
        ('Web', 'Web'),
        ('Pwn', 'Pwn'),
        ('逆向', '逆向工程'),
        ('密码学', '密码学'),
        ('杂项', '杂项'),
        ('综合渗透', '综合渗透'),
        
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
    
    DIFFICULTY_CHOICES = [
        ('Easy', '简单'),
        ('Medium', '中等'),
        ('Hard', '困难'),
    ]
    
    FLAG_TYPE_CHOICES = [
        ('STATIC', '静态Flag'),
        ('DYNAMIC', '动态flag'),
    ]

    DIFFICULTY_COINS = {
        'Easy': 2,
        'Medium': 4,
        'Hard': 6
    }
    
    
    title = models.CharField(max_length=255, verbose_name="题目标题")
    description = models.TextField(verbose_name="题目描述")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='WEB', verbose_name="题目类型")
    difficulty = models.CharField(max_length=10, choices=DIFFICULTY_CHOICES, default='Medium', verbose_name="难度")
    points = models.IntegerField(default=100, verbose_name="分数")
    flag_type = models.CharField(max_length=20, choices=FLAG_TYPE_CHOICES, default='DYNAMIC', verbose_name="Flag类型")
    flag_template = models.TextField(
        verbose_name="Flag值",
        null=True, 
        blank=True,
        help_text="静态Flag：多个答案用英文逗号分隔，如 flag{answer1},flag{answer2}；动态Flag：填写数量即可自动生成多个"
    )
    flag_count = models.IntegerField(
        default=1,
        verbose_name="Flag数量",
        help_text="题目flag的数量（1-10个）。静态Flag会自动检测逗号分隔的数量，动态Flag需手动设置生成数量"
    )
    flag_points = models.JSONField(
        default=list,
        blank=True,
        null=True,
        verbose_name="各Flag分数配置",
        help_text="为每个flag配置分数，如 [10, 20, 30, 40]。必须满足：①数组长度=flag数量 ②总和=题目总分 ③全为正整数。不符合规则时自动重置为平均分配"
    )
    coins = models.IntegerField(default=4, verbose_name="金币")
    solves = models.IntegerField(default=0, verbose_name="解决次数")
    views = models.IntegerField(default=0, verbose_name="浏览量")
    
    # 部署相关字段更新为外键关联
    static_files = models.ForeignKey(
        StaticFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="静态文件",
        help_text="选择要使用的静态文件（仅用于静态文件部署）",
        related_name='practice_tasks_static_files'
    )
    
    static_file_url = models.URLField(
        max_length=500,
        null=True,
        blank=True,
        verbose_name="静态文件URL",
        help_text="填写静态文件的URL地址，如果填写了URL地址，则优先使用URL地址，否则使用静态文件"
    )
    
    docker_image = models.ForeignKey(
        DockerImage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="镜像配置",
        help_text="选择镜像配置",
        related_name='practice_challenges'
    )

    network_topology_config = models.ForeignKey(
        NetworkTopologyConfig,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="多场景题目",
        related_name='practice_challenges',
        help_text="如果是多场景题目，关联到具体的场景配置"
    )
    first_blood_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, 
        null=True,
        blank=True,
        related_name='practice_first_blood_challenges',
        verbose_name="首次解决用户"
    )
    first_blood_time = models.DateTimeField(null=True, blank=True, verbose_name="首次解决时间")
    reward_coins = models.IntegerField(default=0, verbose_name="奖励金币")
    allocated_coins = models.IntegerField(default=0, verbose_name="已分配给作者的金币")
    
    # 题解相关字段（原 hint 字段改为 writeup）
    hint = models.TextField(blank=True, null=True, verbose_name="题目解析/WriteUp", 
                           help_text="题目解析内容，支持 Markdown 格式")
    writeup_is_public = models.BooleanField(default=False, verbose_name="题解是否公开",
                                           help_text="勾选后所有用户可免费查看题解")
    writeup_cost = models.IntegerField(default=1, verbose_name="题解金币成本",
                                      help_text="用户查看题解需要消耗的金币数（公开时无需消耗）")
    writeup_purchased_by = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='purchased_writeups',
        blank=True,
        verbose_name="已购买题解的用户"
    )
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    is_active = models.BooleanField(default=True, verbose_name="是否激活")
    tags = models.ManyToManyField('Tag',blank=True,verbose_name='学习岛标签')
    is_top = models.BooleanField('置顶', default=False)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="作者")
    is_disable = models.BooleanField(default=True, verbose_name="是否全局启用")
    is_member = models.BooleanField(default=False, verbose_name="是为会员题目")

    class Meta:
        verbose_name = "题目配置"
        verbose_name_plural = "题目配置"

    def clean(self):
        """验证题目配置"""
        from django.core.exceptions import ValidationError
        
        # 验证镜像配置互斥：docker_image 和 network_topology_config 只能选其一
        if self.docker_image and self.network_topology_config:
            raise ValidationError("单镜像和多场景题目不能同时设置，请只选择其中一个")

    def __str__(self):
        return self.title

    def update_views(self):
        self.views += 1
        self.save(update_fields=['views'])
    
    def get_absolute_url(self):
        return reverse('practice:challenge_detail', kwargs={'uuid': self.uuid})
    
    def get_flag_points_list(self):
        """
        获取每个flag的分数列表
        如果未配置或配置不合法，则平均分配
        """
        if self.flag_points is None or not self.flag_points or not isinstance(self.flag_points, list):
            # 未配置，平均分配
            return self._calculate_average_points()
        
        if len(self.flag_points) != self.flag_count:
            # 配置数量不匹配，平均分配
            return self._calculate_average_points()
        
        # 验证总分是否匹配
        if sum(self.flag_points) != self.points:
            # 总分不匹配，平均分配
            return self._calculate_average_points()
        
        return self.flag_points
    
    def _calculate_average_points(self):
        """平均分配分数"""
        if self.flag_count <= 1:
            return [self.points]
        
        # 平均分配，处理除不尽的情况
        base_points = self.points // self.flag_count
        remainder = self.points % self.flag_count
        
        points_list = [base_points] * self.flag_count
        # 将余数分配给前几个flag
        for i in range(remainder):
            points_list[i] += 1
        
        return points_list
    
    def get_flag_point(self, flag_index):
        """
        获取指定索引flag的分数
        
        Args:
            flag_index: flag索引（0-based）
        
        Returns:
            int: 该flag对应的分数
        """
        points_list = self.get_flag_points_list()
        if 0 <= flag_index < len(points_list):
            return points_list[flag_index]
        return 0

    def save(self, *args, **kwargs):
        # 调用 clean 方法进行验证（确保即使直接调用 save 也会验证）
        self.full_clean()
        
        # 自动检测flag数量（针对静态flag）
        if self.flag_type == 'STATIC' and self.flag_template:
            # 统计逗号分隔的flag数量
            flags = [f.strip() for f in self.flag_template.split(',') if f.strip()]
            detected_count = len(flags)
            
            # 如果检测到的数量与当前设置不同，自动更新
            if detected_count > 0 and self.flag_count != detected_count:
                self.flag_count = min(detected_count, 10)  # 最多10个
        
        # 处理 None 值（确保不会存储 null）
        if self.flag_points is None:
            self.flag_points = []
        
        # 验证和修正 flag_points 配置
        if self.flag_points and isinstance(self.flag_points, list):
            needs_reset = False
            
            # 检查1：数组长度是否匹配flag数量
            if len(self.flag_points) != self.flag_count:
                needs_reset = True
                import logging
                logger = logging.getLogger('apps.practice')
                logger.warning(
                    f"题目 {self.title} 的 flag_points 长度({len(self.flag_points)}) "
                    f"与 flag_count({self.flag_count}) 不匹配，将重置为自动平均分配"
                )
            
            # 检查2：总和是否等于题目总分
            elif sum(self.flag_points) != self.points:
                needs_reset = True
                import logging
                logger = logging.getLogger('apps.practice')
                logger.warning(
                    f"题目 {self.title} 的 flag_points 总和({sum(self.flag_points)}) "
                    f"与题目总分({self.points}) 不匹配，将重置为自动平均分配"
                )
            
            # 检查3：是否有非法值（负数、0、非整数）
            elif any(not isinstance(p, int) or p <= 0 for p in self.flag_points):
                needs_reset = True
                import logging
                logger = logging.getLogger('apps.practice')
                logger.warning(
                    f"题目 {self.title} 的 flag_points 包含非法值（负数、0或非整数），"
                    f"将重置为自动平均分配"
                )
            
            # 如果验证失败，重置为空（使用自动平均分配）
            if needs_reset:
                self.flag_points = []
        
        # 清除缓存
        difficulties_key = f'ctf_difficulties'
        types_key = f"ctf_challenge_types"
        latest_key = f"latest_challenges"

        cache.delete(types_key)
        cache.delete(difficulties_key)
        cache.delete(latest_key)
        
        # 不在这里做 XSS 过滤，保存原始 Markdown 内容
        # XSS 防护在模板渲染时通过 markdown 过滤器处理
        super().save(*args, **kwargs)
    
    def calculate_reward(self):
        import random
        
        # 计算随机奖励
        reward = random.randint(0, self.reward_coins)
        
        # 更新已分配金币数
        self.save()
        
        return reward

    def get_coins(self, user):
        ctf_user = CTFUser.objects.filter(user=user).first()
        
        return ctf_user.coins

    def get_reward(self):
        return self.reward_coins

    def get_file_download_url(self, user):
        """
        统一获取文件下载URL（支持 static_files 和 static_file_url）
        
        Args:
            user: 当前用户对象
            
        Returns:
            str: 安全的文件下载URL，如果无文件或用户无权限则返回None
        """
        if not user or not user.is_authenticated:
            return None
        
        # 优先使用 static_file_url（外部URL）
        if self.static_file_url:
            from container.download_security import DownloadTokenGenerator
            from django.urls import reverse
            
            # 生成令牌（使用 challenge.id 作为 file_id）
            token_generator = DownloadTokenGenerator()
            token = token_generator.generate_token(self.id, user.id)
            
            # 返回代理下载URL
            return reverse('practice:secure_url_download', kwargs={
                'challenge_uuid': str(self.uuid),
                'token': token
            })
        
        # 否则使用 static_files（系统管理的文件）
        elif self.static_files:
            return self.static_files.get_file_url(user)
        
        return None

    def user_can_manage(self, user):
        """
        检查用户是否有权限管理(删除/激活)此题目
        
        Args:
            user: 当前用户对象
            
        Returns:
            bool: 如果用户是题目作者或超级管理员则返回True
        """
        if not user.is_authenticated:
            return False
        return user.is_superuser or self.author == user

    def toggle_active(self, user):
        """
        切换题目的激活状态
        
        Args:
            user: 当前用户对象
            
        Returns:
            tuple: (bool, str) - (操作是否成功, 消息)
        """
        if not self.user_can_manage(user):
            return False, "您没有权限修改此题目"
        
        self.is_active = not self.is_active
        self.save()
        status = "设为公开状态" if self.is_active else "设为私密状态"
        return True, f"题目已{status}"
    
    def safe_delete(self, user):
        """
        安全删除题目
        
        Args:
            user: 当前用户对象
            
        Returns:
            tuple: (bool, str) - (操作是否成功, 消息)
        """
        if not self.user_can_manage(user):
            return False, "您没有权限删除此题目"
        
        """ if UserContainer.objects.filter(challenge=self).exists():
            return False, "无法删除此题目：已有用户创建了容器" """
        
        try:
            self.delete()
            return True, "题目已成功删除"
        except Exception as e:
            return False, f"删除失败: {str(e)}"
    
    def user_can_view_writeup(self, user):
        """
        检查用户是否可以查看题解
        
        Args:
            user: 当前用户对象
            
        Returns:
            bool: 如果可以查看则返回True
        """
        if not user.is_authenticated:
            return False
        
        # 管理员和题目作者可以免费查看
        if user.is_superuser or self.author == user:
            return True
        
        # 题解公开时所有人可以查看
        if self.writeup_is_public:
            return True
        
        # 检查用户是否已购买
        return self.writeup_purchased_by.filter(id=user.id).exists()
    
    def purchase_writeup(self, user):
        """
        用户购买题解
        
        Args:
            user: 当前用户对象
            
        Returns:
            tuple: (bool, str) - (操作是否成功, 消息)
        """
        if not user.is_authenticated:
            return False, "请先登录"
        
        # 检查是否已经可以查看
        if self.user_can_view_writeup(user):
            return True, "您已经可以查看该题解"
        
        # 检查题解是否存在
        if not self.hint:
            return False, "该题目暂无题解"
        
        # 检查金币是否足够
        from public.models import CTFUser
        try:
            ctf_user = CTFUser.objects.get(user=user)
        except CTFUser.DoesNotExist:
            return False, "用户数据异常"
        
        if ctf_user.coins < self.writeup_cost:
            return False, f"金币不足，需要 {self.writeup_cost} 金币，您当前有 {ctf_user.coins} 金币"
        
        # 扣除金币
        from django.db import transaction
        try:
            with transaction.atomic():
                # 使用 F() 表达式确保并发安全
                from django.db.models import F
                CTFUser.objects.filter(id=ctf_user.id).update(coins=F('coins') - self.writeup_cost)
                
                # 添加到已购买列表
                self.writeup_purchased_by.add(user)
                
                # 刷新用户数据
                ctf_user.refresh_from_db()
                
                return True, f"成功购买题解，消耗 {self.writeup_cost} 金币，剩余 {ctf_user.coins} 金币"
        except Exception as e:
            return False, f"购买失败: {str(e)}"

# CTFUser 已迁移到 public.models，这里保留别名以保持向后兼容
# TODO: 将来所有引用都更新后，可以删除此别名
from public.models import CTFUser


class Tag(models.Model):
    name = models.CharField('学习岛标签', max_length=50,unique=True)
    slug = models.SlugField(unique=True, null=True, blank=True)
    description = models.TextField('描述', default='暂无描述',
                                   help_text='学习岛标签描述，支持Markdown格式')

    class Meta:
        verbose_name = '学习岛'
        verbose_name_plural = verbose_name
        ordering = ['id']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('practice:tag', kwargs={'slug': self.slug})

    def get_Challenge_list(self):
        """"""
        return PC_Challenge.objects.filter(tags=self, is_disable=True)
    
    def save(self, *args, **kwargs):
        """保存时自动生成 slug"""
        if not self.slug:
            # 尝试使用 slugify 生成 slug
            base_slug = slugify(self.name, allow_unicode=True)
            
            # 如果 slugify 后为空（如纯中文），则使用拼音或 ID
            if not base_slug:
                try:
                    from pypinyin import lazy_pinyin
                    base_slug = '-'.join(lazy_pinyin(self.name))
                except ImportError:
                    # 如果没有 pypinyin，使用 tag- 加 ID 的方式
                    # 先保存以获取 ID
                    super().save(*args, **kwargs)
                    self.slug = f'tag-{self.id}'
                    super().save(update_fields=['slug'])
                    return
            
            # 确保 slug 唯一
            slug = base_slug
            counter = 1
            while Tag.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f'{base_slug}-{counter}'
                counter += 1
            
            self.slug = slug
        
        super().save(*args, **kwargs)

class SolveRecord(models.Model):
    user = models.ForeignKey(CTFUser, on_delete=models.CASCADE)
    challenge = models.ForeignKey(PC_Challenge, on_delete=models.CASCADE)
    solved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '解题记录'
        verbose_name_plural = verbose_name
        ordering = ['-solved_at']

    def __str__(self):
        return f"{self.user.user} solved {self.challenge.title} at {self.solved_at}"


class SolvedFlag(models.Model):
    """记录用户解出的每个flag（多段flag支持）"""
    user = models.ForeignKey(CTFUser, on_delete=models.CASCADE, verbose_name="用户")
    challenge = models.ForeignKey(PC_Challenge, on_delete=models.CASCADE, verbose_name="题目")
    flag_index = models.IntegerField(verbose_name="Flag索引", help_text="该flag在题目中的索引（0-based）")
    points_earned = models.IntegerField(default=0, verbose_name="获得分数")
    solved_at = models.DateTimeField(auto_now_add=True, verbose_name="解决时间")
    flag_hash = models.CharField(max_length=64, verbose_name="Flag哈希", help_text="用于验证flag唯一性", blank=True)

    class Meta:
        verbose_name = 'FLAG记录'
        verbose_name_plural = verbose_name
        ordering = ['-solved_at']
        unique_together = [['user', 'challenge', 'flag_index']]  # 防止重复提交同一个flag
        indexes = [
            models.Index(fields=['user', 'challenge']),
            models.Index(fields=['challenge', 'solved_at']),
        ]

    def __str__(self):
        return f"{self.user.user.username} solved {self.challenge.title} flag#{self.flag_index+1} (+{self.points_earned}分)"
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone
from django.conf import settings
from imagekit.models import ProcessedImageField
from imagekit.processors import ResizeToFill
import random
import string
from django.db.models import JSONField 
from django.contrib.auth.models import User 
from public.utils import clear_ranking_cache
from django.core.cache import cache

from django.db.models.signals import post_save
from django.dispatch import receiver
from docker.tls import TLSConfig
from container.models import StaticFile, DockerImage
import uuid
import math
import time
from pypinyin import lazy_pinyin
import re
import os

from django.db.models import F
from django.db.models import Count

from public.utils import sanitize_html,escape_xss
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import FileExtensionValidator,RegexValidator
import logging

logger = logging.getLogger('apps.competition')








class Competition(models.Model):
   
    INDIVIDUAL = 'individual'
    TEAM = 'team'
    COMPETITION_TYPE_CHOICES = [
        (INDIVIDUAL, '个人赛'),
        (TEAM, '团体赛'),
    ]

    PUBLIC = 'public'
    INTERNAL = 'internal'
    COMPETITION_VISIBILITY_CHOICES = [
        (PUBLIC, '公开赛'),
        (INTERNAL, '内部赛'),
    ]

    AUDIT_CHOICES = [
        (True, '需要审核'),
        (False, '不需要审核'),
    ]

    THEME_CHOICES = [
        ('default', '简约风格'),
        ('tech', '科技风格'),
    ]

    DASHBOARD_TEMPLATE_CHOICES = [
        ('dashboardone', '大屏模板1'),
        ('dashboardtwo', '大屏模板2'),
    ]

    title = models.CharField('比赛标题', max_length=255)
    description = models.TextField('比赛描述')
    img_link = ProcessedImageField(
        upload_to='competition/upload/%Y/%m/%d/',
        default='public/default/default.png',
        verbose_name='封面图',
        processors=[ResizeToFill(250, 150)],
        blank=True,
        help_text='上传图片大小建议使用5:3的宽高比，为了清晰度原始图片宽度应该超过250px'
    )
    start_time = models.DateTimeField('比赛开始时间')
    end_time = models.DateTimeField('比赛结束时间')
    is_active = models.BooleanField('激活竞赛', default=True)
    is_register = models.BooleanField('是否允许报名', default=True)
    is_audit = models.BooleanField('比赛报名是否需要审核', default=False, choices=AUDIT_CHOICES)
    invitation_code = models.CharField('邀请码', max_length=255, null=True, blank=True)
    slug = models.SlugField('路由', unique=True, blank=True, null=True)
    re_slug = models.SlugField('报名路由', unique=True, blank=True, null=True)
    challenges = models.ManyToManyField(
        'Challenge',
        blank=True,
        verbose_name='题目',
        help_text='选择与此比赛相关的题目'
    )
    competition_type = models.CharField(
        max_length=10,
        choices=COMPETITION_TYPE_CHOICES,
        default=TEAM,
        verbose_name='比赛类型',
        help_text='选择比赛类型：个人赛或团体赛'
    )
    team_max_members = models.IntegerField(
        '队伍最大人数',
        default=4,
        choices=[(i, f'{i}人') for i in range(2, 5)],
        help_text='团队赛中每个队伍的最大人数（2-4人），仅在团队赛时生效'
    )
    visibility_type = models.CharField(
        '公开类型',
        max_length=10,
        choices=COMPETITION_VISIBILITY_CHOICES,
        default=PUBLIC,
        help_text='选择比赛类型：公开赛或内部赛'
    )
    theme = models.CharField(
        '前端风格',
        max_length=20,
        choices=THEME_CHOICES,
        default='tech',
        help_text='选择比赛前端展示风格'
    )
    dashboard_template = models.CharField(
        '大屏模板',
        max_length=20,
        choices=DASHBOARD_TEMPLATE_CHOICES,
        default='dashboardone',
        help_text='选择数据大屏展示模板'
    )
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    
    # ============ 新增：知识竞赛关联功能 ============
    related_quiz = models.OneToOneField(
        'quiz.Quiz',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='关联知识竞赛',
        related_name='related_competition',
        help_text='一个知识竞赛只能关联一个CTF比赛。关联后将自动生成综合排行榜（CTF+知识竞赛）'
    )
    
    # 综合分数计算配置（仅在关联知识竞赛时使用）
    combined_score_ctf_weight = models.DecimalField(
        'CTF权重',
        max_digits=3,
        decimal_places=2,
        default=0.60,
        help_text='综合分数中CTF所占权重（0-1之间，建议0.6）'
    )
    
    combined_score_top_percent = models.IntegerField(
        '归一化基准百分比',
        default=20,
        help_text='取前百分之几的平均分作为归一化基准（建议10-30之间）'
    )

    class Meta:
        verbose_name = "竞赛配置"
        verbose_name_plural = verbose_name
        ordering = ['-start_time']  # 按开始时间倒序排序

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"

    @property
    def status(self):
        """获取比赛状态"""
        now = timezone.now()
        if now < self.start_time:
            return 'pending'  # 未开始
        elif now > self.end_time:
            return 'ended'    # 已结束
        else:
            return 'running'  # 进行中

    def get_status_display(self):
        """获取状态的显示文本"""
        status_map = {
            'pending': '未开始',
            'running': '进行中',
            'ended': '已结束'
        }
        return status_map.get(self.status, '未知状态')

    def generate_invitation_code(self):
        """生成8位邀请码"""
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    
    def generate_slug_from_title(self):
        """从标题生成 slug（支持中文转拼音）"""
        if not self.title:
            return self.generate_random_slug()
        
        # 将中文转换为拼音
        title_pinyin = ''.join(lazy_pinyin(self.title))
        
        # 清理非字母数字字符，只保留字母和数字
        slug = re.sub(r'[^a-zA-Z0-9]', '', title_pinyin).lower()
        
        # 如果 slug 为空或太短，使用随机生成
        if not slug or len(slug) < 3:
            return self.generate_random_slug()
        
        # 限制长度
        slug = slug[:50]
        
        # 检查唯一性，如果已存在则添加随机后缀
        base_slug = slug
        counter = 1
        while Competition.objects.filter(slug=slug).exclude(pk=self.pk).exists():
            slug = f"{base_slug}{counter}"
            counter += 1
        
        return slug

    def clean(self):
        """表单验证（在后台保存前自动调用）"""
        from django.core.exceptions import ValidationError
        
        # 验证时间合法性
        if self.start_time and self.end_time:
            if self.start_time >= self.end_time:
                raise ValidationError({
                    'end_time': '结束时间必须晚于开始时间'
                })
        
        # 验证 slug 格式
        if self.slug and not self.slug.isalnum():
            raise ValidationError({
                'slug': 'slug 只能包含字母和数字'
            })
    
    def save(self, *args, **kwargs):
        # 调用 clean 方法进行验证（确保即使直接调用 save 也会验证）
        self.full_clean()
        

        # 自动生成 slug（如果为空）
        if not self.slug:
            self.slug = self.generate_slug_from_title()

        # 处理报名 slug
        if not self.re_slug:
            self.re_slug = self.generate_random_slug()
        
        # 内部赛自动生成邀请码
        if self.visibility_type == self.INTERNAL and not self.invitation_code:
            self.invitation_code = self.generate_invitation_code()

        # 先保存对象
        super().save(*args, **kwargs)
        
        # ============ 新增：处理关联知识竞赛 ============
        if self.related_quiz:
            self._sync_quiz_settings()
    
        # 更新Redis缓存
        cache_key = f'competition_time_data_{self.id}'
        
        # 清除旧缓存
        cache.delete(cache_key)
        
        # 创建新的缓存数据
        competition_data = {
            'id': self.id,
            'name': self.title,  # 修正这里，使用 title 而不是 name
            'slug': self.slug,
            'start_time': self.start_time,
            'end_time': self.end_time,
        }
        
        # 计算缓存时间
        now = timezone.now()  # 使用已导入的 timezone
        cache_timeout = None
        if self.end_time > now:
            cache_timeout = int((self.end_time - now).total_seconds()) + 86400
        
        # 设置新缓存
        cache.set(cache_key, competition_data, timeout=cache_timeout)

    def generate_random_slug(self):
        """生成随机slug"""
        return ''.join(random.choices(string.ascii_letters, k=10))

    def get_competition_url(self):
        """生成比赛的页面URL"""
        return reverse('competition_detail', args=[self.slug])

    def get_registration_url(self):
        """生成报名页面URL"""
        return reverse('competition_registration', args=[self.slug])
    
    def get_challenge_types(self):
        """获取与比赛相关的所有题目的类型"""
        return ', '.join([challenge.category for challenge in self.challenges.all()])

    def is_started(self):
        """检查比赛是否已开始"""
        return timezone.now() >= self.start_time

    def is_ended(self):
        """检查比赛是否已结束"""
        return timezone.now() > self.end_time

    def is_running(self):
        """检查比赛是否正在进行中"""
        now = timezone.now()
        return self.start_time <= now <= self.end_time

    def time_until_start(self):
        """获取距离比赛开始还有多长时间"""
        if self.is_started():
            return None
        return self.start_time - timezone.now()

    def time_until_end(self):
        """获取距离比赛结束还有多长时间"""
        if self.is_ended():
            return None
        return self.end_time - timezone.now()
    
    # ============ 新增：知识竞赛关联相关方法 ============
    def _sync_quiz_settings(self):
        """同步知识竞赛设置（被关联时自动设置）"""
        if not self.related_quiz:
            return
        
        quiz = self.related_quiz
        updated = False
        
        # 设置为需要报名模式
        if not quiz.require_registration:
            quiz.require_registration = True
            updated = True

        if quiz.require_approval:
            quiz.require_approval = False
            updated = True
        
        if quiz.show_answer:
            quiz.show_answer = False
            updated = True
        
        if quiz.random_order:
            quiz.random_order = True
            updated = True
        
        # 不设置及格线
        if quiz.enable_pass_score:
            quiz.enable_pass_score = False
            updated = True
        
        if quiz.max_attempts:
            quiz.max_attempts = 1
            updated = True
        
        # 同步时间设置（可选，保持一致性）
        if quiz.start_time != self.start_time:
            quiz.start_time = self.start_time
            updated = True
        
        if quiz.end_time != self.end_time:
            quiz.end_time = self.end_time
            updated = True
        
        if updated:
            # 避免递归调用save，使用update_fields
            quiz.save(update_fields=['require_registration', 'require_approval', 'show_answer', 'random_order', 'enable_pass_score', 'max_attempts', 'start_time', 'end_time'])
    
    def sync_registrations_to_quiz(self):
        """将CTF竞赛的报名数据同步到关联的知识竞赛"""
        if not self.related_quiz:
            return {'success': False, 'message': '未关联知识竞赛'}
        
        from quiz.models import QuizRegistration
        
        # 获取所有通过审核的CTF报名记录
        approved_registrations = self.registrations.filter(audit=True)
        
        sync_count = 0
        skip_count = 0
        
        for reg in approved_registrations:
            # 检查是否已存在知识竞赛报名记录
            quiz_reg, created = QuizRegistration.objects.get_or_create(
                quiz=self.related_quiz,
                user=reg.user,
                defaults={'status': 'approved'}
            )
            
            if created:
                sync_count += 1
            else:
                # 如果已存在但状态不是approved，更新为approved
                if quiz_reg.status != 'approved':
                    quiz_reg.status = 'approved'
                    quiz_reg.save(update_fields=['status'])
                    sync_count += 1
                else:
                    skip_count += 1
        
        return {
            'success': True,
            'message': f'同步完成：新增/更新 {sync_count} 条，跳过 {skip_count} 条',
            'sync_count': sync_count,
            'skip_count': skip_count
        }
    
    def get_combined_leaderboard(self, limit=None, force=False):
        """
        获取CTF+知识竞赛的综合排行榜（优化版 - 支持高并发）
        
        算法：归一化后加权平均
        1. 将CTF分数和知识竞赛分数都归一化为百分比
        2. 按配置的权重计算加权平均分
        3. 使用分布式锁保证并发安全
        
        Args:
            limit: 限制返回数量
            force: 是否强制重新计算（忽略缓存）
        
        Returns:
            dict: 包含排行榜数据的字典
        """
        if not self.related_quiz:
            return {'success': False, 'message': '未关联知识竞赛，无法生成综合排行榜'}
        
        from competition.utils_optimized import CombinedLeaderboardCalculator
        
        calculator = CombinedLeaderboardCalculator(
            competition=self,
            quiz=self.related_quiz
        )
        
        # 使用带分布式锁的安全计算方法
        return calculator.calculate_leaderboard_with_lock(limit=limit, force=force)

class Team(models.Model):
    name = models.CharField('队伍名称', max_length=255)  # 队伍名称
    team_code = models.CharField('队伍认证码', max_length=6, unique=True, null=True, blank=True)
    member_count = models.IntegerField('队伍成员最大数量', default=4)  # 队伍成员数量
    leader = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='led_teams', on_delete=models.CASCADE, verbose_name="队长")  # 队长
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='teams', verbose_name="队员")  # 队员，可以是多个用户
    competition = models.ForeignKey('Competition', related_name='competition_teams', on_delete=models.CASCADE, verbose_name="所属比赛")  # 添加比赛字段
    created_at = models.DateTimeField('创建时间', auto_now_add=True)  # 创建时间
    
    class Meta:
        verbose_name = "队伍配置"
        verbose_name_plural = verbose_name
        unique_together = ('name', 'competition')  # 确保队伍名称在同一比赛中唯一

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # 创建队伍时自动生成6位字母编码
        if not self.team_code:
            while True:
                # 生成6位随机大写字母
                letters = string.ascii_uppercase
                random_code = ''.join(random.choice(letters) for _ in range(6))
                
                # 确保编码唯一
                if not Team.objects.filter(team_code=random_code).exists():
                    self.team_code = random_code
                    break
        
        # 如果是新创建的队伍，自动使用比赛配置的最大人数
        if not self.pk and self.competition:
            # 仅在团队赛时应用比赛配置的人数限制
            if self.competition.competition_type == Competition.TEAM:
                self.member_count = self.competition.team_max_members
        
        super().save(*args, **kwargs)
    
    def get_current_member_count(self):
        """获取当前队伍实际人数（包括队长）"""
        return self.members.count()
    
    def can_add_member(self):
        """检查是否还能添加成员"""
        return self.get_current_member_count() < self.member_count
    
    def get_available_slots(self):
        """获取剩余可加入名额"""
        return max(0, self.member_count - self.get_current_member_count())


class ScoreTeam(models.Model):
    team = models.ForeignKey('Team', related_name='score_team_scores', on_delete=models.CASCADE, verbose_name="所属队伍")
    competition = models.ForeignKey('Competition', related_name='score_team_scores', on_delete=models.CASCADE, verbose_name="所属比赛")
    score = models.IntegerField('队伍得分')
    time = models.DateTimeField('最近得分时间', auto_now_add=True)
    rank = models.IntegerField(default=0, verbose_name="队伍排名")
    solved_challenges = models.ManyToManyField('Challenge', default=None, blank=True, verbose_name="全队已解决的挑战")

    class Meta:
        verbose_name = "队伍计分"
        verbose_name_plural = verbose_name

    def update_score(self, points_to_add):
        """使用原子操作更新分数，防止并发问题"""
        ScoreTeam.objects.filter(pk=self.pk).update(
            score=F('score') + points_to_add,
            time=timezone.now()
        )
        self.refresh_from_db()
        # 排名更新将在事务外批量处理，避免锁定过多行

    def update_rank(self):
        """批量更新排名，减少数据库操作次数"""
        all_teams = ScoreTeam.objects.filter(competition=self.competition).order_by('-score', 'time')
        teams_to_update = []
        for index, team in enumerate(all_teams, 1):
            if team.rank != index:
                team.rank = index
                teams_to_update.append(team)
        
        if teams_to_update:
            ScoreTeam.objects.bulk_update(teams_to_update, ['rank'], batch_size=100)

    def __str__(self):
        return f"{self.team.name} - {self.score} points"


class ScoreUser(models.Model):
    team = models.ForeignKey('Team', related_name='score_uuser_scores', on_delete=models.CASCADE, default=None, null=True, blank=True, verbose_name="所属队伍")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='scores', on_delete=models.CASCADE, verbose_name="用户")
    points = models.IntegerField('得分', default=0)
    competition = models.ForeignKey('Competition', related_name='score_uuser_scores', on_delete=models.CASCADE, default=None, blank=True, verbose_name="所属比赛")
    rank = models.IntegerField(default=0, verbose_name="用户排名")
    solved_challenges = models.ManyToManyField('Challenge', default=None,blank=True, verbose_name="已解决的题目")
    created_at = models.DateTimeField('得分时间', auto_now_add=True)

    class Meta:
        verbose_name = "个人计分"
        verbose_name_plural = verbose_name
        unique_together = ('team', 'user', 'competition')

    def update_score(self, points_to_add):
        """使用原子操作更新分数，防止并发问题"""
        ScoreUser.objects.filter(pk=self.pk).update(
            points=F('points') + points_to_add,
            created_at=timezone.now()
        )
        self.refresh_from_db()
        # 排名更新将在事务外批量处理，避免锁定过多行

    def update_rank(self):
        """批量更新排名，减少数据库操作次数"""
        all_users = ScoreUser.objects.filter(competition=self.competition).order_by('-points', 'created_at')
        users_to_update = []
        for index, user in enumerate(all_users, 1):
            if user.rank != index:
                user.rank = index
                users_to_update.append(user)
        
        if users_to_update:
            ScoreUser.objects.bulk_update(users_to_update, ['rank'], batch_size=100)

    def __str__(self):
        team_name = self.team.name if self.team else '个人参赛'
        return f"{self.user.username} - {team_name} - {self.points} points"



class Registration(models.Model):
    INDIVIDUAL = 'individual'
    TEAM = 'team'
    REGISTRATION_TYPE_CHOICES = [
        (INDIVIDUAL, '个人报名'),
        (TEAM, '团队报名'),
    ]
    AUDIT_CHOICES = [
        (True, '通过'),
        (False, '未通过'),
    ]
    competition = models.ForeignKey('Competition', on_delete=models.CASCADE, related_name='registrations',verbose_name="所属比赛")  # 关联比赛
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,verbose_name="用户ID")  # 报名用户
    team_name = models.ForeignKey('Team', related_name='队伍名称', on_delete=models.CASCADE,null=True, blank=True,verbose_name="所属队伍")  # 团队名称
    registration_type = models.CharField(
        max_length=10,
        choices=REGISTRATION_TYPE_CHOICES,
        default=INDIVIDUAL,
        verbose_name='报名类型',
        help_text='选择报名类型：个人报名或团队报名'
    )
    is_audit = models.BooleanField('是否需要审核', default=False)
    audit = models.BooleanField('审核状态', default=False, choices=AUDIT_CHOICES)
    audit_comment = models.TextField('审核备注', null=True, blank=True)
    created_at = models.DateTimeField('报名时间', auto_now_add=True)
    
    @property
    def student_id(self):
        """从用户模型获取学号/工号"""
        return self.user.student_id if self.user else ''
    
    @property
    def name(self):
        """从用户模型获取真实姓名"""
        return self.user.real_name if self.user else ''
    
    @property
    def role(self):
        """从用户模型获取学院/部门"""
        return self.user.department if self.user else ''
    
    @property
    def phone(self):
        """从用户模型获取联系方式"""
        return self.user.phones if self.user else ''

    class Meta:
        verbose_name = "竞赛报名"
        verbose_name_plural = "竞赛报名"

  

    def __str__(self):
        if self.registration_type == self.INDIVIDUAL:
            return f"{self.user.username} - {self.competition.title} (个人报名)"
        else:
            team_display = self.team_name.name if self.team_name else '未指定队伍'
            return f"{team_display} - {self.competition.title} (团队报名)"
    
    def save(self, *args, **kwargs):
        # 新建记录时，根据比赛设置自动设置审核状态
        if not self.pk:  # 只在创建新记录时执行
            # 设置是否需要审核
            self.is_audit = self.competition.is_audit
            
            # 如果不需要审核，则自动设置为通过
            if not self.is_audit:
                self.audit = True
        
        super().save(*args, **kwargs)


class CheatingLog(models.Model):
    CHEATING_TYPES = [
        ('timing', '暴力提交'),  # 比如不合理的提交频率
        ('exploit', '攻击系统'),         # 比如利用漏洞作弊
        ('bot', '机器人行为'),        # 比如机器人行为
        ('manual', '提交Ta人FLAG'),
        ('ipyichang', 'IP异常'),           # 比如手动作弊行为
        ('file_not_downloaded', '未下载文件提交'),  # 未下载文件就提交正确flag
    ]

    team = models.ForeignKey('Team', related_name='cheating_logs', on_delete=models.CASCADE, null=True, blank=True, verbose_name="所属队伍")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="提交用户")
    competition = models.ForeignKey('Competition', related_name='cheating_logs', on_delete=models.CASCADE, verbose_name="所属比赛")
    cheating_type = models.CharField('作弊类型',max_length=50, choices=CHEATING_TYPES)  # 作弊类型
    description = models.TextField('描述')  # 作弊行为描述
    timestamp = models.DateTimeField('记录时间',default=timezone.now)  # 记录时间
    detected_by = models.CharField('检测者',max_length=100, default="System")  # 检测者，可能是系统或管理员

    class Meta:
        verbose_name = "监控日志"
        verbose_name_plural =  "监控日志"

    def get_cheating_type(self):
        if self.cheating_type == 'manual':
            return '提交Ta人FLAG'
        elif self.cheating_type == 'bot':
            return '机器人行为'
        elif self.cheating_type == 'exploit':
            return '攻击系统'
        elif self.cheating_type == 'timing':
            return '暴力提交'
        elif self.cheating_type == 'file_not_downloaded':
            return '未下载文件提交'
        elif self.cheating_type == 'ipyichang':
            return 'IP地址异常'
        else:
            return '未知类型'

    def __str__(self):
        return f"{self.cheating_type} - {self.timestamp}"

    
class Submission(models.Model):
    STATUS_CHOICES = [
        ('correct', '正确'),
        ('wrong', '错误'),
        ('pending', '待判定'),
    ]

    challenge = models.ForeignKey('Challenge', on_delete=models.CASCADE, related_name='submissions', verbose_name="题目")
    competition = models.ForeignKey('Competition', on_delete=models.CASCADE, null=True, blank=True, related_name='submissions', verbose_name="所属比赛")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="提交用户")
    team = models.ForeignKey('Team', on_delete=models.CASCADE, null=True, blank=True, verbose_name="所属队伍")
    flag = models.CharField('提交的Flag', max_length=255)
    status = models.CharField('状态', max_length=10, choices=STATUS_CHOICES, default='pending')
    ip = models.GenericIPAddressField('提交IP', null=True, blank=True)
    created_at = models.DateTimeField('提交时间', auto_now_add=True)
    points_earned = models.IntegerField('获得分数', default=0)
    
    # 新计分系统的得分明细字段
    base_score = models.IntegerField('基础分数', default=0, help_text='动态基础分')
    blood_bonus = models.IntegerField('血榜奖励', default=0, help_text='一二三血奖励')
    time_bonus = models.IntegerField('时间奖励', default=0, help_text='时间奖励分')
    #quick_bonus = models.IntegerField('快速奖励', default=0, help_text='[已废弃] 快速解题奖励，已并入时间奖励')
    solve_rank = models.IntegerField('解题排名', default=0, help_text='第几个解出的')
    
    class Meta:
        verbose_name = "提交记录"
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['challenge', 'user', 'status']),
            models.Index(fields=['challenge', 'team', 'status']),
            models.Index(fields=['created_at']),
            models.Index(fields=['competition']),
        ]

    def __str__(self):
        team_or_user = self.team.name if self.team else self.user.username
        return f"{team_or_user} - {self.challenge.title} - {self.get_status_display()}"

    def is_first_blood(self):
        """检查是否是一血"""
        query = Submission.objects.filter(
            challenge=self.challenge,
            status='correct',
            created_at__lt=self.created_at
        )
        
        # 如果有比赛信息，则限定在同一比赛内判断一血
        if self.competition:
            query = query.filter(competition=self.competition)
            
        return not query.exists()





    # 其他字段...
    
    def get_first_blood(self):
        """返回一血的用户和时间"""
        submissions = Submission.objects.filter(
            challenge=self.challenge,
            status='correct',
            created_at__lt=self.created_at
        ).order_by('created_at')  # 按提交时间排序

        if self.competition:
            submissions = submissions.filter(competition=self.competition)

        first_blood = submissions.first()
        return first_blood

    def get_second_blood(self):
        """返回二血的用户和时间"""
        submissions = Submission.objects.filter(
            challenge=self.challenge,
            status='correct',
            created_at__lt=self.created_at
        ).order_by('created_at')

        if self.competition:
            submissions = submissions.filter(competition=self.competition)

        second_blood = submissions[1] if submissions.count() >= 2 else None
        return second_blood

    def get_third_blood(self):
        """返回三血的用户和时间"""
        submissions = Submission.objects.filter(
            challenge=self.challenge,
            status='correct',
            created_at__lt=self.created_at
        ).order_by('created_at')

        if self.competition:
            submissions = submissions.filter(competition=self.competition)

        third_blood = submissions[2] if submissions.count() >= 3 else None
        return third_blood


    @property
    def submission_time(self):
        """返回格式化的提交时间"""
        return self.created_at.strftime('%Y-%m-%d %H:%M:%S')

    def get_user_team(self):
        """获取用户所属的队伍名称"""
        return self.team.name if self.team else "个人参赛"

    def is_first_blood(self):
        """检查是否是一血"""
        return not Submission.objects.filter(
            challenge=self.challenge,
            status='correct',
            created_at__lt=self.created_at
        ).exists()






def generate_short_uuid():
    """
    生成8位16进制随机字符串
    """
    characters = string.hexdigits[:16]  # 0-9 和 a-f
    return ''.join(random.choice(characters) for _ in range(10))



        
class Challenge(models.Model):

    uuid = models.CharField(max_length=16, default=generate_short_uuid, editable=False, unique=True, verbose_name="唯一标识符")
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

   
    
    DEPLOYMENT_CHOICES = [
        ('STATIC', '静态文件部署'),
        ('COMPOSE', 'Docker Compose部署'),
    ]
    
    title = models.CharField(max_length=255, verbose_name="题目标题")
    description = models.TextField(verbose_name="题目描述")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='WEB', verbose_name="题目类型")
    difficulty = models.CharField(max_length=10, choices=DIFFICULTY_CHOICES, default='Medium', verbose_name="难度")
    flag_type = models.CharField(max_length=20, choices=FLAG_TYPE_CHOICES, default='DYNAMIC', verbose_name="Flag类型")
    initial_points = models.IntegerField(
        default=500, 
        verbose_name="初始分数",
        help_text="题目初始分数，范围：200-1000分"
    )
    minimum_points = models.IntegerField(
        default=100, 
        verbose_name="最低分数",
        help_text="题目最低分数，不能低于50分"
    )
    points = models.IntegerField(
        default=None, 
        null=True, 
        blank=True,
        verbose_name="当前动态分数",
        help_text="随解题人数动态变化的分数"
    )
    solves = models.IntegerField(default=0, verbose_name="解决次数")
    flag_template = models.CharField(max_length=255, verbose_name="Flag值",null=True, blank=True,help_text="用于生成动态Flag的模板")
    static_files = models.ForeignKey(
        StaticFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="静态文件",
        help_text="选择要使用的静态文件（仅用于静态文件部署）",
        related_name='com_tasks_static_files'
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
        help_text="镜像配置（推荐使用）",
        related_name='com_challenges'
    )

    network_topology_config = models.ForeignKey(
        'container.NetworkTopologyConfig',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="多场景题目",
        help_text="选择一个预定义的网络拓扑配置，用于多场景题目",
        related_name='competition_challenges'
    )


    hint = models.TextField(blank=True, null=True, verbose_name="提示")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    is_active = models.BooleanField(default=True, verbose_name="是否激活")
    tags = models.ManyToManyField('Tag',blank=True,verbose_name='标签')
    is_top = models.BooleanField('置顶', default=False)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="作者")

    class Meta:
        verbose_name = "题目配置"
        verbose_name_plural = "题目配置"
        indexes = [
            # 优化：为常用查询添加复合索引
            models.Index(fields=['uuid'], name='idx_chal_uuid'),
            models.Index(fields=['difficulty', 'points'], name='idx_chal_diff_pts'),
            models.Index(fields=['category', 'is_active'], name='idx_chal_cat_active'),
            models.Index(fields=['solves'], name='idx_chal_solves'),
        ]

    def __str__(self):
        tag_names = ', '.join([tag.name for tag in self.tags.all()]) if self.tags.exists() else '无标签'
        return f"{self.title} ({self.category}) - [{tag_names}]"
    
    def clean(self):
        """验证分数配置和题目配置"""
        from django.core.exceptions import ValidationError
        
        # 验证镜像配置互斥：docker_image 和 network_topology_config 只能选其一
        if self.docker_image and self.network_topology_config:
            raise ValidationError("单镜像和多场景题目不能同时设置，请只选择其中一个")
        
        # 验证初始分数范围：200-1000
        if self.initial_points < 200:
            raise ValidationError({'initial_points': '初始分数不能低于200分'})
        if self.initial_points > 1000:
            raise ValidationError({'initial_points': '初始分数不能超过1000分'})
        
        # 验证最低分数：不能低于50
        if self.minimum_points < 50:
            raise ValidationError({'minimum_points': '最低分数不能低于50分'})
        
        # 验证最低分数必须小于初始分数
        if self.minimum_points >= self.initial_points:
            raise ValidationError({
                'minimum_points': f'最低分数必须小于初始分数（当前初始分数：{self.initial_points}分）'
            })
        
        # 建议最低分数不低于初始分数的20%
        min_suggested = int(self.initial_points * 0.2)
        if self.minimum_points < min_suggested:
            raise ValidationError({
                'minimum_points': f'建议最低分数不低于初始分数的20%（建议最低：{min_suggested}分）'
            })

    def save(self, *args, **kwargs):
        # 调用 clean 方法进行验证
        self.full_clean()
        
        self.description = escape_xss(self.description)
        #self.hint = sanitize_html(self.hint)
        if not self.points:
            self.points = self.initial_points

        super().save(*args, **kwargs)
    
    def get_file_download_url(self, user, competition=None):
        """
        统一获取文件下载URL（支持 static_files 和 static_file_url）
        
        Args:
            user: 当前用户对象
            competition: 比赛对象（用于获取slug）
            
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
            
            # 如果没有提供 competition，尝试通过反向查询获取
            if not competition:
                competition = self.competition_set.first()
            
            if not competition:
                return None
            
            # 返回代理下载URL
            return reverse('competition:secure_url_download', kwargs={
                'slug': competition.slug,
                'challenge_uuid': str(self.uuid),
                'token': token
            })
        
        # 否则使用 static_files（系统管理的文件）
        elif self.static_files:
            return self.static_files.get_file_url(user)
        
        return None
    
    def calculate_dynamic_points(self):
        """
        计算动态分数（使用新的计分系统）
        
        使用公式：score = (initial - minimum) / (1 + k * solves) + minimum
        其中 k 是衰减系数，根据难度不同而变化
        
        Returns:
            当前动态基础分数
        """
        from competition.scoring_system import CTFScoringSystem
        
        return CTFScoringSystem.calculate_dynamic_score(
            initial_points=self.initial_points,
            minimum_points=self.minimum_points,
            current_solves=self.solves,
            difficulty=self.difficulty
        )
    
    def update_points(self, competition):
        """
        更新题目动态分数并同步到已解决用户/队伍的分数
        
        优化说明：
        1. 拆分为多个小事务，减少锁持有时间
        2. 优化N+1查询，先获取team_ids再批量更新
        3. 移除排名更新，改为异步批量更新（防止死锁）
        """
        from django.db import transaction

        # 计算新分数
        new_points = self.calculate_dynamic_points()
        point_difference = new_points - self.points

        if point_difference == 0:
            return

        # 更新题目分数（使用update()绕过模型的full_clean验证）
        Challenge.objects.filter(pk=self.pk).update(points=new_points)
        self.points = new_points  # 同步内存中的值

        # 根据比赛类型更新分数（拆分为独立事务）
        if competition.competition_type == 'team':
            self._update_team_scores(competition, point_difference)
            self._update_team_user_scores(competition, point_difference)
        else:
            self._update_individual_scores(competition, point_difference)

    def _update_team_scores(self, competition, point_difference):
        """更新团队分数（独立事务）"""
        from django.db import transaction
        
        with transaction.atomic():
            ScoreTeam.objects.filter(
                competition=competition,
                solved_challenges=self
            ).update(score=F('score') + point_difference)

    def _update_team_user_scores(self, competition, point_difference):
        """更新团队成员个人分数（独立事务，优化N+1查询）"""
        from django.db import transaction
        
        # 优化：先获取team_ids列表，避免嵌套查询
        team_ids = list(
            ScoreTeam.objects.filter(
                competition=competition,
                solved_challenges=self
            ).values_list('team_id', flat=True)
        )
        
        if team_ids:
            with transaction.atomic():
                ScoreUser.objects.filter(
                    competition=competition,
                    team_id__in=team_ids,
                    solved_challenges=self
                ).update(points=F('points') + point_difference)

    def _update_individual_scores(self, competition, point_difference):
        """更新个人分数（独立事务）"""
        from django.db import transaction
        
        with transaction.atomic():
            ScoreUser.objects.filter(
                competition=competition,
                solved_challenges=self
            ).update(points=F('points') + point_difference)

   

    
    def add_solve(self, competition):
        """增加解题次数并更新分数（原子操作，防止并发问题）"""
        # 使用F()表达式进行原子性更新，避免竞态条件
        Challenge.objects.filter(pk=self.pk).update(solves=F('solves') + 1)
        # 刷新对象以获取最新的solves值
        self.refresh_from_db()
        # 更新动态分数
        self.update_points(competition)
        

    def get_points_for_solve_count(self, solve_count):
        """
        获取指定解题次数时的分数（用于预览）
        
        修复：使用与CTFScoringSystem一致的计算公式
        """
        from competition.scoring_system import CTFScoringSystem
        
        return CTFScoringSystem.calculate_dynamic_score(
            initial_points=self.initial_points,
            minimum_points=self.minimum_points,
            current_solves=solve_count,
            difficulty=self.difficulty
        )
    
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

class Tag(models.Model):
    name = models.CharField('文章标签', max_length=20,unique=True)
    description = models.TextField('描述', max_length=240, default='标签描述',
                                   help_text='用来作为SEO中description,长度参考SEO标准')

    class Meta:
        verbose_name = '标签配置'
        verbose_name_plural = verbose_name
        ordering = ['id']

    def __str__(self):
        return self.name

    def get_Challenge_list(self):
        """"""
        return Challenge.objects.filter(tags=self, is_active=True)


# ============ 新增：综合排行榜模型 ============
class CombinedLeaderboard(models.Model):
    """CTF+知识竞赛综合排行榜模型"""
    
    competition = models.ForeignKey(
        'Competition',
        on_delete=models.CASCADE,
        related_name='combined_leaderboards',
        verbose_name='所属竞赛'
    )
    
    # 个人赛使用user，团队赛使用team
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name='用户',
        help_text='个人赛时使用'
    )
    
    team = models.ForeignKey(
        'Team',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name='队伍',
        help_text='团队赛时使用'
    )
    
    # 分数详情
    ctf_score = models.DecimalField(
        '竞赛分数',
        max_digits=10,
        decimal_places=2,
        default=0
    )
    
    quiz_score = models.DecimalField(
        '知识竞赛分数',
        max_digits=10,
        decimal_places=2,
        default=0
    )
    
    combined_score = models.DecimalField(
        '综合分数',
        max_digits=10,
        decimal_places=2,
        default=0,
        db_index=True,
        help_text='归一化后的综合得分（0-100分）'
    )
    
    rank = models.IntegerField(
        '排名',
        default=0,
        db_index=True
    )
    
    # 额外信息
    ctf_rank = models.IntegerField('CTF排名', default=0)
    quiz_rank = models.IntegerField('知识竞赛排名', default=0)
    is_final = models.BooleanField(
        '是否为最终数据',
        default=False,
        db_index=True,
        help_text='比赛结束后的数据标记为最终，不可变更'
    )
    
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    
    class Meta:
        verbose_name = "综合排行榜"
        verbose_name_plural = verbose_name
        ordering = ['rank', '-combined_score']
        indexes = [
            models.Index(fields=['competition', 'rank']),
            models.Index(fields=['competition', '-combined_score']),
        ]
        # 确保唯一性：个人赛按用户，团队赛按队伍
        unique_together = [
            ['competition', 'user'],
            ['competition', 'team'],
        ]
    
    def __str__(self):
        participant = self.user.username if self.user else (self.team.name if self.team else '未知')
        return f"{self.competition.title} - {participant} - 第{self.rank}名"
    
    @property
    def participant_name(self):
        """获取参与者名称（用户名或队伍名）"""
        if self.user:
            return self.user.username
        elif self.team:
            return self.team.name
        return '未知'
    
    def calculate_combined_score(self, ctf_max_score, quiz_max_score):
        """
        计算综合分数：归一化后平均
        
        算法：
        1. 将CTF分数归一化为百分比：(ctf_score / ctf_max_score) * 100
        2. 将知识竞赛分数归一化为百分比：(quiz_score / quiz_max_score) * 100
        3. 取两个百分比的平均值
        
        Args:
            ctf_max_score: CTF比赛的最高分
            quiz_max_score: 知识竞赛的满分（通常是total_score）
        
        Returns:
            综合分数（0-100分）
        """
        from decimal import Decimal
        
        # 归一化CTF分数
        if ctf_max_score > 0:
            ctf_normalized = (self.ctf_score / Decimal(str(ctf_max_score))) * Decimal('100')
        else:
            ctf_normalized = Decimal('0')
        
        # 归一化知识竞赛分数
        if quiz_max_score > 0:
            quiz_normalized = (self.quiz_score / Decimal(str(quiz_max_score))) * Decimal('100')
        else:
            quiz_normalized = Decimal('0')
        
        # 计算平均分
        self.combined_score = (ctf_normalized + quiz_normalized) / Decimal('2')
        
        return self.combined_score


# ============ 新增：综合排行榜计算状态追踪 ============
class LeaderboardCalculationTask(models.Model):
    """综合排行榜计算任务状态追踪"""
    
    STATUS_CHOICES = [
        ('pending', '待处理'),
        ('running', '计算中'),
        ('completed', '已完成'),
        ('failed', '失败'),
        ('cancelled', '已取消'),
    ]
    
    competition = models.ForeignKey(
        'Competition',
        on_delete=models.CASCADE,
        related_name='leaderboard_tasks',
        verbose_name='所属竞赛'
    )
    
    status = models.CharField(
        '状态',
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        db_index=True
    )
    
    competition_type = models.CharField(
        '竞赛类型',
        max_length=20,
        help_text='individual或team'
    )
    
    # 计算进度
    total_participants = models.IntegerField('参与者总数', default=0)
    processed_count = models.IntegerField('已处理数量', default=0)
    
    # 时间记录
    started_at = models.DateTimeField('开始时间', null=True, blank=True)
    completed_at = models.DateTimeField('完成时间', null=True, blank=True)
    
    # 结果统计
    result_count = models.IntegerField('结果数量', default=0)
    error_message = models.TextField('错误信息', blank=True)
    
    # 数据版本（用于乐观锁）
    data_version = models.CharField(
        '数据版本',
        max_length=64,
        db_index=True,
        help_text='基于CTF和Quiz数据的哈希值'
    )
    
    # 幂等性标识
    idempotency_key = models.CharField(
        '幂等性键',
        max_length=128,
        unique=True,
        db_index=True,
        help_text='确保相同条件下不会重复计算'
    )
    
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        verbose_name = "排行榜计算任务"
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['competition', 'status']),
            models.Index(fields=['competition', '-created_at']),
        ]
    
    def __str__(self):
        return f"{self.competition.title} - {self.get_status_display()} ({self.created_at.strftime('%Y-%m-%d %H:%M')})"
    
    def mark_running(self):
        """标记为运行中"""
        self.status = 'running'
        self.started_at = timezone.now()
        self.save(update_fields=['status', 'started_at', 'updated_at'])
    
    def mark_completed(self, result_count):
        """标记为完成"""
        self.status = 'completed'
        self.completed_at = timezone.now()
        self.result_count = result_count
        self.processed_count = self.total_participants
        self.save(update_fields=['status', 'completed_at', 'result_count', 'processed_count', 'updated_at'])
    
    def mark_failed(self, error_message):
        """标记为失败"""
        self.status = 'failed'
        self.completed_at = timezone.now()
        self.error_message = str(error_message)[:1000]  # 限制长度
        self.save(update_fields=['status', 'completed_at', 'error_message', 'updated_at'])
    
    def update_progress(self, processed_count):
        """更新进度"""
        self.processed_count = processed_count
        self.save(update_fields=['processed_count', 'updated_at'])
    
    @property
    def progress_percentage(self):
        """计算进度百分比"""
        if self.total_participants == 0:
            return 0
        return int((self.processed_count / self.total_participants) * 100)
    
    @property
    def duration_seconds(self):
        """计算耗时（秒）"""
        if not self.started_at:
            return 0
        end_time = self.completed_at or timezone.now()
        return (end_time - self.started_at).total_seconds()


# ============ 新增：信号处理 - 自动同步报名数据 ============
@receiver(post_save, sender=Registration)
def sync_registration_to_quiz(sender, instance, created, **kwargs):
    """
    当CTF竞赛报名被审核通过时，自动同步到关联的知识竞赛
    """
    # 只有当审核通过且关联了知识竞赛时才同步
    if instance.audit and instance.competition.related_quiz:
        from quiz.models import QuizRegistration
        
        # 创建或更新知识竞赛报名记录
        QuizRegistration.objects.update_or_create(
            quiz=instance.competition.related_quiz,
            user=instance.user,
            defaults={'status': 'approved'}
        )


# ============================================
# 信号处理：题目添加到比赛时的逻辑
# ============================================
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.core.exceptions import ValidationError

@receiver(m2m_changed, sender=Competition.challenges.through)
def handle_competition_challenges_change(sender, instance, action, pk_set, **kwargs):
    """
    当比赛的题目关系发生变化时触发
    
    功能：
    1. 防止同一题目被添加到多个比赛
    2. 题目添加到新比赛时，重置 solves 和 points
    """
    if action == "pre_add":
        # 添加题目前检查
        for challenge_pk in pk_set:
            try:
                challenge = Challenge.objects.get(pk=challenge_pk)
                
                # 检查题目是否已被其他比赛使用
                existing_competitions = Competition.objects.filter(
                    challenges=challenge
                ).exclude(pk=instance.pk)
                
                if existing_competitions.exists():
                    comp_titles = ', '.join([c.title for c in existing_competitions])
                    raise ValidationError(
                        f'题目「{challenge.title}」已被添加到其他比赛：{comp_titles}。'
                        f'同一题目不能同时添加到多个比赛中。'
                    )
            except Challenge.DoesNotExist:
                pass
    
    elif action == "post_add":
        # 题目添加后，重置统计数据
        for challenge_pk in pk_set:
            try:
                challenge = Challenge.objects.get(pk=challenge_pk)
                
                # 重置解题次数和分数
                challenge.solves = 0
                challenge.points = challenge.initial_points  # 恢复初始分数
                challenge.save(update_fields=['solves', 'points'])
                
                logger.info(f"题目「{challenge.title}」已添加到比赛「{instance.title}」，统计数据已重置")
            except Challenge.DoesNotExist:
                pass


def writeup_upload_path(instance, filename):
    """
    动态生成 Writeup 文件上传路径和文件名
    格式：writeup/submissions/YYYY/MM/比赛名称-队伍名称.pdf 或 比赛名称-用户名.pdf
    """
    import os
    import re
    from datetime import datetime
    
    # 获取文件扩展名
    ext = os.path.splitext(filename)[1]
    
    # 获取当前年月
    now = datetime.now()
    year = now.strftime('%Y')
    month = now.strftime('%m')
    
    # 生成基础路径
    base_path = f'writeup/submissions/{year}/{month}/'
    
    # 生成文件名
    competition_name = instance.competition.title
    
    if instance.team:
        # 团队赛：比赛名称-队伍名称
        participant_name = instance.team.name
    else:
        # 个人赛：比赛名称-用户名
        participant_name = instance.user.username
    
    # 组合文件名，移除文件名中的非法字符（保留中文）
    filename = f"{competition_name}-{participant_name}{ext}"
    # 移除文件系统不允许的字符
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    
    # 拼接完整路径
    return base_path + filename


class WriteupTemplate(models.Model):
    """Writeup 模板管理（管理员维护 Word 文档模板）"""
    
    uuid = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        verbose_name='唯一标识符'
    )
    
    competition = models.ForeignKey(
        'Competition',
        on_delete=models.CASCADE,
        related_name='writeup_templates',
        null=True,
        blank=True,
        verbose_name='所属比赛',
        help_text='留空表示通用模板'
    )
    
    title = models.CharField(
        '模板标题',
        max_length=255
    )
    
    template_file = models.FileField(
        upload_to='writeup/templates/%Y/%m/',
        verbose_name='模板文件',
        validators=[FileExtensionValidator(allowed_extensions=['doc', 'docx'])],
        help_text='上传 Word 文档格式模板'
    )
    
    is_active = models.BooleanField(
        '是否启用',
        default=True
    )
    
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    
    class Meta:
        verbose_name = "Writeup模板"
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        if self.competition:
            return f"{self.title} - {self.competition.title}"
        return f"{self.title} (通用模板)"


class Writeup(models.Model):
    """用户提交的 Writeup（PDF格式）"""
    
    competition = models.ForeignKey(
        'Competition',
        on_delete=models.CASCADE,
        related_name='writeups',
        verbose_name='所属比赛'
    )
    
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='writeups',
        verbose_name='提交者'
    )
    
    team = models.ForeignKey(
        'Team',
        on_delete=models.CASCADE,
        related_name='writeups',
        null=True,
        blank=True,
        verbose_name='所属队伍'
    )
    
    title = models.CharField(
        'Writeup标题',
        max_length=255
    )
    
    description = models.TextField(
        'Writeup简介',
        blank=True
    )
    
    pdf_file = models.FileField(
        upload_to=writeup_upload_path,
        verbose_name='Writeup文件',
        validators=[FileExtensionValidator(allowed_extensions=['pdf'])]
    )
    
    created_at = models.DateTimeField('提交时间', auto_now_add=True)
    
    class Meta:
        verbose_name = "Writeup报告"
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['competition', '-created_at']),
        ]
    
    def __str__(self):
        team_info = f" ({self.team.name})" if self.team else ""
        return f"{self.title} - {self.user.username}{team_info}"
    
    def save(self, *args, **kwargs):
        # XSS防护
        self.title = escape_xss(self.title)
        if self.description:
            self.description = escape_xss(self.description)
        super().save(*args, **kwargs)


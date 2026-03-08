from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.conf import settings
from django.db.models import Count, Avg, Max, Min, Q
from django.utils import timezone
import uuid
import os


def validate_quiz_cover_size(image):
    """验证竞赛封面图片大小"""
    file_size = image.size
    limit_mb = 5
    if file_size > limit_mb * 1024 * 1024:
        raise ValidationError(f"图片大小不能超过 {limit_mb}MB")


def quiz_cover_image_upload_path(instance, filename):
    """竞赛封面图片上传路径（使用时间戳）"""
    # 获取文件扩展名
    ext = filename.split('.')[-1].lower()
    # 生成新文件名：时间戳_原文件名
    timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    new_filename = f"{timestamp}_{filename}"
    # 按年月组织目录：quiz/covers/2024/01/时间戳_文件名.ext
    date_path = timezone.now().strftime('%Y/%m')
    return os.path.join('quiz', 'covers', date_path, new_filename)






class Question(models.Model):
    """题目模型"""
    
    QUESTION_TYPE_CHOICES = [
        ('single', '单项选择题'),
        ('multiple', '多项选择题'),
        ('judge', '判断题'),
        ('fill_blank', '填空题'),
        ('essay', '简答题'),
    ]
    
    DIFFICULTY_CHOICES = [
        ('easy', '简单'),
        ('medium', '中等'),
        ('hard', '困难'),
    ]
    
    question_type = models.CharField(
        max_length=20,
        choices=QUESTION_TYPE_CHOICES,
        verbose_name='题目类型',
        db_index=True
    )
    content = models.TextField(verbose_name='题目内容',help_text='支持Markdown格式')
    standard_answer = models.TextField(
        blank=True,
        null=True,
        verbose_name='标准答案',
        help_text='填空题和简答题的标准答案'
    )
    explanation = models.TextField(
        blank=True,
        null=True,
        verbose_name='答案解析'
    )
    score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=1.00,
        verbose_name='分数'
    )
    difficulty = models.CharField(
        max_length=20,
        choices=DIFFICULTY_CHOICES,
        default='medium',
        verbose_name='难度'
    )
    category = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='题目分类',
        db_index=True
    )
    is_active = models.BooleanField(default=True, verbose_name='是否启用')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')
    
    class Meta:
        db_table = 'quiz_question'
        verbose_name = '题目'
        verbose_name_plural = '题目'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"[{self.get_question_type_display()}] {self.content[:50]}"
    
    def clean(self):
        """验证题目数据（基础验证）"""
        super().clean()
        # 注意：选项的验证在 Admin 的 save_related() 中进行
        # 因为在 clean() 阶段，inline 的选项还没有保存
    
    def validate_options(self):
        """验证选项配置（在选项保存后调用）"""
        if not self.pk:
            return  # 新题目还没有保存，无法验证
        
        # 填空题和简答题不需要选项验证
        if self.question_type in ['fill_blank', 'essay']:
            return []
        
        options_count = self.options.count()
        correct_options = self.options.filter(is_correct=True).count()
        
        errors = []
        
        # 验证选项数量
        if self.question_type in ['single', 'multiple']:
            if options_count != 4:
                errors.append(f'选择题必须有4个选项，当前有{options_count}个')
        elif self.question_type == 'judge':
            if options_count != 2:
                errors.append(f'判断题必须有2个选项，当前有{options_count}个')
        
        # 验证正确答案
        if correct_options == 0:
            errors.append('至少需要设置一个正确答案')
        
        if self.question_type == 'single' and correct_options != 1:
            errors.append('单项选择题只能有1个正确答案')
        
        if self.question_type == 'judge' and correct_options != 1:
            errors.append('判断题只能有1个正确答案')
        
        if self.question_type == 'multiple' and correct_options < 2:
            errors.append('多项选择题至少需要2个正确答案')
        
        return errors
    
    def get_correct_options(self):
        """获取正确答案选项"""
        return self.options.filter(is_correct=True)
    
    def check_answer(self, selected_option_ids=None, text_answer=None):
        """
        检查答案是否正确
        :param selected_option_ids: 用户选择的选项ID列表（选择题/判断题使用）
        :param text_answer: 用户提交的文本答案（填空题/简答题使用）
        :return: (是否正确, 正确答案)
        """
        # 填空题和简答题：需要人工判分，默认标记为False（待批改）
        if self.question_type in ['fill_blank', 'essay']:
            return False, self.standard_answer
        
        # 选择题和判断题：比较选项ID
        correct_option_ids = set(self.get_correct_options().values_list('id', flat=True))
        selected_ids = set(selected_option_ids) if isinstance(selected_option_ids, list) else {selected_option_ids}
        
        is_correct = correct_option_ids == selected_ids
        return is_correct, list(correct_option_ids)


class Option(models.Model):
    """选项模型"""
    
    OPTION_ORDER_CHOICES = [
        ('A', 'A'),
        ('B', 'B'),
        ('C', 'C'),
        ('D', 'D'),
        ('E', 'E'),
        ('F', 'F'),
        ('G', 'G'),
        ('H', 'H'),
        ('I', 'I'),
        ('J', 'J'),
    ]
    
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name='options',
        verbose_name='所属题目'
    )
    order = models.CharField(
        max_length=1,
        choices=OPTION_ORDER_CHOICES,
        verbose_name='选项序号'
    )
    content = models.TextField(verbose_name='选项内容')
    is_correct = models.BooleanField(default=False, verbose_name='是否正确答案')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    
    class Meta:
        db_table = 'quiz_option'
        verbose_name = '选项'
        verbose_name_plural = '选项'
        ordering = ['order']
        unique_together = [['question', 'order']]
    
    def __str__(self):
        # 显示选项序号和内容的前30个字符
        return ""


class Quiz(models.Model):
    """竞赛/试卷模型"""
    
    title = models.CharField(max_length=200, verbose_name='竞赛标题')
    slug = models.SlugField(max_length=200, unique=True, verbose_name='URL标识')
    description = models.TextField(blank=True, null=True, verbose_name='竞赛说明')
    cover_image = models.ImageField(
        upload_to=quiz_cover_image_upload_path,
        blank=True,
        null=True,
        validators=[
            FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'webp', 'svg']),
            validate_quiz_cover_size
        ],
        verbose_name='竞赛封面图片',
        help_text='推荐尺寸：400x225像素（16:9比例），支持格式：JPG、PNG、WEBP、SVG，大小不超过5MB'
    )
    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_quizzes',
        verbose_name='创建者',
        null=True,
        blank=True,
        help_text='留空表示系统管理员创建'
    )
    questions = models.ManyToManyField(
        Question,
        through='QuizQuestion',
        verbose_name='包含题目'
    )
    total_score = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        default=0.00,
        verbose_name='总分'
    )
    pass_score = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        default=0.00,
        blank=True,
        null=True,
        verbose_name='及格分数',
        help_text='留空或0时自动设置为总分的60%'
    )
    duration = models.IntegerField(
        default=60,
        verbose_name='考试时长(分钟)'
    )
    start_time = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='开始时间'
    )
    end_time = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='结束时间'
    )
    is_active = models.BooleanField(default=True, verbose_name='是否启用')
    show_answer = models.BooleanField(default=True, verbose_name='显示答案解析')
    max_attempts = models.IntegerField(
        default=1,
        verbose_name='最大答题次数',
        help_text='0表示不限制次数'
    )
    enable_pass_score = models.BooleanField(default=False, verbose_name='启用及格线')
    show_leaderboard = models.BooleanField(default=True, verbose_name='显示排行榜')
    
    # 防作弊设置
    max_violations = models.IntegerField(
        default=5,
        verbose_name='最大违规次数',
        help_text='达到此次数将强制提交试卷'
    )
    enable_anti_cheat = models.BooleanField(default=True, verbose_name='启用防作弊')
    
    # 题目顺序随机设置
    random_order = models.BooleanField(
        default=False,
        verbose_name='题目顺序随机',
        help_text='开启后，每个用户看到的题目顺序不同（同一用户多次查看顺序保持一致）'
    )
    
    # 报名设置
    require_registration = models.BooleanField(
        default=False,
        verbose_name='需要报名',
        help_text='开启后，用户需要先报名才能参加竞赛'
    )
    require_approval = models.BooleanField(
        default=False,
        verbose_name='报名需要审核',
        help_text='开启后，报名需要管理员审核通过才能参加竞赛'
    )
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')
    
    class Meta:
        db_table = 'quiz_quiz'
        verbose_name = '知识竞赛'
        verbose_name_plural = '知识竞赛'
        ordering = ['-created_at']
    
    def __str__(self):
        return self.title
    
    def save(self, *args, **kwargs):
        """保存时自动生成slug和计算及格分"""
        from decimal import Decimal
        
        # 自动生成slug
        if not self.slug:
            from django.utils.text import slugify
            import uuid
            base_slug = slugify(self.title) or 'quiz'
            self.slug = f"{base_slug}-{uuid.uuid4().hex[:8]}"
        
        # 自动计算及格分（如果为None、0或未设置）
        if (self.pass_score is None or self.pass_score == 0) and self.total_score > 0:
            self.pass_score = self.total_score * Decimal('0.6')
        
        super().save(*args, **kwargs)
    
    def calculate_total_score(self):
        """计算总分"""
        from decimal import Decimal
        
        total = sum(qq.question.score for qq in self.quiz_questions.all())
        self.total_score = total
        
        # 如果及格分为None或0，自动设置为总分的60%
        if self.pass_score is None or self.pass_score == 0:
            self.pass_score = total * Decimal('0.6')
            self.save(update_fields=['total_score', 'pass_score'])
        else:
            self.save(update_fields=['total_score'])
        
        return total
    
    def is_user_registered(self, user):
        """检查用户是否已报名"""
        if not self.require_registration:
            return True  # 不需要报名直接返回True
        
        return QuizRegistration.objects.filter(
            quiz=self,
            user=user,
            status='approved'
        ).exists()
    
    def can_user_attempt(self, user):
        """检查用户是否还能参加答题"""
        # 先检查是否需要报名
        if self.require_registration and not self.is_user_registered(user):
            return False, "需要先报名才能参加竞赛"
        
        if self.max_attempts == 0:
            return True, "可以答题"
        
        completed_attempts = QuizRecord.objects.filter(
            user=user,
            quiz=self,
            status__in=['completed', 'timeout']
        ).count()
        
        if completed_attempts >= self.max_attempts:
            return False, f"已达到最大答题次数"
        
        return True, f"剩余答题次数: {self.max_attempts - completed_attempts}"
    
    def get_leaderboard(self, limit=10):
        """获取排行榜（带缓存优化，支持大数据量）"""
        if not self.show_leaderboard:
            return []
        
        # 尝试从缓存获取
        from django.core.cache import cache
        cache_key = f'quiz_leaderboard_{self.id}_all'  # 缓存全部数据
        cached_data = cache.get(cache_key)
        if cached_data:
            # 从缓存中切片返回
            return cached_data[:limit] if limit else cached_data
        
        # 优化后的查询：一次性获取所有需要的数据
        from django.db.models import Max, Subquery, OuterRef
        
        # 子查询：获取每个用户的最高分记录ID（取最早的）
        best_records = QuizRecord.objects.filter(
            quiz=self,
            user=OuterRef('user'),
            score=OuterRef('score'),
            status='completed'
        ).order_by('submit_time')
        
        # 主查询：获取每个用户的最高分及对应记录（不限制数量）
        records = QuizRecord.objects.filter(
            quiz=self,
            status='completed'
        ).values('user').annotate(
            best_score=Max('score')
        ).order_by('-best_score')
        
        # 获取详细记录（使用 select_related 优化）
        user_ids = [r['user'] for r in records]
        detailed_records = QuizRecord.objects.filter(
            quiz=self,
            user_id__in=user_ids,
            status='completed'
        ).select_related('user').order_by('user', '-score', 'submit_time')
        
        # 构建排行榜数据
        user_best_records = {}
        for record in detailed_records:
            if record.user_id not in user_best_records:
                user_best_records[record.user_id] = record
        
        leaderboard = []
        for record in user_best_records.values():
            if record.submit_time:
                duration_seconds = int((record.submit_time - record.start_time).total_seconds())
                minutes = duration_seconds // 60
                seconds = duration_seconds % 60
                duration_formatted = f"{minutes}:{seconds:02d}"
                
                leaderboard.append({
                    'user__id': record.user_id,
                    'user__uuid': record.user.uuid if hasattr(record.user, 'uuid') else None,
                    'user__username': record.user.username,
                    'best_score': float(record.score),
                    'duration_seconds': duration_seconds,
                    'duration_formatted': duration_formatted
                })
        
        # 按分数降序排序
        leaderboard.sort(key=lambda x: (-x['best_score'], x['duration_seconds']))
        
        # 缓存全部数据（5分钟）
        cache.set(cache_key, leaderboard, 300)
        
        # 返回指定数量的数据（支持切片）
        return leaderboard[:limit] if limit else leaderboard
    
    def clear_leaderboard_cache(self):
        """清除排行榜缓存"""
        from django.core.cache import cache
        cache_key = f'quiz_leaderboard_{self.id}_all'
        cache.delete(cache_key)
    
    def get_statistics(self):
        """获取竞赛统计数据"""
        stats = QuizRecord.objects.filter(
            quiz=self,
            status__in=['completed', 'timeout']
        ).aggregate(
            total_participants=Count('user', distinct=True),  # 参与人数
            total_attempts=Count('id'),  # 总答题次数
            avg_score=Avg('score'),  # 平均分
            max_score=Max('score'),  # 最高分
            min_score=Min('score'),  # 最低分
        )
        
        # 计算及格人次和及格率
        if self.enable_pass_score:
            stats['pass_count'] = QuizRecord.objects.filter(
                quiz=self,
                status__in=['completed', 'timeout'],
                score__gte=self.pass_score
            ).count()
            
            if stats['total_attempts'] and stats['total_attempts'] > 0:
                stats['pass_rate'] = (stats['pass_count'] / stats['total_attempts']) * 100
            else:
                stats['pass_rate'] = None
        else:
            stats['pass_count'] = None
            stats['pass_rate'] = None
        
        # 在线答题人数（status='in_progress'）
        stats['online_count'] = QuizRecord.objects.filter(
            quiz=self,
            status='in_progress'
        ).count()
        
        return stats
    
    def get_score_distribution(self):
        """获取分数分布（用于绘制图表）"""
        from django.db.models import Count, Case, When, IntegerField
        
        # 按分数段统计
        if self.total_score > 0:
            segment_size = float(self.total_score) / 10  # 分成10段
            
            distribution = QuizRecord.objects.filter(
                quiz=self,
                status__in=['completed', 'timeout']
            ).aggregate(
                segment_0_10=Count('id', filter=Q(score__lt=segment_size)),
                segment_10_20=Count('id', filter=Q(score__gte=segment_size, score__lt=segment_size*2)),
                segment_20_30=Count('id', filter=Q(score__gte=segment_size*2, score__lt=segment_size*3)),
                segment_30_40=Count('id', filter=Q(score__gte=segment_size*3, score__lt=segment_size*4)),
                segment_40_50=Count('id', filter=Q(score__gte=segment_size*4, score__lt=segment_size*5)),
                segment_50_60=Count('id', filter=Q(score__gte=segment_size*5, score__lt=segment_size*6)),
                segment_60_70=Count('id', filter=Q(score__gte=segment_size*6, score__lt=segment_size*7)),
                segment_70_80=Count('id', filter=Q(score__gte=segment_size*7, score__lt=segment_size*8)),
                segment_80_90=Count('id', filter=Q(score__gte=segment_size*8, score__lt=segment_size*9)),
                segment_90_100=Count('id', filter=Q(score__gte=segment_size*9)),
            )
            
            return distribution
        
        return {}
    
    def get_user_rank(self, user):
        """获取指定用户的排名"""
        # 获取用户最高分
        user_best_score = QuizRecord.objects.filter(
            quiz=self,
            user=user,
            status='completed'
        ).aggregate(best_score=Max('score'))['best_score']
        
        if user_best_score is None:
            return None
        
        # 计算排名（比该用户分数高的人数+1）
        higher_scores = QuizRecord.objects.filter(
            quiz=self,
            status='completed'
        ).values('user').annotate(
            best_score=Max('score')
        ).filter(best_score__gt=user_best_score).count()
        
        return higher_scores + 1
    
    def get_questions_for_user(self, user=None):
        """
        为用户获取题目列表
        如果启用了随机顺序，返回随机排序的题目
        """
        import random
        
        # 获取所有题目
        questions = list(self.quiz_questions.select_related('question').prefetch_related('question__options').order_by('order'))
        
        # 如果启用随机顺序且有用户
        if self.random_order and user:
            # 使用 quiz_id + user_id 作为种子，确保同一用户每次看到相同的随机顺序
            random.seed(f"{self.id}_{user.id}")
            random.shuffle(questions)
            random.seed()  # 重置种子
        
        return questions
    
    def get_pending_registrations_count(self):
        """获取待审核报名数量"""
        if not self.require_registration:
            return 0
        return self.registrations.filter(status='pending').count()
    
    def get_pending_grading_count(self):
        """获取待批改的主观题数量"""
        from django.db.models import Q
        
        # 统计所有已提交的答题记录中，填空题和简答题未批改的数量
        return Answer.objects.filter(
            record__quiz=self,
            record__status__in=['completed', 'timeout'],
            question__question_type__in=['fill_blank', 'essay'],
            manual_score__isnull=True
        ).count()
    
    def can_user_grade(self, user):
        """检查用户是否有阅卷权限"""
        # 创建者可以阅卷
        if self.creator == user:
            return True
        # 检查是否是阅卷人
        return self.graders.filter(grader=user, is_active=True).exists()
    
    def get_graders(self):
        """获取所有活跃的阅卷人"""
        return self.graders.filter(is_active=True)


class QuizQuestion(models.Model):
    """竞赛题目关联表（用于设置题目顺序）"""
    
    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name='quiz_questions',
        verbose_name='所属竞赛'
    )
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name='quiz_questions',
        verbose_name='题目'
    )
    order = models.IntegerField(default=0, verbose_name='题目顺序')
    
    class Meta:
        db_table = 'quiz_quiz_question'
        verbose_name = '竞赛题目'
        verbose_name_plural = '竞赛题目'
        ordering = ['order']
        unique_together = [['quiz', 'question']]
    
    def __str__(self):
        # 返回空字符串或简短标识，避免在内联编辑时显示冗余信息
        return ""


class QuizRegistration(models.Model):
    """知识竞赛报名模型"""
    
    STATUS_CHOICES = [
        ('pending', '待审核'),
        ('approved', '已通过'),
        ('rejected', '已拒绝'),
    ]
    
    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name='registrations',
        verbose_name='所属竞赛',
        db_index=True
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name='用户ID',
        db_index=True
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='approved',
        verbose_name='审核状态',
        db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='报名时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')
    
    class Meta:
        db_table = 'quiz_registration'
        verbose_name = '竞赛报名'
        verbose_name_plural = '竞赛报名'
        ordering = ['-created_at']
        unique_together = [['quiz', 'user']]
        indexes = [
            models.Index(fields=['quiz', 'status']),
            models.Index(fields=['user', '-created_at']),
        ]
    
    def __str__(self):
        return f"{self.user.username} - {self.quiz.title}"
    
    @property
    def student_id(self):
        """从用户模型获取学号/工号"""
        return self.user.student_id if hasattr(self.user, 'student_id') and self.user.student_id else ''
    
    @property
    def name(self):
        """从用户模型获取真实姓名"""
        return self.user.real_name if hasattr(self.user, 'real_name') and self.user.real_name else self.user.username
    
    @property
    def role(self):
        """从用户模型获取学院/部门"""
        return self.user.department if hasattr(self.user, 'department') and self.user.department else ''
    
    @property
    def phone(self):
        """从用户模型获取联系方式"""
        return self.user.phones if hasattr(self.user, 'phones') and self.user.phones else ''


class QuizRecord(models.Model):
    """答题记录模型"""
    
    STATUS_CHOICES = [
        ('in_progress', '答题中'),
        ('completed', '已完成'),
        ('timeout', '超时'),
        ('cheating', '作弊被强制提交'),
    ]
    
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name='唯一标识', db_index=True)
    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name='records',
        verbose_name='所属竞赛',
        db_index=True
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='quiz_records',
        verbose_name='答题用户',
        db_index=True
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='in_progress',
        verbose_name='状态',
        db_index=True
    )
    score = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        default=0.00,
        verbose_name='得分',
        db_index=True
    )
    start_time = models.DateTimeField(auto_now_add=True, verbose_name='开始时间', db_index=True)
    submit_time = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='提交时间',
        db_index=True
    )
    
    # 防作弊字段
    violation_count = models.IntegerField(default=0, verbose_name='违规次数')
    violation_logs = models.JSONField(default=list, blank=True, verbose_name='违规日志')
    user_agent = models.TextField(blank=True, verbose_name='用户代理')
    device_type = models.CharField(max_length=20, blank=True, verbose_name='设备类型')
    
    class Meta:
        db_table = 'quiz_record'
        verbose_name = '答题记录'
        verbose_name_plural = '答题记录'
        ordering = ['-start_time']
        indexes = [
            models.Index(fields=['quiz', 'user', 'status']),  # 查询用户某竞赛的记录
            models.Index(fields=['quiz', 'status', '-score']),  # 排行榜查询
            models.Index(fields=['user', '-start_time']),  # 用户记录列表
            models.Index(fields=['uuid', 'user']),  # UUID查询优化
            models.Index(fields=['status', 'submit_time']),  # 按状态和时间查询
        ]
        # 添加约束
        constraints = [
            models.CheckConstraint(
                check=models.Q(score__gte=0),
                name='quiz_record_score_non_negative'
            ),
            models.CheckConstraint(
                check=models.Q(violation_count__gte=0),
                name='quiz_record_violation_non_negative'
            ),
        ]
    
    def __str__(self):
        return f"{self.user.username} - {self.quiz.title} - {self.score}分"
    
    def calculate_score(self):
        """计算得分"""
        from decimal import Decimal
        total_score = Decimal('0')
        
        for answer in self.answers.all():
            # 填空题和简答题：使用人工评分
            if answer.question.question_type in ['fill_blank', 'essay']:
                if answer.manual_score is not None:
                    total_score += answer.manual_score
                # 如果未批改，不计分
            # 选择题和判断题：根据是否正确给分
            elif answer.is_correct:
                total_score += answer.question.score
        
        self.score = total_score
        self.save(update_fields=['score'])
        return total_score


class Answer(models.Model):
    """用户答案模型"""
    
    record = models.ForeignKey(
        QuizRecord,
        on_delete=models.CASCADE,
        related_name='answers',
        verbose_name='答题记录'
    )
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        verbose_name='题目'
    )
    selected_options = models.ManyToManyField(
        Option,
        blank=True,
        verbose_name='选择的选项'
    )
    text_answer = models.TextField(
        blank=True,
        null=True,
        verbose_name='文本答案',
        help_text='填空题和简答题的答案'
    )
    is_correct = models.BooleanField(default=False, verbose_name='是否正确')
    manual_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        blank=True,
        null=True,
        verbose_name='人工评分',
        help_text='简答题的人工评分，为空表示未批改'
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='reviewed_answers',
        verbose_name='批改人'
    )
    review_comment = models.TextField(
        blank=True,
        null=True,
        verbose_name='批改评语'
    )
    reviewed_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='批改时间'
    )
    
    # 阅卷锁定机制
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='locked_answers',
        verbose_name='锁定人',
        help_text='正在批改此答案的阅卷人'
    )
    locked_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='锁定时间'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='答题时间')
    
    class Meta:
        db_table = 'quiz_answer'
        verbose_name = '答题日志'
        verbose_name_plural = '答题日志'
        unique_together = [['record', 'question']]
    
    def __str__(self):
        # 显示答题用户和题目信息
        
        return ""
    
    def check_and_save(self):
        """检查答案并保存结果"""
        # 根据题目类型使用不同的检查方式
        if self.question.question_type in ['fill_blank', 'essay']:
            # 填空题和简答题：使用文本答案
            is_correct, _ = self.question.check_answer(text_answer=self.text_answer)
        else:
            # 选择题和判断题：使用选项ID
            selected_ids = list(self.selected_options.values_list('id', flat=True))
            is_correct, _ = self.question.check_answer(selected_option_ids=selected_ids)
        
        self.is_correct = is_correct
        self.save(update_fields=['is_correct'])
    
    def lock_for_grading(self, user):
        """
        锁定答案用于批改（防止多人同时批改）
        :param user: 锁定的阅卷人
        :return: (是否成功, 消息)
        """
        from django.utils import timezone
        from datetime import timedelta
        
        # 已经批改过的不能锁定
        if self.manual_score is not None:
            return False, '此答案已批改'
        
        # 检查是否被其他人锁定
        if self.locked_by and self.locked_by != user:
            # 检查锁定是否过期（超过30分钟自动释放）
            if self.locked_at and timezone.now() - self.locked_at < timedelta(minutes=30):
                return False, f'此答案正在被其他阅卷人批改中'
        
        # 锁定
        self.locked_by = user
        self.locked_at = timezone.now()
        self.save(update_fields=['locked_by', 'locked_at'])
        return True, '锁定成功'
    
    def unlock(self):
        """解锁答案"""
        self.locked_by = None
        self.locked_at = None
        self.save(update_fields=['locked_by', 'locked_at'])
    
    def is_locked_by_other(self, user):
        """检查是否被其他人锁定"""
        from django.utils import timezone
        from datetime import timedelta
        
        if not self.locked_by or self.locked_by == user:
            return False
        
        # 检查锁定是否过期
        if self.locked_at and timezone.now() - self.locked_at >= timedelta(minutes=30):
            return False
        
        return True
    
    def manual_review(self, score, reviewer, comment=''):
        """
        人工批改填空题和简答题
        :param score: 给定的分数
        :param reviewer: 批改人
        :param comment: 批改评语
        """
        from django.utils import timezone
        
        if self.question.question_type not in ['fill_blank', 'essay']:
            raise ValueError('只有填空题和简答题才能进行人工批改')
        
        if score < 0 or score > self.question.score:
            raise ValueError(f'分数必须在0到{self.question.score}之间')
        
        # 检查是否被其他人锁定
        if self.is_locked_by_other(reviewer):
            raise ValueError('此答案正在被其他阅卷人批改')
        
        self.manual_score = score
        self.reviewer = reviewer
        self.review_comment = comment
        self.reviewed_at = timezone.now()
        
        # 如果给了满分，标记为正确
        self.is_correct = (score == self.question.score)
        
        # 批改完成后自动解锁
        self.locked_by = None
        self.locked_at = None
        
        self.save(update_fields=['manual_score', 'reviewer', 'review_comment', 'reviewed_at', 'is_correct', 'locked_by', 'locked_at'])
        
        # 重新计算答题记录的总分
        self.record.calculate_score()


class QuizGrader(models.Model):
    """阅卷人模型 - 用于管理谁可以批改特定竞赛"""
    
    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name='graders',
        verbose_name='所属竞赛'
    )
    grader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name='阅卷人'
    )
    is_active = models.BooleanField(default=True, verbose_name='是否启用')
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='added_graders',
        verbose_name='添加人'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='添加时间')
    
    class Meta:
        db_table = 'quiz_grader'
        verbose_name = '阅卷人'
        verbose_name_plural = '阅卷人'
        unique_together = [['quiz', 'grader']]
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.quiz.title} - {self.grader.username}"

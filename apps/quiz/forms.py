"""
知识竞赛表单
"""
from django import forms
from django.core.exceptions import ValidationError
from quiz.models import Quiz, Question, Option
from django.utils import timezone
from datetime import datetime


class QuizCreateForm(forms.ModelForm):
    """创建竞赛表单"""
    
    class Meta:
        model = Quiz
        fields = ['title', 'description', 'duration', 'max_attempts', 'cover_image']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '请输入竞赛标题',
                'required': True
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': '请输入竞赛说明（选填）',
                'rows': 4
            }),
            'duration': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '60',
                'min': 1,
                'value': 60
            }),
            'max_attempts': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '0 表示不限制',
                'min': 0,
                'value': 0
            }),
            'cover_image': forms.FileInput(attrs={
                'accept': 'image/jpeg,image/jpg,image/png,image/webp,image/svg+xml'
            }),
        }
        labels = {
            'title': '竞赛标题',
            'description': '竞赛说明',
            'duration': '答题时长（分钟）',
            'max_attempts': '最多答题次数',
            'cover_image': '竞赛封面图片',
        }
        help_texts = {
            'title': '建议使用简洁明确的标题',
            'description': '可以介绍竞赛的主题、难度、适用人群等',
            'duration': '用户答题的时间限制',
            'max_attempts': '设置为 0 表示不限制尝试次数',
            'cover_image': '推荐尺寸：400x225像素（16:9比例），支持格式：JPG、PNG、WEBP、SVG，大小不超过5MB',
        }
    
    def clean_title(self):
        title = self.cleaned_data.get('title', '').strip()
        if not title:
            raise ValidationError('竞赛标题不能为空')
        if len(title) < 2:
            raise ValidationError('竞赛标题至少需要 2 个字符')
        return title
    
    def clean_duration(self):
        duration = self.cleaned_data.get('duration')
        if duration < 1:
            raise ValidationError('答题时长至少为 1 分钟')
        if duration > 3600:
            raise ValidationError('答题时长不能超过 3600 分钟')
        return duration


class QuizEditForm(forms.ModelForm):
    """编辑竞赛表单"""
    
    class Meta:
        model = Quiz
        fields = [
            'title', 'description', 'duration', 'max_attempts',
            'start_time', 'end_time', 'pass_score', 'enable_pass_score',
            'show_answer', 'show_leaderboard', 'enable_anti_cheat', 
            'require_registration', 'require_approval', 'is_active', 'random_order'
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'duration': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'max_attempts': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'start_time': forms.DateTimeInput(attrs={
                'class': 'form-control',
                'type': 'datetime-local'
            }, format='%Y-%m-%dT%H:%M'),
            'end_time': forms.DateTimeInput(attrs={
                'class': 'form-control',
                'type': 'datetime-local'
            }, format='%Y-%m-%dT%H:%M'),
            'pass_score': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'enable_pass_score': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'show_answer': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'show_leaderboard': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'enable_anti_cheat': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'random_order': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'require_registration': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'require_approval': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'title': '竞赛标题',
            'description': '竞赛说明',
            'duration': '答题时长（分钟）',
            'max_attempts': '最多答题次数',
            'start_time': '开始时间',
            'end_time': '结束时间',
            'pass_score': '及格分值',
            'enable_pass_score': '启用及格线',
            'show_answer': '显示答案解析',
            'show_leaderboard': '显示排行榜',
            'enable_anti_cheat': '启用防作弊',
            'random_order': '题目顺序随机',
            'require_registration': '启用报名',
            'require_approval': '启用报名审核',
            'is_active': '激活竞赛',
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # 设置 datetime 字段接受的输入格式
        self.fields['start_time'].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M']
        self.fields['end_time'].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M']
        
        # 格式化 datetime 字段的值以适配 datetime-local 输入
        if self.instance and self.instance.pk:
            if self.instance.start_time:
                # 确保datetime有时区信息
                start_time = self.instance.start_time
                if timezone.is_naive(start_time):
                    start_time = timezone.make_aware(start_time)
                # 转换为本地时间并格式化
                local_time = timezone.localtime(start_time)
                self.initial['start_time'] = local_time.strftime('%Y-%m-%dT%H:%M')
            
            if self.instance.end_time:
                # 确保datetime有时区信息
                end_time = self.instance.end_time
                if timezone.is_naive(end_time):
                    end_time = timezone.make_aware(end_time)
                # 转换为本地时间并格式化
                local_time = timezone.localtime(end_time)
                self.initial['end_time'] = local_time.strftime('%Y-%m-%dT%H:%M')
    
    def clean_start_time(self):
        """处理开始时间，将本地时间转换为带时区的datetime"""
        start_time = self.cleaned_data.get('start_time')
        if start_time and timezone.is_naive(start_time):
            # 如果是 naive datetime（没有时区信息），添加当前时区
            start_time = timezone.make_aware(start_time)
        return start_time
    
    def clean_end_time(self):
        """处理结束时间，将本地时间转换为带时区的datetime"""
        end_time = self.cleaned_data.get('end_time')
        if end_time and timezone.is_naive(end_time):
            # 如果是 naive datetime（没有时区信息），添加当前时区
            end_time = timezone.make_aware(end_time)
        return end_time
    
    def clean(self):
        """验证时间范围"""
        cleaned_data = super().clean()
        start_time = cleaned_data.get('start_time')
        end_time = cleaned_data.get('end_time')
        
        # 如果两个时间都存在，验证结束时间必须晚于开始时间
        if start_time and end_time:
            if end_time <= start_time:
                raise ValidationError('结束时间必须晚于开始时间')
        
        return cleaned_data


class QuestionCreateForm(forms.ModelForm):
    """创建题目表单"""
    
    class Meta:
        model = Question
        fields = ['question_type', 'content', 'standard_answer', 'explanation', 'score', 'difficulty', 'category']
        widgets = {
            'question_type': forms.Select(attrs={'class': 'form-control'}),
            'content': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': '请输入题目内容',
                'rows': 3,
                'required': True
            }),
            'standard_answer': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': '请输入标准答案（填空题和简答题必填）',
                'rows': 3
            }),
            'explanation': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': '请输入答案解析（选填）',
                'rows': 2
            }),
            'score': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 1,
                'value': 10
            }),
            'difficulty': forms.Select(attrs={'class': 'form-control'}),
            'category': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '如：Python基础、网络安全、算法等'
            }),
        }
        labels = {
            'question_type': '题目类型',
            'content': '题目内容',
            'standard_answer': '标准答案',
            'explanation': '答案解析',
            'score': '分值',
            'difficulty': '难度',
            'category': '分类',
        }
    
    def clean_content(self):
        content = self.cleaned_data.get('content', '').strip()
        if not content:
            raise ValidationError('题目内容不能为空')
        if len(content) < 5:
            raise ValidationError('题目内容至少需要 5 个字符')
        return content
    
    def clean_score(self):
        score = self.cleaned_data.get('score')
        if score < 1:
            raise ValidationError('分值至少为 1 分')
        if score > 100:
            raise ValidationError('分值不能超过 100 分')
        return score


class OptionForm(forms.ModelForm):
    """选项表单"""
    
    class Meta:
        model = Option
        fields = ['content', 'is_correct']
        widgets = {
            'content': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '请输入选项内容',
                'required': True
            }),
            'is_correct': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'content': '',  # 不显示标签，使用 A/B/C/D 代替
            'is_correct': '正确答案',
        }
    
    def clean_content(self):
        content = self.cleaned_data.get('content', '').strip()
        if not content:
            raise ValidationError('选项内容不能为空')
        return content


# 选项表单集基类
OptionFormSetBase = forms.inlineformset_factory(
    Question,
    Option,
    form=OptionForm,
    extra=2,  # 默认不显示额外表单，在视图中控制
    max_num=10,  # 最多10个选项
    min_num=2,  # 至少2个选项
    validate_min=True,
    can_delete=True,
)

# 为方便使用，创建一个别名
OptionFormSet = OptionFormSetBase


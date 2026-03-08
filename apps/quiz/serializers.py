from rest_framework import serializers
from quiz.models import Question, Option, Quiz, QuizQuestion, QuizRecord, Answer


class OptionSerializer(serializers.ModelSerializer):
    """选项序列化器"""
    
    class Meta:
        model = Option
        fields = ['id', 'order', 'content', 'is_correct']
        
    def to_representation(self, instance):
        """根据上下文决定是否显示正确答案"""
        data = super().to_representation(instance)
        request = self.context.get('request')
        
        # 如果不是在查看答案的情况下，隐藏is_correct字段
        if request and not self.context.get('show_answer', False):
            data.pop('is_correct', None)
        
        return data


class QuestionListSerializer(serializers.ModelSerializer):
    """题目列表序列化器（简化版）"""
    question_type_display = serializers.CharField(source='get_question_type_display', read_only=True)
    difficulty_display = serializers.CharField(source='get_difficulty_display', read_only=True)
    
    class Meta:
        model = Question
        fields = [
            'id',
            'question_type',
            'question_type_display',
            'content',
            'score',
            'difficulty',
            'difficulty_display',
            'category'
        ]


class QuestionDetailSerializer(serializers.ModelSerializer):
    """题目详情序列化器"""
    question_type_display = serializers.CharField(source='get_question_type_display', read_only=True)
    difficulty_display = serializers.CharField(source='get_difficulty_display', read_only=True)
    options = OptionSerializer(many=True, read_only=True)
    
    class Meta:
        model = Question
        fields = [
            'id',
            'question_type',
            'question_type_display',
            'content',
            'explanation',
            'score',
            'difficulty',
            'difficulty_display',
            'category',
            'options',
            'created_at'
        ]


class QuizQuestionSerializer(serializers.ModelSerializer):
    """竞赛题目序列化器"""
    question = QuestionDetailSerializer(read_only=True)
    
    class Meta:
        model = QuizQuestion
        fields = ['order', 'question']


class QuizListSerializer(serializers.ModelSerializer):
    """竞赛列表序列化器"""
    questions_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Quiz
        fields = [
            'id',
            'title',
            'description',
            'total_score',
            'pass_score',
            'duration',
            'start_time',
            'end_time',
            'questions_count',
            'created_at'
        ]
    
    def get_questions_count(self, obj):
        """获取题目数量"""
        return obj.quiz_questions.count()


class QuizDetailSerializer(serializers.ModelSerializer):
    """竞赛详情序列化器"""
    quiz_questions = QuizQuestionSerializer(many=True, read_only=True)
    questions_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Quiz
        fields = [
            'id',
            'title',
            'description',
            'total_score',
            'pass_score',
            'duration',
            'start_time',
            'end_time',
            'questions_count',
            'quiz_questions',
            'created_at'
        ]
    
    def get_questions_count(self, obj):
        """获取题目数量"""
        return obj.quiz_questions.count()


class AnswerSerializer(serializers.ModelSerializer):
    """答案序列化器"""
    question = QuestionDetailSerializer(read_only=True)
    selected_options = OptionSerializer(many=True, read_only=True)
    selected_option_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False
    )
    
    class Meta:
        model = Answer
        fields = [
            'id',
            'question',
            'selected_options',
            'selected_option_ids',
            'is_correct',
            'created_at'
        ]
        read_only_fields = ['is_correct', 'created_at']
    
    def update(self, instance, validated_data):
        """更新答案"""
        selected_option_ids = validated_data.get('selected_option_ids', [])
        
        # 清除旧选项
        instance.selected_options.clear()
        
        # 设置新选项
        if selected_option_ids:
            options = Option.objects.filter(
                id__in=selected_option_ids,
                question=instance.question
            )
            instance.selected_options.set(options)
        
        # 检查答案正确性
        instance.check_and_save()
        
        return instance


class QuizRecordListSerializer(serializers.ModelSerializer):
    """答题记录列表序列化器"""
    quiz = QuizListSerializer(read_only=True)
    user_name = serializers.CharField(source='user.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = QuizRecord
        fields = [
            'id',
            'quiz',
            'user_name',
            'status',
            'status_display',
            'score',
            'start_time',
            'submit_time'
        ]


class QuizRecordDetailSerializer(serializers.ModelSerializer):
    """答题记录详情序列化器"""
    quiz = QuizDetailSerializer(read_only=True)
    user_name = serializers.CharField(source='user.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    answers = AnswerSerializer(many=True, read_only=True)
    statistics = serializers.SerializerMethodField()
    
    class Meta:
        model = QuizRecord
        fields = [
            'id',
            'quiz',
            'user_name',
            'status',
            'status_display',
            'score',
            'start_time',
            'submit_time',
            'answers',
            'statistics'
        ]
    
    def get_statistics(self, obj):
        """获取答题统计信息"""
        total = obj.answers.count()
        correct = obj.answers.filter(is_correct=True).count()
        wrong = total - correct
        accuracy = (correct / total * 100) if total > 0 else 0
        is_passed = obj.score >= obj.quiz.pass_score
        
        return {
            'total_questions': total,
            'correct_count': correct,
            'wrong_count': wrong,
            'accuracy': round(accuracy, 2),
            'is_passed': is_passed
        }


class QuizRecordCreateSerializer(serializers.ModelSerializer):
    """创建答题记录序列化器"""
    
    class Meta:
        model = QuizRecord
        fields = ['quiz']
    
    def create(self, validated_data):
        """创建答题记录并初始化答案"""
        user = self.context['request'].user
        quiz = validated_data['quiz']
        
        # 创建记录
        record = QuizRecord.objects.create(
            user=user,
            quiz=quiz,
            status='in_progress'
        )
        
        # 为每道题创建答案记录
        for quiz_question in quiz.quiz_questions.all():
            Answer.objects.create(
                record=record,
                question=quiz_question.question
            )
        
        return record


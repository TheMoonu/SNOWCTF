from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from django.utils import timezone
from django.db import transaction
from django.db.models import Q
from quiz.models import Question, Option, Quiz, QuizRecord, Answer
from quiz.serializers import (
    QuestionListSerializer,
    QuestionDetailSerializer,
    QuizListSerializer,
    QuizDetailSerializer,
    QuizRecordListSerializer,
    QuizRecordDetailSerializer,
    QuizRecordCreateSerializer,
    AnswerSerializer
)


class QuestionViewSet(viewsets.ModelViewSet):
    """题目视图集"""
    queryset = Question.objects.filter(is_active=True)
    permission_classes = [IsAdminUser]
    
    def get_serializer_class(self):
        if self.action == 'list':
            return QuestionListSerializer
        return QuestionDetailSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # 按题目类型筛选
        question_type = self.request.query_params.get('type')
        if question_type:
            queryset = queryset.filter(question_type=question_type)
        
        # 按难度筛选
        difficulty = self.request.query_params.get('difficulty')
        if difficulty:
            queryset = queryset.filter(difficulty=difficulty)
        
        # 按分类筛选
        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(category=category)
        
        return queryset


class QuizViewSet(viewsets.ModelViewSet):
    """竞赛视图集"""
    queryset = Quiz.objects.filter(is_active=True)
    
    def get_serializer_class(self):
        if self.action == 'list':
            return QuizListSerializer
        return QuizDetailSerializer
    
    def get_permissions(self):
        """设置权限"""
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [IsAdminUser()]
        return [IsAuthenticated()]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # 只显示时间范围内的竞赛
        show_all = self.request.query_params.get('show_all', 'false').lower() == 'true'
        if not show_all:
            now = timezone.now()
            queryset = queryset.filter(
                Q(start_time__lte=now, end_time__gte=now) |
                Q(start_time__isnull=True, end_time__isnull=True)
            )
        
        return queryset.order_by('-created_at')
    
    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        """开始答题"""
        quiz = self.get_object()
        
        # 检查是否有进行中的记录
        existing_record = QuizRecord.objects.filter(
            user=request.user,
            quiz=quiz,
            status='in_progress'
        ).first()
        
        if existing_record:
            serializer = QuizRecordDetailSerializer(existing_record)
            return Response({
                'message': '您已有进行中的答题记录',
                'record': serializer.data
            })
        
        # 创建新记录
        serializer = QuizRecordCreateSerializer(
            data={'quiz': quiz.id},
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        record = serializer.save()
        
        detail_serializer = QuizRecordDetailSerializer(record)
        return Response(detail_serializer.data, status=status.HTTP_201_CREATED)


class QuizRecordViewSet(viewsets.ModelViewSet):
    """答题记录视图集"""
    serializer_class = QuizRecordListSerializer
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        if self.action == 'create':
            return QuizRecordCreateSerializer
        elif self.action in ['retrieve', 'current']:
            return QuizRecordDetailSerializer
        return QuizRecordListSerializer
    
    def get_queryset(self):
        """只能查看自己的记录"""
        user = self.request.user
        queryset = QuizRecord.objects.filter(user=user)
        
        # 按状态筛选
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # 按竞赛筛选
        quiz_id = self.request.query_params.get('quiz_id')
        if quiz_id:
            queryset = queryset.filter(quiz_id=quiz_id)
        
        return queryset.order_by('-start_time')
    
    @action(detail=False, methods=['get'])
    def current(self, request):
        """获取当前进行中的答题"""
        records = QuizRecord.objects.filter(
            user=request.user,
            status='in_progress'
        )
        serializer = self.get_serializer(records, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    @transaction.atomic
    def submit(self, request, pk=None):
        """提交答卷"""
        record = self.get_object()
        
        if record.status != 'in_progress':
            return Response(
                {'error': '该记录已提交或已超时'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # 检查所有答案
        for answer in record.answers.all():
            answer.check_and_save()
        
        # 计算总分
        record.calculate_score()
        
        # 更新状态
        record.status = 'completed'
        record.submit_time = timezone.now()
        record.save()
        
        serializer = QuizRecordDetailSerializer(record)
        return Response({
            'message': '提交成功',
            'record': serializer.data
        })
    
    @action(detail=True, methods=['get'])
    def result(self, request, pk=None):
        """查看成绩"""
        record = self.get_object()
        
        if record.status == 'in_progress':
            return Response(
                {'error': '答题尚未完成'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = QuizRecordDetailSerializer(record)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def save_answer(self, request, pk=None):
        """保存单题答案（已弃用，建议使用 batch_save_answers）"""
        record = self.get_object()
        
        if record.status != 'in_progress':
            return Response(
                {'error': '该记录已提交或已超时'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        question_id = request.data.get('question_id')
        selected_option_ids = request.data.get('selected_option_ids', [])
        
        if not question_id:
            return Response(
                {'error': '缺少题目ID'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            answer = Answer.objects.get(
                record=record,
                question_id=question_id
            )
            
            serializer = AnswerSerializer(
                answer,
                data={'selected_option_ids': selected_option_ids},
                partial=True
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()
            
            return Response({
                'message': '答案已保存',
                'answer': serializer.data
            })
        except Answer.DoesNotExist:
            return Response(
                {'error': '答案记录不存在'},
                status=status.HTTP_404_NOT_FOUND
            )
    
    @action(detail=True, methods=['post'], url_path='batch-save-answers')
    @transaction.atomic
    def batch_save_answers(self, request, pk=None):
        """批量保存答案（本地缓存优化）"""
        record = self.get_object()
        
        if record.status != 'in_progress':
            return Response(
                {'error': '该记录已提交或已超时'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # 检查是否超时
        from django.utils import timezone
        elapsed_time = (timezone.now() - record.start_time).total_seconds()
        if elapsed_time > record.quiz.duration * 60:
            return Response({
                'success': False,
                'message': '答题时间已结束',
                'timeout': True
            }, status=status.HTTP_400_BAD_REQUEST)
        
        answers_data = request.data.get('answers', {})
        
        if not answers_data:
            return Response({
                'success': True,
                'message': '没有答案需要保存',
                'saved_count': 0
            })
        
        success_count = 0
        error_count = 0
        
        for question_id_str, answer_info in answers_data.items():
            try:
                question_id = int(question_id_str)
                option_ids = answer_info.get('optionIds', [])
                
                # 验证题目属于该竞赛
                if not record.quiz.quiz_questions.filter(question_id=question_id).exists():
                    error_count += 1
                    continue
                
                # 获取答案记录
                answer = Answer.objects.get(
                    record=record,
                    question_id=question_id
                )
                
                # 清除并设置新选项
                answer.selected_options.clear()
                if option_ids:
                    answer.selected_options.set(option_ids)
                
                success_count += 1
                
            except (ValueError, Answer.DoesNotExist):
                error_count += 1
                continue
        
        return Response({
            'success': True,
            'message': f'成功保存 {success_count} 题答案',
            'saved_count': success_count,
            'error_count': error_count
        })
    
    @action(detail=True, methods=['get'], url_path='get-answers')
    def get_answers(self, request, pk=None):
        """获取已保存的答案（用于恢复本地缓存）"""
        record = self.get_object()
        
        # 获取所有答案
        answers = record.answers.prefetch_related('selected_options').all()
        
        answers_data = {}
        for answer in answers:
            option_ids = list(answer.selected_options.values_list('id', flat=True))
            if option_ids:
                answers_data[str(answer.question_id)] = {
                    'optionIds': option_ids,
                    'timestamp': int(answer.created_at.timestamp() * 1000)
                }
        
        return Response({
            'success': True,
            'answers': answers_data
        })


class AnswerViewSet(viewsets.ReadOnlyModelViewSet):
    """答案视图集（只读）"""
    serializer_class = AnswerSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """只能查看自己的答案"""
        return Answer.objects.filter(record__user=self.request.user)


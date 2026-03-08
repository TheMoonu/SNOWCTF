from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .viewsets import QuestionViewSet, QuizViewSet, QuizRecordViewSet, AnswerViewSet

# 创建路由器
router = DefaultRouter()

# 注册视图集
router.register(r'questions', QuestionViewSet, basename='question')
router.register(r'quizzes', QuizViewSet, basename='quiz')
router.register(r'records', QuizRecordViewSet, basename='record')
router.register(r'answers', AnswerViewSet, basename='answer')

app_name = 'quiz-api'

urlpatterns = [
    path('', include(router.urls)),
]


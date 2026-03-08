from django.urls import path
from . import views, creator_views

app_name = 'quiz'

urlpatterns = [
    # 竞赛列表
    path('', views.quiz_list, name='quiz_list'),
    
    # 我的记录 - 必须放在 <slug> 之前
    path('my-records/', views.my_records, name='my_records'),
    
    # 我的报名记录
    path('my-registrations/', views.my_registrations, name='my_registrations'),
    
    # 竞赛创建者功能
    path('my/', creator_views.my_quizzes, name='my_quizzes'),
    path('create/', creator_views.create_quiz, name='create_quiz'),
    path('create/captcha/', creator_views.get_create_captcha, name='get_create_captcha'),
    path('manage/<slug:quiz_slug>/', creator_views.edit_quiz, name='creator_edit_quiz'),
    path('manage/<slug:quiz_slug>/delete/', creator_views.delete_quiz, name='creator_delete_quiz'),
    path('manage/<slug:quiz_slug>/toggle/', creator_views.toggle_quiz_status, name='toggle_quiz_status'),
    path('manage/<slug:quiz_slug>/statistics/', creator_views.quiz_statistics, name='quiz_statistics'),
    path('manage/<slug:quiz_slug>/registrations/', creator_views.quiz_registrations, name='quiz_registrations'),
    path('manage/<slug:quiz_slug>/registrations/<int:registration_id>/update/', creator_views.update_registration_status, name='update_registration_status'),
    
    # 阅卷管理
    path('manage/<slug:slug>/grading/', views.quiz_grading, name='quiz_grading'),
    path('grading/answer/<int:answer_id>/submit/', views.submit_grading, name='submit_grading'),
    path('grading/answer/<int:answer_id>/lock/', views.lock_answer, name='lock_answer'),
    path('grading/answer/<int:answer_id>/unlock/', views.unlock_answer, name='unlock_answer'),
    path('grading/answer/<int:answer_id>/detail/', views.answer_detail, name='answer_detail'),
    path('grading/answer/<int:answer_id>/edit/', views.edit_grading, name='edit_grading'),
    path('manage/<slug:slug>/grading/add-grader/', views.add_grader, name='add_grader'),
    path('grading/grader/<int:grader_id>/remove/', views.remove_grader, name='remove_grader'),
    
    path('manage/<slug:quiz_slug>/create-question/', creator_views.create_question, name='create_question'),
    path('manage/<slug:quiz_slug>/add-question/', creator_views.add_question_page, name='add_question_page'),
    path('manage/<slug:quiz_slug>/add-question/submit/', creator_views.add_question_to_quiz, name='add_question_to_quiz'),
    path('manage/<slug:quiz_slug>/remove-question/', creator_views.remove_question_from_quiz, name='remove_question_from_quiz'),
    path('manage/<slug:slug>/import/', creator_views.import_questions, name='import_questions'),
    path('import/template/', creator_views.download_import_template, name='download_import_template'),
    path('question/<int:question_id>/detail/', creator_views.question_detail, name='question_detail'),
    
    # 答题页面
    path('answer/<uuid:record_uuid>/', views.quiz_answer, name='quiz_answer'),
    
    # 批量保存答案（AJAX - 提交时同步）
    path('answer/<uuid:record_uuid>/batch-save/', views.batch_save_answers, name='batch_save_answers'),
    
    # 获取已保存的答案（AJAX - 恢复本地缓存）
    path('answer/<uuid:record_uuid>/get-answers/', views.get_answers, name='get_answers'),
    
    # 记录违规行为（AJAX）
    path('answer/<uuid:record_uuid>/violation/', views.record_violation, name='record_violation'),
    
    # 提交试卷
    path('answer/<uuid:record_uuid>/submit/', views.submit_quiz, name='submit_quiz'),
    
    # 查询异步提交任务状态（AJAX）
    path('submit-task/<str:task_id>/', views.check_submit_task, name='check_submit_task'),
    
    # 成绩查看
    path('result/<uuid:record_uuid>/', views.quiz_result, name='quiz_result'),
    
    # 竞赛详情 - 放在具体路径之后
    path('<slug:quiz_slug>/', views.quiz_detail, name='quiz_detail'),
    
    # 开始答题
    path('<slug:quiz_slug>/start/', views.start_quiz, name='start_quiz'),
    
    # 排行榜
    path('<slug:quiz_slug>/leaderboard/', views.quiz_leaderboard, name='quiz_leaderboard'),
    
    # 竞赛报名
    path('<slug:quiz_slug>/register/', views.quiz_register, name='quiz_register'),
    path('<slug:quiz_slug>/register/captcha/', views.get_register_captcha, name='get_register_captcha'),
]


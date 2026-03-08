# -*- coding: utf-8 -*-
from django.urls import path
from django.conf import settings
from .views import (SubmissionDynamicView,SubmissionDynamicAPIView,SubmissionDynamicDemoView,challenge_detail,create_web_container,CompetitionCreateView,CompetitionAddChallengesView,
    verify_flag,destroy_web_container,check_container_status,delete_challenge,CompetitionChallengeCreateView,export_registrations,
    CompetitionViewList,Competition_detail,registrationView,RankingsView,competition_manage,get_team_members,export_statistics,export_rankings,audit_registration,
    competition_dashboard,refresh_captcha,delete_registration,delete_team,get_competition_statistics,remove_challenge_from_competition,challenge_edit,
    refresh_invitation_code,combined_rankings_view,writeup_captcha,writeup_upload,template_download,delete_writeup,
    query_container_task_status,cancel_container_task,secure_url_download,adjust_score,dual_track_entrance,ctf_rankings_api,quiz_rankings_api)
from .dashboard_views import (
    competition_dashboard_optimized,
    get_dashboard_data_optimized,
    dashboard_sse_stream,
    refresh_dashboard_data,
    dashboard_demo,
    get_dashboard_demo_data
)
from .dashboard_views_individual import (
    individual_dashboard_optimized,
    get_individual_dashboard_data,
    individual_dashboard_sse_stream
)
from .combined_leaderboard_views import (
    CombinedLeaderboardView,
    SyncQuizRegistrationsView,
    UpdateCombinedLeaderboardView,
    LeaderboardCalculationStatusView,
    VerifyLeaderboardView
)

urlpatterns = [
    path('',CompetitionViewList,name='CompetitionView'),
    # 团队赛数据大屏（已使用优化版，支持SSE实时推送）
    path('<slug:slug>/dashboard/', competition_dashboard_optimized, name='competition_dashboard'),
    path('<slug:slug>/dashboard/sse/', dashboard_sse_stream, name='dashboard_sse'),
    path('api/v1/competition/<slug:slug>/dashboard-data-optimized/', get_dashboard_data_optimized, name='competition_dashboard_data_optimized'),
    path('api/v1/competition/<slug:slug>/dashboard-refresh/', refresh_dashboard_data, name='dashboard_refresh'),
    
    # 个人赛数据大屏
    path('<slug:slug>/individual-dashboard/', individual_dashboard_optimized, name='individual_dashboard'),
    path('<slug:slug>/individual-dashboard/sse/', individual_dashboard_sse_stream, name='individual_dashboard_sse'),
    path('api/v1/competition/<slug:slug>/individual-dashboard-data/', get_individual_dashboard_data, name='individual_dashboard_data'),
    
    # Demo演示（无需登录）
    path('dashboard/demo/', dashboard_demo, name='dashboard_demo'),
    path('api/v1/dashboard/demo/data/', get_dashboard_demo_data, name='dashboard_demo_data'),
    
    path('create/', CompetitionCreateView.as_view(), name='competition_create'),
    path('<slug:slug>/add-challenges/',CompetitionAddChallengesView.as_view(), name='competition_add_challenges'),
    path('<slug:slug>/create-challenge/', CompetitionChallengeCreateView.as_view(), name='competition_create_challenge'),  # 主页，自然排序
    
    
    
    path('api/v1/<slug:slug>/create_web_container/', create_web_container, name='create_web_container'),
    path('api/v1/<slug:slug>/container/task/<str:task_id>/', query_container_task_status, name='query_container_task_status'),
    path('api/v1/<slug:slug>/container/task/<str:task_id>/cancel/', cancel_container_task, name='cancel_container_task'),
    
    path('api/v1/<slug:slug>/verify-flag/', verify_flag, name='verify_flag'),
    path('api/v1/destroy_web_container/', destroy_web_container, name='destroy_web_container'),
    path('api/v1/check_container_status/', check_container_status, name='check_container_status'),

    path('api/v1/challenge/delete/', delete_challenge, name='challenge_delete'),
    path('api/v1/refresh-captcha/', refresh_captcha, name='refresh_captcha'),
    
    # 双赛道入口页面（必须在competition_detail之前，避免路由冲突）
    path('<slug:slug>/tracks/', dual_track_entrance, name='dual_track_entrance'),
    
    # CTF竞赛题目列表页面（从双赛道点击进入CTF赛道时使用）
    path('<slug:slug>/challenges/', Competition_detail.as_view(), {'bypass_dual_track': True}, name='com_index'),
    
    # 竞赛入口页面（有关联quiz时跳转到双赛道，无关联时直接显示CTF题目列表）
    path('<slug:slug>/', Competition_detail.as_view(), name='competition_detail'),
    
    path('rankings/<slug:slug>/<str:ranking_type>/', RankingsView.as_view(), name='rankings'),
    
    # 综合排行榜（CTF+知识竞赛）
    path('combined-rankings/<slug:slug>/', combined_rankings_view, name='combined_rankings'),
    
    # 综合排行榜API接口
    path('api/v1/competition/<slug:competition_slug>/combined-leaderboard/', CombinedLeaderboardView.as_view(), name='combined_leaderboard_api'),
    
    # CTF排行榜API接口（用于综合排行榜页面）
    path('api/v1/competition/<slug:slug>/ctf-rankings/', ctf_rankings_api, name='ctf_rankings_api'),
    
    # 知识竞赛排行榜API接口（用于综合排行榜页面）
    path('api/v1/competition/<slug:slug>/quiz-rankings/', quiz_rankings_api, name='quiz_rankings_api'),
    path('api/v1/competition/<slug:competition_slug>/sync-quiz-registrations/', SyncQuizRegistrationsView.as_view(), name='sync_quiz_registrations'),
    path('api/v1/competition/<slug:competition_slug>/update-combined-leaderboard/', UpdateCombinedLeaderboardView.as_view(), name='update_combined_leaderboard'),
    path('api/v1/competition/<slug:competition_slug>/leaderboard-calculation-status/', LeaderboardCalculationStatusView.as_view(), name='leaderboard_calculation_status'),
    path('api/v1/competition/<slug:competition_slug>/verify-leaderboard/', VerifyLeaderboardView.as_view(), name='verify_leaderboard'),

    # 添加以下URL路由
    path('<slug:slug>/manage/', competition_manage, name='competition_manage'),
    path('api/v1/<slug:slug>/adjust-score/', adjust_score, name='adjust_score'),
    path('api/v1/team/<int:team_id>/members/', get_team_members, name='get_team_members'),
    path('api/v1/registration/<int:registration_id>/delete/', delete_registration, name='delete_registration'),
    path('api/v1/team/<int:team_id>/delete/', delete_team, name='delete_team'),
    path('api/v1/competition/<int:competition_id>/remove-challenge/<int:challenge_id>/', remove_challenge_from_competition, name='remove_challenge_from_competition'),
    path('api/v1/competition/<slug:slug>/statistics/', get_competition_statistics, name='get_competition_statistics'),
    path('export-statistics/<int:competition_id>/', export_statistics, name='export_statistics'),
    path('export-registrations/<int:competition_id>/', export_registrations, name='export_registrations'),
    path('export-rankings/<int:competition_id>/', export_rankings, name='export_rankings'),
    path('challenge/<slug:slug>/<slug:uuid>/edit/', challenge_edit, name='challenge_edit'),
    path('api/v1/audit-registration/', audit_registration, name='audit_registration'),
    path('api/v1/refresh-invitation-code/', refresh_invitation_code, name='refresh_invitation_code'),
    #path('list/', views.CompetitionListView.as_view(), name='competition_list'),
    #path('<str:slug>/', views.CompetitionDetailView.as_view(), name='competition_detail'),
    
    path('register/<slug:slug>/<slug:re_slug>/', registrationView, name='registration_detail'),
    path('<slug:slug>/<slug:challenge_uuid>/download/<str:token>/', secure_url_download, name='secure_url_download'),  # 安全的URL文件下载（必须在详情页之前）
    path('<slug:slug>/<slug:uuid>/', challenge_detail, name='challenge_detail'),


    path('submissions/<slug:slug>', SubmissionDynamicView.as_view(), name='submission_dynamic'),
    path('submissions-demo/demo', SubmissionDynamicDemoView.as_view(), name='submission_dynamic_demo'),
    path('api/v1/<slug:slug>/submissions/', SubmissionDynamicAPIView.as_view(), name='submission_dynamic_api'),
    
    # Writeup 上传（使用 api/v1 前缀避免路由冲突）
    path('api/v1/writeup/captcha/', writeup_captcha, name='writeup_captcha'),
    path('api/v1/writeup/submit/<slug:slug>/', writeup_upload, name='writeup_upload'),
    path('api/v1/writeup/template/<uuid:template_uuid>/download/', template_download, name='template_download'),
    path('api/v1/writeup/<int:writeup_id>/delete/', delete_writeup, name='delete_writeup'),
]

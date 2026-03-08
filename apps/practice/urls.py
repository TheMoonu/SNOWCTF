from django.urls import path
from .views import (toggle_collect,refresh_captcha,create_web_container,remove_container,create_web_container_view,CTFChallengeListView,challenge_detail,verify_flag,destroy_web_container,check_container_status,ChallengeCreateView,delete_challenge,toggle_active_challenge,edit_challenge,TagView,purchase_writeup,query_container_task_status,cancel_container_task,leaderboard_view,secure_url_download
                    )


urlpatterns = [
    path('', CTFChallengeListView.as_view(), name='challenge_list'),
    path('leaderboard/', leaderboard_view, name='leaderboard'),
    path('create_web_container/', create_web_container, name='create_web_container'),
    path('container/task/<str:task_id>/', query_container_task_status, name='query_container_task_status'),
    path('container/task/<str:task_id>/cancel/', cancel_container_task, name='cancel_container_task'),
    path('remove_container/<str:container_id>/', remove_container, name='remove_container'),
    path('api/verify-flag/', verify_flag, name='verify_flag'),
    path('api/v1/destroy_web_container/', destroy_web_container, name='destroy_web_container'),
    path('api/check_container_status/', check_container_status, name='check_container_status'),
    # 可以在这里添加其他 CTF 相关的 URL 路由
    path('create-challenge/', ChallengeCreateView.as_view(), name='challenge_create'),
    path('tag/<slug:slug>/', TagView.as_view(), name='tag'),
    path('api/v1/challenge/delete/', delete_challenge, name='challenge_delete'),
    path('api/v1/challenge/toggle-active/', toggle_active_challenge, name='challenge_toggle_active'),
    path('api/v1/challenge/edit/', edit_challenge, name='challenge_edit'),
    path('api/v1/captcha/refresh/', refresh_captcha, name='refresh_captcha'),
    path("api/v1/challenge/collect/", toggle_collect, name="toggle_collect"),
    path('<uuid:uuid>/purchase-writeup/', purchase_writeup, name='purchase_writeup'),  
    path('<uuid:challenge_uuid>/download/<str:token>/', secure_url_download, name='secure_url_download'),  
    path('<uuid:uuid>/', challenge_detail, name='challenge_detail'),  

]

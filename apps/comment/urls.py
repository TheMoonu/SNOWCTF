# -*- coding: utf-8 -*-
from django.urls import path
from .views import AddCommentView, NotificationView, mark_to_read, mark_to_delete, AddChallengeCommentView

urlpatterns = [
    path('add/', AddCommentView, name='add_comment'),
    path('add_challenge/', AddChallengeCommentView, name='add_comment_challenge'),
    path('notification/', NotificationView, name='notification'),
    path('notification/no-read/', NotificationView, {'is_read': 'false'}, name='notification_no_read'),
    path('notification/mark-to-read/', mark_to_read, name='mark_to_read'),
    path('notification/mark-to-delete/', mark_to_delete, name='mark_to_delete'),
]
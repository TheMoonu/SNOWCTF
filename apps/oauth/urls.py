# -*- coding: utf-8 -*-
from django.urls import path
from .views import profile_view, change_profile_view, dissolve_team, leave_team, follow_toggle, generate_invite_code
from . import api

urlpatterns = [
    path('profile/',profile_view,name='profile'),
    path('profile/change/',change_profile_view,name='change_profile'),
    path('api/v1/team/<int:team_id>/dissolve/', dissolve_team, name='dissolve_team'),
    path('api/v1/team/<int:team_id>/leave/', leave_team, name='leave_team'),
    path('profile/follow/', follow_toggle, name='follow_toggle'),
    path('profile/generate_invite_code/', generate_invite_code, name='generate_invite_code'),
    path('api/v1/user/<int:user_id>/following/', api.get_following, name='api_get_following'),
    path('api/v1/user/<int:user_id>/followers/', api.get_followers, name='api_get_followers'),
    path('api/v1/user/follow/<int:user_id>/', api.toggle_follow, name='api_toggle_follow'),
]
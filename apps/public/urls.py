from django.urls import path
from .views import indexViews, profileViews, licenseViews, reward_list, exchange_reward, get_exchange_captcha

urlpatterns = [
    path('', indexViews, name='indexViews'),
    path('profile/<int:user_uuid>/', profileViews, name='profileViews'),
    path('license/', licenseViews, name='licenseViews'),
    path('reward/', reward_list, name='reward_list'),
    path('public/api/v1/exchange/', exchange_reward, name='exchange_reward'),
    path('public/api/v1/exchange/captcha/', get_exchange_captcha, name='get_exchange_captcha'),
]
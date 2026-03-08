# -*- coding: utf-8 -*-
import random
from django.db.models.signals import pre_save
from django.dispatch import receiver
from oauth.models import Ouser
from django.contrib.auth.signals import user_logged_in

@receiver(pre_save, sender=Ouser)
def generate_avatar(sender, instance, **kwargs):
    if instance._state.adding:
        # 随机选择一个头像地址
        random_avatar = 'avatar/default/default{}.png'.format(random.randint(1, 10))
        instance.avatar = random_avatar


@receiver(user_logged_in)
def on_user_logged_in(sender, request, user, **kwargs):
    """用户登录时的处理"""
    # 保存当前会话key到用户实例
    user._current_session_key = request.session.session_key
    # 强制登出其他设备
    user.force_logout_other_sessions()

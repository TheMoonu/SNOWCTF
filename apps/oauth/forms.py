# -*- coding: utf-8 -*-
from django import forms
from oauth.models import Ouser
from allauth.account.forms import LoginForm
from allauth.account.forms import AddEmailForm
from django.core.validators import EmailValidator
from django.core.exceptions import ValidationError
from allauth.account.adapter import DefaultAccountAdapter
from django.core.mail import EmailMessage
from smtplib import SMTPRecipientsRefused
from django.contrib import messages
from allauth.account.forms import AddEmailForm, SignupForm
from captcha.fields import CaptchaField
from public.models import CTFUser
from django.utils import timezone
from comment.models import SystemNotification
import logging


logger = logging.getLogger('apps.oauth')
class ProfileForm(forms.ModelForm):
    """个人资料修改表单"""
    
    # 手动定义加密字段（这些字段通过 property 实现）
    real_name = forms.CharField(
        label='真实姓名',
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入真实姓名'
        })
    )
    
    phones = forms.CharField(
        label='手机号',
        max_length=11,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入手机号'
        })
    )
    
    department = forms.CharField(
        label='学院/部门',
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入学院或部门名称'
        })
    )
    
    student_id = forms.CharField(
        label='学号/工号',
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入学号或工号'
        })
    )
    
    avatar = forms.ImageField(
        label='头像',
        required=False,
        widget=forms.FileInput(attrs={
            'class': 'form-control-file',
            'accept': 'image/jpeg,image/jpg,image/png,image/gif,image/webp',
            'data-max-size': '5120',  # 最大文件大小（KB）= 5MB
            'id': 'avatar-upload'
        }),
        help_text='支持 JPG、PNG、GIF、WebP 格式，文件小于 5MB'
    )
    
    class Meta:
        model = Ouser
        fields = ['profile', 'avatar']
    
    def clean_real_name(self):
        """验证真实姓名"""
        real_name = self.cleaned_data.get('real_name', '').strip()
        if len(real_name) > 50:
            raise forms.ValidationError('真实姓名不能超过50个字符')
        return real_name
    
    def clean_phones(self):
        """验证手机号"""
        phones = self.cleaned_data.get('phones', '').strip()
        if phones:
            # 验证手机号格式（中国大陆手机号）
            import re
            if not re.match(r'^1[3-9]\d{9}$', phones):
                raise forms.ValidationError('请输入有效的手机号码')
        return phones
    
    def clean_profile(self):
        """验证个人简介"""
        profile = self.cleaned_data.get('profile', '').strip()
        if len(profile) > 200:
            raise forms.ValidationError('个人简介不能超过200个字符')
        return profile
    
    def save(self, commit=True):
        """保存表单，处理加密字段"""
        instance = super().save(commit=False)
        
        # 手动设置加密字段（通过 property setter 自动加密）
        # 只有当用户输入了新值时才更新，留空则保持原值
        if 'real_name' in self.cleaned_data and self.cleaned_data['real_name']:
            instance.real_name = self.cleaned_data['real_name']
        if 'phones' in self.cleaned_data and self.cleaned_data['phones']:
            instance.phones = self.cleaned_data['phones']
        if 'department' in self.cleaned_data and self.cleaned_data['department']:
            instance.department = self.cleaned_data['department']
        if 'student_id' in self.cleaned_data and self.cleaned_data['student_id']:
            instance.student_id = self.cleaned_data['student_id']
        
        if commit:
            instance.save()
        
        return instance

class CustomLoginForm(LoginForm):
    captcha = CaptchaField(
        label='验证码',
        error_messages={
            'invalid': '验证码错误，请重新输入',
            'required': '请输入验证码',
        }
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 添加 Bootstrap 样式
        self.fields['captcha'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': '请输入验证码',
        })






class CustomSignupForm(SignupForm):
    captcha = CaptchaField(
        label='验证码',
        error_messages={
            'invalid': '验证码错误，请重新输入',
            'required': '请输入验证码',
        }
    )
    invite_code = forms.CharField(
        label='邀请码',
        max_length=8,
        required=False,  # 设置为可选，根据需求可以改为必填
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入邀请码（可选）',
        })
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 添加 Bootstrap 样式
        self.fields['captcha'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': '请输入验证码',
        })
        
        # 调整字段顺序，使邀请码在验证码之前
        field_order = list(self.fields.keys())
        if 'captcha' in field_order and 'invite_code' in field_order:
            captcha_index = field_order.index('captcha')
            invite_code_index = field_order.index('invite_code')
            field_order.insert(captcha_index, field_order.pop(invite_code_index))
            self.order_fields(field_order)
    
    def clean_invite_code(self):
        invite_code = self.cleaned_data.get('invite_code')
        
        # 如果没有提供邀请码，则跳过验证
        if not invite_code:
            return invite_code
            
        # 验证邀请码是否存在且有效
        try:
            from oauth.models import Ouser  # 使用正确的用户模型
            inviter = Ouser.objects.get(
                invite_code=invite_code,
                invite_code_expires__gt=timezone.now()  # 确保邀请码未过期
            )
            # 存储邀请人，以便在 save 方法中使用
            self.inviter = inviter
            return invite_code
        except Ouser.DoesNotExist:
            raise ValidationError('无效的邀请码或邀请码已过期')
    
    def save(self, request):
        # 调用父类的 save 方法创建用户
        user = super().save(request)
        
        # 如果提供了有效的邀请码，设置邀请关系并奖励金币
        invite_code = self.cleaned_data.get('invite_code')
        if invite_code and hasattr(self, 'inviter'):
            user.invited_by = self.inviter
            user.save()
            
            # 为邀请人增加金币奖励
            self._reward_inviter(self.inviter)
            
            # 为新用户增加金币奖励
            self._reward_new_user(user)
            
            # 记录邀请日志
            self._log_invitation(self.inviter, user)
        
        return user
    
    def _reward_inviter(self, inviter):
        """为邀请人增加金币奖励"""
        try:
            # 获取邀请人的 CTFUser 记录
            inviter_ctf_user, created = CTFUser.objects.get_or_create(user=inviter)
            
            # 增加金币奖励（邀请人获得20金币）
            inviter_ctf_user.coins += 20
            inviter_ctf_user.save()
            
            # 记录日志
            try:
                notification = SystemNotification.objects.create(
                    title='新的邀请者',
                    content=f'你邀请了一位新用户注册，恭喜你获得20金币奖励'
                )
                # 添加接收者
                notification.get_p.add(inviter)
            except Exception as e:
                logger.error(f"创建系统通知时出错: {str(e)}")
            
            logger.info(f"用户 {inviter.username}(ID:{inviter.id}) 邀请了新用户，获得20金币奖励")
            
        except Exception as e:
            # 记录错误但不中断注册流程
            logger.error(f"为邀请人 {inviter.username} 增加金币奖励时出错: {str(e)}")
    
    def _reward_new_user(self, user):
        """为新用户增加金币奖励"""
        try:
            # 获取新用户的 CTFUser 记录
            new_user_ctf_user, created = CTFUser.objects.get_or_create(user=user)
            
            # 增加金币奖励（新用户获得10金币）
            new_user_ctf_user.coins += 10
            new_user_ctf_user.save()
            
            # 记录日志
            try:
                notification = SystemNotification.objects.create(
                    title='新用户注册通知',
                    content=f'恭喜你通过邀请码注册成功，获得10金币奖励'
                )
                # 添加接收者
                notification.get_p.add(user)
            except Exception as e:
                logger.error(f"创建系统通知时出错: {str(e)}")
       
            logger.info(f"新用户 {user.username}(ID:{user.id}) 通过邀请注册，获得10金币奖励")
            
        except Exception as e:
            # 记录错误但不中断注册流程
            import logging
            logger.error(f"为新用户 {user.username} 增加金币奖励时出错: {str(e)}")
    
    def _log_invitation(self, inviter, invitee):
        """记录邀请关系"""
        try:
            # 可以在这里添加邀请记录到数据库
            from django.contrib.admin.models import LogEntry, ADDITION
            from django.contrib.contenttypes.models import ContentType
            from oauth.models import Ouser
            
            LogEntry.objects.log_action(
                user_id=inviter.id,
                content_type_id=ContentType.objects.get_for_model(Ouser).pk,
                object_id=invitee.id,
                object_repr=invitee.username,
                action_flag=ADDITION,
                change_message=f"邀请了新用户 {invitee.username}"
            )
        except Exception as e:
            # 记录错误但不中断注册流程
            logger.error(f"记录邀请关系时出错: {str(e)}")


class CustomAddEmailForm(AddEmailForm):
    """自定义添加邮箱表单 - 防止添加已被其他用户使用的邮箱"""
    
    def clean_email(self):
        """验证邮箱是否已被其他用户使用"""
        from allauth.account.models import EmailAddress
        
        email = self.cleaned_data.get('email')
        if not email:
            return email
        
        # 检查是否已被其他用户使用
        existing = EmailAddress.objects.filter(email__iexact=email)
        if self.user:
            existing = existing.exclude(user=self.user)
        
        if existing.exists():
            raise ValidationError(f'邮箱 {email} 已被其他用户使用，请使用其他邮箱地址。')
        
        return email





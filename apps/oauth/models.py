import os
import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser
from imagekit.models import ProcessedImageField
from imagekit.processors import ResizeToFill
from django.core.validators import FileExtensionValidator
from django.conf import settings
from django.utils.html import strip_tags
import random
import bleach
import string
from django.utils import timezone 
from datetime import timedelta,datetime
from django.contrib.sessions.models import Session
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad
import base64






def generate_numeric_id():
    """生成一个11位的随机数字ID"""
    # 生成11位随机数字，确保第一位不为0
    return random.randint(10000000000, 99999999999)

def get_encryption_key():
    """获取加密密钥"""
    # 从环境变量或配置中获取密钥，如果没有则使用默认值（生产环境应该使用环境变量）
    key = getattr(settings, 'ENCRYPTION_KEY', 'SecSnowDefaultKey1234567890123')
    # 确保密钥长度为32字节（AES-256）
    return key.encode('utf-8')[:32].ljust(32, b'0')

def encrypt_data(data):
    """加密数据"""
    if not data:
        return ''
    try:
        key = get_encryption_key()
        cipher = AES.new(key, AES.MODE_CBC)
        # 加密数据
        encrypted = cipher.encrypt(pad(data.encode('utf-8'), AES.block_size))
        # 将 IV 和加密数据一起存储
        result = base64.b64encode(cipher.iv + encrypted).decode('utf-8')
        return result
    except Exception as e:
        # 如果加密失败，记录错误并返回原数据（不推荐，但作为后备）
        import logging
        logger = logging.getLogger('apps.oauth')
        logger.error(f"加密失败: {e}")
        return data

def is_data_encrypted(data):
    """
    检查数据是否已加密
    
    Returns:
        bool: True 表示已加密，False 表示明文
    """
    if not data:
        return False
    
    try:
        # 尝试 base64 解码
        encrypted_bytes = base64.b64decode(data.encode('utf-8'))
        # 检查长度（加密数据至少32字节）
        if len(encrypted_bytes) >= 32:
            return True
    except:
        pass
    
    return False


def decrypt_data(encrypted_data):
    """
    解密数据
    
    智能处理三种情况：
    1. 正常加密数据 → 解密返回
    2. 旧明文数据 → 直接返回
    3. 损坏的加密数据 → 返回占位符
    """
    if not encrypted_data:
        return ''
    
    # 判断是否是 base64 编码（加密数据的特征）
    try:
        # 尝试 base64 解码
        encrypted_bytes = base64.b64decode(encrypted_data.encode('utf-8'))
        
        # 检查长度（至少需要 IV(16字节) + 数据(至少16字节)）
        if len(encrypted_bytes) < 32:
            # 太短，不是有效的加密数据，当作明文处理
            return encrypted_data
        
        # 尝试解密
        key = get_encryption_key()
        iv = encrypted_bytes[:16]
        encrypted = encrypted_bytes[16:]
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = unpad(cipher.decrypt(encrypted), AES.block_size)
        return decrypted.decode('utf-8')
        
    except base64.binascii.Error:
        # base64 解码失败，说明是旧的明文数据
        # 检查是否包含特殊字符（明文数据特征）
        if any(c.isalnum() or c in ' @.-_' for c in encrypted_data):
            return encrypted_data
        else:
            # 无法识别的数据，返回占位符
            return '[数据损坏]'
            
    except (ValueError, KeyError) as e:
        # Padding 错误或解密失败，说明：
        # 1. 密钥错误
        # 2. 数据在存储/传输中损坏
        # 3. 加密格式不匹配
        import logging
        logger = logging.getLogger('apps.oauth')
        
        # 只在调试模式下记录详细错误
        if settings.DEBUG:
            logger.error(f"解密失败 - Padding/Key错误: {e}, 数据前缀: {encrypted_data[:20]}...")
        
        # 生产环境返回占位符，不暴露敏感信息
        return '[数据不可用]'
        
    except Exception as e:
        # 其他未知错误
        import logging
        logger = logging.getLogger('apps.oauth')
        logger.error(f"解密时发生未知错误: {type(e).__name__}: {e}")
        return '[解密错误]'

class Ouser(AbstractUser):
    link = models.URLField('个人网址', blank=True, help_text='提示：网址必须填写以http开头的完整形式')
    avatar = ProcessedImageField(upload_to='avatar/upload/%Y/%m/%d/%H-%M-%S',
                                 default='avatar/default/default.png',
                                 verbose_name='头像',
                                 processors=[ResizeToFill(80, 80)],
                                 validators=[
                                        FileExtensionValidator(
                                            allowed_extensions=['jpg', 'jpeg', 'png', 'gif'],
                                            message='只支持jpg、jpeg、png、gif格式的图片'
                                        )
                                ],
                                )
                                 
    is_member = models.BooleanField(
        default=False,
        verbose_name='是否为会员',
        help_text='根据会员有效期自动判断',
        editable=False  # 不允许直接修改，由系统根据时间自动判断
    )
    member_since = models.DateTimeField(
        null=True, 
        blank=True,
        verbose_name='会员开始时间',
        help_text='会员权限开始时间'
    )
    member_until = models.DateTimeField(
        null=True, 
        blank=True,
        verbose_name='会员结束时间',
        help_text='会员权限结束时间'
    )
    uuid = models.BigIntegerField('用户ID', default=generate_numeric_id, editable=False, unique=True)
    # 加密存储的字段（增加长度以容纳加密后的数据）
    _encrypted_real_name = models.CharField('真实姓名(加密)', max_length=255, blank=True, null=True, db_column='real_name')
    _encrypted_phones = models.CharField('手机号(加密)', max_length=255, blank=True, null=True, db_column='phones')
    _encrypted_department = models.CharField('学院/部门(加密)', max_length=255, blank=True, null=True, db_column='department')
    _encrypted_student_id = models.CharField('学号/工号(加密)', max_length=255, blank=True, null=True, db_column='student_id')
    profile = models.TextField('个人简介', blank=True, null=True, max_length=100,help_text='提示：个人简介字数限制在100字以内')

    following = models.ManyToManyField(
        'self',
        verbose_name='关注',
        related_name='followers',
        symmetrical=False,
        blank=True
    )

    invite_code = models.CharField('邀请码', max_length=8, unique=True, blank=True, null=True, help_text='用户唯一邀请码')
    invited_by = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='invitees', verbose_name='邀请人')
    invite_code_expires = models.DateTimeField('邀请码过期时间', blank=True, null=True)

    class Meta:
        verbose_name = '用户'
        verbose_name_plural = verbose_name
        ordering = ['-id']

    def __str__(self):
        return self.username

    def follow(self, user):
        """关注用户"""
        if user != self:  # 不能关注自己
            self.following.add(user)
    
    def unfollow(self, user):
        """取消关注用户"""
        self.following.remove(user)
    
    def is_following(self, user):
        """判断是否已关注某用户"""
        return self.following.filter(id=user.id).exists()
    
    @property
    def following_count(self):
        """关注数"""
        return self.following.count()
    
    @property
    def followers_count(self):
        """粉丝数"""
        return self.followers.count()
    
    @property
    def real_name(self):
        """获取解密后的真实姓名"""
        if self._encrypted_real_name:
            return decrypt_data(self._encrypted_real_name)
        return ''
    
    @real_name.setter
    def real_name(self, value):
        """设置真实姓名（自动加密）"""
        if value:
            self._encrypted_real_name = encrypt_data(value)
        else:
            self._encrypted_real_name = ''
    
    @property
    def real_name_masked(self):
        """获取脱敏后的真实姓名"""
        name = self.real_name
        if not name:
            return ''
        if len(name) <= 1:
            return '*'
        elif len(name) == 2:
            return name[0] + '*'
        else:
            # 保留首尾字符，中间用星号代替
            return name[0] + '*' * (len(name) - 2) + name[-1]
    
    @property
    def phones(self):
        """获取解密后的手机号"""
        if self._encrypted_phones:
            return decrypt_data(self._encrypted_phones)
        return ''
    
    @phones.setter
    def phones(self, value):
        """设置手机号（自动加密）"""
        if value:
            self._encrypted_phones = encrypt_data(value)
        else:
            self._encrypted_phones = ''
    
    @property
    def phones_masked(self):
        """获取脱敏后的手机号"""
        phone = self.phones
        if not phone:
            return ''
        if len(phone) == 11:
            # 标准手机号：保留前3位和后4位，中间4位用星号代替
            return phone[:3] + '****' + phone[-4:]
        elif len(phone) > 7:
            # 其他长号码：保留前3位和后4位
            return phone[:3] + '****' + phone[-4:]
        else:
            # 短号码：只显示最后2位
            return '*' * (len(phone) - 2) + phone[-2:]
    
    @property
    def department(self):
        """获取解密后的学院/部门"""
        if self._encrypted_department:
            return decrypt_data(self._encrypted_department)
        return ''
    
    @department.setter
    def department(self, value):
        """设置学院/部门（自动加密）"""
        if value:
            self._encrypted_department = encrypt_data(value)
        else:
            self._encrypted_department = ''
    
    @property
    def department_masked(self):
        """获取脱敏后的学院/部门"""
        dept = self.department
        if not dept:
            return ''
        length = len(dept)
        
        if length <= 2:
            # 1-2个字：全部显示（太短无法脱敏）
            return dept
        elif length <= 4:
            # 3-4个字：显示首尾各1字
            return dept[0] + '*' * (length - 2) + dept[-1]
        elif length <= 8:
            # 5-8个字：显示首尾各2字
            return dept[:2] + '*' * (length - 4) + dept[-2:]
        else:
            # 9个字以上：显示前3字和后2字
            return dept[:3] + '*' * (length - 5) + dept[-2:]
    
    @property
    def student_id(self):
        """获取解密后的学号/工号"""
        if self._encrypted_student_id:
            return decrypt_data(self._encrypted_student_id)
        return ''
    
    @student_id.setter
    def student_id(self, value):
        """设置学号/工号（自动加密）"""
        if value:
            self._encrypted_student_id = encrypt_data(value)
        else:
            self._encrypted_student_id = ''
    
    @property
    def student_id_masked(self):
        """获取脱敏后的学号/工号"""
        sid = self.student_id
        if not sid:
            return ''
        length = len(sid)
        
        if length <= 4:
            # 4位及以下：显示后2位
            return '*' * (length - 2) + sid[-2:]
        elif length <= 8:
            # 5-8位：显示前2位和后2位
            return sid[:2] + '*' * (length - 4) + sid[-2:]
        elif length <= 12:
            # 9-12位：显示前3位和后4位（标准学号）
            return sid[:3] + '*' * (length - 7) + sid[-4:]
        else:
            # 13位以上：显示前4位和后4位（如身份证号）
            return sid[:4] + '*' * (length - 8) + sid[-4:]

    @property
    def is_invite_code_valid(self):
        """检查邀请码是否有效"""
        if not self.invite_code or not self.invite_code_expires:
            return False
        return timezone.now() <= self.invite_code_expires

    def clean_profile(self):
        """清理个人简介内容"""
        if self.profile:
            # 首先去除所有HTML标签
            cleaned_text = strip_tags(self.profile)
            
            # 使用bleach清理内容，只允许基本的HTML标签
            allowed_tags = []  # 不允许任何HTML标签
            allowed_attributes = {}  # 不允许任何属性
            allowed_protocols = ['http', 'https', 'mailto']  # 允许的URL协议
            
            cleaned_text = bleach.clean(
                cleaned_text,
                tags=allowed_tags,
                attributes=allowed_attributes,
                protocols=allowed_protocols,
                strip=True,
                strip_comments=True
            )
            
            # 截断到最大长度
            if len(cleaned_text) > 100:
                cleaned_text = cleaned_text[:100]
            
            return cleaned_text
        return ''

    def save(self, *args, **kwargs):
        # 清理个人简介
        self.profile = self.clean_profile()
        self.is_member = self.is_valid_member
        
        # 如果是新用户且没有设置uuid，生成一个唯一的数字ID
        if self._state.adding and not self.uuid:
            while True:
                numeric_id = generate_numeric_id()
                if not type(self).objects.filter(uuid=numeric_id).exists():
                    self.uuid = numeric_id
                    break
                    
        super().save(*args, **kwargs)
    

    def generate_invite_code(self):
        """显式生成邀请码的方法"""
        if not self.invite_code or not self.is_invite_code_valid:
            while True:
                code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
                if not type(self).objects.filter(invite_code=code).exists():
                    self.invite_code = code
                    self.invite_code_expires = timezone.now() + timedelta(days=1)
                    self.save()
                    break
            return True
        return False
    
    @property
    def is_valid_member(self):
        """检查用户是否是有效会员"""
        now = timezone.now()
        if self.member_since and self.member_until:
            return self.member_since <= now <= self.member_until
        return False

    def set_member(self, days):
        """设置会员有效期
        
        Args:
            days (int): 会员有效天数
        """
        now = timezone.now()
        if not self.member_since or now > self.member_until:
            # 新会员或已过期会员
            self.member_since = now
            self.member_until = now + timezone.timedelta(days=days)
        else:
            # 续费会员，在原有结束时间基础上增加天数
            self.member_until += timezone.timedelta(days=days)
        
        self.save()  # 移除手动设置is_member，让save方法自动处理

    def check_member_status(self):
        """检查并更新会员状态"""
        self.is_member = self.is_valid_member
        self.save(update_fields=['is_member'])

    
    def force_logout_other_sessions(self):
        """强制登出其他设备"""
        from django.contrib.sessions.backends.db import SessionStore
        
        # 获取当前会话的key
        current_session_key = getattr(self, '_current_session_key', None)
        
        # 删除该用户的其他所有会话
        user_sessions = Session.objects.filter(
            expire_date__gt=timezone.now()
        )
        
        for session in user_sessions:
            if session.session_key != current_session_key:  # 排除当前会话
                try:
                    # 正确解析session数据
                    session_data = SessionStore().decode(session.session_data)
                    # 检查session中的user_id是否与当前用户匹配
                    if str(session_data.get('_auth_user_id')) == str(self.id):
                        session.delete()
                except:
                    continue


# ============================================
# Proxy Models - 用于 Admin 分组显示
# 不改变数据库表结构，仅用于 Admin 界面组织
# ============================================

from django.contrib.auth.models import Group

# 导入 allauth EmailAddress
try:
    from allauth.account.models import EmailAddress
    HAS_ALLAUTH = True
except ImportError:
    HAS_ALLAUTH = False
    EmailAddress = None


class UserGroup(Group):
    """
    用户组代理模型
    - 不创建新表，使用 auth_group 表
    - 在 Admin 中显示为"用户组管理"
    """
    class Meta:
        proxy = True
        verbose_name = '用户组'
        verbose_name_plural = '用户组管理'
        # 注意：不要设置 app_label，保持原表所属


if HAS_ALLAUTH:
    class UserEmailAddress(EmailAddress):
        """
        邮件地址代理模型
        - 不创建新表，使用 account_emailaddress 表
        - 在 Admin 中显示为"邮箱管理"
        """
        class Meta:
            proxy = True
            verbose_name = '用户邮箱'
            verbose_name_plural = '邮箱管理'
            # 注意：不要设置 app_label，保持原表所属


# 注意：UserCTFData Proxy Model 在 admin.py 中定义
# 因为需要避免循环导入（public.models 依赖 oauth.models）
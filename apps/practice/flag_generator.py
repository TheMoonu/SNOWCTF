import random
import string
import hashlib
import hmac
from django.core.cache import cache
from django.conf import settings
import logging

logger = logging.getLogger('apps.practice')

# 延迟导入避免循环依赖
def get_container_cache():
    from practice.redis_cache import UserContainerCache
    return UserContainerCache

def generate_flag(challenge, user):
    """
    生成动态flag（随机性 + 可验证设计）- 支持多段flag
    
    核心设计理念：
    1. 每次生成不同的 flag：使用随机 nonce 确保唯一性
    2. 可验证性：flag = nonce + HMAC(SECRET + nonce + user + challenge)
    3. 防止伪造：攻击者无法在不知道 SECRET_KEY 的情况下生成有效 HMAC
    4. 多 flag 支持：根据 flag_count 生成多个独立的 flag
    
    安全措施：
    - Nonce：12位随机十六进制（确保唯一性）
    - HMAC：HMAC-SHA256(SECRET_KEY, nonce + challenge_id + user_id)
    - Flag 格式：flag{nonce_hmac}
    
    Args:
        challenge: 题目对象
        user: 用户对象
        
    Returns:
        list: 生成的flag列表（单个flag时仍返回列表）
    """
    # 静态 flag 直接返回（解析多个flag）
    if challenge.flag_type == 'STATIC':
        if not challenge.flag_template:
            return []
        # 按逗号分隔，去除空格
        flags = [f.strip() for f in challenge.flag_template.split(',') if f.strip()]
        return flags
    
    # 动态 flag 生成 - 根据 flag_count 生成多个
    flag_count = getattr(challenge, 'flag_count', 1)
    flag_count = max(1, min(flag_count, 10))  # 限制在1-10个之间
    
    user_id = str(user.id)
    challenge_id = str(challenge.id)
    secret_key = settings.SECRET_KEY.encode()
    
    flags = []
    for i in range(flag_count):
        # 每个 flag 生成独立的 nonce（12位十六进制）
        nonce = ''.join(random.choices('0123456789abcdef', k=12))
        
        # 生成 HMAC（包含索引以便验证时区分）
        hmac_data = f"{nonce}_{i}_{challenge_id}_{user_id}".encode()
        hmac_value = hmac.new(secret_key, hmac_data, hashlib.sha256).hexdigest()[:24]
        
        # 最终 flag 格式：flag{12位nonce_24位HMAC}
        # 例如：flag{a1b2c3d4e5f6_5418ce4d815c9f3a2b6d7e8f}
        flag = f'flag{{{nonce}_{hmac_value}}}'
        flags.append(flag)
    
    return flags

def get_or_generate_flag(challenge, user):
    """
    生成flag（随机性设计）- 支持多段flag
    
    设计理念：
    - 每次生成不同的 flags（使用随机 nonce）
    - 验证时通过 HMAC 校验，无需存储 flag
    - 支持多个 flag（通过 index 区分）
    
    Args:
        challenge: 题目对象
        user: 用户对象
        
    Returns:
        list: flag列表（为保持兼容性，单个flag也返回列表）
    """
    # 静态 flag 直接返回（解析多个）
    if challenge.flag_type == 'STATIC':
        return generate_flag(challenge, user)  # 使用统一的解析逻辑
    
    # 动态 flag 直接生成（随机）
    flags = generate_flag(challenge, user)
    flag_count = len(flags)
    logger.debug(
        f"为用户 {user.username} 生成 flag"
        f"（题目: {challenge.title}，数量: {flag_count}）"
    )
    
    return flags

def verify_flag_hash(flag, challenge, user):
    """
    验证 flag 的 HMAC 是否有效（随机性 + 可验证设计）- 支持多段flag
    
    验证流程：
    1. 提取 nonce 和 HMAC
    2. 尝试所有可能的 index，计算 HMAC(SECRET + nonce + index + user + challenge)
    3. 比对 HMAC 是否匹配，返回匹配的 index
    
    优势：
    - 每个 flag 都唯一（因为 nonce 随机）
    - 无需存储 flag 即可验证（通过 HMAC 重新计算）
    - 支持多个独立的 flag（通过 index 区分）
    
    Args:
        flag: 待验证的 flag (格式: flag{nonce_hmac})
        challenge: 题目对象
        user: 用户对象
        
    Returns:
        tuple: (is_valid, flag_index) 或 (False, -1)
    """
    try:
        # 解析 flag 格式: flag{nonce_hmac}
        if not flag.startswith('flag{') or not flag.endswith('}'):
            return (False, -1)
        
        content = flag[5:-1]  # 去掉 "flag{" 和 "}"
        parts = content.split('_')
        
        if len(parts) != 2:
            return (False, -1)
        
        nonce, submitted_hmac = parts
        
        # 验证长度：nonce=12位，hmac=24位
        if len(nonce) != 12 or len(submitted_hmac) != 24:
            return (False, -1)
        
        # 验证 nonce 是否为有效的十六进制
        try:
            int(nonce, 16)
        except ValueError:
            return (False, -1)
        
        # 获取题目的 flag 数量
        flag_count = getattr(challenge, 'flag_count', 1)
        flag_count = max(1, min(flag_count, 10))
        
        user_id = str(user.id)
        challenge_id = str(challenge.id)
        secret_key = settings.SECRET_KEY.encode()
        
        # 尝试所有可能的 index
        for index in range(flag_count):
            # 计算期望的 HMAC（包含 index）
            hmac_data = f"{nonce}_{index}_{challenge_id}_{user_id}".encode()
            expected_hmac = hmac.new(secret_key, hmac_data, hashlib.sha256).hexdigest()[:24]
            
            # 使用恒定时间比较防止时序攻击
            if hmac.compare_digest(submitted_hmac, expected_hmac):
                return (True, index)
        
        # 没有匹配的 index
        return (False, -1)
        
    except Exception as e:
        logger.error(f"验证 flag HMAC 时出错: {e}")
        return (False, -1)

def verify_flag(submitted_flag, challenge, user):
    """
    验证提交的flag（随机性 + 可验证设计）- 支持多段flag
    
    Args:
        submitted_flag: 用户提交的flag
        challenge: 题目对象
        user: 用户对象
        
    Returns:
        tuple: (is_correct, flag_index) - flag_index 为匹配的索引，从0开始
    """
    # 静态flag检查 - 支持多个flag
    if challenge.flag_type == 'STATIC':
        if not challenge.flag_template:
            return (False, -1)
        # 解析多个flag
        correct_flags = [f.strip() for f in challenge.flag_template.split(',') if f.strip()]
        # 检查是否匹配任意一个
        for index, correct_flag in enumerate(correct_flags):
            if submitted_flag == correct_flag:
                logger.info(
                    f"用户 {user.username} flag验证成功（静态）"
                    f"（题目: {challenge.title}，index: {index}）"
                )
                return (True, index)
        return (False, -1)
    
    # 动态flag检查 - 使用 HMAC 校验（随机性 + 可验证）
    # 直接验证提交的 flag HMAC 是否有效，并返回对应的 index
    is_valid, flag_index = verify_flag_hash(submitted_flag, challenge, user)
    
    if is_valid:
        logger.info(
            f"用户 {user.username} flag验证成功（动态）"
            f"（题目: {challenge.title}，index: {flag_index}）"
        )
        return (True, flag_index)
    
    return (False, -1)

def reset_flag(challenge, user):
    """
    重置用户的 flag（重新生成新的 flag）- 支持多段flag
    
    注意：由于 flag 使用随机 nonce 生成，
    每次调用都会生成完全不同的 flags。
    
    Args:
        challenge: 题目对象
        user: 用户对象
        
    Returns:
        list: 新生成的 flag 列表（与之前不同）
    """
    if challenge.flag_type == 'STATIC':
        # 静态flag直接返回解析后的列表
        return generate_flag(challenge, user)
    
    # 重新生成 flag（多个，每次都不同）
    new_flags = generate_flag(challenge, user)
    flag_count = len(new_flags)
    
    logger.info(
        f"为用户 {user.username} 重置 flag"
        f"（题目: {challenge.title}，数量: {flag_count}）"
    )
    
    return new_flags
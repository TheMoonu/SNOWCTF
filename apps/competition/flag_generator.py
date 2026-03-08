import random
import string
import hashlib
import hmac
from django.core.cache import cache
from competition.models import CheatingLog, Team,Submission
from django.utils import timezone
from django.conf import settings
import datetime
import logging

# 使用apps.competition作为logger名称，匹配settings.py中的配置
logger = logging.getLogger('apps.competition')

# 延迟导入避免循环依赖
def get_container_cache():
    from competition.redis_cache import UserContainerCache
    return UserContainerCache

def get_or_generate_flag(challenge, user, competition=None):
    """
    生成动态flag（随机性 + 可验证设计）
    
    核心设计理念：
    1. 每次生成不同的 flag：使用随机 nonce 确保唯一性
    2. 可验证性：flag = nonce + HMAC(SECRET + nonce + team/user + challenge + competition)
    3. 防止伪造：攻击者无法在不知道 SECRET_KEY 的情况下生成有效 HMAC
    4. 团队赛支持：同一团队的成员可以验证团队的 flag
    5. 跨比赛隔离：不同比赛使用不同的 flag
    
    安全措施：
    - Nonce：12位随机十六进制（确保唯一性）
    - HMAC：HMAC-SHA256(SECRET_KEY, nonce + competition_id + challenge_id + team/user_id)
    - Flag 格式：flag{nonce_hmac}
    
    Args:
        challenge: 题目对象
        user: 用户对象
        competition: 比赛对象（可选）
        
    Returns:
        str: 生成的flag
        
    Flag格式: flag{nonce_hmac}（每次都不同）
    """
    # 静态 flag 直接返回
    if challenge.flag_type == 'STATIC':
        return challenge.flag_template
    
    # 动态 flag 生成
    challenge_id = str(challenge.id)
    competition_id = str(competition.id) if competition else "practice"
    secret_key = settings.SECRET_KEY.encode()
    
    # 判断是团队赛还是个人赛
    identifier_id = str(user.id)  # 默认使用用户ID
    
    if competition and competition.competition_type == 'team':
        # 团队赛：查找用户所在的团队
        team = Team.objects.filter(
            members=user,
            competition=competition
        ).first()
        
        if team:
            identifier_id = str(team.id)  # 使用团队ID
            logger.debug(
                f"团队赛模式：用户 {user.username} 属于团队 {team.name} (ID: {identifier_id})"
            )
        else:
            # 用户未加入团队，使用用户ID（容错处理）
            logger.warning(
                f"用户 {user.username} 在团队赛中未加入团队，使用个人ID生成flag"
            )
    
    # 生成随机 nonce（12位十六进制，确保每次生成的 flag 都不同）
    nonce = ''.join(random.choices('0123456789abcdef', k=12))
    
    # 生成 HMAC（包含 nonce，使每个 flag 都可验证）
    # 攻击者无法在不知道 SECRET_KEY 的情况下伪造有效的 HMAC
    hmac_data = f"{nonce}_{competition_id}_{challenge_id}_{identifier_id}".encode()
    hmac_value = hmac.new(secret_key, hmac_data, hashlib.sha256).hexdigest()[:24]
    
    # 最终 flag 格式：flag{12位nonce_24位HMAC}
    # 例如：flag{a1b2c3d4e5f6_5418ce4d815c9f3a2b6d7e8f}
    # 每次生成都不同（因为 nonce 随机）
    flag = f'flag{{{nonce}_{hmac_value}}}'
    
    return flag

def verify_flag_hash(flag, challenge, user, competition=None, is_admin_test=False):
    """
    验证 flag 的 HMAC 是否有效（随机性 + 可验证设计）
    
    验证流程：
    1. 提取 nonce 和 HMAC
    2. 重新计算 HMAC(SECRET + nonce + team/user + challenge + competition)
    3. 比对 HMAC 是否匹配
    
    优势：
    - 每个 flag 都唯一（因为 nonce 随机）
    - 无需存储 flag 即可验证（通过 HMAC 重新计算）
    - 团队成员可以验证团队的 flag
    - 攻击者无法伪造 flag（需要 SECRET_KEY）
    - 管理员测试模式：可以验证任何团队/用户的 flag
    
    Args:
        flag: 待验证的 flag (格式: flag{nonce_hmac})
        challenge: 题目对象
        user: 用户对象
        competition: 比赛对象（可选）
        is_admin_test: 是否为管理员测试模式
        
    Returns:
        bool: HMAC 是否有效
    """
    try:
        # 解析 flag 格式: flag{nonce_hmac}
        if not flag.startswith('flag{') or not flag.endswith('}'):
            return False
        
        content = flag[5:-1]  # 去掉 "flag{" 和 "}"
        parts = content.split('_')
        
        if len(parts) != 2:
            return False
        
        nonce, submitted_hmac = parts
        
        # 验证长度：nonce=12位，hmac=24位
        if len(nonce) != 12 or len(submitted_hmac) != 24:
            return False
        
        # 验证 nonce 是否为有效的十六进制
        try:
            int(nonce, 16)
        except ValueError:
            return False
        
        # 重新计算 HMAC
        challenge_id = str(challenge.id)
        competition_id = str(competition.id) if competition else "practice"
        secret_key = settings.SECRET_KEY.encode()
        
        # 如果是管理员测试模式，尝试验证所有可能的团队/用户
        if is_admin_test and competition and competition.competition_type == 'team':
            # 获取该比赛的所有团队
            teams = Team.objects.filter(competition=competition)
            
            for team in teams:
                identifier_id = str(team.id)
                
                # 计算期望的 HMAC
                hmac_data = f"{nonce}_{competition_id}_{challenge_id}_{identifier_id}".encode()
                expected_hmac = hmac.new(secret_key, hmac_data, hashlib.sha256).hexdigest()[:24]
                
                # 使用恒定时间比较防止时序攻击
                if hmac.compare_digest(submitted_hmac, expected_hmac):
                    logger.info(f"管理员测试模式：验证成功（团队ID: {team.id}, 团队名: {team.name}）")
                    return True
            
            # 也尝试用管理员自己的ID验证（个人赛模式）
            identifier_id = str(user.id)
            hmac_data = f"{nonce}_{competition_id}_{challenge_id}_{identifier_id}".encode()
            expected_hmac = hmac.new(secret_key, hmac_data, hashlib.sha256).hexdigest()[:24]
            
            if hmac.compare_digest(submitted_hmac, expected_hmac):
                logger.info(f"管理员测试模式：验证成功（个人模式，用户ID: {user.id}）")
                return True
            
            return False
        
        # 正常模式：判断是团队赛还是个人赛
        identifier_id = str(user.id)  # 默认使用用户ID
        
        if competition and competition.competition_type == 'team':
            # 团队赛：查找用户所在的团队
            team = Team.objects.filter(
                members=user,
                competition=competition
            ).first()
            
            if team:
                identifier_id = str(team.id)  # 使用团队ID
        
        # 计算期望的 HMAC
        hmac_data = f"{nonce}_{competition_id}_{challenge_id}_{identifier_id}".encode()
        expected_hmac = hmac.new(secret_key, hmac_data, hashlib.sha256).hexdigest()[:24]
        
        # 使用恒定时间比较防止时序攻击
        return hmac.compare_digest(submitted_hmac, expected_hmac)
        
    except Exception as e:
        logger.error(f"验证 flag HMAC 时出错: {e}")
        return False

def verify_flag(submitted_flag, challenge, user, competition, ip, is_admin_test=False, file_downloaded=False):
    """
    验证提交的flag并记录可疑行为（从容器缓存读取）
    
    Args:
        submitted_flag: 用户提交的flag
        challenge: 题目对象
        user: 提交用户
        competition: 比赛对象
        ip: 用户IP地址
        is_admin_test: 是否为管理员测试模式（True时会尝试验证所有团队的flag，跳过作弊检测）
        file_downloaded: 是否已下载文件（用于检测作弊）
    
    Returns:
        tuple: (是否正确, 错误信息)
    """
    # 管理员测试模式：跳过可疑活动检测
    if not is_admin_test:
        # 首先检查可疑活动
        is_suspicious, suspicious_type, description, should_reject = is_suspicious_activity(
            user, challenge, competition, ip, submitted_flag, file_downloaded
        )
        
        """ # 如果应该拒绝提交，直接返回
        if is_suspicious and should_reject:
            return False, "检测到异常行为，请稍后再试" """
    
    # 静态flag检查
    if challenge.flag_type == 'STATIC':
        result = (submitted_flag == challenge.flag_template)
        return result, None
    
    # 动态flag检查 - 使用 HMAC 校验（随机性 + 可验证）
    # 设计理念：
    # 1. 随机性：每次生成不同的 flag（使用随机 nonce）
    # 2. 可验证：通过 HMAC 验证，无需存储 flag
    # 3. 团队赛支持：团队成员可以验证团队创建的 flag
    # 4. 跨比赛隔离：不同比赛的 flag 无法互相验证
    # 5. 防止伪造：攻击者无法在不知道 SECRET_KEY 的情况下生成有效 flag
    # 6. 管理员测试：管理员可以验证任何团队的 flag
    result = verify_flag_hash(submitted_flag, challenge, user, competition, is_admin_test)
    
    # 管理员测试模式：跳过频率检查
    if not is_admin_test:
        # 简单的提交频率检查（作为备份）
        rate_limit_key = f"flag_submit_rate:{user.id}:{challenge.id}"
        submit_times = cache.get(rate_limit_key, 0)
        cache.set(rate_limit_key, submit_times + 1, 60)  # 60秒过期
    
    # 注意：Flag 验证使用 HMAC 校验，不需要缓存
    # flag 正确后也不需要特殊处理，容器会按过期时间自动清理
    
    if result:
        return True, None
    
    return False, "Flag不正确，请重新提交"

def is_suspicious_activity(user, challenge, competition, ip, submitted_flag=None, file_downloaded=False):
    """
    检查是否存在可疑活动并记录作弊行为
    
    Args:
        user: 用户对象
        challenge: 题目对象
        competition: 比赛对象 
        ip: 用户IP地址
        submitted_flag: 用户提交的flag内容
        file_downloaded: 是否已下载文件
        
    Returns:
        tuple: (是否可疑, 可疑类型, 描述信息, 是否应该拒绝提交)
    """
   
    
    # 获取用户所在的队伍
    team = None
    if competition and competition.competition_type == 'team':
        team = Team.objects.filter(
            members=user,
            competition=competition
        ).first()
    
    # 记录作弊行为的函数
    def record_cheating(cheating_type, description):
        CheatingLog.objects.create(
            user=user,
            team=team,
            competition=competition,
            cheating_type=cheating_type,
            description=description,
            detected_by="System"
        )
        
        # 检查是否应该拒绝提交
        recent_logs = CheatingLog.objects.filter(
            user=user,
            competition=competition,
            cheating_type=cheating_type,
            timestamp__gte=timezone.now() - timezone.timedelta(hours=1)
        ).count()
        
        should_reject = recent_logs >= 3 and cheating_type in ['bot', 'timing', 'exploit']
        return True, cheating_type, description, should_reject
    
    # 检查1: 最近的作弊记录
    
    
    # 检查2: IP异常检测（已优化，降低误判率）
    current_ip = ip
    if current_ip:
        # 获取用户最近的提交IP（扩大到24小时，更全面）
        recent_submissions = Submission.objects.filter(
            user=user,
            competition=competition,
            created_at__gte=timezone.now() - timezone.timedelta(hours=24)
        ).exclude(ip=None).order_by('-created_at')[:20]
        
        # 如果有历史提交记录，检查IP是否变化异常频繁
        if recent_submissions.exists():
            ips = set(sub.ip for sub in recent_submissions)
            
            # 阈值提高到6个IP（考虑到移动网络、WiFi切换等正常情况）
            # 且必须是完全不同的IP段（前3段不同）
            if len(ips) >= 6:
                different_segments = set()
                for ip_addr in ips:
                    # 比较前3段而不是前2段，更精确
                    segments = '.'.join(ip_addr.split('.')[:3])
                    different_segments.add(segments)
                
                # 如果有超过5个不同的IP段（C类网络），才认为异常
                if len(different_segments) >= 5:
                    description = f"用户{user.username}在24小时内使用了{len(ips)}个不同IP地址，涉及{len(different_segments)}个不同网段: {', '.join(list(ips)[:5])}..."
                    # 仅记录，不拒绝提交（可能是正常的网络切换）
                    logger.warning(description)
                    # return record_cheating('ipyichang', description)
    
    # 检查3: 提交频率异常（优化后的阈值）
    rate_limit_key = f"suspicious_submit_rate:{user.id}:{challenge.id}"
    submit_times = cache.get(rate_limit_key, [])
    current_time = timezone.now()
    
    # 清理旧的提交记录（超过5分钟的）
    submit_times = [t for t in submit_times if (current_time - t).total_seconds() < 300]
    
    # 提高阈值：5分钟内超过30次才认为异常（从15次提高到30次）
    # 考虑到用户可能需要多次尝试不同的payload
    if len(submit_times) >= 30:
        description = f"用户{user.username}在5分钟内提交了{len(submit_times)}次答案，可能存在暴力尝试行为"
        # 更新提交记录
        submit_times.append(current_time)
        cache.set(rate_limit_key, submit_times, 300)
        # 仅记录警告，不直接标记为作弊（可能是正常的多次尝试）
        logger.warning(description)
        # return record_cheating('timing', description)
    
    # 检查4: 可能的机器人行为（提交间隔过于规律）
    # 优化：需要更多样本才判断，且标准更严格
    if len(submit_times) >= 10:  # 从5次提高到10次
        intervals = [(submit_times[i] - submit_times[i-1]).total_seconds() 
                    for i in range(1, len(submit_times))]
        
        # 计算间隔标准差，如果过小则可能是机器人
        if intervals and len(intervals) >= 8:  # 至少8个间隔
            try:
                import numpy as np
                std_dev = np.std(intervals)
                mean_interval = np.mean(intervals)
                
                # 更严格的判断：标准差小于平均间隔的5%（从10%降低），且平均间隔小于3秒（从5秒降低）
                if std_dev < mean_interval * 0.05 and mean_interval < 3:
                    description = f"用户{user.username}提交间隔过于规律(平均{mean_interval:.2f}秒，标准差{std_dev:.2f})，可能使用了自动化工具"
                    # 更新提交记录
                    submit_times.append(current_time)
                    cache.set(rate_limit_key, submit_times, 300)
                    # 仅记录警告
                    logger.warning(description)
                    # return record_cheating('bot', description)
            except ImportError:
                # numpy未安装，跳过机器人检测
                pass
    
    # 检查5: 使用他人Flag（仅针对动态flag）
    # 重要：静态flag不进行此检查，避免误判
    if challenge.flag_type == 'DYNAMIC' and submitted_flag and '_' in submitted_flag:
        try:
            # 验证提交的 flag 是否为当前用户/团队和题目生成
            # 关键：必须传入 competition 参数，否则团队赛会验证失败
            if not verify_flag_hash(submitted_flag, challenge, user, competition):
                # 哈希验证失败，说明不是为当前用户/团队生成的 flag
                
                # 进一步检查是否是有效的 flag 格式但用户不匹配
                if submitted_flag.startswith('flag{') and submitted_flag.endswith('}'):
                    flag_content = submitted_flag[5:-1]
                    parts = flag_content.split('_')
                    
                    # 如果是正确的格式（nonce_hmac），说明可能是他人的 flag
                    if len(parts) == 2 and len(parts[0]) == 12 and len(parts[1]) == 24:
                        # 验证nonce是否为有效的十六进制
                        try:
                            int(parts[0], 16)
                            # 格式正确但验证失败，说明是其他用户/团队的flag
                            description = f"用户 {user.username} 在题目 {challenge.title} 中使用了其他用户/团队的flag。提交的flag: {submitted_flag}"
                            return record_cheating('manual', description)
                        except ValueError:
                            # nonce格式错误，不是有效的动态flag
                            pass
        except Exception as e:
            # 如果解析出错，记录但不作为作弊（可能只是格式错误）
            logger.warning(f"检查 flag 时出错: {e}, flag: {submitted_flag[:20]}...")
    
    # 检查6: 文件下载检查（未下载文件就提交正确flag可能是作弊）
    # 注意：只有在题目有静态文件时才进行此检查
    has_static_file = bool(challenge.static_files or challenge.static_file_url)
    
    if has_static_file and not file_downloaded:
        # 用户没有下载文件就提交了flag
        # 如果flag是正确的，说明可能是作弊（从他人处获取flag）
        # 这里先记录，在flag验证正确后才真正判定为作弊
        
        # 验证flag是否正确
        is_flag_correct = False
        if challenge.flag_type == 'STATIC':
            is_flag_correct = (submitted_flag == challenge.flag_template)
        else:
            # 动态flag检查
            is_flag_correct = verify_flag_hash(submitted_flag, challenge, user, competition)
        
        # 如果flag正确但没有下载文件，记录为可疑
        if is_flag_correct:
            description = f"用户 {user.username} 在题目 {challenge.title} 中未下载文件就提交了正确的flag，可能从他人处获取答案。提交的flag: {submitted_flag[:20]}..."
            logger.warning(description)
            return record_cheating('file_not_downloaded', description)
    
    # 更新提交记录用于后续检查
    submit_times.append(current_time)
    cache.set(rate_limit_key, submit_times, 100)  # 5分钟过期
    
    # 无可疑活动
    return False, None, None, False
    
def reset_flag(challenge, user, competition=None):
    """
    重置用户的 flag（重新生成新的 flag）
    
    注意：由于 flag 使用随机 nonce 生成，
    每次调用都会生成完全不同的 flag。
    
    Args:
        challenge: 题目对象
        user: 用户对象
        competition: 比赛对象（可选）
        
    Returns:
        str: 新生成的 flag（与之前不同）
    """
    if challenge.flag_type == 'STATIC':
        return challenge.flag_template
    
    # 重新生成 flag（会得到不同的 flag，因为 nonce 随机）
    flag = get_or_generate_flag(challenge, user, competition)
    comp_name = competition.title if competition else "练习模式"
    logger.info(
        f"为用户 {user.username} 重置 flag"
        f"（比赛: {comp_name}, 题目: {challenge.title}）"
    )
    
    return flag
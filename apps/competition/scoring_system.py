"""
CTF比赛计分系统
=================

设计理念：
1. 动态分数：随解题人数增加而降低，鼓励快速解题
2. 血榜奖励：奖励前三名解题者（一血8%、二血5%、三血3%）
3. 时间奖励：所有人都有，早解题多得分（最多10%）
4. 公平性：确保分数分配合理，避免刷分和作弊

优化说明：
- 移除了快速解题奖励，避免与时间奖励重复
- 时间奖励从5%提升到10%，补偿移除的快速奖励
- 简化计分逻辑，更容易理解和维护

Author: TheMoon
Version: 2.1
"""

import math
import logging
from typing import Tuple, Dict
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache

logger = logging.getLogger('apps.competition')


class CTFScoringSystem:
    """CTF比赛计分系统"""
    
    # 难度系数配置（影响动态分数衰减速度）
    DIFFICULTY_DECAY_RATES = {
        'Easy': 0.08,      # 简单题衰减快
        'Medium': 0.05,    # 中等题衰减中等
        'Hard': 0.03,      # 困难题衰减慢
    }
    
    # 血榜奖励比例（基于基础分数）
    BLOOD_BONUS_RATES = {
        1: 0.08,   # 一血：8%
        2: 0.05,   # 二血：5%
        3: 0.03,   # 三血：3%
    }
    
    # 时间奖励最大比例（已移除快速解题奖励以避免重复）
    MAX_TIME_BONUS_RATE = 0.10  # 提升到10%，因为移除了快速解题奖励

    @staticmethod
    @lru_cache(maxsize=2000)
    def _cached_calculate_dynamic_score(
        initial_points: int,
        minimum_points: int,
        current_solves: int,
        difficulty: str
    ) -> int:
        """
        带LRU缓存的动态分数计算（内部方法）
        
        优化说明：
        - 使用LRU缓存避免重复计算相同参数的分数
        - maxsize=2000 足够缓存大部分常见的分数组合
        - 对于高频查询（如排行榜刷新）有显著性能提升
        """
        # 验证难度字符串并获取衰减系数
        decay_rate = CTFScoringSystem.DIFFICULTY_DECAY_RATES.get(difficulty)
        
        if decay_rate is None:
            logger.warning(
                f"未知难度等级: '{difficulty}' (期望: Easy/Medium/Hard), "
                f"使用默认值Medium, initial_points={initial_points}, "
                f"current_solves={current_solves}"
            )
            decay_rate = CTFScoringSystem.DIFFICULTY_DECAY_RATES['Medium']
        
        # 计算动态分数
        score = (initial_points - minimum_points) / (1 + decay_rate * current_solves) + minimum_points
        
        # 向下取整并确保不低于最低分
        return max(int(score), minimum_points)
    
    @staticmethod
    def calculate_dynamic_score(
        initial_points: int,
        minimum_points: int,
        current_solves: int,
        difficulty: str = 'Medium'
    ) -> int:
        """
        计算动态分数（基础分）- 公开接口
        
        使用公式：
        score = (initial - minimum) / (1 + k * solves) + minimum
        
        其中 k 是衰减系数，根据难度不同而变化：
        - Easy: 衰减快，鼓励快速解题
        - Medium: 衰减中等
        - Hard: 衰减慢，因为本身就难
        
        Args:
            initial_points: 初始分数
            minimum_points: 最低分数
            current_solves: 当前解题人数
            difficulty: 题目难度
            
        Returns:
            当前动态分数
        """
        # ✅ 优化：使用带LRU缓存的内部方法
        return CTFScoringSystem._cached_calculate_dynamic_score(
            initial_points,
            minimum_points,
            current_solves,
            difficulty
        )
    
    @staticmethod
    def calculate_blood_bonus(
        base_score: int,
        solve_rank: int
    ) -> int:
        """
        计算血榜奖励
        
        只有前三名有血榜奖励：
        - 一血：基础分的 8%
        - 二血：基础分的 5%
        - 三血：基础分的 3%
        
        Args:
            base_score: 基础分数（动态分数）
            solve_rank: 解题排名（1=一血, 2=二血, 3=三血）
            
        Returns:
            血榜奖励分数
        """
        if solve_rank not in CTFScoringSystem.BLOOD_BONUS_RATES:
            return 0
        
        bonus_rate = CTFScoringSystem.BLOOD_BONUS_RATES[solve_rank]
        return int(base_score * bonus_rate)
    
    @staticmethod
    def calculate_time_bonus(
        base_score: int,
        time_elapsed: float,
        total_duration: float
    ) -> int:
        """
        计算时间奖励
        
        使用二次衰减函数，鼓励尽早解题：
        bonus = max_bonus * (1 - (time_ratio)^2)
        
        特点：
        - 0%时间：100%奖励
        - 50%时间：75%奖励
        - 75%时间：43.75%奖励
        - 100%时间：0%奖励
        
        Args:
            base_score: 基础分数
            time_elapsed: 已过时间（秒）
            total_duration: 比赛总时长（秒）
            
        Returns:
            时间奖励分数
        """
        if time_elapsed <= 0 or total_duration <= 0:
            return 0
        
        # 计算时间比例
        time_ratio = min(1.0, time_elapsed / total_duration)
        
        # 二次衰减
        remaining_ratio = 1.0 - (time_ratio ** 2)
        
        # 计算最大时间奖励
        max_bonus = int(base_score * CTFScoringSystem.MAX_TIME_BONUS_RATE)
        
        return int(max_bonus * remaining_ratio)
    
    
    @staticmethod
    def calculate_total_score(
        initial_points: int,
        minimum_points: int,
        current_solves: int,
        solve_rank: int,
        time_elapsed: float,
        total_duration: float,
        difficulty: str = 'Medium',
        include_time_bonus: bool = True
    ) -> Tuple[int, Dict[str, int]]:
        """
        计算总分数（包含所有奖励）
        
        总分 = 动态基础分 + 血榜奖励(前3名) + 时间奖励(所有人)
        
        优化说明：
        - 移除了快速解题奖励，避免与时间奖励重复
        - 时间奖励提升到10%，补偿移除的快速奖励
        - 血榜奖励只给前3名，更聚焦
        
        Args:
            initial_points: 初始分数
            minimum_points: 最低分数
            current_solves: 当前已解题人数（解题前的数量）
            solve_rank: 解题排名（1=第一个解出）
            time_elapsed: 从比赛开始到现在的时间（秒）
            total_duration: 比赛总时长（秒）
            difficulty: 题目难度
            include_time_bonus: 是否包含时间奖励
            
        Returns:
            (总分数, 分数明细字典)
        """
        # 1. 计算动态基础分
        base_score = CTFScoringSystem.calculate_dynamic_score(
            initial_points, 
            minimum_points, 
            current_solves,
            difficulty
        )
        
        # 2. 计算血榜奖励（前3名）
        blood_bonus = CTFScoringSystem.calculate_blood_bonus(base_score, solve_rank)
        
        # 3. 计算时间奖励（所有人，早解题多得分）
        time_bonus = 0
        if include_time_bonus:
            time_bonus = CTFScoringSystem.calculate_time_bonus(
                base_score,
                time_elapsed,
                total_duration
            )
        
        # 4. 计算总分
        total_score = base_score + blood_bonus + time_bonus
        
        # 5. 返回分数明细
        breakdown = {
            'base_score': base_score,           # 动态基础分
            'blood_bonus': blood_bonus,         # 血榜奖励（前3名）
            'time_bonus': time_bonus,           # 时间奖励（所有人）
            'total_score': total_score,         # 总分
        }
        
        return total_score, breakdown
    
    @staticmethod
    def get_score_preview(
        initial_points: int,
        minimum_points: int,
        difficulty: str = 'Medium',
        max_solves: int = 50
    ) -> list:
        """
        预览分数衰减曲线
        
        用于管理员查看题目分数随解题人数的变化趋势
        
        Args:
            initial_points: 初始分数
            minimum_points: 最低分数
            difficulty: 题目难度
            max_solves: 预览的最大解题人数
            
        Returns:
            分数预览列表 [(解题数, 分数), ...]
        """
        preview = []
        for solves in range(0, max_solves + 1):
            score = CTFScoringSystem.calculate_dynamic_score(
                initial_points,
                minimum_points,
                solves,
                difficulty
            )
            preview.append((solves, score))
        return preview
    
    @staticmethod
    def validate_score_config(
        initial_points: int,
        minimum_points: int
    ) -> Tuple[bool, str]:
        """
        验证分数配置是否合理
        
        规则：
        - 初始分数：200-1000分
        - 最低分数：不低于50分
        - 最低分数必须小于初始分数
        - 建议最低分数不低于初始分数的20%
        
        Args:
            initial_points: 初始分数
            minimum_points: 最低分数
            
        Returns:
            (是否有效, 错误信息)
        """
        # 验证初始分数范围
        if initial_points < 200:
            return False, "初始分数不能低于200分"
        
        if initial_points > 1000:
            return False, "初始分数不能超过1000分"
        
        # 验证最低分数
        if minimum_points < 50:
            return False, "最低分数不能低于50分"
        
        # 验证分数关系
        if minimum_points >= initial_points:
            return False, f"最低分数必须小于初始分数（当前初始分数：{initial_points}分）"
        
        # 建议性验证
        min_suggested = int(initial_points * 0.2)
        if minimum_points < min_suggested:
            return False, f"建议最低分数不低于初始分数的20%（建议最低：{min_suggested}分）"
        
        return True, ""


# 便捷函数
def calculate_ctf_score(
    initial_points: int,
    minimum_points: int,
    current_solves: int,
    solve_rank: int,
    time_elapsed: float,
    total_duration: float,
    difficulty: str = 'Medium'
) -> Tuple[int, Dict[str, int]]:
    """
    便捷函数：计算CTF题目得分
    
    这是对外的主要接口，封装了计分系统的复杂逻辑
    
    Args:
        initial_points: 初始分数
        minimum_points: 最低分数
        current_solves: 当前已解题人数
        solve_rank: 解题排名
        time_elapsed: 已过时间（秒）
        total_duration: 比赛总时长（秒）
        difficulty: 题目难度
        
    Returns:
        (总分数, 分数明细)
    
    Example:
        >>> total, breakdown = calculate_ctf_score(
        ...     initial_points=500,
        ...     minimum_points=100,
        ...     current_solves=0,
        ...     solve_rank=1,
        ...     time_elapsed=3600,
        ...     total_duration=86400,
        ...     difficulty='Medium'
        ... )
        >>> print(f"总分: {total}")
        >>> print(f"明细: {breakdown}")
    """
    return CTFScoringSystem.calculate_total_score(
        initial_points,
        minimum_points,
        current_solves,
        solve_rank,
        time_elapsed,
        total_duration,
        difficulty
    )


# 示例和测试
if __name__ == "__main__":
    print("=" * 60)
    print("CTF计分系统示例")
    print("=" * 60)
    
    # 示例1：一血解题（比赛开始1小时）
    print("\n示例1：一血解题（比赛开始1小时，中等难度500分题）")
    total, breakdown = calculate_ctf_score(
        initial_points=500,
        minimum_points=100,
        current_solves=0,
        solve_rank=1,
        time_elapsed=3600,      # 1小时
        total_duration=86400,   # 24小时
        difficulty='Medium'
    )
    print(f"基础分: {breakdown['base_score']}")
    print(f"血榜奖励(8%): {breakdown['blood_bonus']}")
    print(f"时间奖励(10%): {breakdown['time_bonus']}")
    print(f"总分: {breakdown['total_score']}")
    
    # 示例2：第10个解题（比赛进行12小时）
    print("\n示例2：第10个解题（比赛进行12小时）")
    total, breakdown = calculate_ctf_score(
        initial_points=500,
        minimum_points=100,
        current_solves=9,
        solve_rank=10,
        time_elapsed=43200,     # 12小时
        total_duration=86400,   # 24小时
        difficulty='Medium'
    )
    print(f"基础分: {breakdown['base_score']}")
    print(f"血榜奖励: {breakdown['blood_bonus']} (无，排名>3)")
    print(f"时间奖励: {breakdown['time_bonus']}")
    print(f"总分: {breakdown['total_score']}")
    
    # 示例3：第50个解题（比赛快结束）
    print("\n示例3：第50个解题（比赛进行23小时）")
    total, breakdown = calculate_ctf_score(
        initial_points=500,
        minimum_points=100,
        current_solves=49,
        solve_rank=50,
        time_elapsed=82800,     # 23小时
        total_duration=86400,   # 24小时
        difficulty='Medium'
    )
    print(f"基础分: {breakdown['base_score']}")
    print(f"血榜奖励: {breakdown['blood_bonus']} (无)")
    print(f"时间奖励: {breakdown['time_bonus']} (接近0)")
    print(f"总分: {breakdown['total_score']}")
    
    # 示例4：分数衰减预览
    print("\n示例4：分数衰减预览（前20个解题者）")
    preview = CTFScoringSystem.get_score_preview(500, 100, 'Medium', 20)
    print("解题数 | 基础分数")
    print("-" * 20)
    for solves, score in preview:
        print(f"{solves:6d} | {score:6d}")


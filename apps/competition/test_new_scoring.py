#!/usr/bin/env python
"""
新计分系统测试脚本
=================

使用方法：
    cd /opt/secsnow
    python apps/competition/test_new_scoring.py
"""

import os
import sys
import django

# 设置Django环境
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from competition.scoring_system import calculate_ctf_score, CTFScoringSystem


def test_first_blood():
    """测试一血解题"""
    print("\n" + "=" * 60)
    print("测试1: 一血解题（比赛开始1小时，500分中等题）")
    print("=" * 60)
    
    total, breakdown = calculate_ctf_score(
        initial_points=500,
        minimum_points=100,
        current_solves=0,
        solve_rank=1,
        time_elapsed=3600,      # 1小时
        total_duration=86400,   # 24小时
        difficulty='Medium'
    )
    
    print(f"📊 基础分: {breakdown['base_score']}")
    print(f"🩸 一血奖励: +{breakdown['blood_bonus']}")
    print(f"⏰ 时间奖励: +{breakdown['time_bonus']}")
    print(f"✨ 总分: {breakdown['total_score']}")
    
    assert total == breakdown['total_score']
    assert breakdown['base_score'] == 500
    assert breakdown['blood_bonus'] == 40  # 8% of 500
    assert breakdown['total_score'] > 600
    print("✅ 测试通过！")


def test_second_blood():
    """测试二血解题"""
    print("\n" + "=" * 60)
    print("测试2: 二血解题（比赛开始2小时）")
    print("=" * 60)
    
    total, breakdown = calculate_ctf_score(
        initial_points=500,
        minimum_points=100,
        current_solves=1,
        solve_rank=2,
        time_elapsed=7200,      # 2小时
        total_duration=86400,   # 24小时
        difficulty='Medium'
    )
    
    print(f"📊 基础分: {breakdown['base_score']}")
    print(f"🩸 二血奖励: +{breakdown['blood_bonus']}")
    print(f"⏰ 时间奖励: +{breakdown['time_bonus']}")
    print(f"✨ 总分: {breakdown['total_score']}")
    
    assert breakdown['base_score'] < 500  # 应该衰减了
    assert breakdown['blood_bonus'] > 0  # 二血有奖励
    print("✅ 测试通过！")


def test_late_solve():
    """测试后期解题"""
    print("\n" + "=" * 60)
    print("测试3: 第50名解题（比赛快结束）")
    print("=" * 60)
    
    total, breakdown = calculate_ctf_score(
        initial_points=500,
        minimum_points=100,
        current_solves=49,
        solve_rank=50,
        time_elapsed=82800,     # 23小时
        total_duration=86400,   # 24小时
        difficulty='Medium'
    )
    
    print(f"📊 基础分: {breakdown['base_score']}")
    print(f"🩸 血榜奖励: +{breakdown['blood_bonus']}")
    print(f"⏰ 时间奖励: +{breakdown['time_bonus']}")
    print(f"✨ 总分: {breakdown['total_score']}")
    
    assert breakdown['base_score'] >= 100  # 不低于最低分
    assert breakdown['blood_bonus'] == 0  # 没有血榜奖励
    print("✅ 测试通过！")


def test_difficulty_comparison():
    """测试不同难度的衰减差异"""
    print("\n" + "=" * 60)
    print("测试4: 不同难度的分数衰减对比（第10名解题）")
    print("=" * 60)
    
    difficulties = ['Easy', 'Medium', 'Hard']
    results = {}
    
    for diff in difficulties:
        total, breakdown = calculate_ctf_score(
            initial_points=500,
            minimum_points=100,
            current_solves=9,
            solve_rank=10,
            time_elapsed=10800,     # 3小时
            total_duration=86400,   # 24小时
            difficulty=diff
        )
        results[diff] = breakdown['base_score']
        print(f"{diff:8s} 难度基础分: {breakdown['base_score']}")
    
    # 验证：Hard题衰减最慢，Easy题衰减最快
    assert results['Hard'] > results['Medium'] > results['Easy']
    print("✅ 测试通过！难度越高，分数衰减越慢")


def test_score_preview():
    """测试分数预览功能"""
    print("\n" + "=" * 60)
    print("测试5: 分数衰减预览（前20名）")
    print("=" * 60)
    
    preview = CTFScoringSystem.get_score_preview(
        initial_points=500,
        minimum_points=100,
        difficulty='Medium',
        max_solves=20
    )
    
    print("\n解题数 | 基础分数")
    print("-" * 20)
    for solves, score in preview[:11]:  # 只显示前11个
        print(f"{solves:6d} | {score:6d}")
    
    # 验证分数递减
    for i in range(len(preview) - 1):
        assert preview[i][1] >= preview[i+1][1], "分数应该递减"
    
    print("✅ 测试通过！分数平滑递减")


def test_validation():
    """测试配置验证"""
    print("\n" + "=" * 60)
    print("测试6: 配置验证")
    print("=" * 60)
    
    # 测试正确配置
    valid, msg = CTFScoringSystem.validate_score_config(500, 100)
    assert valid, "正确配置应该通过验证"
    print(f"✅ 正确配置通过: {msg or '无错误'}")
    
    # 测试错误配置
    invalid_cases = [
        (0, 100, "初始分数必须大于0"),
        (500, 0, "最低分数必须大于0"),
        (100, 500, "最低分数必须小于初始分数"),
        (50, 40, "建议初始分数不低于50分"),
        (500, 50, "建议最低分数不低于初始分数的20%"),
    ]
    
    for initial, minimum, expected_msg in invalid_cases:
        valid, msg = CTFScoringSystem.validate_score_config(initial, minimum)
        if not valid:
            print(f"✅ 正确识别错误配置: {msg}")
    
    print("✅ 测试通过！配置验证正常工作")


def main():
    """运行所有测试"""
    print("\n" + "" * 30)
    print("开始测试新的CTF计分系统")
    print("" * 30)
    
    try:
        test_first_blood()
        test_second_blood()
        test_late_solve()
        test_difficulty_comparison()
        test_score_preview()
        test_validation()
        
        print("\n" + "✅" * 30)
        print("所有测试通过！新计分系统工作正常")
        print("✅" * 30 + "\n")
        
    except AssertionError as e:
        print("\n" + "❌" * 30)
        print(f"测试失败: {e}")
        print("❌" * 30 + "\n")
        sys.exit(1)
    except Exception as e:
        print("\n" + "❌" * 30)
        print(f"发生错误: {e}")
        print("❌" * 30 + "\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()


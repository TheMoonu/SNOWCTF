"""
比赛综合排行榜信号处理器
自动触发数据分析和缓存
"""

import logging
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from competition.models import Competition, Submission

logger = logging.getLogger('apps.competition')




def trigger_competition_end_analysis(competition_id):
    """
    触发比赛结束分析（在比赛结束后调用）
    
    使用方法：
    1. 管理员在比赛管理页面点击"更新综合排行榜"按钮
    2. 或者使用 API: POST /api/combined-leaderboard/<slug>/update/
    3. 或者在 Django Shell 中手动调用此函数
    
    Args:
        competition_id: 比赛ID
    """
    try:
        from competition.models import Competition
        from django.utils import timezone
        
        # 检查比赛是否结束
        competition = Competition.objects.get(id=competition_id)
        if timezone.now() <= competition.end_time:
            logger.warning(f'[触发分析] 比赛尚未结束，无法计算综合排行榜: competition_id={competition_id}')
            return {'success': False, 'message': '比赛尚未结束，请等待比赛结束后再计算'}
        
        from easytask.tasks import analyze_combined_leaderboard
        
        logger.info(f'[触发分析] 比赛结束分析: competition_id={competition_id}')
        
        # 立即执行分析任务
        analyze_combined_leaderboard.apply_async(
            args=[competition_id],
            kwargs={'force': True}
        )
        
        return {'success': True, 'message': '已触发分析任务'}
        
    except Exception as e:
        logger.error(f'[触发分析] 触发失败: competition_id={competition_id}, error={e}', exc_info=True)
        return {'success': False, 'message': str(e)}

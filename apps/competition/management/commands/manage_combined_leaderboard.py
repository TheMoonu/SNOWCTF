"""
综合排行榜管理命令
用于计算、验证、修复综合排行榜
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from competition.models import Competition, LeaderboardCalculationTask
from competition.utils_optimized import CombinedLeaderboardCalculator
import logging

logger = logging.getLogger('apps.competition')


class Command(BaseCommand):
    help = '管理综合排行榜：计算、验证、修复'
    
    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            type=str,
            choices=['calculate', 'verify', 'repair', 'status', 'clean'],
            help='操作类型：calculate(计算), verify(验证), repair(修复), status(查看状态), clean(清理旧任务)'
        )
        
        parser.add_argument(
            '--competition-id',
            type=int,
            help='竞赛ID（必须）'
        )
        
        parser.add_argument(
            '--force',
            action='store_true',
            help='强制重新计算（忽略缓存）'
        )
        
        parser.add_argument(
            '--clean-days',
            type=int,
            default=30,
            help='清理多少天前的任务记录（默认30天）'
        )
    
    def handle(self, *args, **options):
        action = options['action']
        competition_id = options.get('competition_id')
        force = options.get('force', False)
        
        # 除了clean操作，其他都需要competition_id
        if action != 'clean' and not competition_id:
            raise CommandError('必须指定 --competition-id')
        
        if action == 'calculate':
            self.calculate_leaderboard(competition_id, force)
        elif action == 'verify':
            self.verify_leaderboard(competition_id)
        elif action == 'repair':
            self.repair_leaderboard(competition_id)
        elif action == 'status':
            self.show_status(competition_id)
        elif action == 'clean':
            self.clean_old_tasks(options['clean_days'])
    
    def calculate_leaderboard(self, competition_id, force=False):
        """计算综合排行榜"""
        self.stdout.write(f'开始计算综合排行榜: competition_id={competition_id}')
        
        try:
            competition = Competition.objects.get(id=competition_id)
        except Competition.DoesNotExist:
            raise CommandError(f'竞赛不存在: id={competition_id}')
        
        if not competition.related_quiz:
            raise CommandError('该竞赛未关联知识竞赛')
        
        # 清除缓存（如果强制刷新）
        if force:
            CombinedLeaderboardCalculator.clear_cache(competition_id)
            self.stdout.write(self.style.SUCCESS('已清除缓存'))
        
        # 执行计算
        calculator = CombinedLeaderboardCalculator(competition, competition.related_quiz)
        result = calculator.calculate_leaderboard_with_lock(force=force)
        
        if result.get('success'):
            self.stdout.write(self.style.SUCCESS(
                f'✓ 计算完成！\n'
                f'  - 竞赛类型: {result.get("competition_type")}\n'
                f'  - 总数: {result.get("total_count")}\n'
                f'  - 是否最终: {result.get("is_final")}\n'
                f'  - 数据版本: {result.get("data_version")}'
            ))
        else:
            self.stdout.write(self.style.ERROR(
                f'✗ 计算失败: {result.get("message")}'
            ))
    
    def verify_leaderboard(self, competition_id):
        """验证排行榜数据"""
        self.stdout.write(f'开始验证排行榜: competition_id={competition_id}')
        
        result = CombinedLeaderboardCalculator.verify_and_repair_leaderboard(competition_id)
        
        if result.get('success'):
            if result['issues_found'] == 0:
                self.stdout.write(self.style.SUCCESS('✓ 数据验证通过，无问题'))
            else:
                self.stdout.write(self.style.WARNING(
                    f'发现 {result["issues_found"]} 个问题：\n' +
                    '\n'.join(f'  - {issue}' for issue in result['issues'][:10])
                ))
                if len(result['issues']) > 10:
                    self.stdout.write(f'  ... 还有 {len(result["issues"]) - 10} 个问题')
        else:
            self.stdout.write(self.style.ERROR(f'✗ 验证失败: {result.get("message")}'))
    
    def repair_leaderboard(self, competition_id):
        """修复排行榜数据"""
        self.stdout.write(f'开始修复排行榜: competition_id={competition_id}')
        
        result = CombinedLeaderboardCalculator.verify_and_repair_leaderboard(competition_id)
        
        if result.get('success'):
            if result['repairs_made'] == 0:
                self.stdout.write(self.style.SUCCESS('✓ 无需修复'))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f'✓ 修复完成，共修复 {result["repairs_made"]} 处：\n' +
                    '\n'.join(f'  - {repair}' for repair in result['repairs'][:10])
                ))
                if len(result['repairs']) > 10:
                    self.stdout.write(f'  ... 还有 {len(result["repairs"]) - 10} 处修复')
        else:
            self.stdout.write(self.style.ERROR(f'✗ 修复失败: {result.get("message")}'))
    
    def show_status(self, competition_id):
        """显示计算任务状态"""
        self.stdout.write(f'查询计算任务状态: competition_id={competition_id}')
        
        try:
            competition = Competition.objects.get(id=competition_id)
        except Competition.DoesNotExist:
            raise CommandError(f'竞赛不存在: id={competition_id}')
        
        # 获取最近的任务
        tasks = LeaderboardCalculationTask.objects.filter(
            competition=competition
        ).order_by('-created_at')[:10]
        
        if not tasks:
            self.stdout.write(self.style.WARNING('未找到任务记录'))
            return
        
        self.stdout.write(f'\n最近 {tasks.count()} 个计算任务：\n')
        
        for i, task in enumerate(tasks, 1):
            status_color = {
                'pending': self.style.WARNING,
                'running': self.style.NOTICE,
                'completed': self.style.SUCCESS,
                'failed': self.style.ERROR,
                'cancelled': self.style.WARNING,
            }.get(task.status, self.style.SUCCESS)
            
            self.stdout.write(
                f'{i}. [{status_color(task.get_status_display())}] '
                f'{task.competition_type} - '
                f'进度: {task.progress_percentage}% - '
                f'耗时: {task.duration_seconds:.1f}s - '
                f'创建于: {task.created_at.strftime("%Y-%m-%d %H:%M:%S")}'
            )
            
            if task.status == 'failed':
                self.stdout.write(f'   错误: {task.error_message[:100]}')
            elif task.status == 'completed':
                self.stdout.write(f'   结果: {task.result_count} 条记录')
    
    def clean_old_tasks(self, days):
        """清理旧的任务记录"""
        self.stdout.write(f'清理 {days} 天前的任务记录...')
        
        from datetime import timedelta
        cutoff_date = timezone.now() - timedelta(days=days)
        
        # 只清理已完成或失败的任务
        old_tasks = LeaderboardCalculationTask.objects.filter(
            created_at__lt=cutoff_date,
            status__in=['completed', 'failed', 'cancelled']
        )
        
        count = old_tasks.count()
        
        if count == 0:
            self.stdout.write(self.style.SUCCESS('无需清理'))
            return
        
        # 确认清理
        self.stdout.write(self.style.WARNING(f'将要删除 {count} 条任务记录'))
        confirm = input('确认删除？(yes/no): ')
        
        if confirm.lower() == 'yes':
            old_tasks.delete()
            self.stdout.write(self.style.SUCCESS(f'✓ 已删除 {count} 条记录'))
        else:
            self.stdout.write('取消操作')


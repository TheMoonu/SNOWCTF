"""
综合排行榜诊断命令
用于快速诊断为什么没有数据
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.db import connection
from competition.models import Competition, CombinedLeaderboard, ScoreUser, ScoreTeam
import sys


class Command(BaseCommand):
    help = '诊断综合排行榜问题'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--competition-slug',
            type=str,
            help='竞赛 slug'
        )
        
        parser.add_argument(
            '--list-competitions',
            action='store_true',
            help='列出所有关联了知识竞赛的比赛'
        )
        
        parser.add_argument(
            '--auto-calculate',
            action='store_true',
            help='如果比赛结束且有CTF数据，自动计算排行榜'
        )
    
    def handle(self, *args, **options):
        if options['list_competitions']:
            self.list_competitions()
            return
        
        competition_slug = options.get('competition_slug')
        if not competition_slug:
            raise CommandError('必须指定 --competition-slug 或使用 --list-competitions')
        
        self.diagnose_competition(competition_slug, options['auto_calculate'])
    
    def list_competitions(self):
        """列出所有关联了知识竞赛的比赛"""
        competitions = Competition.objects.filter(
            related_quiz__isnull=False
        ).select_related('related_quiz')
        
        if not competitions:
            self.stdout.write(self.style.WARNING('没有找到关联了知识竞赛的比赛'))
            return
        
        self.stdout.write(self.style.SUCCESS(f'\n找到 {competitions.count()} 个关联了知识竞赛的比赛：\n'))
        
        for comp in competitions:
            is_ended = timezone.now() > comp.end_time
            status_text = '✓ 已结束' if is_ended else '进行中'
            status_color = self.style.SUCCESS if is_ended else self.style.WARNING
            
            self.stdout.write(
                f'ID: {comp.id:3d} | Slug: {comp.slug:20s} | {comp.title:30s} | '
                f'{status_color(status_text)} | 关联Quiz: {comp.related_quiz.title}'
            )
    
    def diagnose_competition(self, competition_slug, auto_calculate=False):
        """诊断指定比赛"""
        self.stdout.write(self.style.NOTICE(f'\n🔍 诊断比赛: {competition_slug}\n'))
        self.stdout.write('=' * 80 + '\n')
        
        # 1. 检查比赛是否存在
        try:
            competition = Competition.objects.get(slug=competition_slug)
            self.stdout.write(self.style.SUCCESS(f'✓ 比赛存在: {competition.title}'))
            self.stdout.write(f'  - ID: {competition.id}')
            self.stdout.write(f'  - 类型: {competition.get_competition_type_display()}')
            self.stdout.write(f'  - 开始时间: {competition.start_time.strftime("%Y-%m-%d %H:%M")}')
            self.stdout.write(f'  - 结束时间: {competition.end_time.strftime("%Y-%m-%d %H:%M")}')
        except Competition.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'✗ 比赛不存在: {competition_slug}'))
            return
        
        # 2. 检查是否关联知识竞赛
        self.stdout.write('')
        if not competition.related_quiz:
            self.stdout.write(self.style.ERROR('✗ 该比赛未关联知识竞赛'))
            self.stdout.write(self.style.WARNING('  解决方案: 在管理后台为该比赛关联知识竞赛'))
            return
        else:
            self.stdout.write(self.style.SUCCESS(f'✓ 已关联知识竞赛: {competition.related_quiz.title}'))
        
        # 3. 检查比赛是否结束
        self.stdout.write('')
        is_ended = timezone.now() > competition.end_time
        if is_ended:
            self.stdout.write(self.style.SUCCESS('✓ 比赛已结束，可以生成综合排行榜'))
        else:
            self.stdout.write(self.style.WARNING('⚠ 比赛尚未结束'))
            remaining = competition.end_time - timezone.now()
            days = remaining.days
            hours = remaining.seconds // 3600
            self.stdout.write(f'  还剩: {days}天 {hours}小时')
            self.stdout.write(self.style.WARNING('  注意: 只有比赛结束后才能查看综合排行榜'))
        
        # 4. 检查 CTF 数据
        self.stdout.write('')
        if competition.competition_type == 'individual':
            ctf_count = ScoreUser.objects.filter(competition=competition).count()
            self.stdout.write(f'CTF 参与者数量: {ctf_count}')
            if ctf_count > 0:
                top_scores = ScoreUser.objects.filter(
                    competition=competition
                ).order_by('-points')[:3]
                self.stdout.write('  前3名:')
                for i, score in enumerate(top_scores, 1):
                    self.stdout.write(f'    {i}. {score.user.username}: {score.points}分')
            else:
                self.stdout.write(self.style.WARNING('  ⚠ 没有 CTF 参与数据'))
        else:
            ctf_count = ScoreTeam.objects.filter(competition=competition).count()
            self.stdout.write(f'CTF 参与队伍数量: {ctf_count}')
            if ctf_count > 0:
                top_scores = ScoreTeam.objects.filter(
                    competition=competition
                ).order_by('-score')[:3]
                self.stdout.write('  前3名:')
                for i, score in enumerate(top_scores, 1):
                    self.stdout.write(f'    {i}. {score.team.name}: {score.score}分')
            else:
                self.stdout.write(self.style.WARNING('  ⚠ 没有 CTF 参与数据'))
        
        # 5. 检查知识竞赛数据
        self.stdout.write('')
        from quiz.models import QuizRecord
        quiz_count = QuizRecord.objects.filter(
            quiz=competition.related_quiz,
            status='completed'
        ).count()
        self.stdout.write(f'知识竞赛完成人数: {quiz_count}')
        if quiz_count > 0:
            top_quiz = QuizRecord.objects.filter(
                quiz=competition.related_quiz,
                status='completed'
            ).order_by('-score')[:3]
            self.stdout.write('  前3名:')
            for i, record in enumerate(top_quiz, 1):
                self.stdout.write(f'    {i}. {record.user.username}: {record.score}分')
        else:
            self.stdout.write(self.style.WARNING('  ⚠ 没有知识竞赛完成记录'))
        
        # 6. 检查综合排行榜数据
        self.stdout.write('')
        combined_count = CombinedLeaderboard.objects.filter(competition=competition).count()
        if combined_count > 0:
            self.stdout.write(self.style.SUCCESS(f'✓ 综合排行榜已有数据: {combined_count} 条'))
            top_combined = CombinedLeaderboard.objects.filter(
                competition=competition
            ).order_by('rank')[:3]
            self.stdout.write('  前3名:')
            for item in top_combined:
                if competition.competition_type == 'individual':
                    name = item.user.username
                else:
                    name = item.team.name
                self.stdout.write(
                    f'    {item.rank}. {name}: '
                    f'综合分={item.combined_score:.2f} '
                    f'(CTF={item.ctf_score:.2f}, Quiz={item.quiz_score:.2f})'
                )
        else:
            self.stdout.write(self.style.ERROR('✗ 综合排行榜无数据'))
        
        # 7. 检查计算任务
        self.stdout.write('')
        try:
            from competition.models import LeaderboardCalculationTask
            tasks = LeaderboardCalculationTask.objects.filter(
                competition=competition
            ).order_by('-created_at')[:5]
            
            if tasks:
                self.stdout.write(f'最近的计算任务: {tasks.count()} 个')
                for i, task in enumerate(tasks, 1):
                    status_color = {
                        'completed': self.style.SUCCESS,
                        'failed': self.style.ERROR,
                        'running': self.style.WARNING,
                    }.get(task.status, self.style.NOTICE)
                    
                    self.stdout.write(
                        f'  {i}. [{status_color(task.get_status_display())}] '
                        f'{task.created_at.strftime("%Y-%m-%d %H:%M:%S")} - '
                        f'进度: {task.progress_percentage}%'
                    )
                    if task.status == 'failed':
                        self.stdout.write(f'     错误: {task.error_message[:80]}')
            else:
                self.stdout.write('没有计算任务记录')
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'⚠ 无法查询计算任务: {e}'))
            self.stdout.write(self.style.WARNING('  可能需要运行: python manage.py migrate competition'))
        
        # 8. 给出建议
        self.stdout.write('')
        self.stdout.write('=' * 80)
        self.stdout.write(self.style.NOTICE('\n💡 诊断结果和建议:\n'))
        
        if not is_ended:
            self.stdout.write(self.style.WARNING('❌ 比赛尚未结束，无法查看综合排行榜'))
            self.stdout.write(f'   请等待比赛结束后再访问: {competition.end_time.strftime("%Y-%m-%d %H:%M")}')
            return
        
        if ctf_count == 0:
            self.stdout.write(self.style.WARNING('❌ 没有 CTF 参与数据，无法生成综合排行榜'))
            return
        
        if quiz_count == 0:
            self.stdout.write(self.style.WARNING('⚠ 没有知识竞赛数据，综合排行榜将只包含 CTF 分数'))
        
        if combined_count == 0:
            self.stdout.write(self.style.ERROR('❌ 综合排行榜无数据'))
            self.stdout.write('\n建议执行以下命令生成数据:')
            self.stdout.write(self.style.SUCCESS(
                f'  python manage.py manage_combined_leaderboard calculate '
                f'--competition-id={competition.id} --force'
            ))
            
            if auto_calculate:
                self.stdout.write('\n' + '=' * 80)
                self.stdout.write(self.style.NOTICE(' 自动计算排行榜...\n'))
                
                from competition.utils_optimized import CombinedLeaderboardCalculator
                calculator = CombinedLeaderboardCalculator(competition, competition.related_quiz)
                
                try:
                    result = calculator.calculate_leaderboard_with_lock(force=True)
                    if result.get('success'):
                        self.stdout.write(self.style.SUCCESS(
                            f'\n✓ 计算成功！生成 {result.get("total_count")} 条记录'
                        ))
                    else:
                        self.stdout.write(self.style.ERROR(
                            f'\n✗ 计算失败: {result.get("message")}'
                        ))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'\n✗ 计算异常: {e}'))
                    import traceback
                    traceback.print_exc()
        else:
            self.stdout.write(self.style.SUCCESS('✓ 综合排行榜数据正常'))
            self.stdout.write(f'\n可以访问: /ctf/combined-rankings/{competition_slug}/')
        
        self.stdout.write('')


"""
Django管理命令：批量创建测试用户并自动报名比赛
用于并发测试容器创建接口

使用方法：
    # 创建用户并报名个人赛
    python manage.py create_test_users --competition test-comp --count 1000
    
    # 创建用户（不报名）
    python manage.py create_test_users --count 1000
    
    # 自定义参数
    python manage.py create_test_users --competition test-comp --count 500 --start 1 --password 123456
    
    # 删除所有测试用户
    python manage.py create_test_users --delete
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from oauth.models import Ouser
from competition.models import Competition, Registration
import logging

logger = logging.getLogger('apps.oauth')


class Command(BaseCommand):
    help = '批量创建测试用户（用于并发测试）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--count',
            type=int,
            default=1000,
            help='要创建的用户数量（默认: 1000）'
        )
        
        parser.add_argument(
            '--start',
            type=int,
            default=1,
            help='起始编号（默认: 1）'
        )
        
        parser.add_argument(
            '--password',
            type=str,
            default='test123456',
            help='用户密码（默认: test123456）'
        )
        
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='每批创建的用户数量（默认: 100）'
        )
        
        parser.add_argument(
            '--competition',
            type=str,
            default='',
            help='比赛slug（指定后自动报名该比赛）'
        )
        
        parser.add_argument(
            '--delete',
            action='store_true',
            help='删除所有测试用户（test开头的用户）'
        )
        
        parser.add_argument(
            '--email-domain',
            type=str,
            default='test.secsnow.local',
            help='邮箱域名（默认: test.secsnow.local）'
        )

    def handle(self, *args, **options):
        count = options['count']
        start = options['start']
        password = options['password']
        batch_size = options['batch_size']
        delete_mode = options['delete']
        email_domain = options['email_domain']
        competition_slug = options['competition']
        
        # 删除模式
        if delete_mode:
            self.delete_test_users()
            return
        
        # 验证比赛（如果指定了）
        competition = None
        if competition_slug:
            try:
                competition = Competition.objects.get(slug=competition_slug)
                self.stdout.write(self.style.SUCCESS(
                    f'✓ 找到比赛: {competition.title} (类型: {competition.get_competition_type_display()})'
                ))
                
                # 验证是否为个人赛
                if competition.competition_type != Competition.INDIVIDUAL:
                    self.stdout.write(self.style.WARNING(
                        f'⚠️  警告: 该比赛是团队赛，测试用户将以个人身份报名（不加入队伍）'
                    ))
                    
            except Competition.DoesNotExist:
                self.stdout.write(self.style.ERROR(
                    f'✗ 错误: 未找到比赛 "{competition_slug}"'
                ))
                return
        
        # 创建模式
        self.stdout.write(self.style.WARNING(
            f'\n开始批量创建测试用户...'
        ))
        self.stdout.write(f'  用户名范围: test{start} - test{start + count - 1}')
        self.stdout.write(f'  用户密码: {password}')
        self.stdout.write(f'  批量大小: {batch_size}')
        if competition:
            self.stdout.write(f'  自动报名: {competition.title}')
        self.stdout.write('')
        
        end = start + count
        created_count = 0
        skipped_count = 0
        registered_count = 0
        
        # 分批创建用户
        for batch_start in range(start, end, batch_size):
            batch_end = min(batch_start + batch_size, end)
            batch_created, batch_skipped = self.create_user_batch(
                batch_start, 
                batch_end, 
                password,
                email_domain
            )
            created_count += batch_created
            skipped_count += batch_skipped
            
            # 显示进度
            progress = ((batch_end - start) / count) * 100
            self.stdout.write(
                f'进度: {progress:.1f}% | 已创建: {created_count} | 已跳过: {skipped_count}',
                ending='\r'
            )
        
        self.stdout.write('\n')
        
        # 批量报名比赛
        if competition:
            self.stdout.write(self.style.WARNING('开始批量报名比赛...'))
            registered_count = self.register_users_to_competition(
                start, 
                end, 
                competition
            )
        
        # 完成提示
        self.stdout.write(self.style.SUCCESS(
            f'✓ 批量创建完成！'
        ))
        self.stdout.write(f'  成功创建: {created_count} 个用户')
        self.stdout.write(f'  跳过已存在: {skipped_count} 个用户')
        if competition:
            self.stdout.write(f'  成功报名: {registered_count} 个用户')
        self.stdout.write(f'  总计: {created_count + skipped_count} 个用户\n')
        
        # 测试建议
        self.stdout.write(self.style.WARNING('测试建议:'))
        self.stdout.write(f'  1. 登录测试: curl -X POST /api/login/ -d "username=test1&password={password}"')
        if competition:
            self.stdout.write(f'  2. 并发测试: locust -f locustfile.py --host=http://127.0.0.1:8000 --challenge-uuid=题目UUID')
        else:
            self.stdout.write(f'  2. 并发测试: 使用 locust 或 ab 工具进行压力测试')
        self.stdout.write(f'  3. 清理数据: python manage.py create_test_users --delete\n')

    def create_user_batch(self, start, end, password, email_domain):
        """批量创建一批用户"""
        users_to_create = []
        created_count = 0
        skipped_count = 0
        
        for i in range(start, end):
            username = f'test{i}'
            email = f'test{i}@{email_domain}'
            
            # 检查用户是否已存在
            if Ouser.objects.filter(username=username).exists():
                skipped_count += 1
                continue
            
            # 准备用户对象（不保存）
            user = Ouser(
                username=username,
                email=email,
                first_name='测试',
                last_name=f'用户{i}',
                is_active=True,
                is_staff=False,
                is_superuser=False,
            )
            
            # 设置密码
            user.set_password(password)
            
            users_to_create.append(user)
        
        # 批量创建用户
        if users_to_create:
            try:
                with transaction.atomic():
                    # 使用 bulk_create 批量插入
                    Ouser.objects.bulk_create(users_to_create, batch_size=100)
                    created_count = len(users_to_create)
                    
                    logger.info(f'成功创建 {created_count} 个测试用户（test{start}-test{end-1}）')
                    
            except Exception as e:
                self.stdout.write(self.style.ERROR(
                    f'✗ 创建用户失败（test{start}-test{end-1}）: {str(e)}'
                ))
                logger.error(f'批量创建用户失败: {str(e)}')
        
        return created_count, skipped_count

    def register_users_to_competition(self, start, end, competition):
        """批量报名用户到比赛"""
        registered_count = 0
        
        for i in range(start, end):
            username = f'test{i}'
            
            try:
                user = Ouser.objects.get(username=username)
                
                # 检查是否已报名
                if Registration.objects.filter(
                    competition=competition,
                    user=user
                ).exists():
                    continue
                
                # 创建报名记录（个人赛）
                registration = Registration.objects.create(
                    competition=competition,
                    user=user,
                    registration_type=Registration.INDIVIDUAL,
                    team_name=None  # 个人赛不需要队伍
                )
                
                registered_count += 1
                
                # 每100个显示一次进度
                if registered_count % 100 == 0:
                    self.stdout.write(
                        f'  已报名: {registered_count} 个用户',
                        ending='\r'
                    )
                    
            except Ouser.DoesNotExist:
                continue
            except Exception as e:
                logger.error(f'用户 {username} 报名失败: {str(e)}')
                continue
        
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'✓ 成功为 {registered_count} 个用户报名比赛'
        ))
        
        return registered_count

    def delete_test_users(self):
        """删除所有测试用户"""
        self.stdout.write(self.style.WARNING(
            '\n⚠️  即将删除所有测试用户（用户名以"test"开头的用户）'
        ))
        
        # 统计测试用户数量
        test_users = Ouser.objects.filter(username__startswith='test')
        count = test_users.count()
        
        if count == 0:
            self.stdout.write(self.style.SUCCESS('没有找到测试用户，无需删除。\n'))
            return
        
        self.stdout.write(f'找到 {count} 个测试用户')
        
        # 统计报名记录
        registration_count = Registration.objects.filter(
            user__username__startswith='test'
        ).count()
        if registration_count > 0:
            self.stdout.write(f'相关报名记录: {registration_count} 条')
        
        # 确认删除
        confirm = input('\n确认删除？(yes/no): ')
        
        if confirm.lower() != 'yes':
            self.stdout.write(self.style.WARNING('已取消删除操作。\n'))
            return
        
        # 执行删除
        try:
            with transaction.atomic():
                # 删除报名记录会随用户级联删除
                deleted_count, _ = test_users.delete()
                
                self.stdout.write(self.style.SUCCESS(
                    f'\n✓ 成功删除 {deleted_count} 个测试用户'
                ))
                if registration_count > 0:
                    self.stdout.write(f'✓ 相关报名记录已自动删除\n')
                
                logger.info(f'批量删除了 {deleted_count} 个测试用户')
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(
                f'\n✗ 删除失败: {str(e)}\n'
            ))
            logger.error(f'删除测试用户失败: {str(e)}')


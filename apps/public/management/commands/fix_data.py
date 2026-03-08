"""
Django 迁移文件修复命令
用于修复因删除并重新生成迁移文件导致的正式环境迁移问题

使用方法:
    python manage.py fix_data
    python manage.py fix_data --rollback backup_table_name  # 回滚到指定备份
    python manage.py fix_data --show-only  # 只查看当前状态
"""

from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from django.db import connection, transaction
from django.conf import settings
from datetime import datetime
import sys


class Command(BaseCommand):
    help = '修复Django迁移记录（因删除并重新生成迁移文件导致的问题）'

    # 需要修复的自定义app列表
    CUSTOM_APPS = [
        'comment',
        'competition',
        'container',
        'easytask',
        'oauth',
        'public',
        'logs',
        'blog',
        'practice',
        'rsshub',
        'tool',
        'vulnerability',
        'recruit',
        'quiz',
    ]
    
    # 需要同时清除的第三方应用（由于依赖关系）
    # 这些应用依赖于自定义app，需要一起处理
    THIRD_PARTY_APPS = [
        'account',  # django-allauth，依赖oauth
        'socialaccount',  # django-allauth social
    ]

    def add_arguments(self, parser):
        parser.add_argument(
            '--rollback',
            type=str,
            help='从指定的备份表恢复迁移记录',
        )
        parser.add_argument(
            '--show-only',
            action='store_true',
            help='只显示当前迁移状态，不执行修复',
        )
        parser.add_argument(
            '--no-backup',
            action='store_true',
            help='跳过备份步骤（不推荐）',
        )
        parser.add_argument(
            '--apps',
            type=str,
            help='只处理指定的app（逗号分隔），例如: --apps oauth,blog',
        )

    def print_header(self, message, style='SUCCESS'):
        """打印标题"""
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.WARNING(f"  {message}"))
        self.stdout.write("=" * 60)

    def backup_migrations_table(self):
        """备份 django_migrations 表"""
        self.print_header("步骤 1: 备份迁移记录表")
        
        backup_table_name = f"django_migrations_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        with connection.cursor() as cursor:
            try:
                # 检查表是否存在
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = 'django_migrations'
                    );
                """)
                
                if not cursor.fetchone()[0]:
                    self.stdout.write(self.style.ERROR("❌ django_migrations 表不存在"))
                    return None
                
                # 创建备份表
                cursor.execute(f"""
                    CREATE TABLE {backup_table_name} AS 
                    SELECT * FROM django_migrations;
                """)
                
                # 检查备份的记录数
                cursor.execute(f"SELECT COUNT(*) FROM {backup_table_name};")
                count = cursor.fetchone()[0]
                
                self.stdout.write(self.style.SUCCESS(
                    f"✅ 成功备份 {count} 条迁移记录到表: {backup_table_name}"
                ))
                return backup_table_name
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"❌ 备份失败: {e}"))
                return None

    def show_current_migrations(self):
        """显示当前迁移状态"""
        self.print_header("当前迁移状态")
        
        try:
            # 显示第三方app和自定义app的迁移状态
            all_apps = list(self.THIRD_PARTY_APPS) + list(self.apps_to_process)
            call_command('showmigrations', *all_apps)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ 查看迁移状态失败: {e}"))

    def get_migration_records(self, app_label):
        """获取指定app的迁移记录"""
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT id, app, name, applied 
                FROM django_migrations 
                WHERE app = %s
                ORDER BY applied;
            """, [app_label])
            return cursor.fetchall()

    def clear_app_migrations(self, app_label):
        """清除指定app的迁移记录"""
        with connection.cursor() as cursor:
            try:
                # 获取当前记录数
                cursor.execute("""
                    SELECT COUNT(*) FROM django_migrations WHERE app = %s;
                """, [app_label])
                count = cursor.fetchone()[0]
                
                if count == 0:
                    self.stdout.write(f"  ℹ️  {app_label}: 没有迁移记录")
                    return True
                
                # 删除记录
                cursor.execute("""
                    DELETE FROM django_migrations WHERE app = %s;
                """, [app_label])
                
                self.stdout.write(self.style.SUCCESS(
                    f"  ✅ {app_label}: 清除了 {count} 条迁移记录"
                ))
                return True
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  ❌ {app_label}: 清除失败 - {e}"))
                return False

    def clear_all_migrations(self):
        """清除所有自定义app和相关第三方app的迁移记录"""
        self.print_header("步骤 2: 清除旧的迁移记录")
        
        # 先清除第三方应用（它们依赖于自定义app）
        self.stdout.write("\n[1/2] 清除第三方应用的迁移记录...")
        third_party_success = 0
        for app in self.THIRD_PARTY_APPS:
            if self.clear_app_migrations(app):
                third_party_success += 1
        
        # 再清除自定义应用
        self.stdout.write("\n[2/2] 清除自定义app的迁移记录...")
        custom_success = 0
        for app in self.apps_to_process:
            if self.clear_app_migrations(app):
                custom_success += 1
        
        total = len(self.THIRD_PARTY_APPS) + len(self.apps_to_process)
        success = third_party_success + custom_success
        
        self.stdout.write(self.style.SUCCESS(
            f"\n清除完成: {success}/{total} 个app处理成功 "
            f"(第三方: {third_party_success}/{len(self.THIRD_PARTY_APPS)}, "
            f"自定义: {custom_success}/{len(self.apps_to_process)})"
        ))
        return success == total

    def fake_migrate_app(self, app_label):
        """对指定app执行fake迁移"""
        try:
            self.stdout.write(f"  处理 {app_label}...", ending=" ")
            call_command('migrate', app_label, '--fake', verbosity=0)
            self.stdout.write(self.style.SUCCESS("✅"))
            return True
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ {e}"))
            return False

    def fake_all_migrations(self):
        """对所有app执行fake迁移（按依赖顺序）"""
        self.print_header("步骤 3: 伪造迁移记录（fake）")
        
        self.stdout.write("\n开始伪造迁移记录...")
        self.stdout.write("(这将标记所有迁移为已执行，但不实际执行SQL)\n")
        
        # 按依赖顺序处理：
        # 1. 先处理oauth（很多第三方app依赖它）
        # 2. 再处理第三方app（account, socialaccount等）
        # 3. 最后处理其他自定义app
        
        success_count = 0
        total_count = 0
        
        # [1/3] 先fake oauth（被依赖的基础app）
        if 'oauth' in self.apps_to_process:
            self.stdout.write("\n[1/3] 处理基础应用 (oauth)...")
            if self.fake_migrate_app('oauth'):
                success_count += 1
            total_count += 1
        
        # [2/3] 再fake第三方app
        self.stdout.write("\n[2/3] 处理第三方应用 (allauth等)...")
        for app in self.THIRD_PARTY_APPS:
            if self.fake_migrate_app(app):
                success_count += 1
            total_count += 1
        
        # [3/3] 最后fake其他自定义app
        self.stdout.write("\n[3/3] 处理其他自定义应用...")
        for app in self.apps_to_process:
            if app != 'oauth':  # oauth已经处理过了
                if self.fake_migrate_app(app):
                    success_count += 1
                total_count += 1
        
        self.stdout.write(self.style.SUCCESS(
            f"\n伪造完成: {success_count}/{total_count} 个app处理成功"
        ))
        return success_count == total_count

    def verify_migrations(self):
        """验证迁移状态"""
        self.print_header("步骤 4: 验证迁移状态")
        
        self.stdout.write("\n最终迁移状态:")
        try:
            # 显示所有处理过的app的迁移状态
            all_apps = list(self.THIRD_PARTY_APPS) + list(self.apps_to_process)
            call_command('showmigrations', *all_apps)
            
            # 统计每个app的迁移数
            self.stdout.write("\n统计信息:")
            self.stdout.write("\n第三方应用:")
            with connection.cursor() as cursor:
                for app in self.THIRD_PARTY_APPS:
                    cursor.execute("""
                        SELECT COUNT(*) FROM django_migrations WHERE app = %s;
                    """, [app])
                    count = cursor.fetchone()[0]
                    self.stdout.write(f"  {app:20} {count:3} 条迁移记录")
                
                self.stdout.write("\n自定义应用:")
                for app in self.apps_to_process:
                    cursor.execute("""
                        SELECT COUNT(*) FROM django_migrations WHERE app = %s;
                    """, [app])
                    count = cursor.fetchone()[0]
                    self.stdout.write(f"  {app:20} {count:3} 条迁移记录")
            
            return True
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ 验证失败: {e}"))
            return False

    def rollback_from_backup(self, backup_table_name):
        """从备份恢复"""
        self.print_header("回滚操作", style='ERROR')
        
        with connection.cursor() as cursor:
            try:
                # 检查备份表是否存在
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = %s
                    );
                """, [backup_table_name])
                
                if not cursor.fetchone()[0]:
                    raise CommandError(f"备份表 {backup_table_name} 不存在")
                
                # 清空当前表
                cursor.execute("TRUNCATE TABLE django_migrations;")
                
                # 从备份恢复
                cursor.execute(f"""
                    INSERT INTO django_migrations (id, app, name, applied)
                    SELECT id, app, name, applied FROM {backup_table_name};
                """)
                
                # 获取恢复的记录数
                cursor.execute(f"SELECT COUNT(*) FROM {backup_table_name};")
                count = cursor.fetchone()[0]
                
                self.stdout.write(self.style.SUCCESS(
                    f"✅ 已从备份表 {backup_table_name} 恢复 {count} 条迁移记录"
                ))
                return True
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"❌ 恢复失败: {e}"))
                return False

    def list_backups(self):
        """列出所有备份表"""
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_name LIKE 'django_migrations_backup_%'
                ORDER BY table_name DESC;
            """)
            backups = cursor.fetchall()
            
            if backups:
                self.stdout.write("\n可用的备份表:")
                for backup in backups:
                    self.stdout.write(f"  - {backup[0]}")
            else:
                self.stdout.write("\n没有找到备份表")

    def handle(self, *args, **options):
        """主处理函数"""
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.WARNING("  Django 迁移修复命令"))
        self.stdout.write(self.style.WARNING("  项目: secsnow"))
        self.stdout.write(self.style.WARNING(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"))
        self.stdout.write("=" * 60)

        # 处理apps参数
        if options['apps']:
            self.apps_to_process = [app.strip() for app in options['apps'].split(',')]
            # 验证app是否在CUSTOM_APPS中
            for app in self.apps_to_process:
                if app not in self.CUSTOM_APPS:
                    raise CommandError(f"无效的app: {app}。可用的app: {', '.join(self.CUSTOM_APPS)}")
        else:
            self.apps_to_process = self.CUSTOM_APPS

        self.stdout.write(f"\n将要处理的自定义app: {', '.join(self.apps_to_process)}")
        self.stdout.write(f"同时处理的第三方app: {', '.join(self.THIRD_PARTY_APPS)}")

        # 处理回滚操作
        if options['rollback']:
            backup_table = options['rollback']
            self.stdout.write(self.style.WARNING(f"\n⚠️  准备从备份表 {backup_table} 恢复迁移记录"))
            confirm = input("是否继续? (输入 yes 继续): ")
            if confirm.lower() != 'yes':
                self.stdout.write(self.style.ERROR("\n❌ 操作已取消"))
                return
            
            if self.rollback_from_backup(backup_table):
                self.verify_migrations()
            return

        # 只显示状态
        if options['show_only']:
            self.show_current_migrations()
            self.list_backups()
            return

        # 执行修复操作
        self.stdout.write(self.style.WARNING("\n⚠️  警告: 此操作将修改数据库的迁移记录！"))
        self.stdout.write("   建议先在测试环境验证此命令")
        
        confirm = input("\n是否继续? (输入 yes 继续): ")
        if confirm.lower() != 'yes':
            self.stdout.write(self.style.ERROR("\n❌ 操作已取消"))
            return

        try:
            backup_table_name = None
            
            # 1. 备份（除非指定了--no-backup）
            if not options['no_backup']:
                backup_table_name = self.backup_migrations_table()
                if not backup_table_name:
                    raise CommandError("备份失败，操作中止")
            else:
                self.stdout.write(self.style.WARNING("\n⚠️  已跳过备份步骤"))
            
            # 2. 清除旧记录
            if not self.clear_all_migrations():
                self.stdout.write(self.style.WARNING("\n⚠️  部分app清除失败，但继续执行..."))
            
            # 3. 伪造迁移
            if not self.fake_all_migrations():
                self.stdout.write(self.style.WARNING("\n⚠️  部分app伪造失败，但继续验证..."))
            
            # 4. 验证结果
            self.verify_migrations()
            
            self.print_header("修复完成")
            self.stdout.write(self.style.SUCCESS("\n✅ 迁移修复完成！"))
            
            if backup_table_name:
                self.stdout.write(self.style.SUCCESS(f"\n备份表: {backup_table_name}"))
                self.stdout.write("\n如果出现问题，可以使用以下命令回滚:")
                self.stdout.write(self.style.WARNING(
                    f"  python manage.py fix_data --rollback {backup_table_name}"
                ))
            
            self.stdout.write("\n后续步骤:")
            self.stdout.write("  1. 检查上面的迁移状态，确保所有迁移都已标记为 [X]")
            self.stdout.write(f"     (包括第三方app: {', '.join(self.THIRD_PARTY_APPS)})")
            self.stdout.write("  2. 如果有新的迁移文件，运行: python manage.py migrate")
            self.stdout.write("  3. 测试应用功能是否正常（特别是用户认证和授权功能）")
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"\n❌ 执行过程中出错: {e}"))
            if backup_table_name:
                self.stdout.write(self.style.WARNING(
                    f"\n可以使用以下命令回滚:\n  python manage.py fix_data --rollback {backup_table_name}"
                ))
            raise
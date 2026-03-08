"""
清理简历相关的数据库表

用于删除 resume app 的两个表：
- resume_resume (个人简历)
- resume_resumetemplate (简历模板)

使用方法：
python manage.py clean_resume_tables
python manage.py clean_resume_tables --dry-run  # 预览模式
"""
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = '删除简历相关的数据库表（resume_resume 和 resume_resumetemplate）'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='仅预览，不实际删除表',
        )
    
    def get_db_type(self):
        """获取数据库类型"""
        return connection.vendor
    
    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        db_type = self.get_db_type()
        
        # 要删除的表
        tables_to_drop = [
            'resume_resume',          # 个人简历表
            'resume_resumetemplate',  # 简历模板表
        ]
        
        with connection.cursor() as cursor:
            self.stdout.write(self.style.NOTICE(f'检查简历相关的表... (数据库类型: {db_type})'))
            
            # 检查表是否存在（兼容 MySQL 和 PostgreSQL）
            if db_type == 'postgresql':
                cursor.execute("""
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public'
                    AND table_name IN ('resume_resume', 'resume_resumetemplate')
                """)
            else:  # MySQL
                cursor.execute("""
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = DATABASE()
                    AND table_name IN ('resume_resume', 'resume_resumetemplate')
                """)
            
            existing_tables = [row[0] for row in cursor.fetchall()]
            
            if not existing_tables:
                self.stdout.write(self.style.WARNING('\n未找到简历相关的表，可能已经被删除'))
                return
            
            self.stdout.write(self.style.SUCCESS(f'\n找到 {len(existing_tables)} 个表：'))
            for table in existing_tables:
                # 获取表的行数
                try:
                    cursor.execute(f'SELECT COUNT(*) FROM `{table}`')
                    count = cursor.fetchone()[0]
                    self.stdout.write(f'  - {table} ({count} 条数据)')
                except:
                    self.stdout.write(f'  - {table}')
            
            if dry_run:
                self.stdout.write(self.style.WARNING('\n--- 预览模式（不会实际删除）---'))
                self.stdout.write('如需实际删除，请移除 --dry-run 参数')
                return
            
            # 确认删除
            self.stdout.write(self.style.WARNING('\n⚠️  警告：此操作将删除以上表及其所有数据，不可恢复！'))
            
            # 开始删除
            self.stdout.write(self.style.NOTICE('\n开始删除表...'))
            
            success_count = 0
            
            # 禁用外键检查（根据数据库类型）
            if db_type == 'postgresql':
                # PostgreSQL 不需要特别禁用外键检查，使用 CASCADE 即可
                pass
            else:  # MySQL
                cursor.execute('SET FOREIGN_KEY_CHECKS = 0')
            
            for table in existing_tables:
                try:
                    if db_type == 'postgresql':
                        # PostgreSQL 使用 CASCADE 删除
                        cursor.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
                    else:  # MySQL
                        cursor.execute(f'DROP TABLE IF EXISTS `{table}`')
                    success_count += 1
                    self.stdout.write(self.style.SUCCESS(f'  ✓ 已删除: {table}'))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'  ✗ 删除失败: {table} - {str(e)}'))
            
            # 恢复外键检查（仅 MySQL）
            if db_type == 'mysql':
                cursor.execute('SET FOREIGN_KEY_CHECKS = 1')
            
            # 删除迁移记录
            self.stdout.write(self.style.NOTICE('\n清理 django_migrations 中的记录...'))
            try:
                cursor.execute("DELETE FROM django_migrations WHERE app = 'resume'")
                deleted_count = cursor.rowcount
                if deleted_count > 0:
                    self.stdout.write(self.style.SUCCESS(f'  ✓ 已删除 {deleted_count} 条迁移记录'))
                else:
                    self.stdout.write('  - 没有需要清理的迁移记录')
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'  - 清理迁移记录失败: {str(e)}'))
            
            # 汇总
            self.stdout.write(self.style.SUCCESS(f'\n✓ 清理完成！'))
            self.stdout.write(f'  - 成功删除 {success_count} 个表')
            self.stdout.write(f'  - 清理了 resume app 的迁移记录')
            
            self.stdout.write(self.style.NOTICE('\n后续步骤：'))
            self.stdout.write('  1. 确保 settings.py 的 INSTALLED_APPS 中已移除 resume')
            self.stdout.write('  2. 删除 apps/resume 目录')
            self.stdout.write('  3. 删除相关的 URL 配置')


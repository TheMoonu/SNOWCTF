# -*- coding: utf-8 -*-
"""
EasyTask 管理工具 - 统一的管理命令

用法：
    python manage.py easytask_admin <子命令> [选项]

子命令：
    check           检查定时任务配置状态
    debug           诊断定时任务配置问题
    init            初始化定时任务开关配置
    fix-migration   修复迁移历史记录
    rebuild-tables  强制重建 Celery Results 表
    reset-all       完全重置迁移状态
    clean-017-018   清理数据库中的 0017 和 0018 迁移记录

示例：
    python manage.py easytask_admin check
    python manage.py easytask_admin fix-migration --auto
    python manage.py easytask_admin rebuild-tables
"""
from django.core.management.base import BaseCommand
from django.db import connection
from django.core.management import call_command
from django.conf import settings
from django.core.cache import cache
import os


class Command(BaseCommand):
    help = 'EasyTask 统一管理工具'

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest='subcommand', help='子命令')
        
        # check 子命令
        subparsers.add_parser('check', help='检查定时任务配置状态')
        
        # debug 子命令
        subparsers.add_parser('debug', help='诊断定时任务配置问题')
        
        # init 子命令
        subparsers.add_parser('init', help='初始化定时任务开关配置')
        
        # fix-migration 子命令
        fix_parser = subparsers.add_parser('fix-migration', help='修复迁移历史记录')
        fix_parser.add_argument('--auto', action='store_true', help='自动执行修复')
        
        # rebuild-tables 子命令
        subparsers.add_parser('rebuild-tables', help='强制重建 Celery Results 表')
        
        # reset-all 子命令
        subparsers.add_parser('reset-all', help='完全重置迁移状态')
        
        # clean-017-018 子命令
        subparsers.add_parser('clean-017-018', help='清理数据库中的 0017 和 0018 迁移记录')

    def handle(self, *args, **options):
        subcommand = options.get('subcommand')
        
        if not subcommand:
            self.print_help('manage.py', 'easytask_admin')
            return
        
        # 路由到对应的处理方法
        handler = getattr(self, f'handle_{subcommand.replace("-", "_")}', None)
        if handler:
            handler(options)
        else:
            self.stdout.write(self.style.ERROR(f'未知的子命令: {subcommand}'))

    # ==================== check 子命令 ====================
    def handle_check(self, options):
        """检查定时任务配置状态"""
        from easytask.models import ScheduledTaskSwitch
        
        self.stdout.write("=" * 70)
        self.stdout.write(self.style.SUCCESS("[诊断] 检查定时任务配置"))
        self.stdout.write("=" * 70)
        
        # 1. 检查数据库中的任务
        self.stdout.write("\n[步骤1] 数据库中的任务配置：")
        try:
            all_tasks = ScheduledTaskSwitch.objects.all()
            total = all_tasks.count()
            enabled = all_tasks.filter(enabled=True).count()
            
            self.stdout.write(f"  总任务数: {total}")
            self.stdout.write(f"  启用任务: {enabled}")
            self.stdout.write(f"  禁用任务: {total - enabled}")
            
            if total == 0:
                self.stdout.write(self.style.ERROR("\n[错误] 数据库中没有任务配置！"))
                self.stdout.write("  请运行: python manage.py easytask_admin init")
                return
            
            self.stdout.write("\n[启用的任务列表]")
            for task in all_tasks.filter(enabled=True):
                self.stdout.write(f"  - {task.task_name}: {task.display_name}")
                self.stdout.write(f"    任务路径: {task.get_task_path()}")
                self.stdout.write(f"    间隔: {task.interval_seconds}秒 ({task.schedule_info})")
                self.stdout.write(f"    过期: {task.expires_seconds}秒")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"[错误] 查询数据库失败: {e}"))
            import traceback
            traceback.print_exc()
            return
        
        # 2. 测试 get_dynamic_schedule
        self.stdout.write("\n[步骤2] 测试 get_dynamic_schedule() 方法：")
        try:
            schedule = ScheduledTaskSwitch.get_dynamic_schedule()
            
            if not schedule:
                self.stdout.write(self.style.ERROR("  [错误] get_dynamic_schedule() 返回空字典"))
            else:
                self.stdout.write(self.style.SUCCESS(f"  [成功] 生成了 {len(schedule)} 个任务配置"))
                
                for task_name, config in schedule.items():
                    self.stdout.write(f"\n  任务: {task_name}")
                    self.stdout.write(f"    task: {config['task']}")
                    self.stdout.write(f"    schedule: {config['schedule']}")
                    self.stdout.write(f"    options: {config.get('options', {})}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  [错误] 生成配置失败: {e}"))
            import traceback
            traceback.print_exc()
        
        # 3. 检查缓存
        self.stdout.write("\n[步骤3] 检查缓存状态：")
        try:
            update_flag = cache.get('scheduled_tasks_updated')
            self.stdout.write(f"  更新标志: {update_flag}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  [错误] 检查缓存失败: {e}"))
        
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write(self.style.SUCCESS("[完成] 诊断完成"))
        self.stdout.write("=" * 70)



    # ==================== fix-migration 子命令 ====================
    def handle_fix_migration(self, options):
        """修复迁移历史记录"""
        auto_fix = options.get('auto', False)
        
        self.stdout.write("=" * 70)
        self.stdout.write(self.style.SUCCESS("[修复] 修复迁移历史记录"))
        self.stdout.write("=" * 70)
        
        # 步骤0：删除错误的迁移文件
        self.stdout.write("\n[步骤0] 检查并删除错误的迁移文件...")
        self._remove_bad_migration_files()
        
        # 步骤1：显示当前状态
        self.stdout.write("\n[步骤1] 当前相关的迁移记录：")
        self._show_migration_records()
        
        # 步骤2：清理错误的迁移记录
        self.stdout.write(f"\n[步骤2] 删除错误的迁移记录...")
        deleted_count = self._clean_migration_records()
        
        if deleted_count == 0:
            self.stdout.write(self.style.WARNING("  [信息] 没有找到需要删除的迁移记录"))
        
        # 步骤3：检查表状态
        self.stdout.write(f"\n[步骤3] 检查数据库表状态...")
        taskresult_exists, groupresult_exists = self._check_tables_exist()
        
        if taskresult_exists:
            self.stdout.write(self.style.SUCCESS(f"  [成功] django_celery_results_taskresult 表存在"))
        else:
            self.stdout.write(self.style.ERROR(f"  [失败] django_celery_results_taskresult 表不存在"))
        
        if groupresult_exists:
            self.stdout.write(self.style.SUCCESS(f"  [成功] django_celery_results_groupresult 表存在"))
        else:
            self.stdout.write(self.style.ERROR(f"  [失败] django_celery_results_groupresult 表不存在"))
        
        # 步骤4：显示修复后的状态
        self.stdout.write(f"\n[步骤4] 修复后的迁移记录：")
        self._show_migration_records()
        
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write(self.style.SUCCESS("[完成] 清理完成"))
        self.stdout.write("=" * 70)
        
        # 自动修复模式
        if auto_fix:
            self.stdout.write("\n[自动修复] 开始自动修复...")
            
            if not taskresult_exists or not groupresult_exists:
                self.stdout.write("\n[重建] 重建数据库表...")
                self._recreate_tables()
                self.stdout.write(self.style.SUCCESS("  [成功] 表重建完成"))
            
            self.stdout.write("\n[更新] 更新迁移状态...")
            try:
                call_command('migrate', 'django_celery_results', '0016', fake=True, verbosity=0)
                self.stdout.write(self.style.SUCCESS("  [成功] django_celery_results 迁移状态已更新到 0016"))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"  [警告] 迁移状态更新失败: {e}"))
            
            self.stdout.write(self.style.SUCCESS("\n[完成] 自动修复完成"))
            self.stdout.write("\n[下一步] 现在可以运行：")
            self.stdout.write("   1. python manage.py makemigrations easytask")
            self.stdout.write("   2. python manage.py migrate easytask")
        else:
            self.stdout.write("\n[下一步] 下一步操作：")
            
            if not taskresult_exists or not groupresult_exists:
                self.stdout.write(self.style.WARNING("  [警告] 表不存在，运行自动修复："))
                self.stdout.write("   python manage.py easytask_admin fix-migration --auto")
            else:
                self.stdout.write(self.style.SUCCESS("  [成功] 表已存在，创建代理模型："))
                self.stdout.write("   1. python manage.py makemigrations easytask")
                self.stdout.write("   2. python manage.py migrate easytask")
        
        self.stdout.write("=" * 70)

    # ==================== rebuild-tables 子命令 ====================
    def handle_rebuild_tables(self, options):
        """强制重建 Celery Results 表"""
        self.stdout.write("=" * 70)
        self.stdout.write(self.style.WARNING("[强制重建] 开始强制重建 Celery Results 表"))
        self.stdout.write("=" * 70)
        
        with connection.cursor() as cursor:
            # 步骤1：删除所有相关的索引
            self.stdout.write("\n[步骤1] 删除所有相关索引...")
            cursor.execute("""
                SELECT indexname 
                FROM pg_indexes 
                WHERE indexname LIKE 'django_cele%' OR tablename LIKE 'django_celery_results%'
                ORDER BY indexname;
            """)
            
            indexes = cursor.fetchall()
            for (idx_name,) in indexes:
                try:
                    cursor.execute(f"DROP INDEX IF EXISTS {idx_name} CASCADE;")
                    self.stdout.write(f"  [删除] {idx_name}")
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"  [跳过] {idx_name}: {e}"))
            
            self.stdout.write(self.style.SUCCESS(f"[完成] 共删除 {len(indexes)} 个索引"))
            
            # 步骤2：删除所有相关的表
            self.stdout.write("\n[步骤2] 删除所有相关表...")
            tables_to_drop = [
                'django_celery_results_taskresult',
                'django_celery_results_groupresult',
                'django_celery_results_chordcounter'
            ]
            
            for table in tables_to_drop:
                try:
                    cursor.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")
                    self.stdout.write(f"  [删除] {table}")
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"  [错误] {table}: {e}"))
            
            self.stdout.write(self.style.SUCCESS("[完成] 表删除完成"))
            
            # 步骤3：重新创建表
            self.stdout.write("\n[步骤3] 创建 TaskResult 表...")
            self._recreate_tables()
            
            # 步骤4：验证表结构
            self.stdout.write("\n[步骤4] 验证表结构...")
            cursor.execute("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'django_celery_results_taskresult'
                ORDER BY ordinal_position;
            """)
            
            columns = cursor.fetchall()
            self.stdout.write("  TaskResult 表字段：")
            for col_name, col_type in columns:
                self.stdout.write(f"    - {col_name}: {col_type}")
            
            cursor.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name = 'django_celery_results_taskresult' 
                    AND column_name = 'date_started'
                );
            """)
            has_date_started = cursor.fetchone()[0]
            
            if has_date_started:
                self.stdout.write(self.style.SUCCESS("\n[验证] date_started 字段存在"))
            else:
                self.stdout.write(self.style.ERROR("\n[错误] date_started 字段不存在"))
        
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write(self.style.SUCCESS("[完成] 表重建完成"))
        self.stdout.write("=" * 70)
        self.stdout.write("\n[下一步] 运行以下命令：")
        self.stdout.write("  1. python manage.py migrate django_celery_results 0016 --fake")
        self.stdout.write("  2. python manage.py easytask_admin fix-migration --auto")
        self.stdout.write("  3. python manage.py makemigrations easytask")
        self.stdout.write("  4. python manage.py migrate easytask")
        self.stdout.write("=" * 70)

    # ==================== reset-all 子命令 ====================
    def handle_reset_all(self, options):
        """完全重置迁移状态"""
        self.stdout.write("=" * 70)
        self.stdout.write(self.style.WARNING("[重置] 完全重置迁移状态"))
        self.stdout.write("=" * 70)
        
        with connection.cursor() as cursor:
            # 步骤1: 删除所有相关的迁移记录
            self.stdout.write("\n[步骤1] 删除数据库中的迁移记录...")
            
            cursor.execute("""
                DELETE FROM django_migrations 
                WHERE app IN ('django_celery_results', 'easytask');
            """)
            deleted = cursor.rowcount
            self.stdout.write(self.style.SUCCESS(f"  [成功] 已删除 {deleted} 条迁移记录"))
            
            # 步骤2: 删除 django_celery_results 的表
            self.stdout.write("\n[步骤2] 删除 django_celery_results 表...")
            
            tables_to_drop = [
                'django_celery_results_taskresult',
                'django_celery_results_groupresult',
                'django_celery_results_chordcounter'
            ]
            
            for table in tables_to_drop:
                try:
                    cursor.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")
                    self.stdout.write(f"  [删除] {table}")
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"  [跳过] {table}: {e}"))
            
            # 步骤3: 删除 easytask 的表
            self.stdout.write("\n[步骤3] 删除 easytask 表...")
            
            cursor.execute("""
                SELECT tablename 
                FROM pg_tables 
                WHERE tablename LIKE 'easytask_%'
                ORDER BY tablename;
            """)
            
            easytask_tables = cursor.fetchall()
            for (table_name,) in easytask_tables:
                try:
                    cursor.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE;")
                    self.stdout.write(f"  [删除] {table_name}")
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"  [跳过] {table_name}: {e}"))
        
        # 步骤4: 删除 easytask 的迁移文件
        self.stdout.write("\n[步骤4] 删除 easytask 迁移文件...")
        
        migrations_dir = os.path.join(settings.BASE_DIR, 'apps', 'easytask', 'migrations')
        if os.path.exists(migrations_dir):
            for filename in os.listdir(migrations_dir):
                if filename.endswith('.py') and filename != '__init__.py':
                    file_path = os.path.join(migrations_dir, filename)
                    try:
                        os.remove(file_path)
                        self.stdout.write(f"  [删除] {filename}")
                    except Exception as e:
                        self.stdout.write(self.style.WARNING(f"  [跳过] {filename}: {e}"))
            
            pycache_dir = os.path.join(migrations_dir, '__pycache__')
            if os.path.exists(pycache_dir):
                import shutil
                shutil.rmtree(pycache_dir)
                self.stdout.write("  [删除] __pycache__")
        
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write(self.style.SUCCESS("[完成] 重置完成"))
        self.stdout.write("=" * 70)
        self.stdout.write("\n[下一步] 按顺序执行以下命令：")
        self.stdout.write("  1. python manage.py migrate django_celery_results")
        self.stdout.write("  2. python manage.py makemigrations easytask")
        self.stdout.write("  3. python manage.py migrate easytask")
        self.stdout.write("  4. 重启 Django 服务")
        self.stdout.write("=" * 70)

    # ==================== clean-017-018 子命令 ====================
    def handle_clean_017_018(self, options):
        """清理数据库中的 0017 和 0018 迁移记录"""
        self.stdout.write("=" * 70)
        self.stdout.write(self.style.WARNING("清理 0017 和 0018 迁移记录"))
        self.stdout.write("=" * 70)
        
        with connection.cursor() as cursor:
            # 检查当前记录
            cursor.execute("""
                SELECT name 
                FROM django_migrations 
                WHERE app = 'django_celery_results' AND name IN ('0017_delete_groupresult_delete_taskresult', '0018_groupresult_taskresult')
                ORDER BY name;
            """)
            records = cursor.fetchall()
            
            if not records:
                self.stdout.write(self.style.SUCCESS("\n数据库中没有 0017 和 0018 的迁移记录"))
            else:
                self.stdout.write(f"\n找到 {len(records)} 条迁移记录：")
                for (name,) in records:
                    self.stdout.write(f"  - {name}")
                
                # 删除记录
                cursor.execute("""
                    DELETE FROM django_migrations 
                    WHERE app = 'django_celery_results' AND name IN ('0017_delete_groupresult_delete_taskresult', '0018_groupresult_taskresult');
                """)
                
                self.stdout.write(self.style.SUCCESS(f"\n已删除 {len(records)} 条迁移记录"))
            
            # 检查 easytask 中是否有错误的依赖
            cursor.execute("""
                SELECT name 
                FROM django_migrations 
                WHERE app = 'easytask'
                ORDER BY name;
            """)
            easytask_records = cursor.fetchall()
            
            self.stdout.write(f"\neasytask 当前迁移记录：")
            if not easytask_records:
                self.stdout.write("  无迁移记录")
            else:
                for (name,) in easytask_records:
                    self.stdout.write(f"  - {name}")
        
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write(self.style.SUCCESS("清理完成"))
        self.stdout.write("=" * 70)
        self.stdout.write("\n现在 django_celery_results 的最新迁移是 0016")
        self.stdout.write("可以运行以下命令验证：")
        self.stdout.write("  python manage.py showmigrations django_celery_results")
        self.stdout.write("  python manage.py migrate")
        self.stdout.write("=" * 70)

    # ==================== 辅助方法 ====================
    def _remove_bad_migration_files(self):
        """删除错误的迁移文件"""
        easytask_migrations = os.path.join(settings.BASE_DIR, 'apps', 'easytask', 'migrations')
        bad_migration = os.path.join(easytask_migrations, '0007_groupresult_taskresult.py')
        
        if os.path.exists(bad_migration):
            try:
                os.remove(bad_migration)
                self.stdout.write(self.style.SUCCESS(f"  [成功] 已删除: {bad_migration}"))
                
                pyc_file = bad_migration + 'c'
                if os.path.exists(pyc_file):
                    os.remove(pyc_file)
                
                pycache_dir = os.path.join(easytask_migrations, '__pycache__')
                if os.path.exists(pycache_dir):
                    for filename in os.listdir(pycache_dir):
                        if filename.startswith('0007_groupresult_taskresult'):
                            os.remove(os.path.join(pycache_dir, filename))
                            
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"  [警告] 删除失败: {e}"))
        else:
            self.stdout.write(self.style.WARNING(f"  [信息] 未找到错误的迁移文件"))
    
    def _show_migration_records(self):
        """显示迁移记录"""
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT app, name, applied 
                FROM django_migrations 
                WHERE app IN ('easytask', 'django_celery_results')
                ORDER BY applied DESC
                LIMIT 10;
            """)
            
            migrations = cursor.fetchall()
            for app, name, applied in migrations:
                self.stdout.write(f"  - {app}.{name} (应用于: {applied})")
    
    def _clean_migration_records(self):
        """清理错误的迁移记录"""
        deleted_count = 0
        
        with connection.cursor() as cursor:
            migrations_to_delete = [
                ('easytask', '0007_groupresult_taskresult'),
                ('django_celery_results', '0017_delete_groupresult_delete_taskresult'),
                ('django_celery_results', '0018_groupresult_taskresult'),
            ]
            
            for app, name in migrations_to_delete:
                try:
                    cursor.execute("""
                        DELETE FROM django_migrations 
                        WHERE app = %s AND name = %s;
                    """, [app, name])
                    if cursor.rowcount > 0:
                        self.stdout.write(self.style.SUCCESS(f"  [成功] 已删除: {app}.{name}"))
                        deleted_count += cursor.rowcount
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"  [警告] 删除失败: {e}"))
        
        return deleted_count
    
    def _check_tables_exist(self):
        """检查表是否存在"""
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'django_celery_results_taskresult'
                );
            """)
            taskresult_exists = cursor.fetchone()[0]
            
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'django_celery_results_groupresult'
                );
            """)
            groupresult_exists = cursor.fetchone()[0]
            
            return taskresult_exists, groupresult_exists
    
    def _recreate_tables(self):
        """重建 django_celery_results 的表"""
        with connection.cursor() as cursor:
            # 先检查表是否存在
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'django_celery_results_taskresult'
                );
            """)
            taskresult_exists = cursor.fetchone()[0]
            
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'django_celery_results_groupresult'
                );
            """)
            groupresult_exists = cursor.fetchone()[0]
            
            # 如果表已存在，先删除
            if taskresult_exists or groupresult_exists:
                self.stdout.write("    表已存在，先删除...")
                if taskresult_exists:
                    cursor.execute("DROP TABLE IF EXISTS django_celery_results_taskresult CASCADE;")
                    self.stdout.write("    [删除] django_celery_results_taskresult")
                if groupresult_exists:
                    cursor.execute("DROP TABLE IF EXISTS django_celery_results_groupresult CASCADE;")
                    self.stdout.write("    [删除] django_celery_results_groupresult")
            
            # 创建 TaskResult 表
            self.stdout.write("    [创建] django_celery_results_taskresult")
            cursor.execute("""
                CREATE TABLE django_celery_results_taskresult (
                    id SERIAL PRIMARY KEY,
                    task_id VARCHAR(255) UNIQUE NOT NULL,
                    status VARCHAR(50) NOT NULL,
                    content_type VARCHAR(128),
                    content_encoding VARCHAR(64),
                    result TEXT,
                    date_done TIMESTAMP WITH TIME ZONE NOT NULL,
                    traceback TEXT,
                    meta TEXT,
                    task_args TEXT,
                    task_kwargs TEXT,
                    task_name VARCHAR(255),
                    worker VARCHAR(100),
                    date_created TIMESTAMP WITH TIME ZONE NOT NULL,
                    date_started TIMESTAMP WITH TIME ZONE,
                    periodic_task_name VARCHAR(255)
                );
            """)
            
            # 创建索引
            cursor.execute("CREATE INDEX django_celery_results_taskresult_task_id_idx ON django_celery_results_taskresult(task_id);")
            cursor.execute("CREATE INDEX django_celery_results_taskresult_status_idx ON django_celery_results_taskresult(status);")
            cursor.execute("CREATE INDEX django_celery_results_taskresult_date_done_idx ON django_celery_results_taskresult(date_done);")
            cursor.execute("CREATE INDEX django_celery_results_taskresult_date_created_idx ON django_celery_results_taskresult(date_created);")
            cursor.execute("CREATE INDEX django_celery_results_taskresult_task_name_idx ON django_celery_results_taskresult(task_name);")
            
            # 创建 GroupResult 表
            self.stdout.write("    [创建] django_celery_results_groupresult")
            cursor.execute("""
                CREATE TABLE django_celery_results_groupresult (
                    id SERIAL PRIMARY KEY,
                    group_id VARCHAR(255) UNIQUE NOT NULL,
                    date_created TIMESTAMP WITH TIME ZONE NOT NULL,
                    date_done TIMESTAMP WITH TIME ZONE NOT NULL,
                    content_type VARCHAR(128),
                    content_encoding VARCHAR(64),
                    result TEXT
                );
            """)
            
            # 创建 GroupResult 索引
            cursor.execute("CREATE INDEX django_celery_results_groupresult_group_id_idx ON django_celery_results_groupresult(group_id);")
            cursor.execute("CREATE INDEX django_celery_results_groupresult_date_created_idx ON django_celery_results_groupresult(date_created);")
            cursor.execute("CREATE INDEX django_celery_results_groupresult_date_done_idx ON django_celery_results_groupresult(date_done);")
            
            self.stdout.write(self.style.SUCCESS("    [成功] 表和索引创建完成"))


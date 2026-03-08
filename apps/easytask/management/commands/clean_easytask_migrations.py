# -*- coding: utf-8 -*-
"""
清理 easytask 的所有迁移记录

用法：
    python manage.py clean_easytask_migrations
"""
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = '清理 easytask 的所有迁移记录（不再使用 Proxy 模型）'

    def handle(self, *args, **options):
        self.stdout.write("=" * 70)
        self.stdout.write(self.style.WARNING("清理 easytask 迁移记录"))
        self.stdout.write("=" * 70)
        
        with connection.cursor() as cursor:
            # 检查当前记录
            cursor.execute("""
                SELECT name 
                FROM django_migrations 
                WHERE app = 'easytask'
                ORDER BY name;
            """)
            records = cursor.fetchall()
            
            if not records:
                self.stdout.write(self.style.SUCCESS("\n数据库中没有 easytask 的迁移记录"))
            else:
                self.stdout.write(f"\n找到 {len(records)} 条迁移记录：")
                for (name,) in records:
                    self.stdout.write(f"  - {name}")
                
                # 删除记录
                cursor.execute("""
                    DELETE FROM django_migrations 
                    WHERE app = 'easytask';
                """)
                
                self.stdout.write(self.style.SUCCESS(f"\n已删除 {len(records)} 条迁移记录"))
        
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write(self.style.SUCCESS("清理完成"))
        self.stdout.write("=" * 70)
        self.stdout.write("\n现在运行：")
        self.stdout.write("  python manage.py makemigrations easytask")
        self.stdout.write("  python manage.py migrate easytask")
        self.stdout.write("\n说明：")
        self.stdout.write("  - TaskResult 和 GroupResult 直接使用 django_celery_results 的模型")
        self.stdout.write("  - 不再创建 Proxy 模型，因此没有迁移依赖问题")
        self.stdout.write("  - 它们会显示在 'Celery Results' 应用下")
        self.stdout.write("=" * 70)


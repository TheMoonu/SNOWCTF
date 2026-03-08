"""
数据迁移命令：将旧的 FooterItem 数据迁移到新的 FooterColumn 和 FooterLink 模型
使用方法：python manage.py migrate_footer_data
"""
from django.core.management.base import BaseCommand
from django.db import transaction
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = '将旧的 FooterItem 数据迁移到新的 FooterColumn 和 FooterLink 模型'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='仅预览迁移，不实际执行',
        )
    
    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        
        try:
            # 动态导入，避免模型不存在的错误
            from public.models import FooterColumn, FooterLink
            
            # 检查旧模型是否存在
            try:
                from public.models import FooterItem
                old_items_exist = FooterItem.objects.exists()
            except:
                self.stdout.write(self.style.WARNING('旧的 FooterItem 模型不存在，无需迁移'))
                return
            
            if not old_items_exist:
                self.stdout.write(self.style.SUCCESS('没有需要迁移的旧数据'))
                return
            
            # 开始迁移
            self.stdout.write(self.style.NOTICE('开始迁移页脚数据...'))
            
            if dry_run:
                self.stdout.write(self.style.WARNING('--- 预览模式（不会实际修改数据）---'))
            
            with transaction.atomic():
                # 获取所有一级栏目（父级为空）
                old_columns = FooterItem.objects.filter(parent__isnull=True).order_by('order', 'id')
                
                migrated_columns = 0
                migrated_links = 0
                
                for old_column in old_columns:
                    self.stdout.write(f'\n处理栏目: {old_column.title}')
                    
                    # 创建新的栏目
                    if not dry_run:
                        new_column, created = FooterColumn.objects.get_or_create(
                            title=old_column.title,
                            defaults={
                                'order': old_column.order,
                                'is_active': old_column.is_active,
                            }
                        )
                        if created:
                            migrated_columns += 1
                            self.stdout.write(self.style.SUCCESS(f'  ✓ 创建栏目: {new_column.title}'))
                        else:
                            self.stdout.write(self.style.WARNING(f'  - 栏目已存在: {new_column.title}'))
                    else:
                        self.stdout.write(f'  [预览] 将创建栏目: {old_column.title}')
                        # 在预览模式下，使用临时对象
                        new_column = None
                    
                    # 迁移该栏目下的链接
                    old_links = old_column.children.all().order_by('order', 'id')
                    
                    for old_link in old_links:
                        if not dry_run:
                            if new_column:
                                new_link, created = FooterLink.objects.get_or_create(
                                    column=new_column,
                                    title=old_link.title,
                                    url=old_link.url,
                                    defaults={
                                        'url_type': old_link.url_type,
                                        'target': old_link.target,
                                        'order': old_link.order,
                                        'is_active': old_link.is_active,
                                    }
                                )
                                if created:
                                    migrated_links += 1
                                    self.stdout.write(self.style.SUCCESS(f'    ✓ 创建链接: {new_link.title} -> {new_link.url}'))
                                else:
                                    self.stdout.write(self.style.WARNING(f'    - 链接已存在: {new_link.title}'))
                        else:
                            self.stdout.write(f'    [预览] 将创建链接: {old_link.title} -> {old_link.url}')
                
                if dry_run:
                    self.stdout.write(self.style.WARNING('\n--- 预览模式结束 ---'))
                    self.stdout.write('如需实际执行迁移，请移除 --dry-run 参数')
                    # 回滚事务
                    transaction.set_rollback(True)
                else:
                    self.stdout.write(self.style.SUCCESS(f'\n✓ 迁移完成！'))
                    self.stdout.write(self.style.SUCCESS(f'  - 迁移栏目数: {migrated_columns}'))
                    self.stdout.write(self.style.SUCCESS(f'  - 迁移链接数: {migrated_links}'))
                    self.stdout.write(self.style.NOTICE('\n注意：旧数据仍然保留，您可以在确认无误后手动删除'))
                    self.stdout.write(self.style.WARNING('建议操作：先在后台检查新数据，确认无误后再删除旧的 FooterItem 数据'))
        
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'迁移失败: {str(e)}'))
            logger.exception('页脚数据迁移失败')
            raise


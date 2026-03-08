# -*- coding: utf-8 -*-
"""
导入工具数据到数据库的管理命令
使用方法: python manage.py import_tools [--force]
"""
from django.core.management.base import BaseCommand
from tool.models import ToolCategory, Tool
from tool.utils import IZONE_TOOLS


class Command(BaseCommand):
    help = '导入工具数据到数据库'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='强制覆盖现有数据（包括更新所有字段）',
        )

    def handle(self, *args, **options):
        force = options.get('force', False)
        
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(self.style.SUCCESS('开始导入工具数据到数据库...'))
        self.stdout.write(self.style.SUCCESS('=' * 60))
        
        # 定义分类信息和图标映射
        categories_info = {
            'office': {'name': '办公工具', 'order_num': 1, 'icon': 'fa fa-briefcase'},
            'auxiliary': {'name': '辅助工具', 'order_num': 2, 'icon': 'fa fa-wrench'},
            'develop': {'name': '开发工具', 'order_num': 3, 'icon': 'fa fa-code'},
            'web': {'name': '站长工具', 'order_num': 4, 'icon': 'fa fa-globe'},
        }
        
        # 图标路径到 Font Awesome 类名的映射
        icon_mapping = {
            'editor/images/logos/editormd-logo-96x96.png': 'fa fa-edit',
            'blog/img/word-cloud.png': 'fa fa-cloud',
            'tool/img/tax128.png': 'fa fa-calculator',
            'tool/img/query_ip.png': 'fa fa-map-marker',
            'tool/img/golang.png': 'fa fa-code',
            'blog/img/docker.png': 'fa fa-ship',
            'blog/img/html.png': 'fa fa-html5',
            'blog/img/chrome.png': 'fa fa-chrome',
            'blog/img/regex.png': 'fa fa-terminal',
            'blog/img/baidu-2.png': 'fa fa-search',
            'blog/img/map.png': 'fa fa-sitemap',
        }
        
        self.stdout.write('\n📂 第一步：创建或更新分类')
        self.stdout.write('-' * 60)
        
        # 创建或更新分类
        category_map = {}
        for key, info in categories_info.items():
            category, created = ToolCategory.objects.get_or_create(
                key=key,
                defaults={
                    'name': info['name'],
                    'order_num': info['order_num'],
                    'icon': info['icon'],
                    'is_active': True
                }
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f'  ✅ 创建分类: {category.name} ({key})'))
            else:
                if force:
                    # 强制模式下更新分类信息
                    category.name = info['name']
                    category.order_num = info['order_num']
                    category.icon = info['icon']
                    category.save()
                    self.stdout.write(self.style.WARNING(f'  🔄 更新分类: {category.name} ({key})'))
                else:
                    self.stdout.write(f'  ⏭️  跳过分类: {category.name} (已存在)')
            
            category_map[key] = category
        
        self.stdout.write('\n🔧 第二步：导入或更新工具')
        self.stdout.write('-' * 60)
        
        # 导入工具
        imported_count = 0
        updated_count = 0
        skipped_count = 0
        
        for category_key, category_data in IZONE_TOOLS.items():
            category = category_map.get(category_key)
            if not category:
                continue
            
            self.stdout.write(f'\n  📁 分类: {category.name}')
            
            tools_list = category_data.get('tools', [])
            
            for order_idx, tool_data in enumerate(tools_list, start=1):
                tool_name = tool_data['name']
                url_name = tool_data['url']
                
                # 将图片路径转换为 Font Awesome 图标
                original_icon = tool_data.get('img', '')
                icon = icon_mapping.get(original_icon, 'fa fa-link')
                
                # 检查工具是否已存在
                tool, created = Tool.objects.get_or_create(
                    url_name=url_name,
                    defaults={
                        'name': tool_name,
                        'description': tool_data['desc'],
                        'tool_type': 'internal',
                        'icon': icon,
                        'category': category,
                        'order_num': order_idx * 10,
                        'is_published': True,
                    }
                )
                
                if created:
                    imported_count += 1
                    self.stdout.write(self.style.SUCCESS(
                        f'    ✅ 导入: {tool_name} (序号: {order_idx * 10})'
                    ))
                else:
                    if force:
                        # 强制模式下更新已存在的工具
                        tool.name = tool_name
                        tool.description = tool_data['desc']
                        tool.icon = icon
                        tool.category = category
                        tool.order_num = order_idx * 10
                        tool.save()
                        updated_count += 1
                        self.stdout.write(self.style.WARNING(
                            f'    🔄 更新: {tool_name}'
                        ))
                    else:
                        skipped_count += 1
                        self.stdout.write(
                            f'    ⏭️  跳过: {tool_name} (已存在，使用 --force 强制更新)'
                        )
        
        # 统计信息
        self.stdout.write('\n')
        self.stdout.write('=' * 60)
        self.stdout.write(self.style.SUCCESS('✨ 导入完成！统计信息：'))
        self.stdout.write('=' * 60)
        self.stdout.write(self.style.SUCCESS(f'  ✅ 新增工具: {imported_count} 个'))
        if force:
            self.stdout.write(self.style.WARNING(f'  🔄 更新工具: {updated_count} 个'))
        else:
            self.stdout.write(f'  ⏭️  跳过工具: {skipped_count} 个 (已存在)')
        self.stdout.write(self.style.SUCCESS(f'  📊 数据库共有: {Tool.objects.count()} 个工具'))
        self.stdout.write(self.style.SUCCESS(f'  📁 数据库共有: {ToolCategory.objects.count()} 个分类'))
        self.stdout.write('=' * 60)
        
        if not force and skipped_count > 0:
            self.stdout.write(self.style.WARNING(
                '\n💡 提示: 使用 python manage.py import_tools --force 可以强制更新现有数据'
            ))


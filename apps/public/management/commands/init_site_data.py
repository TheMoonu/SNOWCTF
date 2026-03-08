# -*- coding: utf-8 -*-
"""
初始化网站配置和页脚数据（使用新模型）
使用方法：python manage.py init_site_data
"""
from django.core.management.base import BaseCommand
from django.conf import settings
from datetime import date
from public.models import (
    SiteSettings, 
    FooterColumn, 
    FooterLink, 
    HomePageConfig, 
    ServiceCard
)


class Command(BaseCommand):
    help = '初始化网站配置和页脚数据（从settings.py迁移）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='强制覆盖已存在的配置',
        )

    def handle(self, *args, **options):
        force = options['force']
        
        # 1. 初始化网站配置
        self.stdout.write(self.style.WARNING('\n[1/3] 初始化网站配置...'))
        
        existing_settings = SiteSettings.objects.filter(is_active=True).first()
        
        if existing_settings and not force:
            self.stdout.write(self.style.WARNING(
                f'  ⚠ 已存在配置: {existing_settings.site_name}'
            ))
            self.stdout.write(self.style.WARNING(
                '  如需覆盖，请使用 --force 参数'
            ))
        else:
            # 从 settings.py 读取配置
            site_settings = SiteSettings.objects.create(
                site_name='SECSNOW',
                site_description='SECSNOW 一个开源、共创、共享网络安全技术学习网站',
                site_keywords='secsnow,CTF竞赛、漏洞靶场、网络安全',
                site_create_date='2024-01-01',
                beian='',
                cnzz_code='',
                la51_code='',
                site_verification='',
                # 邮箱配置（默认不启用）
                email_enabled=False,
                email_host=getattr(settings, 'EMAIL_HOST', 'smtp.163.com'),
                email_port=getattr(settings, 'EMAIL_PORT', 465),
                email_host_user=getattr(settings, 'EMAIL_HOST_USER', ''),
                email_host_password=getattr(settings, 'EMAIL_HOST_PASSWORD', ''),
                email_use_ssl=getattr(settings, 'EMAIL_USE_SSL', True),
                email_from=getattr(settings, 'DEFAULT_FROM_EMAIL', 'SECSNOW'),
                # 第三方登录配置（默认不启用）
                github_login_enabled=False,
                is_active=True,
            )
            self.stdout.write(self.style.SUCCESS(
                f'  ✓ 网站配置已创建: {site_settings.site_name}'
            ))
        
        # 2. 初始化页脚栏目（使用新模型）
        self.stdout.write(self.style.WARNING('\n[2/3] 初始化页脚栏目...'))
        
        existing_columns = FooterColumn.objects.exists()
        
        if existing_columns and not force:
            self.stdout.write(self.style.WARNING(
                '  ⚠ 已存在页脚栏目配置'
            ))
            self.stdout.write(self.style.WARNING(
                '  如需覆盖，请使用 --force 参数'
            ))
        else:
            if force:
                # 清空现有数据（级联删除链接）
                FooterColumn.objects.all().delete()
                self.stdout.write(self.style.WARNING('  已清空现有页脚配置'))
            
            # 创建默认页脚栏目
            footer_data = [
            ]
            
            created_count = 0
            for column_data in footer_data:
                # 创建栏目（使用新模型）
                column = FooterColumn.objects.create(
                    title=column_data['title'],
                    order=column_data['order'],
                    is_active=True
                )
                
                # 创建链接（使用新模型）
                for i, link_data in enumerate(column_data['links']):
                    FooterLink.objects.create(
                        column=column,
                        title=link_data['title'],
                        url=link_data['url'],
                        url_type=link_data['url_type'],
                        target=link_data['target'],
                        order=i + 1,
                        is_active=True
                    )
                
                created_count += 1
                self.stdout.write(self.style.SUCCESS(
                    f'  ✓ 已创建栏目: {column.title} ({len(column_data["links"])} 个链接)'
                ))
            
            self.stdout.write(self.style.SUCCESS(
                f'\n✓ 共创建 {created_count} 个页脚栏目'
            ))
        
        # 3. 初始化首页内容（使用新模型）
        self.stdout.write(self.style.WARNING('\n[3/3] 初始化首页内容...'))
        
        # 检查是否已存在首页配置
        existing_homepage = HomePageConfig.objects.filter(is_active=True).first()
        
        if existing_homepage and not force:
            self.stdout.write(self.style.WARNING(
                f'  ⚠ 已存在首页配置'
            ))
            self.stdout.write(self.style.WARNING(
                '  如需覆盖，请使用 --force 参数'
            ))
        else:
            if force:
                # 清空所有首页配置
                HomePageConfig.objects.all().delete()
                ServiceCard.objects.all().delete()
                self.stdout.write(self.style.WARNING('  已清空现有首页配置'))
            
            # 创建首页配置（使用新模型）
            homepage = HomePageConfig.objects.create(
                main_title='小雪花安全实验室',
                main_subtitle='SECSNOW',
                main_description='一个致力于开源、共享、共创的网络安全、数据安全、人工智能安全研究的实验室，我们的使命：坚决保护国家网络安全。',
                service_badge='Get started',
                service_title='我们的服务与价值',
                service_description='伟大的服务源于对他人需求的深刻理解。',
                is_active=True
            )
            
            # 创建服务卡片（使用新模型）
            service_cards_data = [
                {
                    'title': 'CTF竞赛平台',
                    'description': '提供专业的CTF竞赛环境，支持多种题型，助力网络安全人才培养',
                    'order': 1
                },
                {
                    'title': '漏洞靶场',
                    'description': '真实的漏洞环境模拟，在实战中学习和提升安全技能',
                    'order': 2
                },
                {
                    'title': 'WIKI知识库',
                    'description': '共建共享的安全知识库，汇聚安全研究成果与经验',
                    'order': 3
                },
            ]
            
            # 批量创建服务卡片
            for card_data in service_cards_data:
                ServiceCard.objects.create(
                    title=card_data['title'],
                    description=card_data['description'],
                    order=card_data['order'],
                    is_active=True
                )
            
            self.stdout.write(self.style.SUCCESS(
                f'  ✓ 首页配置已创建，包含 {len(service_cards_data)} 个服务卡片'
            ))
        
        # 完成提示
        self.stdout.write(self.style.SUCCESS('\n' + '='*50))
        self.stdout.write(self.style.SUCCESS('✓ 数据初始化完成！'))
        self.stdout.write(self.style.SUCCESS('='*50))


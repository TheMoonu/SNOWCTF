"""
修复 Django Sites 站点数据
用于恢复被误删除的站点，防止系统500错误

使用方法：
python manage.py fix_site
python manage.py fix_site --domain=your-domain.com --name="Your Site Name"
"""
from django.core.management.base import BaseCommand
from django.contrib.sites.models import Site
from django.conf import settings


class Command(BaseCommand):
    help = '修复或创建 Django Sites 站点数据，防止因站点缺失导致的500错误'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--domain',
            type=str,
            default='localhost:8000',
            help='站点域名（默认：localhost:8000）',
        )
        parser.add_argument(
            '--name',
            type=str,
            default='本地开发站点',
            help='站点名称（默认：本地开发站点）',
        )
    
    def handle(self, *args, **options):
        domain = options['domain']
        name = options['name']
        
        # 获取配置的 SITE_ID
        site_id = getattr(settings, 'SITE_ID', 1)
        
        self.stdout.write(self.style.NOTICE(f'检查站点配置...'))
        self.stdout.write(f'  - SITE_ID: {site_id}')
        self.stdout.write(f'  - 域名: {domain}')
        self.stdout.write(f'  - 名称: {name}')
        
        try:
            # 尝试获取现有站点
            site = Site.objects.get(pk=site_id)
            self.stdout.write(self.style.SUCCESS(f'\n✓ 站点已存在'))
            self.stdout.write(f'  - ID: {site.id}')
            self.stdout.write(f'  - 域名: {site.domain}')
            self.stdout.write(f'  - 名称: {site.name}')
            
            # 询问是否需要更新
            self.stdout.write(self.style.WARNING('\n站点已存在，是否需要更新？'))
            self.stdout.write('如需更新，请使用后台管理界面修改')
            
        except Site.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'\n✗ 未找到 ID={site_id} 的站点'))
            self.stdout.write(self.style.NOTICE('开始创建新站点...'))
            
            # 创建新站点
            site = Site.objects.create(
                id=site_id,
                domain=domain,
                name=name
            )
            
            self.stdout.write(self.style.SUCCESS(f'\n✓ 站点创建成功！'))
            self.stdout.write(f'  - ID: {site.id}')
            self.stdout.write(f'  - 域名: {site.domain}')
            self.stdout.write(f'  - 名称: {site.name}')
            self.stdout.write(self.style.NOTICE('\n提示：请在后台管理中修改站点信息为正确的域名'))
        
        # 显示所有站点
        self.stdout.write(self.style.NOTICE('\n当前数据库中的所有站点：'))
        all_sites = Site.objects.all()
        if all_sites.exists():
            for s in all_sites:
                mark = '★' if s.id == site_id else ' '
                self.stdout.write(f'  {mark} [{s.id}] {s.domain} - {s.name}')
        else:
            self.stdout.write('  （无）')
        
        # 清除缓存
        Site.objects.clear_cache()
        self.stdout.write(self.style.SUCCESS('\n✓ 站点缓存已清除'))
        self.stdout.write(self.style.SUCCESS('✓ 修复完成！'))


"""
Django 管理命令：清除 K8s 服务缓存

使用方法：
    python manage.py clear_k8s_cache                    # 清除所有缓存
    python manage.py clear_k8s_cache --namespace NAME   # 清除指定命名空间缓存
    python manage.py clear_k8s_cache --all              # 强制清除所有

适用场景：
    - 修改了引擎网络策略配置后
    - 手动修改了 K8s 网络策略后
    - 遇到缓存不一致问题时
"""

from django.core.management.base import BaseCommand
from django.core.cache import cache
from container.k8s_service import K8sService
from container.models import DockerEngine


class Command(BaseCommand):
    help = '清除 K8s 服务缓存（网络策略、命名空间等）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--namespace',
            type=str,
            help='指定要清除缓存的命名空间',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='清除所有引擎的缓存',
        )

    def handle(self, *args, **options):
        namespace = options.get('namespace')
        clear_all = options.get('all')

        if namespace:
            # 清除指定命名空间
            self.stdout.write(f"清除命名空间缓存: {namespace}")
            K8sService.clear_all_caches(namespace=namespace)
            self.stdout.write(self.style.SUCCESS(f'✅ 成功清除命名空间 {namespace} 的缓存'))
        
        elif clear_all:
            # 清除所有 K8s 引擎的缓存
            self.stdout.write("清除所有 K8s 引擎缓存...")
            engines = DockerEngine.objects.filter(
                engine_type__in=['K8S', 'K3S'],
                is_active=True
            )
            
            count = 0
            for engine in engines:
                K8sService.clear_all_caches(namespace=engine.namespace)
                count += 1
                self.stdout.write(f"  - 已清除: {engine.name} ({engine.namespace})")
            
            self.stdout.write(self.style.SUCCESS(f'✅ 成功清除 {count} 个引擎的缓存'))
        
        else:
            # 显示帮助信息
            self.stdout.write(self.style.WARNING('请指定 --namespace 或 --all 参数'))
            self.stdout.write('\n使用示例:')
            self.stdout.write('  python manage.py clear_k8s_cache --namespace ctf-challenges')
            self.stdout.write('  python manage.py clear_k8s_cache --all')
            
            # 列出可用的命名空间
            engines = DockerEngine.objects.filter(
                engine_type__in=['K8S', 'K3S'],
                is_active=True
            )
            
            if engines:
                self.stdout.write('\n当前活跃的 K8s 引擎:')
                for engine in engines:
                    self.stdout.write(f'  - {engine.name}: {engine.namespace}')


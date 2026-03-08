"""
清理孤儿 NetworkPolicy（Pod 已删除但策略还在）

使用方法:
    python manage.py cleanup_orphan_netpol
"""
from django.core.management.base import BaseCommand
from container.k8s_service import K8sService
from container.models import DockerEngine


class Command(BaseCommand):
    help = '清理孤儿 NetworkPolicy（Pod 已删除但策略还在）'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--engine',
            type=str,
            help='指定引擎名称（可选，默认使用第一个 K8s 引擎）'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='仅查看，不实际删除'
        )
    
    def handle(self, *args, **options):
        engine_name = options.get('engine')
        dry_run = options.get('dry_run', False)
        
        # 获取 K8s 引擎
        try:
            if engine_name:
                engine = DockerEngine.objects.get(name=engine_name, engine_type='KUBERNETES')
            else:
                engine = DockerEngine.objects.filter(engine_type='KUBERNETES').first()
            
            if not engine:
                self.stdout.write(self.style.ERROR('❌ 未找到可用的 Kubernetes 引擎'))
                return
            
            self.stdout.write(f"🔍 使用引擎: {engine.name} ({engine.get_engine_type_display()})")
        except DockerEngine.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'❌ 引擎不存在: {engine_name}'))
            return
        
        # 创建 K8s 服务实例
        try:
            k8s_service = K8sService(engine)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ 初始化 K8s 服务失败: {str(e)}'))
            return
        
        if dry_run:
            self.stdout.write(self.style.WARNING('🔍 DRY-RUN 模式：仅查看，不实际删除\n'))
        
        # 执行清理
        self.stdout.write('开始扫描孤儿 NetworkPolicy...\n')
        
        try:
            result = k8s_service.cleanup_orphan_network_policies()
            
            if result['success']:
                self.stdout.write(self.style.SUCCESS(
                    f"\n✅ 清理完成:\n"
                    f"  总策略数: {result['total_policies']}\n"
                    f"  孤儿策略数: {result['orphan_policies']}\n"
                    f"  已删除: {len(result['deleted_policies'])}"
                ))
                
                if result['deleted_policies']:
                    self.stdout.write('\n已删除的策略:')
                    for policy_name in result['deleted_policies']:
                        self.stdout.write(f"  ✓ {policy_name}")
                
                if result['errors']:
                    self.stdout.write(self.style.WARNING('\n⚠️  部分策略删除失败:'))
                    for error in result['errors']:
                        self.stdout.write(f"  • {error}")
            else:
                self.stdout.write(self.style.ERROR('\n❌ 清理失败'))
                for error in result['errors']:
                    self.stdout.write(f"  • {error}")
        
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ 清理过程发生异常: {str(e)}'))


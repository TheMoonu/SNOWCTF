"""
令牌桶管理命令（生产环境使用）
"""
from django.core.management.base import BaseCommand
from apps.container.resource_reservation import ResourceReservationManager


class Command(BaseCommand):
    help = '管理容器创建令牌桶'

    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            type=str,
            choices=['status', 'reset'],
            help='操作: status(查看状态) 或 reset(重置令牌桶)'
        )

    def handle(self, *args, **options):
        action = options['action']

        if action == 'status':
            self.show_status()
        elif action == 'reset':
            self.reset_bucket()

    def show_status(self):
        """显示令牌桶状态"""
        status = ResourceReservationManager.get_reserved_resources()
        
        self.stdout.write("\n" + "="*70)
        self.stdout.write(self.style.SUCCESS("📊 令牌桶状态"))
        self.stdout.write("="*70)
        self.stdout.write(f"当前可用令牌: {status['available_tokens']:.2f} / {status['max_tokens']}")
        self.stdout.write(f"令牌使用率: {status['usage_percent']:.1f}%")
        self.stdout.write(f"补充速率: {status['refill_rate_per_sec']} 个/秒")
        
        # 计算恢复时间
        if status['available_tokens'] < status['max_tokens']:
            recovery_time = (status['max_tokens'] - status['available_tokens']) / status['refill_rate_per_sec']
            self.stdout.write(f"恢复至满状态需要: {recovery_time:.1f} 秒")
        else:
            self.stdout.write(self.style.SUCCESS("✅ 令牌桶已满"))
        
        self.stdout.write("="*70 + "\n")

    def reset_bucket(self):
        """重置令牌桶"""
        self.stdout.write(self.style.WARNING("\n⚠️  即将重置令牌桶到满状态"))
        self.stdout.write("这会将所有令牌重置为最大值，并清除所有预占记录")
        
        confirm = input("\n确认继续? [yes/no]: ")
        
        if confirm.lower() in ['yes', 'y']:
            ResourceReservationManager.reset_all_reservations()
            self.stdout.write(self.style.SUCCESS("\n✅ 令牌桶已重置\n"))
            self.show_status()
        else:
            self.stdout.write(self.style.WARNING("操作已取消\n"))


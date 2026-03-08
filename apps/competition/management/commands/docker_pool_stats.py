"""
Docker连接池统计命令
查看和管理Docker连接池状态
"""

from django.core.management.base import BaseCommand
from competition.docker_connection_pool import DockerConnectionPool
import json


class Command(BaseCommand):
    help = 'Docker连接池统计和管理'
    
    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            type=str,
            choices=['stats', 'close'],
            help='操作类型：stats(查看统计), close(关闭所有连接池)'
        )
        
        parser.add_argument(
            '--json',
            action='store_true',
            help='输出JSON格式'
        )
    
    def handle(self, *args, **options):
        action = options['action']
        output_json = options.get('json', False)
        
        if action == 'stats':
            self.show_stats(output_json)
        elif action == 'close':
            self.close_all_pools()
    
    def show_stats(self, output_json=False):
        """显示连接池统计"""
        stats = DockerConnectionPool.get_all_pools_stats()
        
        if output_json:
            self.stdout.write(json.dumps(stats, indent=2))
        else:
            if not stats:
                self.stdout.write(self.style.WARNING('没有活跃的连接池'))
                return
            
            self.stdout.write(
                self.style.SUCCESS(f'\n找到 {len(stats)} 个连接池：\n')
            )
            
            for i, pool_stat in enumerate(stats, 1):
                self.stdout.write(
                    f'{i}. Engine ID: {pool_stat["engine_id"]}'
                )
                self.stdout.write(f'   URL: {pool_stat["url"]}')
                self.stdout.write(
                    f'   连接数: {pool_stat["pool_size"]}/{pool_stat["max_size"]} '
                    f'(最小: {pool_stat["min_size"]})'
                )
                self.stdout.write(
                    f'   活跃连接: {pool_stat["active_connections"]}'
                )
                self.stdout.write(
                    f'   历史创建: {pool_stat["total_created"]}'
                )
                self.stdout.write(
                    f'   当前总数: {pool_stat["total_connections"]}'
                )
                
                # 健康度评估
                usage_rate = pool_stat["active_connections"] / pool_stat["max_size"]
                if usage_rate > 0.8:
                    self.stdout.write(
                        self.style.ERROR(f'   ⚠️  使用率过高: {usage_rate*100:.1f}%')
                    )
                elif usage_rate > 0.5:
                    self.stdout.write(
                        self.style.WARNING(f'   ⚠️  使用率较高: {usage_rate*100:.1f}%')
                    )
                else:
                    self.stdout.write(
                        self.style.SUCCESS(f'   ✓ 使用率正常: {usage_rate*100:.1f}%')
                    )
                
                self.stdout.write('')
    
    def close_all_pools(self):
        """关闭所有连接池"""
        self.stdout.write('正在关闭所有连接池...')
        
        pools = DockerConnectionPool.get_all_pools_stats()
        
        if not pools:
            self.stdout.write(self.style.WARNING('没有活跃的连接池'))
            return
        
        # 确认
        confirm = input(f'确认关闭 {len(pools)} 个连接池？(yes/no): ')
        
        if confirm.lower() != 'yes':
            self.stdout.write('操作已取消')
            return
        
        # 关闭所有池
        from competition.docker_connection_pool import DockerConnectionPool
        with DockerConnectionPool._pools_lock:
            for engine_id, pool in list(DockerConnectionPool._pools.items()):
                try:
                    pool.close_all()
                    self.stdout.write(
                        self.style.SUCCESS(f'✓ 已关闭Engine {engine_id}的连接池')
                    )
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f'✗ 关闭Engine {engine_id}失败: {e}')
                    )
            
            # 清空字典
            DockerConnectionPool._pools.clear()
        
        self.stdout.write(self.style.SUCCESS('\n所有连接池已关闭'))


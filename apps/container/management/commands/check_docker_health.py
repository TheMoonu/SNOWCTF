# -*- coding: utf-8 -*-
"""
Docker 引擎健康检查管理命令

用法：
    python manage.py check_docker_health

可配合 crontab 或 systemd timer 定时执行：
    */5 * * * * cd /opt/secsnow && python manage.py check_docker_health >> /var/log/docker_health.log 2>&1
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from container.models import DockerEngine
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = '检查所有 Docker 引擎的健康状态'

    def add_arguments(self, parser):
        parser.add_argument(
            '--engine-id',
            type=int,
            help='检查指定 ID 的 Docker 引擎',
        )
        parser.add_argument(
            '--timeout',
            type=int,
            default=10,
            help='健康检查超时时间（秒），默认 10 秒',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='显示详细信息',
        )

    def handle(self, *args, **options):
        engine_id = options.get('engine_id')
        timeout = options.get('timeout')
        verbose = options.get('verbose')
        
        self.stdout.write(
            self.style.NOTICE(
                f'\n{"="*60}\n'
                f'Docker 引擎健康检查\n'
                f'时间: {timezone.now().strftime("%Y-%m-%d %H:%M:%S")}\n'
                f'{"="*60}\n'
            )
        )
        
        try:
            if engine_id:
                # 检查单个引擎
                self._check_single_engine(engine_id, timeout, verbose)
            else:
                # 检查所有引擎
                self._check_all_engines(timeout, verbose)
                
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'\n✗ 健康检查失败: {str(e)}\n')
            )
            logger.error(f"健康检查命令执行失败: {str(e)}", exc_info=True)

    def _check_single_engine(self, engine_id, timeout, verbose):
        """检查单个引擎"""
        try:
            engine = DockerEngine.objects.get(id=engine_id)
            self.stdout.write(f'\n检查引擎: {engine.name} (ID: {engine.id})')
            
            result = engine.check_health(timeout=timeout)
            self._print_result(engine, result, verbose)
            
        except DockerEngine.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f'\n✗ 引擎不存在: ID={engine_id}\n')
            )

    def _check_all_engines(self, timeout, verbose):
        """检查所有激活的引擎"""
        engines = DockerEngine.objects.filter(is_active=True)
        
        if not engines.exists():
            self.stdout.write(
                self.style.WARNING('\n⚠ 没有找到激活的 Docker 引擎\n')
            )
            return
        
        self.stdout.write(f'\n找到 {engines.count()} 个激活的引擎\n')
        
        # 统计数据
        stats = {
            'total': 0,
            'healthy': 0,
            'warning': 0,
            'critical': 0,
            'offline': 0,
            'unknown': 0
        }
        
        for engine in engines:
            self.stdout.write(f'\n检查引擎: {engine.name} (ID: {engine.id})')
            result = engine.check_health(timeout=timeout)
            self._print_result(engine, result, verbose)
            
            # 统计
            stats['total'] += 1
            status = result['status']
            if status == 'HEALTHY':
                stats['healthy'] += 1
            elif status == 'WARNING':
                stats['warning'] += 1
            elif status == 'CRITICAL':
                stats['critical'] += 1
            elif status == 'OFFLINE':
                stats['offline'] += 1
            else:
                stats['unknown'] += 1
        
        # 打印汇总
        self._print_summary(stats)

    def _print_result(self, engine, result, verbose):
        """打印单个引擎的检查结果"""
        status = result['status']
        
        # 根据状态选择颜色
        if status == 'HEALTHY':
            style = self.style.SUCCESS
            icon = '✓'
        elif status == 'WARNING':
            style = self.style.WARNING
            icon = '⚠'
        elif status == 'CRITICAL':
            style = self.style.ERROR
            icon = '✗'
        elif status == 'OFFLINE':
            style = self.style.ERROR
            icon = '✗'
        else:
            style = self.style.NOTICE
            icon = '?'
        
        self.stdout.write(style(f'  {icon} 状态: {status}'))
        
        # 显示错误信息
        if result['error']:
            self.stdout.write(self.style.ERROR(f'  错误: {result["error"]}'))
        
        # 显示详细信息
        if verbose and result['details']:
            details = result['details']
            self.stdout.write('  详细信息:')
            
            if 'docker_version' in details:
                self.stdout.write(f'    Docker 版本: {details["docker_version"]}')
            if 'os' in details:
                self.stdout.write(f'    操作系统: {details["os"]}')
            if 'cpu_count' in details:
                self.stdout.write(f'    CPU 核心数: {details["cpu_count"]}')
            if 'total_memory_gb' in details:
                self.stdout.write(f'    总内存: {details["total_memory_gb"]} GB')
            if 'running_containers' in details:
                self.stdout.write(
                    f'    容器数: {details["running_containers"]} 运行中 / '
                    f'{details["total_containers"]} 总计'
                )
            if 'response_time_ms' in details:
                self.stdout.write(f'    响应时间: {details["response_time_ms"]} ms')
            
            # 警告信息
            if details.get('warnings'):
                self.stdout.write(self.style.WARNING('  警告:'))
                for warning in details['warnings']:
                    self.stdout.write(self.style.WARNING(f'    - {warning}'))

    def _print_summary(self, stats):
        """打印汇总统计"""
        self.stdout.write(
            f'\n{"="*60}\n'
            f'检查完成\n'
            f'{"="*60}\n'
        )
        
        self.stdout.write(f'总计: {stats["total"]} 个引擎')
        self.stdout.write(self.style.SUCCESS(f'  ✓ 健康: {stats["healthy"]}'))
        
        if stats['warning'] > 0:
            self.stdout.write(self.style.WARNING(f'  ⚠ 警告: {stats["warning"]}'))
        
        if stats['critical'] > 0:
            self.stdout.write(self.style.ERROR(f'  ✗ 严重: {stats["critical"]}'))
        
        if stats['offline'] > 0:
            self.stdout.write(self.style.ERROR(f'  ✗ 离线: {stats["offline"]}'))
        
        if stats['unknown'] > 0:
            self.stdout.write(self.style.NOTICE(f'  ? 未知: {stats["unknown"]}'))
        
        self.stdout.write('')


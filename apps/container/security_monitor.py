"""
K8s 容器安全监控服务

提供：
1. 网络策略管理
2. 流量监控和审计
3. 异常行为检测
4. 安全告警
"""

from kubernetes import client
from kubernetes.client.rest import ApiException
import logging
from datetime import datetime, timedelta
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger('apps.container')


class SecurityMonitor:
    """K8s 安全监控器"""
    
    def __init__(self, core_api, networking_api, namespace='ctf-challenges'):
        self.core_api = core_api
        self.networking_api = networking_api
        self.namespace = namespace
    
    # ==================== 网络策略管理 ====================
    
    def get_network_policies(self):
        """
        获取当前命名空间的所有网络策略
        
        Returns:
            list: 网络策略列表
        """
        try:
            policies = self.networking_api.list_namespaced_network_policy(
                namespace=self.namespace
            )
            
            policy_list = []
            for policy in policies.items:
                policy_spec = policy.spec
                ingress_rules = policy_spec.ingress or []
                egress_rules = policy_spec.egress or []
                policy_types = policy_spec.policy_types or []
                
                # 更准确的 deny-all 检测
                is_ingress_deny_all = 'Ingress' in policy_types and len(ingress_rules) == 0
                is_egress_deny_all = 'Egress' in policy_types and len(egress_rules) == 0
                
                # 检查是否有 DNS 允许规则（53端口）
                has_dns_rule = False
                for egress in egress_rules:
                    if egress.ports:
                        for port in egress.ports:
                            if port.port == 53:
                                has_dns_rule = True
                                break
                
                policy_list.append({
                    'name': policy.metadata.name,
                    'created_at': policy.metadata.creation_timestamp,
                    'pod_selector': policy_spec.pod_selector.match_labels if policy_spec.pod_selector else {},
                    'policy_types': policy_types,
                    'ingress_rules': len(ingress_rules),
                    'egress_rules': len(egress_rules),
                    'is_ingress_deny_all': is_ingress_deny_all,
                    'is_egress_deny_all': is_egress_deny_all,
                    'has_dns_rule': has_dns_rule
                })
            
            return policy_list
            
        except Exception as e:
            logger.error(f"获取网络策略失败: {str(e)}")
            return []
    
    def check_security_status(self):
        """
        检查安全状态（改进版：更准确的检测逻辑）
        
        Returns:
            dict: 安全状态摘要
        """
        policies = self.get_network_policies()
        
        # 统计各类策略
        has_egress_deny = any(p['is_egress_deny_all'] for p in policies)
        has_ingress_deny = any(p['is_ingress_deny_all'] for p in policies)
        has_dns_allow = any(p['has_dns_rule'] for p in policies)
        
        # 统计有规则的策略数量（不包括纯 deny-all）
        policies_with_rules = [p for p in policies if p['ingress_rules'] > 0 or p['egress_rules'] > 0]
        
        # 更精确的安全等级判断
        security_level = 'LOW'
        if has_egress_deny and has_ingress_deny:
            # 有完整的入站和出站限制
            security_level = 'HIGH'
        elif has_egress_deny or has_ingress_deny:
            # 有部分限制
            security_level = 'MEDIUM'
        elif len(policies_with_rules) > 0:
            # 有网络策略但没有默认拒绝
            security_level = 'MEDIUM'
        else:
            # 没有任何有效策略
            security_level = 'LOW'
        
        status = {
            'network_policies_count': len(policies),
            'policies_with_rules_count': len(policies_with_rules),
            'has_egress_deny': has_egress_deny,
            'has_ingress_deny': has_ingress_deny,
            'has_dns_allow': has_dns_allow,
            'security_level': security_level,
            'policies': policies,
            'warnings': []
        }
        
        # 生成更详细的警告
        if not has_egress_deny:
            status['warnings'].append('⚠️ 未配置出站流量默认拒绝策略，容器可自由访问外网')
        if not has_ingress_deny:
            status['warnings'].append('⚠️ 未配置入站流量默认拒绝策略，容器可能被外部访问')
        if has_egress_deny and not has_dns_allow:
            status['warnings'].append('⚠️ 已配置出站拒绝但未允许 DNS（53端口），容器可能无法解析域名')
        if len(policies) == 0:
            status['warnings'].append('⚠️ 未配置任何网络策略，命名空间完全开放')
        
        return status
    
    # ==================== 流量监控 ====================
    
    def get_pod_network_stats(self, pod_name):
        """
        获取 Pod 网络统计
        
        Args:
            pod_name: Pod 名称
            
        Returns:
            dict: 网络统计信息
        """
        try:
            from kubernetes.stream import stream
            
            # 执行命令获取网络统计
            command = [
                '/bin/sh', '-c',
                "cat /proc/net/dev 2>/dev/null | grep eth0 || echo 'eth0: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0'"
            ]
            
            resp = stream(
                self.core_api.connect_get_namespaced_pod_exec,
                name=pod_name,
                namespace=self.namespace,
                command=command,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
                _preload_content=False
            )
            
            output = ""
            while resp.is_open():
                resp.update(timeout=1)
                if resp.peek_stdout():
                    output += resp.read_stdout()
                if resp.peek_stderr():
                    logger.error(f"获取网络统计错误: {resp.read_stderr()}")
                    break
            
            resp.close()
            
            # 解析输出
            if output and 'eth0' in output:
                parts = output.split()
                if len(parts) >= 17:
                    return {
                        'rx_bytes': int(parts[1]),
                        'rx_packets': int(parts[2]),
                        'tx_bytes': int(parts[9]),
                        'tx_packets': int(parts[10]),
                        'timestamp': timezone.now().isoformat()
                    }
            
            return {
                'rx_bytes': 0,
                'rx_packets': 0,
                'tx_bytes': 0,
                'tx_packets': 0,
                'timestamp': timezone.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"获取 Pod {pod_name} 网络统计失败: {str(e)}")
            return None
    
    def get_pod_active_connections(self, pod_name):
        """
        获取 Pod 当前活动的网络连接
        
        Args:
            pod_name: Pod 名称
            
        Returns:
            list: 连接列表
        """
        try:
            from kubernetes.stream import stream
            
            # 获取建立的 TCP 连接
            command = [
                '/bin/sh', '-c',
                'ss -tn state established 2>/dev/null || netstat -tn 2>/dev/null | grep ESTABLISHED || echo "No connections"'
            ]
            
            resp = stream(
                self.core_api.connect_get_namespaced_pod_exec,
                name=pod_name,
                namespace=self.namespace,
                command=command,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
                _preload_content=False
            )
            
            output = ""
            while resp.is_open():
                resp.update(timeout=1)
                if resp.peek_stdout():
                    output += resp.read_stdout()
                if not resp.is_open():
                    break
            
            resp.close()
            
            # 解析连接
            connections = []
            if output and "No connections" not in output:
                lines = output.strip().split('\n')
                for line in lines:
                    if 'ESTAB' in line or 'ESTABLISHED' in line:
                        parts = line.split()
                        if len(parts) >= 5:
                            connections.append({
                                'local_address': parts[3] if len(parts) > 3 else 'unknown',
                                'remote_address': parts[4] if len(parts) > 4 else 'unknown',
                                'state': 'ESTABLISHED'
                            })
            
            return connections
            
        except Exception as e:
            logger.error(f"获取 Pod {pod_name} 连接失败: {str(e)}")
            return []
    
    def detect_suspicious_activity(self, pod_name):
        """
        检测可疑活动
        
        Args:
            pod_name: Pod 名称
            
        Returns:
            dict: 检测结果
        """
        result = {
            'suspicious': False,
            'alerts': [],
            'connections': [],
            'network_stats': None
        }
        
        # 获取连接
        connections = self.get_pod_active_connections(pod_name)
        result['connections'] = connections
        
        # 检查连接数
        if len(connections) > 10:
            result['suspicious'] = True
            result['alerts'].append({
                'level': 'WARNING',
                'message': f'连接数异常：{len(connections)} 个活动连接',
                'timestamp': timezone.now().isoformat()
            })
        
        # 检查是否有到外部高危端口的连接
        dangerous_ports = [22, 23, 3389, 445, 135, 139]  # SSH, Telnet, RDP, SMB
        for conn in connections:
            remote = conn.get('remote_address', '')
            for port in dangerous_ports:
                if f':{port}' in remote:
                    result['suspicious'] = True
                    result['alerts'].append({
                        'level': 'CRITICAL',
                        'message': f'检测到到高危端口的连接: {remote}',
                        'timestamp': timezone.now().isoformat()
                    })
        
        # 获取流量统计
        stats = self.get_pod_network_stats(pod_name)
        result['network_stats'] = stats
        
        # 检查流量异常（与缓存的历史数据对比）
        if stats:
            cache_key = f'pod_network_stats_{pod_name}'
            previous_stats = cache.get(cache_key)
            
            if previous_stats:
                # 计算流量增长
                tx_diff = stats['tx_bytes'] - previous_stats['tx_bytes']
                
                # 解析之前的时间戳
                previous_time = datetime.fromisoformat(previous_stats['timestamp'])
                current_time = timezone.now()
                
                # 统一为 naive datetime（因为 Django USE_TZ=False）
                if previous_time.tzinfo is not None:
                    previous_time = previous_time.replace(tzinfo=None)
                if current_time.tzinfo is not None:
                    current_time = current_time.replace(tzinfo=None)
                
                time_diff = (current_time - previous_time).total_seconds()
                
                if time_diff > 0:
                    tx_rate = tx_diff / time_diff  # 字节/秒
                    
                    # 如果发送速率超过 10MB/s
                    if tx_rate > 10 * 1024 * 1024:
                        result['suspicious'] = True
                        result['alerts'].append({
                            'level': 'CRITICAL',
                            'message': f'出站流量异常：{tx_rate / (1024*1024):.2f} MB/s',
                            'timestamp': timezone.now().isoformat()
                        })
            
            # 更新缓存
            cache.set(cache_key, stats, 300)  # 缓存 5 分钟
        
        return result
    
    # ==================== 批量监控 ====================
    
    def monitor_all_pods(self):
        """
        监控所有 Pod
        
        Returns:
            dict: 监控结果汇总
        """
        try:
            pods = self.core_api.list_namespaced_pod(namespace=self.namespace)
            
            results = {
                'total_pods': len(pods.items),
                'suspicious_count': 0,
                'pods': [],
                'alerts': []
            }
            
            for pod in pods.items:
                pod_name = pod.metadata.name
                pod_status = pod.status.phase
                
                if pod_status != 'Running':
                    continue
                
                # 检测可疑活动
                detection = self.detect_suspicious_activity(pod_name)
                
                # 处理创建时间
                created_at = None
                if pod.metadata.creation_timestamp:
                    ct = pod.metadata.creation_timestamp
                    if ct.tzinfo is not None:
                        from django.utils import timezone as dj_tz
                        ct = dj_tz.localtime(ct).replace(tzinfo=None)
                    created_at = ct.strftime('%Y-%m-%d %H:%M:%S')
                
                pod_info = {
                    'name': pod_name,
                    'status': pod_status,
                    'created_at': created_at,
                    'labels': pod.metadata.labels or {},
                    'suspicious': detection['suspicious'],
                    'connections_count': len(detection['connections']),
                    'alerts': detection['alerts']
                }
                
                results['pods'].append(pod_info)
                
                if detection['suspicious']:
                    results['suspicious_count'] += 1
                    results['alerts'].extend(detection['alerts'])
            
            return results
            
        except Exception as e:
            logger.error(f"批量监控失败: {str(e)}")
            return {
                'total_pods': 0,
                'suspicious_count': 0,
                'pods': [],
                'alerts': [],
                'error': str(e)
            }
    
    # ==================== 事件日志 ====================
    
    def get_security_events(self, hours=24):
        """
        获取安全事件日志
        
        Args:
            hours: 获取最近多少小时的事件
            
        Returns:
            list: 事件列表
        """
        try:
            # 获取 Pod 事件
            field_selector = f"involvedObject.namespace={self.namespace}"
            events = self.core_api.list_event_for_all_namespaces(
                field_selector=field_selector
            )
            
            # 过滤最近的事件
            cutoff_time = timezone.now() - timedelta(hours=hours)
            # 确保 cutoff_time 是 naive datetime（因为 Django USE_TZ=False）
            if cutoff_time.tzinfo is not None:
                cutoff_time = cutoff_time.replace(tzinfo=None)
            
            recent_events = []
            
            for event in events.items:
                # 优先使用 event_time（新版本），然后是 last_timestamp（旧版本）
                # last_timestamp 表示事件最后一次发生的时间
                event_time = getattr(event, 'event_time', None) or event.last_timestamp
                
                # 获取第一次发生时间（可选）
                first_time = event.first_timestamp
                
                if event_time:
                    # 统一转换为 naive datetime
                    if event_time.tzinfo is not None:
                        # 转换为本地时间并移除时区信息
                        from django.utils import timezone as dj_tz
                        event_time = dj_tz.localtime(event_time).replace(tzinfo=None)
                    
                    # 处理第一次发生时间
                    first_time_str = None
                    if first_time:
                        if first_time.tzinfo is not None:
                            from django.utils import timezone as dj_tz
                            first_time = dj_tz.localtime(first_time).replace(tzinfo=None)
                        first_time_str = first_time.strftime('%Y-%m-%d %H:%M:%S')
                    
                    if event_time > cutoff_time:
                        # 识别安全相关事件
                        is_security_related = any(keyword in event.message.lower() for keyword in [
                            'failed', 'error', 'kill', 'oom', 'evict', 'network', 'denied'
                        ])
                        
                        if is_security_related:
                            recent_events.append({
                                'timestamp': event_time.strftime('%Y-%m-%d %H:%M:%S'),  # 最后一次发生
                                'first_timestamp': first_time_str,  # 第一次发生
                                'type': event.type,
                                'reason': event.reason,
                                'message': event.message,
                                'object': event.involved_object.name,
                                'count': event.count or 1  # 发生次数
                            })
            
            # 按时间倒序排序
            recent_events.sort(key=lambda x: x['timestamp'], reverse=True)
            
            return recent_events
            
        except Exception as e:
            logger.error(f"获取安全事件失败: {str(e)}")
            return []
    
    # ==================== 资源使用监控 ====================
    
    def get_resource_usage_stats(self):
        """
        获取命名空间资源使用统计
        
        Returns:
            dict: 资源使用情况
        """
        try:
            pods = self.core_api.list_namespaced_pod(namespace=self.namespace)
            
            stats = {
                'total_pods': len(pods.items),
                'running_pods': 0,
                'cpu_requests': 0,
                'cpu_limits': 0,
                'memory_requests': 0,
                'memory_limits': 0,
                'pods_without_limits': []
            }
            
            for pod in pods.items:
                if pod.status.phase == 'Running':
                    stats['running_pods'] += 1
                
                for container in pod.spec.containers:
                    if container.resources:
                        # CPU
                        if container.resources.requests and 'cpu' in container.resources.requests:
                            cpu_req = container.resources.requests['cpu']
                            stats['cpu_requests'] += self._parse_cpu(cpu_req)
                        
                        if container.resources.limits and 'cpu' in container.resources.limits:
                            cpu_lim = container.resources.limits['cpu']
                            stats['cpu_limits'] += self._parse_cpu(cpu_lim)
                        
                        # Memory
                        if container.resources.requests and 'memory' in container.resources.requests:
                            mem_req = container.resources.requests['memory']
                            stats['memory_requests'] += self._parse_memory(mem_req)
                        
                        if container.resources.limits and 'memory' in container.resources.limits:
                            mem_lim = container.resources.limits['memory']
                            stats['memory_limits'] += self._parse_memory(mem_lim)
                    else:
                        # 没有资源限制
                        stats['pods_without_limits'].append(pod.metadata.name)
            
            return stats
            
        except Exception as e:
            logger.error(f"获取资源使用统计失败: {str(e)}")
            return {}
    
    @staticmethod
    def _parse_cpu(cpu_str):
        """解析 CPU 字符串为 millicores"""
        if isinstance(cpu_str, (int, float)):
            return cpu_str * 1000
        if 'm' in str(cpu_str):
            return float(str(cpu_str).replace('m', ''))
        return float(cpu_str) * 1000
    
    @staticmethod
    def _parse_memory(mem_str):
        """解析内存字符串为 MB"""
        if isinstance(mem_str, (int, float)):
            return mem_str / (1024 * 1024)
        
        mem_str = str(mem_str).upper()
        if 'MI' in mem_str:
            return float(mem_str.replace('MI', ''))
        elif 'GI' in mem_str:
            return float(mem_str.replace('GI', '')) * 1024
        elif 'KI' in mem_str:
            return float(mem_str.replace('KI', '')) / 1024
        return float(mem_str) / (1024 * 1024)


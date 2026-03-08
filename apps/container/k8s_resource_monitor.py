# -*- coding: utf-8 -*-
"""
K8s节点资源监控模块
基于实际资源使用率防止节点崩溃
"""
import time
from kubernetes import client
from django.core.cache import cache
from django.conf import settings
import logging
from container.models import ContainerEngineConfig
logger = logging.getLogger('apps.container')


class K8sResourceMonitor:
    """节点资源实时监控，基于真实资源使用率防止崩溃"""
    
    def __init__(self, core_api, namespace):
        
        self.config = ContainerEngineConfig.get_config()
        
        self.core_api = core_api
        self.namespace = namespace
        
        # 节点资源安全阈值（从数据库配置读取）
        self.memory_threshold = self.config.k8s_node_memory_threshold
        self.cpu_threshold = self.config.k8s_node_cpu_threshold
        
        # 集群总资源阈值
        self.cluster_memory_threshold = self.config.k8s_cluster_memory_threshold
        self.cluster_cpu_threshold = self.config.k8s_cluster_cpu_threshold
        self.k8s_node_cache_timeout = self.config.k8s_node_cache_timeout
    
    def select_safe_node(self, required_memory_mb, required_cpu_cores):
        """
        选择安全的节点（基于实际资源使用率 + 原子预占）
        
        Args:
            required_memory_mb: 需要的内存（MB）
            required_cpu_cores: 需要的CPU（核心数）
            
        Returns:
            str: 节点名称，或 None（无可用节点）
        """
        logger.info(
            f"开始选择K8s节点: 需要资源 memory={required_memory_mb}MB, cpu={required_cpu_cores}核"
        )
        
        max_retries = 10  # 最多重试10次
        
        for retry in range(max_retries):
            try:
                # 1. 先检查集群总资源是否充足
                cluster_ok, cluster_msg = self._check_cluster_resources(
                    required_memory_mb, required_cpu_cores
                )
                if not cluster_ok:
                    logger.error(f"集群资源不足: {cluster_msg}")
                    return None
                
                # 2. 获取所有节点的资源使用情况
                node_resources = self._get_all_nodes_resources()
                if not node_resources:
                    logger.error("无法获取节点资源信息（可能Metrics API不可用或无可用节点）")
                    return None
                
                # 3. 筛选安全的节点
                safe_nodes = []
                required_memory_bytes = required_memory_mb * 1024 * 1024
                
                for node_info in node_resources:
                    node_name = node_info['name']
                    
                    #  关键：读取Redis中的预占计数（原子操作）
                    pending_memory, pending_cpu = self._get_pending_resources(node_name)
                    
                    # 计算创建后的预期使用率（包含预占量）
                    predicted_memory_usage = (
                        node_info['memory_used'] + pending_memory + required_memory_bytes
                    ) / node_info['memory_total']
                    
                    predicted_cpu_usage = (
                        node_info['cpu_used'] + pending_cpu + required_cpu_cores
                    ) / node_info['cpu_total']
                    
                    # 检查是否超过安全阈值
                    if (predicted_memory_usage < self.memory_threshold and 
                        predicted_cpu_usage < self.cpu_threshold):
                        
                        safe_nodes.append({
                            'name': node_name,
                            'memory_usage': node_info['memory_usage_percent'],
                            'cpu_usage': node_info['cpu_usage_percent'],
                            'predicted_memory': predicted_memory_usage * 100,
                            'predicted_cpu': predicted_cpu_usage * 100,
                            'pending_memory_mb': pending_memory / 1024 / 1024,
                            'pending_cpu': pending_cpu,
                            'score': predicted_memory_usage + predicted_cpu_usage
                        })
                        
                        logger.debug(
                            f"节点{node_name}可用: "
                            f"内存{node_info['memory_usage_percent']:.1f}%→{predicted_memory_usage*100:.1f}% "
                            f"(预占{pending_memory/1024/1024:.0f}MB), "
                            f"CPU{node_info['cpu_usage_percent']:.1f}%→{predicted_cpu_usage*100:.1f}% "
                            f"(预占{pending_cpu:.2f}核)"
                        )
                    else:
                        logger.debug(
                            f"节点{node_name}不安全: "
                            f"内存{predicted_memory_usage*100:.1f}% (阈值{self.memory_threshold*100:.0f}%), "
                            f"CPU{predicted_cpu_usage*100:.1f}% (阈值{self.cpu_threshold*100:.0f}%)"
                        )
                
                if not safe_nodes:
                    logger.error("所有节点资源使用率都将超过安全阈值")
                    return None
                
                # 4.  增加随机性：打乱列表，避免所有请求选择同一节点
                import random
                random.shuffle(safe_nodes)
                
                # 然后按负载排序（保留前30%的低负载节点，在这些节点中随机选择）
                safe_nodes.sort(key=lambda x: x['score'])
                top_nodes_count = max(1, len(safe_nodes) // 3)  # 前30%的节点
                top_nodes = safe_nodes[:top_nodes_count]
                random.shuffle(top_nodes)  # 再次打乱前30%的节点
                
                for best_node in top_nodes:
                    node_name = best_node['name']
                    
                    # 获取节点信息（用于超限检查）
                    node_info = next((n for n in node_resources if n['name'] == node_name), None)
                    if not node_info:
                        continue
                    
                    #  5.  关键：原子预占+超限检查+自动回滚
                    success = self._atomic_reserve_and_check(
                        node_name,
                        required_memory_mb,
                        required_cpu_cores,
                        node_info['memory_total'],
                        node_info['memory_used'],
                        node_info['cpu_total'],
                        node_info['cpu_used']
                    )
                    
                    if success:
                        logger.info(
                            f"✓ 选择并预占节点: {node_name} "
                            f"(内存: {best_node['memory_usage']:.1f}%→{best_node['predicted_memory']:.1f}%, "
                            f"CPU: {best_node['cpu_usage']:.1f}%→{best_node['predicted_cpu']:.1f}%, "
                            f"预占: +{required_memory_mb}MB/+{required_cpu_cores:.2f}核)"
                        )
                        return node_name
                    else:
                        # 预占后超限已自动回滚，尝试下一个节点
                        logger.debug(f"节点{node_name}预占后超限已回滚，尝试下一个节点")
                        continue
                
                # 所有安全节点都尝试失败，重试整个流程
                logger.debug(f"所有候选节点都超限，重试{retry+1}/{max_retries}")
                time.sleep(0.01)  # 短暂等待后重试
                continue
                
            except Exception as e:
                logger.error(f"选择安全节点失败（重试{retry+1}/{max_retries}）: {e}", exc_info=True)
                continue
        
        logger.error(f"选择节点失败：重试{max_retries}次后仍无可用节点")
        return None
    
    def _get_pending_resources(self, node_name):
        """
        获取节点的待创建资源预占量（Redis计数器）
        
        Returns:
            tuple: (pending_memory_bytes, pending_cpu_cores)
        """
        try:
            memory_key = f"k8s:node_pending_memory:{self.namespace}:{node_name}"
            cpu_key = f"k8s:node_pending_cpu:{self.namespace}:{node_name}"
            
            pending_memory_mb = float(cache.get(memory_key) or 0)
            pending_cpu = float(cache.get(cpu_key) or 0)
            
            return pending_memory_mb * 1024 * 1024, pending_cpu
        except Exception as e:
            logger.warning(f"获取节点预占量失败: {e}")
            return 0, 0
    
    def _atomic_reserve_and_check(self, node_name, memory_mb, cpu_cores,
                                   node_memory_total, node_memory_used,
                                   node_cpu_total, node_cpu_used):
        """
         高并发核心：先预占、再检查、超限则回滚（Lua脚本保证原子性）
        
        Args:
            node_name: 节点名称
            memory_mb: 需要预占的内存（MB）
            cpu_cores: 需要预占的CPU（核心数）
            node_memory_total: 节点总内存（bytes）
            node_memory_used: 节点已使用内存（bytes）
            node_cpu_total: 节点总CPU（cores）
            node_cpu_used: 节点已使用CPU（cores）
            
        Returns:
            bool: True=预占成功, False=超限已回滚
        """
        try:
            memory_key = f"k8s:node_pending_memory:{self.namespace}:{node_name}"
            cpu_key = f"k8s:node_pending_cpu:{self.namespace}:{node_name}"
            
            redis_client = cache.client.get_client()
            
            #  Lua脚本：原子检查+预占（关键：先读pending、再判断、再预占）
            lua_script = """
            local memory_key = KEYS[1]
            local cpu_key = KEYS[2]
            
            local memory_inc = tonumber(ARGV[1])
            local cpu_inc = tonumber(ARGV[2])
            local timeout = tonumber(ARGV[3])
            
            local node_memory_total = tonumber(ARGV[4])
            local node_memory_used = tonumber(ARGV[5])
            local node_cpu_total = tonumber(ARGV[6])
            local node_cpu_used = tonumber(ARGV[7])
            local memory_threshold = tonumber(ARGV[8])
            local cpu_threshold = tonumber(ARGV[9])
            
            -- 1.  关键：先读取当前预占量（预占前的值）
            local current_pending_memory = tonumber(redis.call('GET', memory_key) or 0)
            local current_pending_cpu = tonumber(redis.call('GET', cpu_key) or 0)
            
            -- 2. 计算如果预占后的使用率（使用预占前的pending + 新请求）
            local predicted_memory_usage = (node_memory_used + (current_pending_memory + memory_inc) * 1024 * 1024) / node_memory_total
            local predicted_cpu_usage = (node_cpu_used + current_pending_cpu + cpu_inc) / node_cpu_total
            
            -- 3. 检查是否超过阈值（预占前判断）
            if predicted_memory_usage >= memory_threshold or predicted_cpu_usage >= cpu_threshold then
                -- 超限，不预占，直接返回失败
                return {0, predicted_memory_usage, predicted_cpu_usage}
            end
            
            -- 4. 未超限，执行原子预占
            local new_pending_memory = redis.call('INCRBYFLOAT', memory_key, memory_inc)
            local new_pending_cpu = redis.call('INCRBYFLOAT', cpu_key, cpu_inc)
            
            -- 5. 设置过期时间
            redis.call('EXPIRE', memory_key, timeout)
            redis.call('EXPIRE', cpu_key, timeout)
            
            return {1, predicted_memory_usage, predicted_cpu_usage}  -- 成功
            """
            
            timeout = getattr(settings, 'K8S_NODE_RESERVATION_TIMEOUT', 30)
            
            result = redis_client.eval(
                lua_script,
                2,  # key数量
                memory_key,
                cpu_key,
                memory_mb,
                cpu_cores,
                timeout,
                node_memory_total,
                node_memory_used,
                node_cpu_total,
                node_cpu_used,
                self.memory_threshold,
                self.cpu_threshold
            )
            
            # 解析结果
            success = int(float(result[0])) if result[0] else 0
            predicted_memory = float(result[1]) if result[1] else 0.0
            predicted_cpu = float(result[2]) if result[2] else 0.0
            
            if success:
                logger.debug(
                    f"✓ 预占成功: {node_name} "
                    f"(预占后使用率: 内存{predicted_memory*100:.1f}%, CPU{predicted_cpu*100:.1f}%)"
                )
                return True
            else:
                logger.debug(
                    f"✗ 预占后超限已回滚: {node_name} "
                    f"(预测使用率: 内存{predicted_memory*100:.1f}%, CPU{predicted_cpu*100:.1f}%)"
                )
                return False
            
        except Exception as e:
            logger.error(f"原子预占+检查失败: {node_name}, {e}")
            return False
    
    def release_node_reservation(self, node_name, memory_mb, cpu_cores):
        """
        释放节点资源预占（任务完成或失败时调用）
        
        Args:
            node_name: 节点名称
            memory_mb: 内存（MB）
            cpu_cores: CPU（核心数）
        """
        try:
            memory_key = f"k8s:node_pending_memory:{self.namespace}:{node_name}"
            cpu_key = f"k8s:node_pending_cpu:{self.namespace}:{node_name}"
            
            redis_client = cache.client.get_client()
            
            # 原子减少
            redis_client.incrbyfloat(memory_key, -memory_mb)
            redis_client.incrbyfloat(cpu_key, -cpu_cores)
            
            logger.debug(
                f"✓ 释放节点预占: {node_name} "
                f"({memory_mb:.0f}MB, {cpu_cores:.2f}核)"
            )
            
        except Exception as e:
            logger.warning(f"释放节点预占失败: {e}")
    
    def _check_cluster_resources(self, required_memory_mb, required_cpu_cores):
        """检查集群总资源是否充足"""
        try:
            nodes = self.core_api.list_node()
            
            total_memory = 0
            total_cpu = 0
            used_memory = 0
            used_cpu = 0
            
            for node in nodes.items:
                # 跳过不可用节点
                if node.spec.unschedulable:
                    continue
                
                is_ready = False
                if node.status.conditions:
                    for condition in node.status.conditions:
                        if condition.type == 'Ready' and condition.status == 'True':
                            is_ready = True
                            break
                
                if not is_ready:
                    continue
                
                allocatable = node.status.allocatable
                if not allocatable:
                    continue
                
                node_memory = self._parse_memory(allocatable.get('memory', '0'))
                node_cpu = self._parse_cpu(allocatable.get('cpu', '0'))
                
                total_memory += node_memory
                total_cpu += node_cpu
                
                # 获取节点已用资源
                node_usage = self._get_node_usage(node.metadata.name)
                if node_usage:
                    used_memory += node_usage['memory']
                    used_cpu += node_usage['cpu']
            
            if total_memory == 0 or total_cpu == 0:
                return False, "无法获取集群资源信息"
            
            # 计算创建后的集群使用率
            required_memory_bytes = required_memory_mb * 1024 * 1024
            predicted_memory_usage = (used_memory + required_memory_bytes) / total_memory
            predicted_cpu_usage = (used_cpu + required_cpu_cores) / total_cpu
            
            memory_ok = predicted_memory_usage < self.cluster_memory_threshold
            cpu_ok = predicted_cpu_usage < self.cluster_cpu_threshold
            
            msg = (
                f"集群资源: "
                f"内存{used_memory/1024/1024/1024:.1f}GB→{(used_memory+required_memory_bytes)/1024/1024/1024:.1f}GB/"
                f"{total_memory/1024/1024/1024:.1f}GB ({predicted_memory_usage*100:.1f}%), "
                f"CPU{used_cpu:.2f}→{used_cpu+required_cpu_cores:.2f}/{total_cpu:.2f}核 "
                f"({predicted_cpu_usage*100:.1f}%)"
            )
            
            if memory_ok and cpu_ok:
                logger.info(f"✓ {msg}")
                return True, msg
            else:
                logger.error(f"✗ {msg}")
                return False, f"集群资源不足: {msg}"
            
        except Exception as e:
            logger.error(f"检查集群资源失败: {e}", exc_info=True)
            return False, f"检查失败: {str(e)}"
    
    def _get_all_nodes_resources(self):
        """获取所有节点的资源使用情况（带缓存10秒，高并发下更准确）"""
        cache_key = f"k8s:nodes_resources:{self.namespace}"
        cached = cache.get(cache_key)
        if cached:
            logger.debug(f"使用缓存的节点资源信息（{len(cached)}个节点）")
            return cached
        
        try:
            logger.info("开始获取K8s节点资源信息...")
            nodes = self.core_api.list_node()
            logger.info(f"获取到 {len(nodes.items)} 个K8s节点")
            
            node_resources = []
            
            for node in nodes.items:
                node_name = node.metadata.name
                logger.debug(f"检查节点: {node_name}")
                
                # 跳过不可用节点
                if node.spec.unschedulable:
                    logger.debug(f"跳过节点 {node_name}: 不可调度")
                    continue
                
                is_ready = False
                if node.status.conditions:
                    for condition in node.status.conditions:
                        if condition.type == 'Ready' and condition.status == 'True':
                            is_ready = True
                            break
                
                if not is_ready:
                    logger.debug(f"跳过节点 {node_name}: 未就绪")
                    continue
                
                # 检查污点
                if node.spec.taints:
                    has_block = any(
                        t.effect in ('NoSchedule', 'NoExecute') 
                        for t in node.spec.taints
                    )
                    if has_block:
                        logger.debug(f"跳过节点 {node_name}: 有阻止调度的污点")
                        continue
                
                allocatable = node.status.allocatable
                
                if not allocatable:
                    logger.debug(f"跳过节点 {node_name}: 无allocatable信息")
                    continue
                
                # 解析总资源
                total_memory = self._parse_memory(allocatable.get('memory', '0'))
                total_cpu = self._parse_cpu(allocatable.get('cpu', '0'))
                
                logger.debug(
                    f"节点 {node_name} 总资源: "
                    f"memory={total_memory/1024/1024:.0f}MB, cpu={total_cpu:.2f}核"
                )
                
                # 获取已用资源
                usage = self._get_node_usage(node_name)
                if not usage:
                    logger.warning(f"跳过节点 {node_name}: 无法获取使用量")
                    continue
                
                used_memory = usage['memory']
                used_cpu = usage['cpu']
                
                memory_percent = (used_memory / total_memory * 100) if total_memory > 0 else 0
                cpu_percent = (used_cpu / total_cpu * 100) if total_cpu > 0 else 0
                
                
                node_resources.append({
                    'name': node_name,
                    'memory_total': total_memory,
                    'memory_used': used_memory,
                    'memory_usage_percent': memory_percent,
                    'cpu_total': total_cpu,
                    'cpu_used': used_cpu,
                    'cpu_usage_percent': cpu_percent
                })
            
            
            #  使用配置的缓存时间（默认2秒，高并发下快速刷新）
         
            cache_timeout = self.k8s_node_cache_timeout
            cache.set(cache_key, node_resources, timeout=cache_timeout)
            return node_resources
            
        except Exception as e:
            logger.error(f"获取节点资源失败: {e}", exc_info=True)
            return []
    
    def _get_node_usage(self, node_name):
        """获取节点的实际资源使用量（通过Metrics API）"""
        try:
            custom_api = client.CustomObjectsApi(self.core_api.api_client)
            metrics = custom_api.get_cluster_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                plural="nodes",
                name=node_name
            )
            
            usage_data = metrics.get('usage', {})
            memory_used = self._parse_memory(usage_data.get('memory', '0'))
            cpu_used = self._parse_cpu(usage_data.get('cpu', '0'))
            
            logger.debug(
                f"Metrics API 成功: {node_name} "
                f"memory={memory_used/1024/1024:.0f}MB, cpu={cpu_used:.2f}核"
            )
            
            return {
                'memory': memory_used,
                'cpu': cpu_used
            }
            
        except Exception as e:
            logger.warning(
                f"节点 {node_name} Metrics API 不可用（{type(e).__name__}: {str(e)[:100]}），"
                f"使用降级方案（基于Pod requests估算）"
            )
            # 降级方案：基于Pod requests估算
            return self._estimate_node_usage_by_pods(node_name)
    
    def _estimate_node_usage_by_pods(self, node_name):
        """
        降级方案：通过Pod的requests估算节点使用量
        
        注意：这个方法只是估算，不如Metrics API准确
        优先级：Metrics API > 系统级监控(psutil) > Pod requests估算
        """
        try:
            # 🔧 新增：如果可以SSH到节点，使用psutil获取真实资源（需要配置）
            # 这里暂时保留Pod requests方案，因为SSH到K8s节点需要额外配置
            
            pods = self.core_api.list_pod_for_all_namespaces(
                field_selector=f'spec.nodeName={node_name},status.phase!=Succeeded,status.phase!=Failed'
            )
            
            total_memory = 0
            total_cpu = 0
            
            for pod in pods.items:
                for container in pod.spec.containers:
                    if container.resources and container.resources.requests:
                        mem = container.resources.requests.get('memory', '0')
                        cpu = container.resources.requests.get('cpu', '0')
                        total_memory += self._parse_memory(mem)
                        total_cpu += self._parse_cpu(cpu)
            
            return {
                'memory': total_memory,
                'cpu': total_cpu
            }
            
        except Exception as e:
            logger.error(f"估算节点{node_name}使用量失败: {e}")
            return {'memory': 0, 'cpu': 0}
    
    @staticmethod
    def _parse_memory(mem_str):
        """解析内存字符串为字节数"""
        if not mem_str:
            return 0
        mem_str = str(mem_str).strip()
        
        if mem_str.endswith('Ki'):
            return int(mem_str[:-2]) * 1024
        elif mem_str.endswith('Mi'):
            return int(mem_str[:-2]) * 1024 * 1024
        elif mem_str.endswith('Gi'):
            return int(mem_str[:-2]) * 1024 * 1024 * 1024
        elif mem_str.endswith('Ti'):
            return int(mem_str[:-2]) * 1024 * 1024 * 1024 * 1024
        else:
            try:
                return int(mem_str)
            except:
                return 0
    
    @staticmethod
    def _parse_cpu(cpu_str):
        """解析CPU字符串为核心数"""
        if not cpu_str:
            return 0.0
        cpu_str = str(cpu_str).strip()
        
        if cpu_str.endswith('n'):
            return int(cpu_str[:-1]) / 1_000_000_000
        elif cpu_str.endswith('m'):
            return int(cpu_str[:-1]) / 1000
        else:
            try:
                return float(cpu_str)
            except:
                return 0.0


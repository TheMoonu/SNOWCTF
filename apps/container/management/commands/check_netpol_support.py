"""
检查 Kubernetes 集群的 NetworkPolicy 支持情况

使用方法:
    python manage.py check_netpol_support
"""
from django.core.management.base import BaseCommand
from kubernetes import client, config
from kubernetes.client.rest import ApiException


class Command(BaseCommand):
    help = '检查 Kubernetes 集群的 NetworkPolicy 支持情况'
    
    def handle(self, *args, **options):
        try:
            # 加载 kubeconfig
            try:
                config.load_incluster_config()
                self.stdout.write("✓ 使用集群内配置")
            except:
                config.load_kube_config()
                self.stdout.write("✓ 使用 kubeconfig 文件")
            
            v1 = client.CoreV1Api()
            
            # 1. 检查 CNI 插件
            self.stdout.write("\n" + "="*60)
            self.stdout.write("1. 检查 CNI 插件")
            self.stdout.write("="*60)
            
            nodes = v1.list_node()
            if nodes.items:
                node = nodes.items[0]
                
                # 检查节点注解
                annotations = node.metadata.annotations or {}
                
                cni_info = None
                for key in ['projectcalico.org/IPv4Address', 'flannel.alpha.coreos.com/public-ip', 'cilium.io/bgp']:
                    if key in annotations:
                        if 'calico' in key:
                            cni_info = "Calico"
                        elif 'flannel' in key:
                            cni_info = "Flannel"
                        elif 'cilium' in key:
                            cni_info = "Cilium"
                        break
                
                # 检查 kube-proxy 模式
                kube_system_pods = v1.list_namespaced_pod(namespace='kube-system')
                for pod in kube_system_pods.items:
                    if 'calico' in pod.metadata.name:
                        cni_info = "Calico"
                        break
                    elif 'flannel' in pod.metadata.name:
                        cni_info = "Flannel"
                        break
                    elif 'cilium' in pod.metadata.name:
                        cni_info = "Cilium"
                        break
                    elif 'weave' in pod.metadata.name:
                        cni_info = "Weave Net"
                        break
                
                if cni_info:
                    self.stdout.write(f"\n检测到的 CNI: {cni_info}")
                    
                    # NetworkPolicy 支持说明
                    if cni_info == "Calico":
                        self.stdout.write(self.style.SUCCESS("  ✓ Calico 完全支持 NetworkPolicy（包括 ICMP）"))
                    elif cni_info == "Cilium":
                        self.stdout.write(self.style.SUCCESS("  ✓ Cilium 完全支持 NetworkPolicy"))
                    elif cni_info == "Flannel":
                        self.stdout.write(self.style.WARNING("  ⚠️  Flannel 不支持 NetworkPolicy"))
                        self.stdout.write("     建议: 使用 Canal (Flannel + Calico) 或切换到 Calico")
                    elif cni_info == "Weave Net":
                        self.stdout.write(self.style.SUCCESS("  ✓ Weave Net 支持 NetworkPolicy"))
                else:
                    self.stdout.write(self.style.WARNING("  ⚠️  无法检测 CNI 类型"))
            
            # 2. 检查 NetworkPolicy 资源是否可用
            self.stdout.write("\n" + "="*60)
            self.stdout.write("2. 检查 NetworkPolicy API")
            self.stdout.write("="*60)
            
            networking_v1 = client.NetworkingV1Api()
            try:
                policies = networking_v1.list_network_policy_for_all_namespaces(limit=1)
                self.stdout.write(self.style.SUCCESS("\n✓ NetworkPolicy API 可用"))
            except ApiException as e:
                if e.status == 404:
                    self.stdout.write(self.style.ERROR("\n✗ NetworkPolicy API 不可用"))
                else:
                    self.stdout.write(self.style.WARNING(f"\n⚠️  NetworkPolicy API 检查失败: {e.reason}"))
            
            # 3. 测试建议
            self.stdout.write("\n" + "="*60)
            self.stdout.write("3. 测试建议")
            self.stdout.write("="*60)
            
            self.stdout.write("""
测试 NetworkPolicy 是否生效的步骤：

1. 测试 TCP 连接（大多数 CNI 都支持）:
   kubectl exec -n ctf-challenges <pod-A> -- nc -zv <pod-C-IP> 80
   
2. 测试 ICMP (ping)（某些 CNI 不支持）:
   kubectl exec -n ctf-challenges <pod-A> -- ping -c 3 <pod-C-IP>

3. 如果 ping 可以但 TCP 被阻止，说明:
   - NetworkPolicy 基本生效
   - 但 CNI 不支持 ICMP 限制（如 Flannel）
   
4. 如果都可以通过，可能原因:
   - CNI 不支持 NetworkPolicy（如纯 Flannel）
   - NetworkPolicy 配置有误
   - NetworkPolicy 未正确应用

推荐的 CNI 选择:
  ✓ Calico  - 完全支持，性能好
  ✓ Cilium  - 完全支持，功能强大
  ✓ Canal   - Flannel + Calico，兼容性好
  ✗ Flannel - 不支持 NetworkPolicy
""")
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ 检查失败: {str(e)}'))


"""
网络拓扑可视化编排视图
"""
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from .models import NetworkTopologyConfig, DockerImage
import json
import logging

logger = logging.getLogger(__name__)


@login_required
def topology_editor(request, topology_id):
    """可视化编排界面"""
    topology = get_object_or_404(NetworkTopologyConfig, id=topology_id)
    
    # 权限检查
    if not (request.user.is_superuser or request.user == topology.author):
        return JsonResponse({'error': '无权访问'}, status=403)
    
    # 获取所有可用镜像
    docker_images = DockerImage.objects.filter(
        is_active=True,
        review_status='APPROVED'
    ).order_by('name')
    
    # 确保 topology_data 是有效的字典
    topology_data = topology.topology_data
    if not isinstance(topology_data, dict):
        topology_data = {}
    
    context = {
        'topology': topology,
        'docker_images': docker_images,
        'topology_data_json': json.dumps(topology_data),
    }
    
    return render(request, 'container/topology_editor.html', context)


@require_http_methods(["POST"])
@csrf_exempt
@login_required
def save_topology(request, topology_id):
    """保存拓扑配置"""
    topology = get_object_or_404(NetworkTopologyConfig, id=topology_id)
    
    # 权限检查
    if not (request.user.is_superuser or request.user == topology.author):
        return JsonResponse({'error': '无权操作'}, status=403)
    
    try:
        data = json.loads(request.body)
        topology.topology_data = data
        topology.save()
        
        # 计算节点数量
        elements = data.get('elements', {})
        nodes_count = len(elements.get('nodes', []))
        
        logger.info(f"拓扑配置已保存: {topology.name} by {request.user.username}, 节点数: {nodes_count}")
        
        return JsonResponse({
            'success': True,
            'message': '保存成功',
            'nodes_count': nodes_count
        })
    
    except json.JSONDecodeError:
        return JsonResponse({'error': '无效的JSON数据'}, status=400)
    except Exception as e:
        logger.error(f"保存拓扑配置失败: {e}", exc_info=True)
        return JsonResponse({'error': f'保存失败: {str(e)}'}, status=500)


@login_required
def get_docker_images(request):
    """获取可用镜像列表"""
    images = DockerImage.objects.filter(
        is_active=True,
        review_status='APPROVED'
    ).values('id', 'name', 'tag', 'category', 'exposed_ports')
    
    return JsonResponse({
        'images': list(images)
    })


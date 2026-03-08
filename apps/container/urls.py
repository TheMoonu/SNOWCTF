from django.urls import path
from . import views
from . import topology_views

app_name = 'container'

urlpatterns = [
    # 网络拓扑编排
    path('topology/<int:topology_id>/editor/', topology_views.topology_editor, name='topology_editor'),
    path('topology/<int:topology_id>/save/', topology_views.save_topology, name='save_topology'),
    path('api/docker-images/', topology_views.get_docker_images, name='get_docker_images'),
    

    # 静态文件相关URL
    path('static-file/create/', views.StaticFileCreateView.as_view(), name='static_file_create'),
    path('static-file/list/', views.StaticFileListView.as_view(), name='static_file_list'),
    # 安全文件下载（带令牌验证和频率限制）
    path('download/<int:file_id>/<str:token>/', views.secure_file_download, name='secure_download'),
    
    
    
    
    # Docker Image相关URL
    path('docker-image/create/', views.DockerImageCreateView.as_view(), name='docker_image_create'),
    path('docker-image/list/', views.DockerImageListView.as_view(), name='docker_image_list'),
    path('docker-image/<int:pk>/update/', views.DockerImageUpdateView.as_view(), name='docker_image_update'),
    path('api/v1/docker-image/<int:pk>/delete/', views.docker_image_delete, name='docker_image_delete'),
    # 添加静态文件删除URL
    path('api/v1/static-files/<int:pk>/delete/', views.static_file_delete, name='static_file_delete'),
    path('api/v1/captcha/refresh/', views.refresh_captcha, name='refresh_captcha'),
    
    # 容器引擎健康监控（支持 Docker 和 K8s）
    path('engine-health/', views.docker_health_dashboard, name='engine_health_dashboard'),
    path('api/v1/engine/<int:engine_id>/health/', views.docker_engine_health_check, name='engine_health_check'),
    path('api/v1/engines/health/', views.docker_engines_health_status, name='engines_health_status'),
    path('api/v1/engines/check-all/', views.docker_engines_check_all, name='engines_check_all'),
    
    # 向后兼容的 URL（废弃，但保留）
    path('docker-health/', views.docker_health_dashboard, name='docker_health_dashboard'),
    path('api/v1/docker-engine/<int:engine_id>/health/', views.docker_engine_health_check, name='docker_engine_health_check'),
    path('api/v1/docker-engines/health/', views.docker_engines_health_status, name='docker_engines_health_status'),
    path('api/v1/docker-engines/check-all/', views.docker_engines_check_all, name='docker_engines_check_all'),
    
    # 镜像状态异步刷新
    path('api/v1/docker-image/refresh-status/', views.refresh_image_status, name='refresh_image_status'),
    
    # K8s 安全监控
    path('security/', views.security_dashboard, name='security_dashboard'),
    path('api/v1/security/status/', views.security_status, name='security_status'),
    path('api/v1/security/engine/<int:engine_id>/pods/', views.security_monitor_pods, name='security_monitor_pods'),
    path('api/v1/security/engine/<int:engine_id>/events/', views.security_events, name='security_events'),
    path('api/v1/security/engine/<int:engine_id>/pod/<str:pod_name>/', views.security_pod_details, name='security_pod_details'),
    path('api/v1/security/engine/<int:engine_id>/pod/<str:pod_name>/connections/', views.security_pod_connections, name='security_pod_connections'),
    path('api/v1/security/engine/<int:engine_id>/resources/', views.security_resource_stats, name='security_resource_stats'),
    path('api/v1/security/cache/clear/', views.security_clear_cache, name='security_clear_cache'),

]
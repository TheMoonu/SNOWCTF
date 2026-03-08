/**
 * Docker 镜像状态异步刷新
 * 
 * 功能：点击刷新按钮异步加载镜像在各个引擎的状态
 */

(function() {
    'use strict';
    
    // 添加按钮 hover 样式
    const style = document.createElement('style');
    style.textContent = `
        .btn-refresh-image-status:hover:not(:disabled) {
            background: linear-gradient(to bottom, #d0ebff 0%, #b8deff 100%) !important;
            border-color: #9ac7e0 !important;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            transform: translateY(-1px);
        }
        .btn-refresh-image-status:active:not(:disabled) {
            background: linear-gradient(to bottom, #b8deff 0%, #a0d0ff 100%) !important;
            transform: translateY(0);
            box-shadow: inset 0 1px 2px rgba(0,0,0,0.1);
        }
    `;
    document.head.appendChild(style);
    
    // 等待 DOM 加载完成
    document.addEventListener('DOMContentLoaded', function() {
        initRefreshButtons();
    });
    
    /**
     * 初始化所有刷新按钮
     */
    function initRefreshButtons() {
        const buttons = document.querySelectorAll('.btn-refresh-image-status');
        buttons.forEach(function(button) {
            button.addEventListener('click', handleRefreshClick);
        });
    }
    
    /**
     * 处理刷新按钮点击
     */
    function handleRefreshClick(event) {
        event.preventDefault();
        
        const button = event.currentTarget;
        const imageId = button.getAttribute('data-image-id');
        
        if (!imageId) {
            console.error('缺少 image-id 属性');
            return;
        }
        
        // 禁用按钮并显示加载状态
        button.disabled = true;
        button.textContent = '⏳ 检查中...';
        button.style.background = 'linear-gradient(to bottom, #f0f0f0 0%, #e0e0e0 100%)';
        button.style.color = '#999';
        button.style.cursor = 'not-allowed';
        
        // 获取 CSRF token
        const csrfToken = getCsrfToken();
        
        // 发送 AJAX 请求
        fetch('/container/api/v1/docker-image/refresh-status/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-CSRFToken': csrfToken
            },
            body: 'image_id=' + encodeURIComponent(imageId)
        })
        .then(function(response) {
            if (!response.ok) {
                throw new Error('网络响应错误: ' + response.status);
            }
            return response.json();
        })
        .then(function(data) {
            if (data.success) {
                // 更新显示区域的 HTML
                const statusContainer = document.getElementById('image-status-' + imageId);
                if (statusContainer) {
                    statusContainer.innerHTML = data.html;
                    
                    // 重新绑定新按钮的事件
                    initRefreshButtons();
                }
                
                // 找到新的按钮并显示成功状态
                const newButton = statusContainer.querySelector('.btn-refresh-image-status');
                if (newButton) {
                    newButton.textContent = '✓ 已更新';
                    newButton.style.background = 'linear-gradient(to bottom, #e3ffe3 0%, #c8f5c8 100%)';
                    newButton.style.color = '#1a5f1a';
                    newButton.style.borderColor = '#8fc98f';
                    
                    // 3秒后恢复原状
                    setTimeout(function() {
                        newButton.textContent = '🔄 刷新';
                        newButton.style.background = 'linear-gradient(to bottom, #e3f4ff 0%, #cfe9ff 100%)';
                        newButton.style.color = '#205067';
                        newButton.style.borderColor = '#b4d5e6';
                    }, 3000);
                }
                
                console.log('镜像状态已更新:', data);
            } else {
                throw new Error(data.error || '刷新失败');
            }
        })
        .catch(function(error) {
            console.error('刷新镜像状态失败:', error);
            
            // 显示错误状态
            button.disabled = false;
            button.textContent = '✗ 失败';
            button.style.background = 'linear-gradient(to bottom, #ffe3e3 0%, #ffcfcf 100%)';
            button.style.color = '#a82828';
            button.style.borderColor = '#e89898';
            button.style.cursor = 'pointer';
            
            // 5秒后恢复原状
            setTimeout(function() {
                button.textContent = '🔄 刷新';
                button.style.background = 'linear-gradient(to bottom, #e3f4ff 0%, #cfe9ff 100%)';
                button.style.color = '#205067';
                button.style.borderColor = '#b4d5e6';
            }, 5000);
            
            // 显示错误提示
            alert('刷新状态失败: ' + error.message);
        });
    }
    
    /**
     * 获取 CSRF Token
     */
    function getCsrfToken() {
        // 从 cookie 获取
        const cookieValue = document.cookie
            .split('; ')
            .find(row => row.startsWith('csrftoken='));
        
        if (cookieValue) {
            return cookieValue.split('=')[1];
        }
        
        // 从表单获取
        const tokenInput = document.querySelector('[name=csrfmiddlewaretoken]');
        if (tokenInput) {
            return tokenInput.value;
        }
        
        // 从 meta 标签获取
        const tokenMeta = document.querySelector('meta[name="csrf-token"]');
        if (tokenMeta) {
            return tokenMeta.getAttribute('content');
        }
        
        console.warn('未找到 CSRF Token');
        return '';
    }
    
    // 支持动态加载的内容（如分页后）
    const observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(mutation) {
            if (mutation.addedNodes.length) {
                initRefreshButtons();
            }
        });
    });
    
    // 观察 changelist 容器的变化
    const changelistContainer = document.querySelector('#changelist');
    if (changelistContainer) {
        observer.observe(changelistContainer, {
            childList: true,
            subtree: true
        });
    }
    
})();


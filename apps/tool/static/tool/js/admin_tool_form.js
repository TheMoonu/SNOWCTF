/**
 * 工具管理表单的动态交互
 */
(function() {
    'use strict';
    
    // 等待 DOM 加载完成
    document.addEventListener('DOMContentLoaded', function() {
        // 获取表单元素
        const toolTypeField = document.getElementById('id_tool_type');
        const urlNameRow = document.querySelector('.field-url_name');
        const externalUrlRow = document.querySelector('.field-external_url');
        
        if (!toolTypeField) return;
        
        /**
         * 根据工具类型显示/隐藏相应字段
         */
        function toggleFields() {
            const toolType = toolTypeField.value;
            
            if (toolType === 'internal') {
                // 内部工具：显示 url_name，隐藏 external_url
                if (urlNameRow) {
                    urlNameRow.style.display = '';
                    // 添加必填标记
                    const label = urlNameRow.querySelector('label');
                    if (label && !label.classList.contains('required')) {
                        label.classList.add('required');
                    }
                }
                if (externalUrlRow) {
                    externalUrlRow.style.display = 'none';
                    // 清空外部链接的值
                    const externalUrlInput = document.getElementById('id_external_url');
                    if (externalUrlInput) {
                        externalUrlInput.value = '';
                    }
                }
            } else if (toolType === 'external') {
                // 外部工具：隐藏 url_name，显示 external_url
                if (urlNameRow) {
                    urlNameRow.style.display = 'none';
                    // 清空 URL 路由的值
                    const urlNameInput = document.getElementById('id_url_name');
                    if (urlNameInput) {
                        urlNameInput.value = '';
                    }
                }
                if (externalUrlRow) {
                    externalUrlRow.style.display = '';
                    // 添加必填标记
                    const label = externalUrlRow.querySelector('label');
                    if (label && !label.classList.contains('required')) {
                        label.classList.add('required');
                    }
                }
            }
        }
        
        // 初始化时执行一次
        toggleFields();
        
        // 监听工具类型变化
        toolTypeField.addEventListener('change', toggleFields);
        
        // 添加图标预览功能
        const iconField = document.getElementById('id_icon');
        if (iconField) {
            const iconPreview = document.createElement('div');
            iconPreview.style.cssText = 'margin-top: 10px; padding: 15px; background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 4px; width: 50%;';
            iconPreview.innerHTML = '<strong>图标预览：</strong> <i id="icon-preview-icon" class="fa fa-question-circle" style="font-size: 24px; margin-left: 10px; color: #6c757d;"></i>';
            
            iconField.parentNode.appendChild(iconPreview);
            
            const previewIcon = document.getElementById('icon-preview-icon');
            
            function updateIconPreview() {
                const iconClass = iconField.value.trim() || 'fa fa-question-circle';
                previewIcon.className = iconClass;
                previewIcon.style.color = iconField.value.trim() ? '#3b82f6' : '#6c757d';
            }
            
            // 初始化预览
            updateIconPreview();
            
            // 监听输入变化
            iconField.addEventListener('input', updateIconPreview);
            iconField.addEventListener('change', updateIconPreview);
        }
        
        // 添加常用图标快捷选择
        const iconFieldRow = document.querySelector('.field-icon');
        if (iconFieldRow && iconField) {
            const quickIcons = [
                { icon: 'fa fa-code', name: '代码' },
                { icon: 'fa fa-wrench', name: '扳手' },
                { icon: 'fa fa-link', name: '链接' },
                { icon: 'fa fa-globe', name: '地球' },
                { icon: 'fa fa-cog', name: '设置' },
                { icon: 'fa fa-terminal', name: '终端' },
                { icon: 'fa fa-file-code', name: '文件' },
                { icon: 'fa fa-cloud', name: '云' },
                { icon: 'fa fa-database', name: '数据库' },
                { icon: 'fa fa-calculator', name: '计算器' },
            ];
            
            const quickSelect = document.createElement('div');
            quickSelect.style.cssText = 'margin-top: 10px; padding: 10px; background: #fff3cd; border: 1px solid #ffc107; border-radius: 4px; width: 50%;';
            quickSelect.innerHTML = '<strong>快捷选择：</strong>';
            
            quickIcons.forEach(item => {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'button';
                btn.style.cssText = 'margin: 5px; padding: 5px 10px; font-size: 12px;';
                btn.innerHTML = `<i class="${item.icon}"></i> ${item.name}`;
                btn.onclick = function() {
                    iconField.value = item.icon;
                    iconField.dispatchEvent(new Event('input'));
                };
                quickSelect.appendChild(btn);
            });
            
            iconField.parentNode.appendChild(quickSelect);
        }
        
    });
})();


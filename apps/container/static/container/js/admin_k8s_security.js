/**
 * K8s 安全级别配置 JavaScript
 * 根据选择的安全级别自动填充安全配置
 */

(function() {
    'use strict';
    
    // 安全级别预设配置
    const SECURITY_PRESETS = {
        'LOW': {
            enable_network_policy: false,
            enable_seccomp: false,
            enable_service_account: true,
            allow_privileged: true,
            allow_host_network: false,
            allow_host_pid: false,
            allow_host_ipc: false,
            drop_capabilities: '',
            description: '宽松模式 - 适合高权限题目'
        },
        'MEDIUM': {
            enable_network_policy: true,
            enable_seccomp: true,
            enable_service_account: false,
            allow_privileged: false,
            allow_host_network: false,
            allow_host_pid: false,
            allow_host_ipc: false,
            drop_capabilities: 'NET_RAW,SYS_ADMIN,SYS_MODULE,SYS_PTRACE',
            description: '推荐模式 - 适合大多数题目'
        },
        'HIGH': {
            enable_network_policy: true,
            enable_seccomp: true,
            enable_service_account: false,
            allow_privileged: false,
            allow_host_network: false,
            allow_host_pid: false,
            allow_host_ipc: false,
            drop_capabilities: 'NET_RAW,SYS_ADMIN,SYS_MODULE,SYS_PTRACE,DAC_OVERRIDE,DAC_READ_SEARCH',
            description: '严格模式 - 高安全场景'
        },
        'CUSTOM': {
            description: '自定义配置'
        }
    };
    
    // 等待 DOM 加载完成
    document.addEventListener('DOMContentLoaded', function() {
        const securityLevelField = document.getElementById('id_security_level');
        const engineTypeField = document.getElementById('id_engine_type');
        
        if (!securityLevelField) {
            return; // 不是 DockerEngine 表单
        }
        
        // 获取所有安全配置字段
        const securityFields = {
            enable_network_policy: document.getElementById('id_enable_network_policy'),
            enable_seccomp: document.getElementById('id_enable_seccomp'),
            enable_service_account: document.getElementById('id_enable_service_account'),
            allow_privileged: document.getElementById('id_allow_privileged'),
            allow_host_network: document.getElementById('id_allow_host_network'),
            allow_host_pid: document.getElementById('id_allow_host_pid'),
            allow_host_ipc: document.getElementById('id_allow_host_ipc'),
            drop_capabilities: document.getElementById('id_drop_capabilities'),
        };
        
        // 显示/隐藏 K8s 安全策略字段组
        function toggleK8sSecurityFields() {
            const engineType = engineTypeField.value;
            const securityFieldset = document.querySelector('.module:has(#id_security_level)');
            
            if (securityFieldset) {
                if (engineType === 'KUBERNETES') {
                    securityFieldset.style.display = 'block';
                } else {
                    securityFieldset.style.display = 'none';
                }
            }
        }
        
        // 应用安全级别预设
        function applySecurityPreset(level) {
            const preset = SECURITY_PRESETS[level];
            
            if (!preset || level === 'CUSTOM') {
                // 自定义模式：启用所有字段
                Object.values(securityFields).forEach(field => {
                    if (field && field.tagName === 'INPUT' && field.type === 'checkbox') {
                        field.disabled = false;
                    } else if (field && field.tagName === 'TEXTAREA') {
                        field.disabled = false;
                    }
                });
                
                // 显示提示
                showSecurityHint(preset ? preset.description : '');
                return;
            }
            
            // 应用预设配置
            Object.keys(securityFields).forEach(fieldName => {
                const field = securityFields[fieldName];
                if (!field) return;
                
                const value = preset[fieldName];
                
                if (field.tagName === 'INPUT' && field.type === 'checkbox') {
                    field.checked = value;
                    // 非自定义模式：禁用字段（只读）
                    field.disabled = false; // 允许用户修改，但会切换到自定义模式
                } else if (field.tagName === 'TEXTAREA') {
                    field.value = value;
                    field.disabled = false;
                }
            });
            
            // 显示提示
            showSecurityHint(preset.description);
        }
        
        // 显示安全级别说明
        function showSecurityHint(description) {
            // 查找或创建提示元素
            let hintDiv = document.getElementById('security-level-hint');
            
            if (!hintDiv) {
                hintDiv = document.createElement('div');
                hintDiv.id = 'security-level-hint';
                hintDiv.style.cssText = 'margin-top: 6px; padding: 8px 12px; border-radius: 4px; font-size: 13px; width: 50%;';
                
                // 插入到 security_level 字段后面
                const securityLevelDiv = securityLevelField.closest('.form-row');
                if (securityLevelDiv) {
                    securityLevelDiv.appendChild(hintDiv);
                }
            }
            
            if (description) {
                hintDiv.innerHTML = description;
                hintDiv.style.backgroundColor = '#f8f9fa';
                hintDiv.style.border = '1px solid #dee2e6';
                hintDiv.style.color = '#495057';
            } else {
                hintDiv.innerHTML = '';
                hintDiv.style.display = 'none';
            }
        }
        
        // 添加简洁的字段提示
        function addFieldHints() {
            // 只保留最关键的提示
            const hints = {
                'drop_capabilities': '多个值用逗号分隔'
            };
            
            Object.keys(hints).forEach(fieldName => {
                const field = securityFields[fieldName];
                if (!field) return;
                
                const helpText = field.closest('.form-row')?.querySelector('.help');
                if (helpText) {
                    const originalText = helpText.textContent;
                    helpText.innerHTML = `${originalText} <em style="color: #999;">${hints[fieldName]}</em>`;
                }
            });
        }
        
        // 检测用户手动修改配置
        function detectManualChange() {
            const currentLevel = securityLevelField.value;
            
            if (currentLevel === 'CUSTOM') {
                return; // 已经是自定义模式
            }
            
            // 检查当前配置是否与预设一致
            const preset = SECURITY_PRESETS[currentLevel];
            if (!preset) return;
            
            let isModified = false;
            
            Object.keys(securityFields).forEach(fieldName => {
                const field = securityFields[fieldName];
                if (!field) return;
                
                const presetValue = preset[fieldName];
                let currentValue;
                
                if (field.tagName === 'INPUT' && field.type === 'checkbox') {
                    currentValue = field.checked;
                } else if (field.tagName === 'TEXTAREA') {
                    currentValue = field.value;
                }
                
                if (currentValue !== presetValue && presetValue !== undefined) {
                    isModified = true;
                }
            });
            
            if (isModified) {
                // 自动切换到自定义模式
                securityLevelField.value = 'CUSTOM';
                showSecurityHint('已切换到自定义模式');
            }
        }
        
        // 事件监听：安全级别变化
        if (securityLevelField) {
            securityLevelField.addEventListener('change', function() {
                const level = this.value;
                applySecurityPreset(level);
            });
            
            // 初始化时应用当前级别
            const currentLevel = securityLevelField.value;
            if (currentLevel) {
                applySecurityPreset(currentLevel);
            }
        }
        
        // 事件监听：引擎类型变化
        if (engineTypeField) {
            engineTypeField.addEventListener('change', toggleK8sSecurityFields);
            // 初始化
            toggleK8sSecurityFields();
        }
        
        // 事件监听：检测手动修改
        Object.values(securityFields).forEach(field => {
            if (field) {
                field.addEventListener('change', detectManualChange);
            }
        });
        
        // 添加字段提示
        addFieldHints();
    });
})();


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
            description: '🟡 宽松模式 - 适合需要高权限的题目（如 Docker-in-Docker）'
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
            description: '🟢 平衡模式 - 推荐使用，适合大多数题目'
        },
        'HIGH': {
            enable_network_policy: true,
            enable_seccomp: true,
            enable_service_account: false,
            allow_privileged: false,
            allow_host_network: false,
            allow_host_pid: false,
            allow_host_ipc: false,
            drop_capabilities: 'NET_RAW,SYS_ADMIN,SYS_MODULE,SYS_PTRACE,DAC_OVERRIDE,DAC_READ_SEARCH,SETUID,SETGID',
            description: '🔴 严格模式 - 高安全场景，最大限度限制容器权限'
        },
        'CUSTOM': {
            description: '⚙️ 自定义 - 手动配置所有安全选项'
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
                hintDiv.style.cssText = 'margin-top: 10px; padding: 12px; border-radius: 6px; font-size: 14px;';
                
                // 插入到 security_level 字段后面
                const securityLevelDiv = securityLevelField.closest('.form-row');
                if (securityLevelDiv) {
                    securityLevelDiv.appendChild(hintDiv);
                }
            }
            
            if (description) {
                hintDiv.innerHTML = `<strong>📋 说明：</strong> ${description}`;
                hintDiv.style.backgroundColor = '#e8f4f8';
                hintDiv.style.border = '1px solid #bee5eb';
                hintDiv.style.color = '#0c5460';
            } else {
                hintDiv.innerHTML = '';
                hintDiv.style.display = 'none';
            }
        }
        
        // 添加安全提示到各个字段
        function addFieldHints() {
            const hints = {
                'enable_network_policy': '✅ 推荐启用。限制容器出站流量，防止攻击外网',
                'enable_seccomp': '✅ 推荐启用。限制系统调用，降低内核漏洞风险',
                'enable_service_account': '⚠️ 建议禁用。防止通过 K8s API 攻击',
                'allow_privileged': '❌ 高风险。仅在必要时启用（如 Docker-in-Docker）',
                'allow_host_network': '❌ 高风险。会绕过 NetworkPolicy',
                'allow_host_pid': '❌ 高风险。可访问宿主机进程',
                'allow_host_ipc': '❌ 高风险。可访问宿主机 IPC',
                'drop_capabilities': '多个值用逗号分隔。推荐移除：NET_RAW, SYS_ADMIN, SYS_MODULE, SYS_PTRACE'
            };
            
            Object.keys(hints).forEach(fieldName => {
                const field = securityFields[fieldName];
                if (!field) return;
                
                const helpText = field.closest('.form-row')?.querySelector('.help');
                if (helpText) {
                    const originalText = helpText.textContent;
                    helpText.innerHTML = `${originalText}<br><em style="color: #666;">${hints[fieldName]}</em>`;
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
                showSecurityHint('⚙️ 自定义 - 检测到手动修改，已切换到自定义模式');
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


/**
 * 容器引擎管理表单的动态交互（支持 Docker 和 K8s）
 */
(function() {
    'use strict';
    
    // 等待 DOM 加载完成
    document.addEventListener('DOMContentLoaded', function() {
        // 获取表单元素
        const engineTypeField = document.getElementById('id_engine_type');
        const hostTypeField = document.getElementById('id_host_type');
        const portRow = document.querySelector('.field-port');
        const domainRow = document.querySelector('.field-domain');
        const tlsEnabledRow = document.querySelector('.field-tls_enabled');
        const caCertRow = document.querySelector('.field-ca_cert');
        const clientCertRow = document.querySelector('.field-client_cert');
        const clientKeyRow = document.querySelector('.field-client_key');
        
        // K8s 字段
        const kubeconfigFileRow = document.querySelector('.field-kubeconfig_file');
        const namespaceRow = document.querySelector('.field-namespace');
        
        // 调试：检查所有字段是否存在
        console.log('=== 容器引擎表单字段检查 ===');
        console.log('engineTypeField:', engineTypeField ? '✓' : '✗');
        console.log('hostTypeField:', hostTypeField ? '✓' : '✗');
        console.log('kubeconfigFileRow:', kubeconfigFileRow ? '✓' : '✗');
        console.log('namespaceRow:', namespaceRow ? '✓' : '✗');
        console.log('portRow:', portRow ? '✓' : '✗');
        console.log('tlsEnabledRow:', tlsEnabledRow ? '✓' : '✗');
        
        if (!hostTypeField && !engineTypeField) {
            console.warn('未找到引擎类型或主机类型字段，脚本退出');
            return;
        }
        
        /**
         * 显示/隐藏配置区块（fieldset）
         */
        function toggleFieldset(titleKeyword, show) {
            let found = false;
            document.querySelectorAll('h2').forEach(h2 => {
                if (h2.textContent.includes(titleKeyword)) {
                    const module = h2.closest('.module');
                    if (module) {
                        module.style.display = show ? '' : 'none';
                        found = true;
                    }
                }
            });
            if (!found && show) {
                console.warn(`  ⚠️  未找到包含 "${titleKeyword}" 的配置区块`);
            }
        }
        
        /**
         * 根据引擎类型和主机类型显示/隐藏相应字段
         */
        function toggleFields() {
            const engineType = engineTypeField ? engineTypeField.value : 'DOCKER';
            const hostType = hostTypeField ? hostTypeField.value : 'LOCAL';
            
            
            // K8s 引擎
            if (engineType === 'KUBERNETES') {
                console.log(' 切换到 Kubernetes 引擎');
                
                // 隐藏 Docker 专用字段
                if (hostTypeField && hostTypeField.closest('.form-row')) {
                    hostTypeField.closest('.form-row').style.display = 'none';
                }
                if (portRow) portRow.style.display = 'none';
                // 域名字段保持显示
                if (domainRow) domainRow.style.display = '';
                
                // 隐藏 TLS 相关字段
                if (tlsEnabledRow) tlsEnabledRow.style.display = 'none';
                if (caCertRow) caCertRow.style.display = 'none';
                if (clientCertRow) clientCertRow.style.display = 'none';
                if (clientKeyRow) clientKeyRow.style.display = 'none';
                
                // 显示 K8s 专用字段
                if (kubeconfigFileRow) {
                    kubeconfigFileRow.style.display = '';
                    
                }
                if (namespaceRow) {
                    namespaceRow.style.display = '';
                    
                }
                
                // 显示 K8s 配置区块，隐藏 TLS 配置区块
                toggleFieldset('K8s', true);
                toggleFieldset('TLS', false);
                
                document.querySelectorAll('h2').forEach(h2 => {
                    const text = h2.textContent.trim();
                    if (text.includes('安全策略') || text.includes('安全')) {
                        const module = h2.closest('.module');
                        if (module) {
                            module.style.display = '';
                
                        }
                    }
                });
                
            } else {
          
                
                // 显示 Docker 专用字段
                if (hostTypeField && hostTypeField.closest('.form-row')) {
                    hostTypeField.closest('.form-row').style.display = '';
                }
                
                // 隐藏 K8s 专用字段
                if (kubeconfigFileRow) {
                    kubeconfigFileRow.style.display = 'none';
                    console.log('  ✗ K8s kubeconfig 字段');
                }
                if (namespaceRow) {
                    namespaceRow.style.display = 'none';
                    console.log('  ✗ K8s namespace 字段');
                }
                
                // 隐藏 K8s 配置区块
                toggleFieldset('K8s', false);
                
                document.querySelectorAll('h2').forEach(h2 => {
                    const text = h2.textContent.trim();
                    if (text.includes('安全策略') || text.includes('安全')) {
                        const module = h2.closest('.module');
                        if (module) {
                            module.style.display = '';
                        }
                    }
                });
                
                // 根据主机类型显示/隐藏字段
                if (hostType === 'LOCAL') {
                   
                    
                    if (portRow) {
                        portRow.style.display = 'none';
                        const portInput = document.getElementById('id_port');
                        if (portInput) portInput.value = '';
                    }
                    
                    // 域名字段保持显示
                    if (domainRow) domainRow.style.display = '';
                    
                    // 隐藏所有 TLS 相关字段
                    if (tlsEnabledRow) tlsEnabledRow.style.display = 'none';
                    if (caCertRow) caCertRow.style.display = 'none';
                    if (clientCertRow) clientCertRow.style.display = 'none';
                    if (clientKeyRow) clientKeyRow.style.display = 'none';
                    
                    // 隐藏整个 TLS 配置区块
                    toggleFieldset('TLS', false);
                    
                    // 取消 TLS 勾选
                    const tlsCheckbox = document.getElementById('id_tls_enabled');
                    if (tlsCheckbox) tlsCheckbox.checked = false;
                    
                } else if (hostType === 'REMOTE') {
                    
                    
                    if (portRow) {
                        portRow.style.display = '';
                        const label = portRow.querySelector('label');
                        if (label && !label.classList.contains('required')) {
                            label.classList.add('required');
                        }
                    }
                    
                    if (domainRow) domainRow.style.display = '';
                    if (tlsEnabledRow) tlsEnabledRow.style.display = '';
                    
                    // 显示整个 TLS 配置区块
                    toggleFieldset('TLS', true);
                    
                    // TLS 证书字段根据 checkbox 显示/隐藏
                    toggleTLSFields();
                }
            }
        }
        
        /**
         * 根据TLS启用状态显示/隐藏证书字段
         */
        function toggleTLSFields() {
            const tlsCheckbox = document.getElementById('id_tls_enabled');
            const tlsEnabled = tlsCheckbox ? tlsCheckbox.checked : false;
            
            const certFields = [caCertRow, clientCertRow, clientKeyRow];
            
            certFields.forEach(function(row) {
                if (row) {
                    if (tlsEnabled) {
                        row.style.display = '';
                        // 添加必填标记
                        const label = row.querySelector('label');
                        if (label && !label.classList.contains('required')) {
                            label.classList.add('required');
                        }
                    } else {
                        row.style.display = 'none';
                        // 移除必填标记
                        const label = row.querySelector('label');
                        if (label) {
                            label.classList.remove('required');
                        }
                    }
                }
            });
        }
        
        document.querySelectorAll('h2').forEach(h2 => {
            const text = h2.textContent.trim();
            if (text.includes('安全策略') || text.includes('安全')) {
                const module = h2.closest('.module');
                if (module) {
                    module.style.display = '';
                }
            }
        });
        
        // 然后根据引擎类型调整其他字段
        toggleFields();
        
        // 监听引擎类型变化
        if (engineTypeField) {
            engineTypeField.addEventListener('change', toggleFields);
        }
        
        // 监听主机类型变化
        if (hostTypeField) {
            hostTypeField.addEventListener('change', toggleFields);
        }
        
        // 监听TLS启用状态变化
        const tlsCheckbox = document.getElementById('id_tls_enabled');
        if (tlsCheckbox) {
            toggleTLSFields(); // 初始化
            tlsCheckbox.addEventListener('change', toggleTLSFields);
        }
        
        // 添加引擎类型说明
        if (engineTypeField && engineTypeField.parentNode) {
            const engineTypeHelp = document.createElement('div');
            engineTypeHelp.style.cssText = 'margin-top: 8px; padding: 10px; background: #f8f9fa; border-left: 3px solid #6c757d; border-radius: 3px; font-size: 13px; width: 50%;';
            engineTypeHelp.innerHTML = `
                <strong>引擎类型：</strong>
                <span style="margin-left: 10px;">Docker - 小规模部署</span>
                <span style="margin-left: 15px;">Kubernetes - 中大规模集群</span>
            `;
            
            const fieldBox = engineTypeField.closest('.form-row') || engineTypeField.parentNode;
            fieldBox.appendChild(engineTypeHelp);
        }
        
        // 添加Docker主机类型说明
        if (hostTypeField && hostTypeField.parentNode) {
            const hostTypeHelp = document.createElement('div');
            hostTypeHelp.style.cssText = 'margin-top: 8px; padding: 10px; background: #fff3cd; border-left: 3px solid #ffc107; border-radius: 3px; font-size: 13px; width: 50%;';
            hostTypeHelp.innerHTML = `
                <strong>主机类型：</strong>
                <span style="margin-left: 10px;">本地 - 使用宿主机Docker服务，不建议生产环境使用</span>
                <span style="margin-left: 15px;">远程 - 使用独立主机Docker服务，建议生产环境使用</span>
            `;
            
            const fieldBox = hostTypeField.closest('.form-row') || hostTypeField.parentNode;
            fieldBox.appendChild(hostTypeHelp);
        }
        
        // 添加 K8s 字段提示
        const kubeconfigField = document.getElementById('id_kubeconfig_file');
        if (kubeconfigField) {
            const kubeconfigHelp = document.createElement('div');
            kubeconfigHelp.style.cssText = 'margin-top: 5px; font-size: 12px; color: #666;';
            kubeconfigField.parentNode.appendChild(kubeconfigHelp);
        }
        
        const namespaceField = document.getElementById('id_namespace');
        if (namespaceField) {
            const namespaceHelp = document.createElement('div');
            namespaceHelp.style.cssText = 'margin-top: 5px; font-size: 12px; color: #666;';
            namespaceHelp.innerHTML = '💡 留空使用 ctf-challenges';
            namespaceField.parentNode.appendChild(namespaceHelp);
        }
        
        // 添加 Docker TLS 证书字段提示
        const caCertField = document.getElementById('id_ca_cert');
        if (caCertField) {
            const caCertHelp = document.createElement('div');
            caCertHelp.style.cssText = 'margin-top: 5px; font-size: 12px; color: #856404;';
            caCertHelp.innerHTML = '💡 上传 ca.pem 文件';
            caCertField.parentNode.appendChild(caCertHelp);
        }
        
        const clientCertField = document.getElementById('id_client_cert');
        if (clientCertField) {
            const clientCertHelp = document.createElement('div');
            clientCertHelp.style.cssText = 'margin-top: 5px; font-size: 12px; color: #856404;';
            clientCertHelp.innerHTML = '💡 上传 cert.pem 文件';
            clientCertField.parentNode.appendChild(clientCertHelp);
        }
        
        const clientKeyField = document.getElementById('id_client_key');
        if (clientKeyField) {
            const clientKeyHelp = document.createElement('div');
            clientKeyHelp.style.cssText = 'margin-top: 5px; font-size: 12px; color: #856404;';
            clientKeyHelp.innerHTML = '💡 上传 key.pem 文件';
            clientKeyField.parentNode.appendChild(clientKeyHelp);
        }
    });
})();


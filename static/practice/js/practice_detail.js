/**
 * 练习模块异步容器创建管理器
 * 支持异步任务提交、轮询和状态更新
 */

// ==================== 立即执行：消除按钮抖动 ====================
// 在 DOM 加载完成前就检查 localStorage，立即隐藏加载占位符
(function() {
    // 等待 DOM 中的按钮元素出现
    function init() {
        const btn = document.querySelector('[id="createContainerBtn"]');
        if (!btn) {
            // 如果按钮还没加载，等待 DOMContentLoaded
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', init);
            }
            return;
        }
        
        const challengeUuid = btn.getAttribute('data-challenge-uuid');
        if (!challengeUuid) return;
        
        const containerTaskIdKey = `container_task_id_${challengeUuid}`;
        const containerCreatingKey = `container_creating_${challengeUuid}`;
        const containerCreatedKey = `container_created_${challengeUuid}`;
        
        const hasAnyContainerData = localStorage.getItem(containerTaskIdKey) || 
                                   localStorage.getItem(containerCreatingKey) || 
                                   localStorage.getItem(containerCreatedKey) === 'true';
        
        // 立即隐藏加载占位符
        const placeholder = document.getElementById('containerLoadingPlaceholder');
        if (placeholder) {
            placeholder.style.display = 'none';
        }
        
        // 如果没有容器数据，确保创建按钮可见
        if (!hasAnyContainerData) {
            btn.style.display = '';
            const destroyBtn = document.getElementById('destroyContainerBtn');
            if (destroyBtn) destroyBtn.style.display = 'none';
            const results = document.getElementById('results');
            if (results) results.style.display = 'none';
        }
    }
    
    init();
})();

$(document).ready(function () {
    // 使用属性选择器并获取第一个匹配元素的数据
    const $createBtn = $('[id="createContainerBtn"]').first();
    const challengeUuid = $createBtn.data('challenge-uuid');
    const csrfToken = $createBtn.data('csrf') || $('[name=csrfmiddlewaretoken]').val();



    // 如果没有找到按钮或UUID，说明页面不支持容器功能，直接返回
    if (!challengeUuid || !$createBtn.length) {

        return;
    }

    const containerStatusKey = `container_status_${challengeUuid}`;
    const containerInfoKey = `container_info_${challengeUuid}`;
    const containerCreatedKey = `container_created_${challengeUuid}`;
    const containerTaskIdKey = `container_task_id_${challengeUuid}`;
    const containerProgressKey = `container_progress_${challengeUuid}`; // 保存进度状态
    const containerCreatingKey = `container_creating_${challengeUuid}`; // 标记正在创建中（用于刷新恢复）

    
    let countdownInterval;
    let pollInterval;
    let isRequestPending = false;
    let currentTaskId = null;
    let currentProgress = 0; // 当前显示的进度
    let targetProgress = 0;  // 目标进度
    let progressInterval = null; // 进度动画定时器
    let lastServerProgress = 0; // 上次服务器返回的进度
    let stuckAtProgressTime = null; // 停留在某个进度的时间

    // ==================== 倒计时相关函数 ====================
    
    function startCountdown(expirationTime) {
        if (countdownInterval) {
            clearInterval(countdownInterval);
        }

        var countdownElement = $('#countdown');
        countdownInterval = setInterval(function () {
            var now = new Date().getTime();
            var distance = expirationTime - now;

            if (distance < 0) {
                clearInterval(countdownInterval);
                countdownElement.text("容器已过期");
                // 倒计时结束，触发容器销毁
                destroyContainer();
                return;
            }

            var hours = Math.floor((distance % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
            var minutes = Math.floor((distance % (1000 * 60 * 60)) / (1000 * 60));
            var seconds = Math.floor((distance % (1000 * 60)) / 1000);

            $('#hours-tens').text(Math.floor(hours / 10));
            $('#hours-ones').text(hours % 10);
            $('#minutes-tens').text(Math.floor(minutes / 10));
            $('#minutes-ones').text(minutes % 10);
            $('#seconds-tens').text(Math.floor(seconds / 10));
            $('#seconds-ones').text(seconds % 10);
        }, 1000);
    }

    function clearCountdown() {
        if (countdownInterval) {
            clearInterval(countdownInterval);
            countdownInterval = null;
        }
        //  清理进度动画定时器
        if (progressInterval) {
            clearInterval(progressInterval);
            progressInterval = null;
        }
    }

    // ==================== UI更新函数 ====================
    
    function updateContainerInfo(containerInfo) {
        //  检测是单入口还是多入口
        const isSingleEntry = typeof containerInfo.container_urls[0] === 'string';
        
        let urlsHtml = '容器访问地址: ';
        
        if (isSingleEntry) {
            // 单入口：简单显示
            urlsHtml += containerInfo.container_urls.map((url, index) => {
                const parsed = parseProtocolUrl(url);
                const isHttp = parsed.protocol === 'http' || parsed.protocol === 'https';
                
                if (isHttp) {
                    const fullUrl = url.includes('://') ? url : `${parsed.protocol}://${parsed.host}:${parsed.port}`;
                    const displayText = index === 0 ? fullUrl : parsed.port;
                    return `<a href="${fullUrl}" target="_blank" class="container-link">${displayText}</a>`;
                } else {
                    // 非HTTP协议显示连接信息（命令行格式：空格分隔）
                    return `<span>${parsed.protocol.toLowerCase()} ${parsed.host} ${parsed.port}</span>`;
                }
            }).join(' | ');
        } else {
            //  多入口：每行显示一个
            urlsHtml += '<br>' + containerInfo.container_urls.map((entry) => {
                const protocol = entry.protocol || 'http';
                const parsed = parseProtocolUrl(entry.url, protocol);
                const label = entry.label || '入口';
                const isHttp = protocol === 'http' || protocol === 'https';
                
                if (isHttp) {
                    const fullUrl = entry.url.includes('://') ? entry.url : `${protocol}://${parsed.host}:${parsed.port}`;
                    return `<strong>${label}:</strong> <a href="${fullUrl}" target="_blank" class="container-link">${fullUrl}</a>`;
                } else {
                    // 非HTTP协议显示连接信息（命令行格式：空格分隔）
                    return `<strong>${label} (${protocol.toLowerCase()}):</strong> ${parsed.host} ${parsed.port}`;
                }
            }).join('<br>');
        }
        
        $('#results').html(`
            <div class="alert alert-primary d-flex align-items-center" role="alert">
                <div class="containers-info flex-grow-1">
                    <div class="url-display">
                        ${urlsHtml}
                    </div>
                </div>
                <div class="countdown-wrapper d-flex align-items-center">
                    <div class="countdown-container" id="countdown">
                        <div class="countdown-block">
                            <div class="countdown-digits">
                                <span id="hours-tens">0</span><span id="hours-ones">0</span>
                            </div>
                        </div>
                        <div class="countdown-block">
                            <div class="countdown-digits">
                                <span id="minutes-tens">0</span><span id="minutes-ones">0</span>
                            </div>
                        </div>
                        <div class="countdown-block">
                            <div class="countdown-digits">
                                <span id="seconds-tens">0</span><span id="seconds-ones">0</span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `).show();
        startCountdown(new Date(containerInfo.expires_at).getTime());
        showDestroyButton();
    }
    
    /**
     * 解析协议URL（支持空格分隔格式和标准URL格式）
     */
    function parseProtocolUrl(url, defaultProtocol = 'http') {
        // 格式1: "protocol host port" (空格分隔，后端新格式)
        const spaceMatch = url.match(/^(\w+)\s+([^\s]+)\s+(\d+)$/);
        if (spaceMatch) {
            return { protocol: spaceMatch[1].toLowerCase(), host: spaceMatch[2], port: spaceMatch[3] };
        }
        
        // 格式2: "protocol://host:port" (标准URL格式)
        const urlMatch = url.match(/^(\w+):\/\/([^:\/]+):(\d+)/);
        if (urlMatch) {
            return { protocol: urlMatch[1].toLowerCase(), host: urlMatch[2], port: urlMatch[3] };
        }
        
        // 格式3: "host:port" (无协议前缀)
        const simpleMatch = url.match(/^([^:]+):(\d+)/);
        if (simpleMatch) {
            return { protocol: defaultProtocol, host: simpleMatch[1], port: simpleMatch[2] };
        }
        
        // 兜底：无法解析
        return { protocol: defaultProtocol, host: url, port: '' };
    }
    
    /**
     * 生成SSH连接命令（兼容多种格式）- 保留给旧代码使用
     */
    function generateSSHCommand(url) {
        const parsedUrl = parseProtocolUrl(url, 'ssh');
        return `ssh root@${parsedUrl.host} -p ${parsedUrl.port}`;
    }
    
    /**
     * 解析RDP URL（兼容多种格式）
     */
    function parseRDPUrl(url) {
        const parsedUrl = parseProtocolUrl(url, 'rdp');
        return { 
            host: parsedUrl.host, 
            port: parsedUrl.port || '3389' 
        };
    }
    
    /**
     * 解析VNC URL（兼容多种格式）
     */
    function parseVNCUrl(url) {
        const parsedUrl = parseProtocolUrl(url, 'vnc');
        const port = parsedUrl.port || '5900';
        const portNum = parseInt(port);
        // 如果有novnc服务，可以生成Web访问URL（通常是VNC端口+1000）
        const webUrl = portNum >= 5900 && portNum < 6000 
            ? `http://${parsedUrl.host}:${portNum + 1000}` 
            : null;
        return { 
            host: parsedUrl.host, 
            port: port, 
            webUrl: webUrl 
        };
    }
    
    //  复制到剪贴板
    window.copyToClipboard = function(text) {
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(text).then(() => {
                showSuccessToast('已复制到剪贴板');
            }).catch(() => {
                fallbackCopyToClipboard(text);
            });
        } else {
            fallbackCopyToClipboard(text);
        }
    };
    
    function fallbackCopyToClipboard(text) {
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        try {
            document.execCommand('copy');
            showSuccessToast('已复制到剪贴板');
        } catch (err) {
            showErrorToast('复制失败，请手动复制');
        }
        document.body.removeChild(textarea);
    }
    
    //  下载RDP文件
    window.downloadRDPFile = function(url, label) {
        const rdpInfo = parseRDPUrl(url);
        const rdpContent = `full address:s:${rdpInfo.host}:${rdpInfo.port}
screen mode id:i:2
use multimon:i:0
desktopwidth:i:1920
desktopheight:i:1080
session bpp:i:32
compression:i:1
keyboardhook:i:2
audiocapturemode:i:0
videoplaybackmode:i:1
connection type:i:7
networkautodetect:i:1
bandwidthautodetect:i:1
displayconnectionbar:i:1
enableworkspacereconnect:i:0
disable wallpaper:i:0
allow font smoothing:i:0
allow desktop composition:i:0
disable full window drag:i:1
disable menu anims:i:1
disable themes:i:0
disable cursor setting:i:0
bitmapcachepersistenable:i:1
audiomode:i:0
redirectprinters:i:1
redirectcomports:i:0
redirectsmartcards:i:1
redirectclipboard:i:1
redirectposdevices:i:0
autoreconnection enabled:i:1
authentication level:i:2
prompt for credentials:i:0
negotiate security layer:i:1
remoteapplicationmode:i:0
alternate shell:s:
shell working directory:s:
gatewayhostname:s:
gatewayusagemethod:i:4
gatewaycredentialssource:i:4
gatewayprofileusagemethod:i:0
promptcredentialonce:i:0
gatewaybrokeringtype:i:0
use redirection server name:i:0
rdgiskdcproxy:i:0
kdcproxyname:s:`;
        
        const blob = new Blob([rdpContent], { type: 'application/x-rdp' });
        const downloadUrl = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = downloadUrl;
        a.download = `${label.replace(/[^a-zA-Z0-9]/g, '_')}.rdp`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(downloadUrl);
        showSuccessToast('RDP文件已下载');
    };

    function showCreateButton() {

        
        // 使用属性选择器确保选中所有创建按钮（兼容HTML中可能存在的重复ID）
        const createBtns = $('[id="createContainerBtn"]');
        
        // 隐藏加载占位符
        $('#containerLoadingPlaceholder').hide();
        
        //  确保按钮已经从 loading 状态恢复
        createBtns.each(function() {
            const $btn = $(this);
            if ($btn.prop('disabled')) {
                const originalText = $btn.data('original-text');
                if (originalText) {
                    $btn.prop('disabled', false).html(originalText);
                }
            }
        });
        
        createBtns.show();
        $('#destroyContainerBtn').hide();
        $('#results').empty().hide();  //  清空内容，不仅是隐藏
        
    }

    function showDestroyButton() {
        // 隐藏加载占位符
        $('#containerLoadingPlaceholder').hide();
        // 使用属性选择器确保隐藏所有创建按钮
        $('[id="createContainerBtn"]').hide();
        $('#destroyContainerBtn').show();
    }

    function toggleButtonLoading(button, isLoading, loadingText) {
        loadingText = loadingText || 'Loading...';
        if (isLoading) {
            button.prop('disabled', true);
            button.html(`
                <div style="display: flex; align-items: center; justify-content: center;">
                <span class="spinner-border spinner-border-sm spinner-border-xs" role="status" aria-hidden="true" style="margin-right: 5px;"></span>
                <span>${loadingText}</span>
            </div>
            `);
        } else {
            button.prop('disabled', false);
            button.html(button.data('original-text'));
        }
    }

    function showProgressMessage(message, percent, skipSave) {
        percent = percent || 0;
        
        //  如果是100%，快速完成动画
        if (percent === 100 && currentProgress < 95) {
            currentProgress = 95;  // 立即跳到95%，让动画快速完成最后5%
        }
        
        // 检测是否在相同进度停留
        if (percent === lastServerProgress && percent >= 60 && percent < 90) {
            // 如果是第一次停留，记录时间
            if (!stuckAtProgressTime) {
                stuckAtProgressTime = Date.now();
            } else {
                // 如果停留超过2秒，模拟缓慢增长
                const stuckDuration = (Date.now() - stuckAtProgressTime) / 1000;
                if (stuckDuration > 2) {
                    // 缓慢增加到85%（模拟容器创建过程）
                    const simulatedProgress = Math.min(85, percent + Math.floor(stuckDuration));
                    percent = simulatedProgress;
                }
            }
        } else {
            // 进度变化了，重置停留时间
            lastServerProgress = percent;
            stuckAtProgressTime = null;
        }
        
        targetProgress = percent;
        
        // 持久化进度状态到 localStorage（用于页面刷新后恢复）
        if (!skipSave && percent < 100) {
            try {
                localStorage.setItem(containerProgressKey, JSON.stringify({
                    message: message,
                    percent: percent,
                    timestamp: Date.now()
                }));
            } catch (e) {
                console.warn('Failed to save progress to localStorage:', e);
            }
        }
        
        // 确保 results 容器存在且可见
        const $results = $('#results');
        if ($results.length === 0) {
            console.warn('Results container not found');
            return;
        }
        
        // 如果进度条不存在，创建它
        if ($results.find('.progress-card').length === 0) {
            currentProgress = 0;
            $results.html(`
                <div class="progress-card">
                    <div class="progress-header">
                        <span class="progress-message">${message}</span>
                        <span class="progress-percent" style="margin-left: auto; color: #007bff; font-weight: 600; font-size: 13px;">0%</span>
                    </div>
                    <div class="progress">
                        <div class="progress-bar" role="progressbar" style="width: 0%;" aria-valuenow="0" aria-valuemin="0" aria-valuemax="100"></div>
                    </div>
                </div>
            `);
        } else {
            // 只更新消息
            $('.progress-message').text(message);
        }
        
        // 确保进度条可见
        $results.show();
        
        // 启动平滑过渡动画
        if (progressInterval) {
            clearInterval(progressInterval);
        }
        
        progressInterval = setInterval(function() {
            if (currentProgress < targetProgress) {
                // 逐步增加进度（每次增加1-2%）
                var step = Math.max(1, Math.ceil((targetProgress - currentProgress) / 10));
                currentProgress = Math.min(currentProgress + step, targetProgress);
                
                // 更新显示
                $('.progress-percent').text(currentProgress + '%');
                $('.progress-bar').css('width', Math.max(currentProgress, 2) + '%')
                               .attr('aria-valuenow', currentProgress);
                
                // 达到目标后停止
                if (currentProgress >= targetProgress) {
                    clearInterval(progressInterval);
                    progressInterval = null;
                }
            }
        }, 100); // 每100ms更新一次
    }

    // ==================== 容器销毁函数 ====================
    
    function destroyContainer() {
        if (isRequestPending) {
            return;
        }
        
        isRequestPending = true;
        
        return $.ajax({
            url: '/snowlab/api/v1/destroy_web_container/',
            type: 'POST',
            data: {
                challenge_uuid: challengeUuid
            },
            headers: {
                "X-CSRFToken": csrfToken,
                "X-Requested-With": "XMLHttpRequest"
            },
            dataType: 'json',
            success: function(data) {
                // 清除本地存储的容器信息
                localStorage.removeItem(containerStatusKey);
                localStorage.removeItem(containerInfoKey);
                localStorage.removeItem(containerCreatedKey);
                localStorage.removeItem(containerTaskIdKey);
                localStorage.removeItem(containerProgressKey);
                localStorage.removeItem(containerCreatingKey);
                
                // 清空并隐藏结果区域
                $('#results').empty().hide();
                
                // 显示创建按钮
                showCreateButton();
                clearCountdown();
                
                if (typeof showSuccessToast === 'function') {
                    showSuccessToast("容器已自动销毁");
                }
                isRequestPending = false;
            },
            error: function(xhr, status, error) {
                console.error('容器销毁失败:', error);
                
                // 即使销毁失败，也清除本地存储（容器可能已被后端清理）
                localStorage.removeItem(containerStatusKey);
                localStorage.removeItem(containerInfoKey);
                localStorage.removeItem(containerCreatedKey);
                localStorage.removeItem(containerTaskIdKey);
                localStorage.removeItem(containerProgressKey);
                localStorage.removeItem(containerCreatingKey);
                
                // 清空并隐藏结果区域
                $('#results').empty().hide();
                
                showCreateButton();
                clearCountdown();
                
                if (typeof showErrorToast === 'function') {
                    showErrorToast("容器销毁失败，请刷新页面重试");
                }
                isRequestPending = false;
            },
            complete: function() {
                isRequestPending = false;
            }
        });
    }

    // ==================== 异步任务轮询函数 ====================
    
    function pollTaskStatus(taskId) {
        if (pollInterval) {
            clearTimeout(pollInterval);
        }

        const pollUrl = `/snowlab/container/task/${taskId}/`;
        let pollCount = 0;
        const maxPolls = 150; // 最多轮询150次（5分钟）
        let pollDelay = 2000; // 初始延迟2秒
        
        function doPoll() {
            pollCount++;
            
            if (pollCount > maxPolls) {
                localStorage.removeItem(containerTaskIdKey);
                localStorage.removeItem(containerProgressKey);
                currentTaskId = null;
                
                //  清理进度相关状态
                if (progressInterval) {
                    clearInterval(progressInterval);
                    progressInterval = null;
                }
                currentProgress = 0;
                targetProgress = 0;
                lastServerProgress = 0;
                stuckAtProgressTime = null;
                
                // 清空进度条并显示错误
                $('#results').html(`
                    <div class="alert alert-danger f-14 text-center" role="alert">
                        容器创建超时，请稍后重试
                    </div>
                `).show();
                
                showErrorToast("容器创建超时，请稍后重试");
                showCreateButton();
                return;
            }

            $.ajax({
                url: pollUrl,
                type: 'GET',
                headers: {
                    "X-Requested-With": "XMLHttpRequest"
                },
                dataType: 'json',
                success: function(response) {
                    const state = response.state;
                    const message = response.message || '';
                    const percent = response.percent || 0;

                    if (state === 'PENDING' || state === 'PROGRESS') {
                      // 更新进度信息（确保进度条可见）
                        showProgressMessage(message, percent);
                        

                        if (percent === 0) {
                            pollDelay = 2000; 
                        } else if (percent < 60) {
                            pollDelay = 2500; 
                        } else if (percent < 90) {
                            pollDelay = 3000; 
                        } else {
                            pollDelay = 2000; 
                        }
                        
                        // 继续轮询
                        pollInterval = setTimeout(doPoll, pollDelay);
                    } else if (state === 'SUCCESS') {
                        // 任务成功完成
                        const result = response.result;

                        showProgressMessage('容器创建成功', 100);
                        
                        // 清除进度状态
                        localStorage.removeItem(containerProgressKey);

                        setTimeout(function() {
                            if (result && result.container_urls && result.expires_at) {
                                localStorage.setItem(containerStatusKey, 'active');
                                localStorage.setItem(containerInfoKey, JSON.stringify(result));
                                localStorage.setItem(containerCreatedKey, 'true');
                                localStorage.removeItem(containerTaskIdKey);
                                
                                updateContainerInfo(result);
                                if (typeof showSuccessToast === 'function') {
                                    showSuccessToast("容器创建成功");
                                }
                            } else {
                                console.error('返回数据格式错误：', result);
                                $('#results').html(`
                                    <div class="alert alert-warning f-14 text-center" role="alert">
                                        容器创建成功但信息格式异常，请刷新页面
                                    </div>
                                `).show();
                            }
                            currentTaskId = null;
                        }, 500);  // 等待500ms
                    } else if (state === 'FAILURE') {
                        // 任务失败
                        const error = response.error || response.message || '容器创建失败';
                        
                        localStorage.removeItem(containerTaskIdKey);
                        localStorage.removeItem(containerProgressKey);
                        currentTaskId = null;
                        
                        // 清理进度相关状态
                        if (progressInterval) {
                            clearInterval(progressInterval);
                            progressInterval = null;
                        }
                        currentProgress = 0;
                        targetProgress = 0;
                        lastServerProgress = 0;
                        stuckAtProgressTime = null;
                        
                        $('#results').html(`
                            <div class="alert alert-danger f-14 text-center" role="alert">${error}</div>
                        `).show();
                        
                        showErrorToast(error);
                        showCreateButton();
                    } else if (state === 'REVOKED') {
                        // 任务已取消
                        localStorage.removeItem(containerTaskIdKey);
                        localStorage.removeItem(containerProgressKey);
                        currentTaskId = null;
                        
                        // 清理进度相关状态
                        if (progressInterval) {
                            clearInterval(progressInterval);
                            progressInterval = null;
                        }
                        currentProgress = 0;
                        targetProgress = 0;
                        lastServerProgress = 0;
                        stuckAtProgressTime = null;
                        
                        $('#results').html(`
                            <div class="alert alert-warning f-14 text-center" role="alert">容器创建已取消</div>
                        `).show();
                        
                        showWarningToast("容器创建已取消");
                        showCreateButton();
                    } else {
                        // 未知状态，停止轮询
                        localStorage.removeItem(containerTaskIdKey);
                        localStorage.removeItem(containerProgressKey);
                        currentTaskId = null;
                        
                        if (progressInterval) {
                            clearInterval(progressInterval);
                            progressInterval = null;
                        }
                        currentProgress = 0;
                        targetProgress = 0;
                        lastServerProgress = 0;
                        stuckAtProgressTime = null;
                        
                        $('#results').html(`
                            <div class="alert alert-danger f-14 text-center" role="alert">任务状态异常，请刷新页面重试</div>
                        `).show();
                        
                        showErrorToast('任务状态异常');
                        showCreateButton();
                    }
                },
                error: function(xhr, status, error) {
                    console.error('轮询任务状态失败:', error);
                    
                    // 如果是404，说明任务不存在，停止轮询
                    if (xhr.status === 404) {
                        localStorage.removeItem(containerTaskIdKey);
                        localStorage.removeItem(containerProgressKey);
                        currentTaskId = null;
                        
                        if (progressInterval) {
                            clearInterval(progressInterval);
                            progressInterval = null;
                        }
                        currentProgress = 0;
                        targetProgress = 0;
                        lastServerProgress = 0;
                        stuckAtProgressTime = null;
                        
                        $('#results').html(`
                            <div class="alert alert-danger f-14 text-center" role="alert">任务不存在或已过期</div>
                        `).show();
                        
                        showErrorToast("任务不存在或已过期");
                        showCreateButton();
                    } else {
                        // 网络错误，继续轮询
                        pollInterval = setTimeout(doPoll, pollDelay);
                    }
                }
            });
        }
        
        // 开始第一次轮询
        doPoll();
    }

    // ==================== 容器状态加载函数 ====================
    
    function loadContainerStatus() {
        // 快速检查：如果用户从未创建过容器，立即显示按钮（0延迟）
        const hasAnyContainerData = localStorage.getItem(containerTaskIdKey) || 
                                   localStorage.getItem(containerCreatingKey) || 
                                   localStorage.getItem(containerCreatedKey) === 'true';
        
        if (!hasAnyContainerData) {
            // 用户从未创建过容器，立即显示创建按钮
            $('#containerLoadingPlaceholder').hide();
            $('[id="createContainerBtn"]').show();
            $('#destroyContainerBtn').hide();
            $('#results').hide();
            return;
        }
        
        // 首先检查是否有正在进行的任务
        const savedTaskId = localStorage.getItem(containerTaskIdKey);
        
        if (savedTaskId) {

            currentTaskId = savedTaskId;
            
   
            $('#containerLoadingPlaceholder').hide();
            $('[id="createContainerBtn"]').hide();
            
            //  确保 results 容器存在
            if ($('#results').length === 0) {

                showCreateButton();
                return;
            }
            
            //  尝试从 localStorage 恢复上次的进度状态
            let restoredPercent = 15;
            let restoredMessage = '正在恢复任务状态...';
            try {
                const savedProgress = localStorage.getItem(containerProgressKey);
                if (savedProgress) {
                    const progressData = JSON.parse(savedProgress);
                    // 检查进度数据是否过期（5分钟内有效）
                    if (progressData.timestamp && (Date.now() - progressData.timestamp) < 5 * 60 * 1000) {
                        restoredPercent = Math.max(progressData.percent || 15, 15);
                        restoredMessage = progressData.message || '正在恢复任务状态...';
                    }
                }
            } catch (e) {
                console.warn('Failed to restore progress from localStorage:', e);
            }

            showProgressMessage(restoredMessage, restoredPercent, true);
            

            pollTaskStatus(savedTaskId);
            return;
        }
        
        //  检查是否有"正在创建中"的标记（用于处理刷新页面的情况）
        const creatingData = localStorage.getItem(containerCreatingKey);
        if (creatingData) {
            try {
                const creating = JSON.parse(creatingData);
                // 检查标记是否在5分钟内有效
                if (creating.timestamp && (Date.now() - creating.timestamp) < 5 * 60 * 1000) {
                    console.log('[容器恢复] 检测到正在创建中的任务，尝试恢复...', creating);
                    
                    // 隐藏按钮，显示进度条
                    $('#containerLoadingPlaceholder').hide();
                    $('[id="createContainerBtn"]').hide();
                    
                    // 确保 results 容器可见
                    $('#results').show();
                    showProgressMessage('正在恢复创建任务...', 10, true);
                    
                    // 重新发送创建请求，后端会返回已存在的 task_id
                    // 注意：必须使用刷新后的新 CSRF token
                    // 定义恢复函数（支持重试）
                    function attemptRecover(retryCount) {
                        retryCount = retryCount || 0;
                        const maxRetries = 5;
                        
                        $.ajax({
                            url: creating.url || $createBtn.data('ajax-url'),
                            type: 'POST',
                            data: {
                                challenge_uuid: challengeUuid,
                                csrfmiddlewaretoken: csrfToken
                            },
                            timeout: 30000,
                            success: function(response) {
                                localStorage.removeItem(containerCreatingKey);
                                
                                if ((response.status === 'pending' || response.status === 'queued') && response.task_id) {
                                    currentTaskId = response.task_id;
                                    localStorage.setItem(containerTaskIdKey, response.task_id);
                                    showProgressMessage('任务已恢复，继续创建中...', 20, true);
                                    pollTaskStatus(response.task_id);
                                } else if (response.status === 'existing') {
                                    localStorage.setItem(containerStatusKey, 'active');
                                    localStorage.setItem(containerInfoKey, JSON.stringify(response));
                                    localStorage.setItem(containerCreatedKey, 'true');
                                    updateContainerInfo(response);
                                } else if (response.container_urls && response.expires_at) {
                                    localStorage.setItem(containerStatusKey, 'active');
                                    localStorage.setItem(containerInfoKey, JSON.stringify(response));
                                    localStorage.setItem(containerCreatedKey, 'true');
                                    updateContainerInfo(response);
                                } else {
                                    $('#results').empty().hide();
                                    showCreateButton();
                                }
                            },
                            error: function(xhr) {
                                // 429 表示任务正在处理中，等待后重试
                                if (xhr.status === 429 && retryCount < maxRetries) {
                                    showProgressMessage('任务正在处理中，请稍候...', 15 + retryCount * 5, true);
                                    setTimeout(function() {
                                        attemptRecover(retryCount + 1);
                                    }, 3000);
                                    return;
                                }
                                
                                localStorage.removeItem(containerCreatingKey);
                                localStorage.removeItem(containerProgressKey);
                                $('#results').empty().hide();
                                showCreateButton();
                                
                                if (xhr.responseJSON && xhr.responseJSON.error) {
                                    showErrorToast(xhr.responseJSON.error);
                                }
                            }
                        });
                    }
                    
                    attemptRecover(0);
                    return;
                } else {
                    // 标记已过期，清除
                    console.log('[容器恢复] 创建标记已过期，清除');
                    localStorage.removeItem(containerCreatingKey);
                }
            } catch (e) {
                console.warn('[容器恢复] 解析创建标记失败:', e);
                localStorage.removeItem(containerCreatingKey);
            }
        }

        // 检查是否已有活跃容器
        if (localStorage.getItem(containerCreatedKey) === 'true') {
            //  隐藏占位符和创建按钮
            $('#containerLoadingPlaceholder').hide();
            $('[id="createContainerBtn"]').hide();
            
            $.ajax({
                url: '/snowlab/api/check_container_status/',
                type: 'GET',
                data: { 
                    challenge_uuid: challengeUuid,
                },
                timeout: 20000,
                success: function(response) {
                    if (response.status === 'active') {
                        updateContainerInfo(response);
                    } else {
                        // 容器已过期或不存在
                        showCreateButton();
                        localStorage.removeItem(containerStatusKey);
                        localStorage.removeItem(containerInfoKey);
                        localStorage.removeItem(containerCreatedKey);
                    }
                },
                error: function(xhr) {
                    //  区分不同的错误类型
                    if (xhr.status === 404) {
                        // 容器不存在
                        showCreateButton();
                        localStorage.removeItem(containerStatusKey);
                        localStorage.removeItem(containerInfoKey);
                        localStorage.removeItem(containerCreatedKey);
                    } else {
                        // 网络错误或服务器错误，显示重试提示
                        $('#results').html(`
                            <div class="alert alert-warning f-14 text-center" role="alert">
                                容器状态检查失败，请
                                <a href="javascript:void(0)" onclick="location.reload()" class="alert-link">刷新页面</a>
                                重试
                            </div>
                        `).show();
                    }
                }
            });
        } else {
            showCreateButton();
        }
    }

    // ==================== 事件处理函数 ====================
    
    function handleCreateContainer(e) {
        e.preventDefault();
        
        if (isRequestPending) return;
        
        var button = $(this);
        var csrf = button.data('csrf');
        var url = button.data('ajax-url');

        if (!url || !csrf) {
            console.error('Missing required data attributes');
            return;
        }

        //  第一步：立即保存"正在创建中"标记（必须在任何异步操作之前）
        // 这样即使用户立即刷新页面，标记也已经保存了
        try {
            localStorage.setItem(containerCreatingKey, JSON.stringify({
                timestamp: Date.now(),
                url: url
            }));
            console.log('[容器创建] 已保存创建标记到 localStorage');
        } catch (e) {
            console.warn('[容器创建] 保存创建标记失败:', e);
        }
        
        //  第二步：隐藏按钮并显示进度条
        $('[id="createContainerBtn"]').hide();
        showProgressMessage('正在提交创建任务...', 8);
        isRequestPending = true;

        $.ajax({
            url: url,
            type: 'POST',
            data: {
                challenge_uuid: challengeUuid,
                csrfmiddlewaretoken: csrf
            },
            timeout: 30000,
            success: function (response) {
                //  清除"正在创建中"标记
                localStorage.removeItem(containerCreatingKey);
                
                // 收到任务ID，开始轮询
                if (response.status === 'pending' && response.task_id) {
                    currentTaskId = response.task_id;
                    localStorage.setItem(containerTaskIdKey, response.task_id);
                    isRequestPending = false;
                    
           
                   
                    
                    // 开始轮询任务状态
                    pollTaskStatus(response.task_id);
                } else if (response.status === 'existing') {
                    // 容器已存在
                    localStorage.setItem(containerStatusKey, 'active');
                    localStorage.setItem(containerInfoKey, JSON.stringify(response));
                    localStorage.setItem(containerCreatedKey, 'true');
                    updateContainerInfo(response);
                    showSuccessToast("容器已存在");
                    isRequestPending = false;
                } else {
                    // 兼容旧的同步响应格式
                    localStorage.setItem(containerStatusKey, 'active');
                    localStorage.setItem(containerInfoKey, JSON.stringify(response));
                    localStorage.setItem(containerCreatedKey, 'true');
                    updateContainerInfo(response);
                    showSuccessToast("容器创建成功");
                    isRequestPending = false;
                }
            },
            error: function (xhr, status, error) {
                let errorMessage = "请求失败，请稍后重试";
                if (xhr.responseJSON && xhr.responseJSON.error) {
                    errorMessage = xhr.responseJSON.error;
                } else if (status === 'timeout') {
                    errorMessage = "请求超时，请稍后重试";
                }
                
                //  清除"正在创建中"标记
                localStorage.removeItem(containerCreatingKey);
                
                // 显示错误并恢复按钮
                isRequestPending = false;
                
                // 清理进度状态
                if (progressInterval) {
                    clearInterval(progressInterval);
                    progressInterval = null;
                }
                currentProgress = 0;
                targetProgress = 0;
                lastServerProgress = 0;
                stuckAtProgressTime = null;
                
                // 清空进度条卡片并隐藏
                $('#results').empty().hide();
                
                showCreateButton();
                
                if (typeof showErrorToast === 'function') {
                    showErrorToast(errorMessage);
                }
            }
        });
    }

    function handleDestroyContainer(e) {
        e.preventDefault();
        
        if (isRequestPending) return;
        
        var button = $(this);
        var csrf = button.data('csrf');
        var url = button.data('ajax-url');

        if (!url || !csrf) {
            console.error('Missing required data attributes');
            return;
        }

        button.data('original-text', button.html());
        toggleButtonLoading(button, true, '销毁中...');
        isRequestPending = true;

        $.ajax({
            url: url,
            type: 'POST',
            data: {
                challenge_uuid: challengeUuid,
                csrfmiddlewaretoken: csrf
            },
            timeout: 120000,
            success: function (response) {
                // 清除所有缓存和状态
                localStorage.removeItem(containerStatusKey);
                localStorage.removeItem(containerInfoKey);
                localStorage.removeItem(containerCreatedKey);
                localStorage.removeItem(containerTaskIdKey);
                localStorage.removeItem(containerProgressKey);
                localStorage.removeItem(containerCreatingKey);
                clearCountdown();
                
                // 清空并隐藏结果区域
                $('#results').empty().hide();
                
                // 显示创建按钮
                showCreateButton();
                
                // 显示成功提示
                if (typeof showSuccessToast === 'function') {
                    showSuccessToast("容器销毁成功");
                }
            },
            error: function (xhr, status, error) {
                let errorMessage = "请求失败，请稍后重试";
                if (xhr.responseJSON && xhr.responseJSON.error) {
                    errorMessage = xhr.responseJSON.error;
                } else if (status === 'timeout') {
                    errorMessage = "请求超时，请稍后重试";
                }
                
                // 清除localStorage以防止缓存问题
                localStorage.removeItem(containerStatusKey);
                localStorage.removeItem(containerInfoKey);
                localStorage.removeItem(containerCreatedKey);
                localStorage.removeItem(containerTaskIdKey);
                localStorage.removeItem(containerProgressKey);
                localStorage.removeItem(containerCreatingKey);
                clearCountdown();
                
                //  清空并隐藏结果区域
                $('#results').empty().hide();
                
                //  显示创建按钮（即使失败也要恢复按钮状态）
                showCreateButton();
                
                if (typeof showErrorToast === 'function') {
                    showErrorToast(errorMessage);
                }
            },
            complete: function () {
                toggleButtonLoading(button, false);
                isRequestPending = false;
            }
        });
    }

    // ==================== 事件绑定 ====================
    
    // 使用属性选择器绑定事件，支持多个创建按钮
    $(document).on('click', '[id="createContainerBtn"]', handleCreateContainer);
    $(document).on('click', '#destroyContainerBtn', handleDestroyContainer);

    // ==================== 页面生命周期管理 ====================
    
    // 页面卸载前清理
    $(window).on('beforeunload', function() {
        clearCountdown();
        if (pollInterval) {
            clearTimeout(pollInterval);
        }
    });


    
    // 立即执行，实现0延迟（DOM已经加载完成）
    loadContainerStatus();

});


/**
 * 挑战收藏 & 报名
 * 依赖：jQuery 3.x 、Bootstrap Icons
 * 用法：页面里只要按钮带对应 class 即可，无需再写任何内联 JS
 */
$(document).ready(function () {

    /* ===== 1. 工具 ===== */
    
    /* 页面加载时统一渲染初始图标 */
    $('.js-challenge-collect').each(function () {
        const $btn  = $(this);
        const $icon = $btn.find('i');
        const collected = $btn.data('collected');   // 初始值 true / false
        $icon.toggleClass('bi-heart-fill text-dark', collected)
            .toggleClass('bi-heart', !collected);
    });

    /* 点击事件：乐观切换 → 调接口 → 用后端返回的最新值重新渲染 */
    $(document).on('click', '.js-challenge-collect', function () {
        const $btn  = $(this);
        const $icon = $btn.find('i');
        const uuid  = $btn.data('uuid');

        /* 1. 乐观切换 */
        const oldCollected = $btn.data('collected');
        $icon.toggleClass('bi-heart-fill text-dark bi-heart');

        /* 2. 同步后端 */
        $btn.prop('disabled', true);
        postForm('/snowlab/api/v1/challenge/collect/', { challenge_uuid: uuid })
            .then(res => {
                /* 3. 用后端给的最新值重新设置一次，保证永远一致 */
                $btn.data('collected', res.collected);
                $icon.toggleClass('bi-heart-fill text-dark', res.collected)
                    .toggleClass('bi-heart', !res.collected);
                showSuccessToast(res.message);
            })
            .catch(() => {
                /* 出错回滚 */
                $icon.toggleClass('bi-heart-fill text-dark bi-heart');
                showErrorToast('网络错误');
            })
            .finally(() => $btn.prop('disabled', false));
    });
});

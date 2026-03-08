/**
 * 异步容器创建管理器 - 科技感主题版
 * 用于处理异步容器创建任务的提交、轮询和状态更新
 */

// ==================== 立即执行：消除按钮抖动 ====================
(function() {
    function init() {
        const btn = document.getElementById('createContainerBtn');
        if (!btn) {
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
        
        const placeholder = document.getElementById('containerLoadingPlaceholder');
        if (placeholder) {
            placeholder.style.display = 'none';
        }
        
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
    const challengeUuid = $('#createContainerBtn').data('challenge-uuid');
    const csrfToken = $('#createContainerBtn').data('csrf') || $('[name=csrfmiddlewaretoken]').val();

    const containerStatusKey = `container_status_${challengeUuid}`;
    const containerInfoKey = `container_info_${challengeUuid}`;
    const containerCreatedKey = `container_created_${challengeUuid}`;
    const containerTaskIdKey = `container_task_id_${challengeUuid}`;
    const containerCreatingKey = `container_creating_${challengeUuid}`;
    
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

        countdownInterval = setInterval(function () {
            var now = new Date().getTime();
            var distance = expirationTime - now;

            if (distance < 0) {
                clearInterval(countdownInterval);
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

        if (progressInterval) {
            clearInterval(progressInterval);
            progressInterval = null;
        }
    }

    // ==================== UI更新函数 ====================
    
    /**
     * 解析协议URL（支持空格分隔格式）
     */
    function parseProtocolUrl(url) {
        // 格式: "protocol host port" (空格分隔)
        const spaceMatch = url.match(/^(\w+)\s+([^\s]+)\s+(\d+)$/);
        if (spaceMatch) {
            return { protocol: spaceMatch[1].toLowerCase(), host: spaceMatch[2], port: spaceMatch[3] };
        }
        // 标准URL格式
        const urlMatch = url.match(/^(\w+):\/\/([^:\/]+):(\d+)/);
        if (urlMatch) {
            return { protocol: urlMatch[1].toLowerCase(), host: urlMatch[2], port: urlMatch[3] };
        }
        // 默认 http
        const simpleMatch = url.match(/^([^:]+):(\d+)/);
        if (simpleMatch) {
            return { protocol: 'http', host: simpleMatch[1], port: simpleMatch[2] };
        }
        return { protocol: 'http', host: url, port: '' };
    }
    
    function updateContainerInfo(containerInfo) {
        // 检测是否多入口
        const isSingleEntry = typeof containerInfo.container_urls[0] === 'string';
        
        let urlsHtml = '';
        
        if (isSingleEntry) {
            // 单入口：原来的样式
            urlsHtml = containerInfo.container_urls.map((url, index) => {
                const parsed = parseProtocolUrl(url);
                const isHttp = parsed.protocol === 'http' || parsed.protocol === 'https';
                
                if (isHttp) {
                    const fullUrl = url.includes('://') ? url : `${parsed.protocol}://${parsed.host}:${parsed.port}`;
                    const displayText = index === 0 ? fullUrl : parsed.port;
                    return `<a href="${fullUrl}" target="_blank" class="url-item">
                        <i class="fa fa-external-link"></i> ${displayText}
                    </a>`;
                } else {
                    // 非HTTP协议（命令行格式：空格分隔）
                    return `<span class="url-item" style="cursor: default;">
                        <i class="fa fa-terminal"></i> ${parsed.protocol.toLowerCase()} ${parsed.host} ${parsed.port}
                    </span>`;
                }
            }).join('');
        } else {
            // 多入口：显示每个入口
            urlsHtml = containerInfo.container_urls.map((entry) => {
                const protocol = entry.protocol || 'http';
                const parsed = parseProtocolUrl(entry.url);
                const label = entry.label || '入口';
                const isHttp = protocol === 'http' || protocol === 'https';
                
                if (isHttp) {
                    const fullUrl = entry.url.includes('://') ? entry.url : `${protocol}://${parsed.host}:${parsed.port}`;
                    return `<a href="${fullUrl}" target="_blank" class="url-item">
                        <i class="fa fa-external-link"></i> ${label}: ${fullUrl}
                    </a>`;
                } else {
                    // 非HTTP协议（命令行格式：空格分隔）
                    return `<span class="url-item" style="cursor: default;">
                        <i class="fa fa-terminal"></i> ${label} (${protocol.toLowerCase()}): ${parsed.host} ${parsed.port}
                    </span>`;
                }
            }).join('');
        }
        
        $('#results').html(`
            <div class="ctf-container-card">
                <div class="ctf-container-header">
                    <div class="ctf-container-status">
                        <span class="ctf-status-dot"></span>
                        <span>靶机运行中</span>
                    </div>
                    <div class="ctf-container-timer">
                        <i class="fa fa-clock-o"></i>
                        <span id="countdown-display">
                            <span id="hours-tens">0</span><span id="hours-ones">0</span>:<span id="minutes-tens">0</span><span id="minutes-ones">0</span>:<span id="seconds-tens">0</span><span id="seconds-ones">0</span>
                        </span>
                    </div>
                </div>
                <div class="ctf-container-urls">
                    ${urlsHtml}
                </div>
            </div>
        `).show();
        
        startCountdown(new Date(containerInfo.expires_at).getTime());
        showDestroyButton();
    }

    function showCreateButton() {
        const createBtn = $('#createContainerBtn');
        
        //  隐藏加载占位符
        $('#containerLoadingPlaceholder').hide();

        if (createBtn.prop('disabled')) {
            const originalText = createBtn.data('original-text');
            if (originalText) {
                createBtn.prop('disabled', false).html(originalText);
            }
        }
        
        createBtn.show();
        $('#destroyContainerBtn').hide();
        $('#results').empty().hide();  
    }

    function showDestroyButton() {
        //  隐藏加载占位符
        $('#containerLoadingPlaceholder').hide();
        $('#createContainerBtn').hide();
        $('#destroyContainerBtn').show();
    }

    function toggleButtonLoading(button, isLoading, loadingText) {
        loadingText = loadingText || 'Loading...';
        if (isLoading) {
            button.prop('disabled', true);
            button.html(`
                <div style="display: flex; align-items: center; justify-content: center; gap: 8px;">
                    <div style="width: 14px; height: 14px; border: 2px solid rgba(255,255,255,0.2); border-top-color: #fff; border-radius: 50%; animation: spin 0.8s linear infinite;"></div>
                    <span>${loadingText}</span>
                </div>
            `);
        } else {
            button.prop('disabled', false);
            button.html(button.data('original-text'));
        }
    }

    function showProgressMessage(message, percent) {
        percent = percent || 0;
        
        if (percent === 100 && currentProgress < 95) {
            currentProgress = 95;  
        }
        
        if (percent === lastServerProgress && percent >= 60 && percent < 90) {
            if (!stuckAtProgressTime) {
                stuckAtProgressTime = Date.now();
            } else {
                const stuckDuration = (Date.now() - stuckAtProgressTime) / 1000;
                if (stuckDuration > 2) {
                    const simulatedProgress = Math.min(85, percent + Math.floor(stuckDuration));
                    percent = simulatedProgress;
                }
            }
        } else {
            lastServerProgress = percent;
            stuckAtProgressTime = null;
        }
        
        targetProgress = percent;
        
        if ($('#results .ctf-progress-card').length === 0) {
            currentProgress = 0;
            $('#results').html(`
                <div class="ctf-progress-card">
                    <div class="ctf-progress-info">
                        <span class="ctf-progress-message">${message}</span>
                        <span class="ctf-progress-percent">0%</span>
                    </div>
                    <div class="ctf-progress-track">
                        <div class="ctf-progress-fill" style="width: 0%"></div>
                    </div>
                </div>
            `).show();
        } else {
            $('.ctf-progress-message').text(message);
        }
        
        if (progressInterval) {
            clearInterval(progressInterval);
        }
        
        progressInterval = setInterval(function() {
            if (currentProgress < targetProgress) {
                var step = Math.max(1, Math.ceil((targetProgress - currentProgress) / 10));
                currentProgress = Math.min(currentProgress + step, targetProgress);
                
                $('.ctf-progress-percent').text(currentProgress + '%');
                $('.ctf-progress-fill').css('width', Math.max(currentProgress, 2) + '%');
                
                if (currentProgress >= targetProgress) {
                    clearInterval(progressInterval);
                    progressInterval = null;
                }
            }
        }, 100);
    }

    // ==================== 容器销毁函数 ====================
    
    function destroyContainer() {
        if (isRequestPending) {
            return;
        }
        
        isRequestPending = true;
        
        // 禁用全局加载动画
        if (typeof disableTechLoadingOnce === 'function') {
            disableTechLoadingOnce();
        }
        
        return $.ajax({
            url: '/ctf/api/v1/destroy_web_container/',
            type: 'POST',
            data: {
                challenge_uuid: challengeUuid
            },
            headers: {
                "X-CSRFToken": csrfToken,
                "X-Requested-With": "XMLHttpRequest"
            },
            dataType: 'json',
            showLoading: false,  // 禁用全局加载动画
            success: function(data) {
                localStorage.removeItem(containerStatusKey);
                localStorage.removeItem(containerInfoKey);
                localStorage.removeItem(containerCreatedKey);
                localStorage.removeItem(containerTaskIdKey);
                localStorage.removeItem(containerCreatingKey);
                
                $('#results').empty().hide();
                showCreateButton();
                clearCountdown();
                
                if (typeof showSuccessToast === 'function') {
                    showSuccessToast("容器已自动销毁");
                }
                isRequestPending = false;
            },
            error: function(xhr, status, error) {
                localStorage.removeItem(containerStatusKey);
                localStorage.removeItem(containerInfoKey);
                localStorage.removeItem(containerCreatedKey);
                localStorage.removeItem(containerTaskIdKey);
                localStorage.removeItem(containerCreatingKey);
                
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
    
    function pollTaskStatus(taskId, competitionSlug) {
        if (pollInterval) {
            clearTimeout(pollInterval);
        }

        const pollUrl = `/ctf/api/v1/${competitionSlug}/container/task/${taskId}/`;
        let pollCount = 0;
        const maxPolls = 20;
        let pollDelay = 2000; 
        
        function doPoll() {
            pollCount++;
            
            if (pollCount > maxPolls) {
                localStorage.removeItem(containerTaskIdKey);
                currentTaskId = null;

                if (progressInterval) {
                    clearInterval(progressInterval);
                    progressInterval = null;
                }
                currentProgress = 0;
                targetProgress = 0;
                lastServerProgress = 0;
                stuckAtProgressTime = null;
                
                showCreateButton();
                if (typeof showErrorToast === 'function') {
                    showErrorToast("容器创建超时，请稍后重试");
                }
                return;
            }

            $.ajax({
                url: pollUrl,
                type: 'GET',
                headers: {
                    "X-Requested-With": "XMLHttpRequest"
                },
                dataType: 'json',
                showLoading: false,  
                success: function(response) {
                    const state = response.state;
                    const message = response.message || '';
                    const percent = response.percent || 0;

                    if (state === 'PENDING' || state === 'PROGRESS') {
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
                        
                      
                        pollInterval = setTimeout(doPoll, pollDelay);
                    } else if (state === 'SUCCESS') {
                      
                        const result = response.result;

                        
                    
                        showProgressMessage('容器创建成功', 100);
                        
                  
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
                                showCreateButton();
                                if (typeof showErrorToast === 'function') {
                                    showErrorToast('容器创建成功但信息格式异常，请刷新页面');
                                }
                            }
                            currentTaskId = null;
                        }, 500);  
                    } else if (state === 'FAILURE') {
                        const error = response.error || '容器创建失败';
                        
                        localStorage.removeItem(containerTaskIdKey);
                        currentTaskId = null;
                 
                        if (progressInterval) {
                            clearInterval(progressInterval);
                            progressInterval = null;
                        }
                        currentProgress = 0;
                        targetProgress = 0;
                        lastServerProgress = 0;
                        stuckAtProgressTime = null;
                        
                        showCreateButton();
                        if (typeof showErrorToast === 'function') {
                            showErrorToast(error);
                        }
                    } else if (state === 'REVOKED') {
                        localStorage.removeItem(containerTaskIdKey);
                        currentTaskId = null;
                        
                        if (progressInterval) {
                            clearInterval(progressInterval);
                            progressInterval = null;
                        }
                        currentProgress = 0;
                        targetProgress = 0;
                        lastServerProgress = 0;
                        stuckAtProgressTime = null;
                        
                        showCreateButton();
                        if (typeof showWarningToast === 'function') {
                            showWarningToast("容器创建已取消");
                        }
                    }
                },
                error: function(xhr, status, error) {
                    console.error('轮询任务状态失败:', error);
                    
                    if (xhr.status === 404) {
                        localStorage.removeItem(containerTaskIdKey);
                        currentTaskId = null;
                        
          
                        if (progressInterval) {
                            clearInterval(progressInterval);
                            progressInterval = null;
                        }
                        currentProgress = 0;
                        targetProgress = 0;
                        lastServerProgress = 0;
                        stuckAtProgressTime = null;
                        
                        showCreateButton();
                        if (typeof showErrorToast === 'function') {
                            showErrorToast("任务不存在或已过期");
                        }
                    } else {
                  
                        pollInterval = setTimeout(doPoll, pollDelay);
                    }
                }
            });
        }
        

        doPoll();
    }

    // ==================== 容器状态加载函数 ====================
    
    function loadContainerStatus() {
        // 快速检查：如果用户从未创建过容器，只隐藏占位符（按钮已显示，0延迟）
        const hasAnyContainerData = localStorage.getItem(containerTaskIdKey) || 
                                   localStorage.getItem(containerCreatingKey) || 
                                   localStorage.getItem(containerCreatedKey) === 'true';
        
        if (!hasAnyContainerData) {
            // 用户从未创建过容器，确保按钮可见
            $('#containerLoadingPlaceholder').hide();
            $('#createContainerBtn').show();
            $('#destroyContainerBtn').hide();
            $('#results').hide();
            return;
        }
        
        const savedTaskId = localStorage.getItem(containerTaskIdKey);
        if (savedTaskId) {
            const competitionSlug = $('#createContainerBtn').data('competition-slug');
            currentTaskId = savedTaskId;
            
            $('#containerLoadingPlaceholder').hide();
            showProgressMessage('正在恢复任务状态...', 15);
            pollTaskStatus(savedTaskId, competitionSlug);
            return;
        }
        
        // 检查是否有"正在创建中"的标记
        const creatingData = localStorage.getItem(containerCreatingKey);
        if (creatingData) {
            try {
                const creating = JSON.parse(creatingData);
                if (creating.timestamp && (Date.now() - creating.timestamp) < 5 * 60 * 1000) {
                    const competitionSlug = $('#createContainerBtn').data('competition-slug');
                    
                    $('#containerLoadingPlaceholder').hide();
                    $('#createContainerBtn').hide();
                    showProgressMessage('正在恢复创建任务...', 10);
                    
                    function attemptRecover(retryCount) {
                        retryCount = retryCount || 0;
                        const maxRetries = 5;
                        
                        $.ajax({
                            url: creating.url || $('#createContainerBtn').data('ajax-url'),
                            type: 'POST',
                            data: {
                                challenge_uuid: challengeUuid,
                                csrfmiddlewaretoken: csrfToken
                            },
                            timeout: 30000,
                            showLoading: false,  // 禁用加载动画（恢复任务过程中使用进度条）
                            success: function(response) {
                                localStorage.removeItem(containerCreatingKey);
                                
                                if ((response.status === 'pending' || response.status === 'queued') && response.task_id) {
                                    currentTaskId = response.task_id;
                                    localStorage.setItem(containerTaskIdKey, response.task_id);
                                    showProgressMessage('任务已恢复，继续创建中...', 20);
                                    pollTaskStatus(response.task_id, competitionSlug);
                                } else if (response.status === 'running' && response.access_url) {
                                    // 比赛模块返回的容器已存在格式
                                    const containerInfo = {
                                        container_urls: response.access_url,
                                        expires_at: response.expires_at
                                    };
                                    localStorage.setItem(containerStatusKey, 'active');
                                    localStorage.setItem(containerInfoKey, JSON.stringify(containerInfo));
                                    localStorage.setItem(containerCreatedKey, 'true');
                                    updateContainerInfo(containerInfo);
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
                                if ((xhr.status === 429 || xhr.status === 503) && retryCount < maxRetries) {
                                    showProgressMessage('任务正在处理中，请稍候...', 15 + retryCount * 5);
                                    setTimeout(function() {
                                        attemptRecover(retryCount + 1);
                                    }, 3000);
                                    return;
                                }
                                
                                localStorage.removeItem(containerCreatingKey);
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
                    localStorage.removeItem(containerCreatingKey);
                }
            } catch (e) {
                localStorage.removeItem(containerCreatingKey);
            }
        }

        if (localStorage.getItem(containerCreatedKey) === 'true') {
            $('#containerLoadingPlaceholder').hide();
            $('#results').html(`
                <div class="ctf-progress-card ctf-progress-loading">
                    <div class="ctf-progress-info">
                        <span class="ctf-progress-message">正在检查容器状态...</span>
                    </div>
                </div>
            `).show();
            
            $.ajax({
                url: '/ctf/api/v1/check_container_status/',
                type: 'GET',
                data: { 
                    challenge_uuid: challengeUuid,
                },
                timeout: 20000,
                showLoading: false,  
                success: function(response) {
                    if (response.status === 'active') {
                        updateContainerInfo(response);
                    } else {
                        showCreateButton();
                        localStorage.removeItem(containerStatusKey);
                        localStorage.removeItem(containerInfoKey);
                        localStorage.removeItem(containerCreatedKey);
                    }
                },
                error: function(xhr) {
                    if (xhr.status === 404) {
                        showCreateButton();
                        localStorage.removeItem(containerStatusKey);
                        localStorage.removeItem(containerInfoKey);
                        localStorage.removeItem(containerCreatedKey);
                    } else {
                        $('#results').html(`
                            <div class="ctf-progress-card ctf-progress-warning">
                                <div class="ctf-progress-info">
                                    <span class="ctf-progress-message">容器状态检查失败，请 <a href="javascript:void(0)" onclick="location.reload()">刷新页面</a> 重试</span>
                                </div>
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
        var competitionSlug = button.data('competition-slug');

        if (!url || !csrf) {
            return;
        }

        // 检查是否禁用加载动画
        if (button.hasClass('no-loading') || 
            button.attr('data-no-loading') !== undefined || 
            button.data('loading') === 'false') {
            if (typeof disableTechLoadingOnce === 'function') {
                disableTechLoadingOnce();
            }
        }

        // 第一步：保存创建标记
        try {
            localStorage.setItem(containerCreatingKey, JSON.stringify({
                timestamp: Date.now(),
                url: url
            }));
        } catch (e) {}
        
        // 第二步：隐藏按钮并显示进度条
        $('#createContainerBtn').hide();
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
            showLoading: false,  // 禁用全局加载动画，使用本地进度条
            success: function (response) {
                localStorage.removeItem(containerCreatingKey);
                
                if ((response.status === 'pending' || response.status === 'queued') && response.task_id) {
                    currentTaskId = response.task_id;
                    localStorage.setItem(containerTaskIdKey, response.task_id);
                    isRequestPending = false;
                    
                    pollTaskStatus(response.task_id, competitionSlug);
                } else if (response.status === 'running' && response.access_url) {
                    // 比赛模块返回的容器已存在格式
                    const containerInfo = {
                        container_urls: response.access_url,
                        expires_at: response.expires_at
                    };
                    localStorage.setItem(containerStatusKey, 'active');
                    localStorage.setItem(containerInfoKey, JSON.stringify(containerInfo));
                    localStorage.setItem(containerCreatedKey, 'true');
                    updateContainerInfo(containerInfo);
                    if (typeof showSuccessToast === 'function') {
                        showSuccessToast("容器已存在");
                    }
                    isRequestPending = false;
                } else {
                    localStorage.setItem(containerStatusKey, 'active');
                    localStorage.setItem(containerInfoKey, JSON.stringify(response));
                    localStorage.setItem(containerCreatedKey, 'true');
                    updateContainerInfo(response);
                    if (typeof showSuccessToast === 'function') {
                        showSuccessToast("容器创建成功");
                    }
                    isRequestPending = false;
                }
            },
            error: function (xhr, status, error) {
                localStorage.removeItem(containerCreatingKey);
                
                let errorMessage = "请求失败，请稍后重试";
                if (xhr.responseJSON && xhr.responseJSON.error) {
                    errorMessage = xhr.responseJSON.error;
                } else if (status === 'timeout') {
                    errorMessage = "请求超时，请稍后重试";
                }
                
                isRequestPending = false;
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

        // 检查是否禁用加载动画
        if (button.hasClass('no-loading') || 
            button.attr('data-no-loading') !== undefined || 
            button.data('loading') === 'false') {
            if (typeof disableTechLoadingOnce === 'function') {
                disableTechLoadingOnce();
            }
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
            showLoading: false,  // 禁用全局加载动画，使用按钮本身的加载状态
            success: function (response) {
                localStorage.removeItem(containerStatusKey);
                localStorage.removeItem(containerInfoKey);
                localStorage.removeItem(containerCreatedKey);
                localStorage.removeItem(containerTaskIdKey);
                localStorage.removeItem(containerCreatingKey);
                clearCountdown();
                
                $('#results').empty().hide();
                showCreateButton();
                
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
                
                localStorage.removeItem(containerStatusKey);
                localStorage.removeItem(containerInfoKey);
                localStorage.removeItem(containerCreatedKey);
                localStorage.removeItem(containerTaskIdKey);
                localStorage.removeItem(containerCreatingKey);
                clearCountdown();
    
                $('#results').empty().hide();
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
    
    $(document).on('click', '#createContainerBtn', handleCreateContainer);
    $(document).on('click', '#destroyContainerBtn', handleDestroyContainer);

    // ==================== 页面生命周期管理 ====================
    
    $(window).on('beforeunload', function() {
        clearCountdown();
        if (pollInterval) {
            clearInterval(pollInterval);
        }
    });

    loadContainerStatus();


    if (!document.getElementById('tech-spin-style')) {
        const style = document.createElement('style');
        style.id = 'tech-spin-style';
        style.textContent = `
            @keyframes spin {
                to { transform: rotate(360deg); }
            }
        `;
        document.head.appendChild(style);
    }

});


/**
 * 异步容器创建管理器
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
        console.log('[容器信息] 更新容器显示:', containerInfo);
        
        // 验证必要字段
        if (!containerInfo.container_urls || !Array.isArray(containerInfo.container_urls)) {
            console.error('[容器信息] container_urls字段缺失或格式错误，刷新页面可能解决');
            if (typeof showWarningToast === 'function') {
                showWarningToast("容器信息可能不完整，请刷新页面");
            }
            return;
        }
        
        if (!containerInfo.expires_at) {
            console.error('[容器信息] expires_at字段缺失');
            if (typeof showWarningToast === 'function') {
                showWarningToast("容器信息不完整，请刷新页面");
            }
            return;
        }
        
        // 检测是否多入口
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
                    return `<span">${parsed.protocol.toLowerCase()} ${parsed.host} ${parsed.port}</span>`;
                }
            }).join(' | ');
        } else {
            // 多入口：显示每个入口的信息
            urlsHtml += '<br>' + containerInfo.container_urls.map((entry) => {
                const protocol = entry.protocol || 'http';
                const parsed = parseProtocolUrl(entry.url);
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
        
        // 使用原来的样式
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
        $('#results').empty().hide();  //  清空内容，不仅是隐藏
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
        
        // 如果进度条不存在，创建它
        if ($('#results .progress-card').length === 0) {
            currentProgress = 0;
            $('#results').html(`
                <div class="progress-card">
                    <div class="progress-header">
                        <span class="progress-message">${message}</span>
                        <span class="progress-percent" style="margin-left: auto; color: #007bff; font-weight: 600; font-size: 13px;">0%</span>
                    </div>
                    <div class="progress">
                        <div class="progress-bar" role="progressbar" style="width: 0%;" aria-valuenow="0" aria-valuemin="0" aria-valuemax="100"></div>
                    </div>
                </div>
            `).show();
        } else {
            // 只更新消息
            $('.progress-message').text(message);
        }
        
        // 启动平滑过渡动画
        if (progressInterval) {
            clearInterval(progressInterval);
        }
        
        progressInterval = setInterval(function() {
            if (currentProgress < targetProgress) {
  
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
        }, 100); 
    }

    // ==================== 容器销毁函数 ====================
    
    function destroyContainer() {
        if (isRequestPending) {
            return;
        }
        
        isRequestPending = true;
        
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
                
                //  清理进度相关状态
                if (progressInterval) {
                    clearInterval(progressInterval);
                    progressInterval = null;
                }
                currentProgress = 0;
                targetProgress = 0;
                lastServerProgress = 0;
                stuckAtProgressTime = null;
                
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
                        const error = response.error || '容器创建失败';
                        
                        //  清理状态
                        localStorage.removeItem(containerTaskIdKey);
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
                        
                        //  手动控制按钮显示，不调用 showCreateButton() 以保留错误信息
                        $('#createContainerBtn').show();
                        $('#destroyContainerBtn').hide();
                        
                        $('#results').html(`
                            <div class="alert alert-danger f-14 text-center" role="alert">${error}</div>
                        `).show();
                        
                        showErrorToast(error);
                    } else if (state === 'REVOKED') {
                        // 任务已取消
                        
                        //  清理状态
                        localStorage.removeItem(containerTaskIdKey);
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
                        
                        //  手动控制按钮显示，不调用 showCreateButton() 以保留警告信息
                        $('#createContainerBtn').show();
                        $('#destroyContainerBtn').hide();
                        
                        $('#results').html(`
                            <div class="alert alert-warning f-14 text-center" role="alert">容器创建已取消</div>
                        `).show();
                        
                        showWarningToast("容器创建已取消");
                    }
                },
                error: function(xhr, status, error) {
                    console.error('轮询任务状态失败:', error);
                    
                    // 如果是404，说明任务不存在，停止轮询
                    if (xhr.status === 404) {
                        localStorage.removeItem(containerTaskIdKey);
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
                        
                        showCreateButton();
                        showErrorToast("任务不存在或已过期");
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
        
        // 首先检查是否有正在进行的任务
        const savedTaskId = localStorage.getItem(containerTaskIdKey);
        if (savedTaskId) {
            const competitionSlug = $('#createContainerBtn').data('competition-slug');
            currentTaskId = savedTaskId;
            
            // 隐藏加载占位符，显示进度
            $('#containerLoadingPlaceholder').hide();
            showProgressMessage('正在恢复任务状态...', 15);
            pollTaskStatus(savedTaskId, competitionSlug);
            return;
        }
        
        // 检查是否有"正在创建中"的标记（处理刷新页面的情况）
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

        // 检查是否已有活跃容器
        if (localStorage.getItem(containerCreatedKey) === 'true') {
            //  隐藏占位符，显示检查状态
            $('#containerLoadingPlaceholder').hide();
            $('#results').html(`
                <div class="alert alert-info f-14 text-center" role="alert">
                    <span class="spinner-border spinner-border-sm mr-2" role="status"></span>
                    正在检查容器状态...
                </div>
            `).show();
            
            $.ajax({
                url: '/ctf/api/v1/check_container_status/',
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
        var competitionSlug = button.data('competition-slug');

        if (!url || !csrf) {
            console.error('Missing required data attributes');
            return;
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
                
                if (progressInterval) {
                    clearInterval(progressInterval);
                    progressInterval = null;
                }
                currentProgress = 0;
                targetProgress = 0;
                lastServerProgress = 0;
                stuckAtProgressTime = null;
                
                $('#results').empty().hide();
                $('#createContainerBtn').show();
                $('#destroyContainerBtn').hide();
                
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
                
                $('#results').html(`<div class="alert alert-danger f-14 text-center" role="alert">${errorMessage}</div>`).show();
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
    
    // 页面卸载前清理
    $(window).on('beforeunload', function() {
        clearCountdown();
        if (pollInterval) {
            clearTimeout(pollInterval);
        }
    });

    // 页面加载时恢复状态
    loadContainerStatus();

});


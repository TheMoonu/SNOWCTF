$(document).ready(function () {
    const challengeUuid = $('#createContainerBtn').data('challenge-uuid');
    const csrfToken = $('#createContainerBtn').data('csrf') || $('[name=csrfmiddlewaretoken]').val();

    const containerStatusKey = `container_status_${challengeUuid}`;
    const containerInfoKey = `container_info_${challengeUuid}`;
    const containerCreatedKey = `container_created_${challengeUuid}`;
    let countdownInterval;
    let isRequestPending = false;
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
    }

    /**
     * 解析协议URL（支持空格分隔格式）
     */
    function parseProtocolUrl(url, defaultProtocol = 'http') {
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
            return { protocol: defaultProtocol, host: simpleMatch[1], port: simpleMatch[2] };
        }
        return { protocol: defaultProtocol, host: url, port: '' };
    }
    
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
                    return `<span style="color: #495057;">${parsed.protocol.toLowerCase()} ${parsed.host} ${parsed.port}</span>`;
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
                    ${urlsHtml}
                </div>
                <div class="countdown-wrapper d-flex align-items-center">
                    <i class="fa fa-clock-o mr-2"></i>
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
        $('#createContainerBtn').show();
        $('#destroyContainerBtn').hide();
        $('#results').hide();
    }

    function showDestroyButton() {
        $('#createContainerBtn').hide();
        $('#destroyContainerBtn').show();
    }

    function toggleButtonLoading(button, isLoading) {
        if (isLoading) {
            button.prop('disabled', true);
            button.html(`
                <div style="display: flex; align-items: center; justify-content: center;">
                <span class="spinner-border spinner-border-sm spinner-border-xs" role="status" aria-hidden="true" style="margin-right: 5px;"></span>
                <span>Loading...</span>
            </div>
            `);
        } else {
            button.prop('disabled', false);
            button.html(button.data('original-text'));
        }
    }
    // 销毁容器函数
    function destroyContainer() {
        // 防止重复请求
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
                // 清除本地存储的容器信息
                localStorage.removeItem(containerStatusKey);
                localStorage.removeItem(containerInfoKey);
                localStorage.removeItem(containerCreatedKey);
                
                // 显示创建按钮
                showCreateButton();
                clearCountdown();
                
                showSuccessToast("容器已自动销毁");
                isRequestPending = false;
            },
            error: function(xhr, status, error) {
                console.error('容器销毁失败:', error);
                
                // 即使销毁失败，也清除本地存储（容器可能已被后端清理）
                localStorage.removeItem(containerStatusKey);
                localStorage.removeItem(containerInfoKey);
                localStorage.removeItem(containerCreatedKey);
                
                showCreateButton();
                clearCountdown();
                
                showErrorToast("容器销毁失败，请刷新页面重试");
                isRequestPending = false;
            },
            complete: function() {
                isRequestPending = false;
            }
        });
    }


    function loadContainerStatus() {
        if (localStorage.getItem(containerCreatedKey) === 'true') {
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
                        showCreateButton();
                        localStorage.removeItem(containerStatusKey);
                        localStorage.removeItem(containerInfoKey);
                        localStorage.removeItem(containerCreatedKey);
                    }
                },
                error: function() {
                    showCreateButton();
                    localStorage.removeItem(containerStatusKey);
                    localStorage.removeItem(containerInfoKey);
                    localStorage.removeItem(containerCreatedKey);
                }
            });
        } else {
            showCreateButton();
        }
    }

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

        button.data('original-text', button.html());
        toggleButtonLoading(button, true);
        isRequestPending = true;

        $.ajax({
            url: url,
            type: 'POST',
            data: {
                challenge_uuid: challengeUuid,
                csrfmiddlewaretoken: csrf
            },
            timeout: 1200000,
            success: function (response) {
                localStorage.setItem(containerStatusKey, 'active');
                localStorage.setItem(containerInfoKey, JSON.stringify(response));
                localStorage.setItem(containerCreatedKey, 'true');
                updateContainerInfo(response);
            },
            error: function (xhr, status, error) {
                let errorMessage = "请求失败，请稍后重试";
                if (xhr.responseJSON && xhr.responseJSON.error) {
                    errorMessage = xhr.responseJSON.error;
                } else if (status === 'timeout') {
                    errorMessage = "请求超时，请稍后重试";
                }
                $('#results').html(`<div class="alert alert-danger f-14 text-center" role="alert">${errorMessage}</div>`).show();
            },
            complete: function () {
                toggleButtonLoading(button, false);
                isRequestPending = false;
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
        toggleButtonLoading(button, true);
        isRequestPending = true;

        $.ajax({
            url: url,
            type: 'POST',
            data: {
                challenge_uuid: challengeUuid,
                csrfmiddlewaretoken: csrf
            },
            timeout: 1200000,
            success: function (response) {
                $('#results').html(`<div class="alert alert-primary" role="alert">容器已摧毁</div>`).show();
                localStorage.removeItem(containerStatusKey);
                localStorage.removeItem(containerInfoKey);
                localStorage.removeItem(containerCreatedKey);
                clearCountdown();
                showCreateButton();
            },
            error: function (xhr, status, error) {
                let errorMessage = "请求失败，请稍后重试";
                if (xhr.responseJSON && xhr.responseJSON.error) {
                    errorMessage = xhr.responseJSON.error;
                } else if (status === 'timeout') {
                    errorMessage = "请求超时，请稍后重试";
                }
                $('#results').html(`<div class="alert alert-danger f-14 text-center" role="alert">${errorMessage}</div>`).show();
            },
            complete: function () {
                toggleButtonLoading(button, false);
                isRequestPending = false;
            }
        });
    }

    $(document).on('click', '#createContainerBtn', handleCreateContainer);
    $(document).on('click', '#destroyContainerBtn', handleDestroyContainer);

    // 页面卸载前清理
    $(window).on('beforeunload', function() {
        clearCountdown();
    });

    loadContainerStatus();

});

/**
 * 复制到剪贴板（全局函数）
 */
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
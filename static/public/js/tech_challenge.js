/**
 * 科技感CTF题目详情页 - FLAG提交
 * 容器管理功能请查看 async_container_tech.js
 */

// ==================== FLAG提交逻辑 ====================

$(document).ready(function () {
    var $submitFlag = $('#submit-flag');
    var $flagInput = $('#flag-input');
    var $result = $('#result');
    var challengeUuid = $submitFlag.data('challenge-uuid');
    // 容器相关的 localStorage keys（用于自动销毁时清理）
    const containerStatusKey = `container_status_${challengeUuid}`;
    const containerInfoKey = `container_info_${challengeUuid}`;
    const containerCreatedKey = `container_created_${challengeUuid}`;
    const containerTaskIdKey = `container_task_id_${challengeUuid}`;
    const fileDownloadedKey = `file_downloaded_${challengeUuid}`;
    const containerCreatingKey = `container_creating_${challengeUuid}`;
    var verifyFlagUrl = $submitFlag.data('verify-url');
    var csrfToken = $submitFlag.data('csrf');
    var submitTimeout = null;
    var hasStaticFile = $submitFlag.data('has-static-file') === 'True';
    var countdownInterval = null;

    // 检查是否已下载文件
    function checkFileDownloaded() {
        if (hasStaticFile && !localStorage.getItem(fileDownloadedKey)) {
            return false;
        }
        return true;
    }

    // 记录文件下载状态
    $('.challenge-file-download').click(function() {
        localStorage.setItem(fileDownloadedKey, 'true');
        
    });

    function disableSubmit(duration) {
        $submitFlag.prop('disabled', true);
        $submitFlag.find('.button-text').addClass('d-none');
        $submitFlag.find('.spinner-border').removeClass('d-none');
        clearTimeout(submitTimeout);
        submitTimeout = setTimeout(function() {
            $submitFlag.prop('disabled', false);
            $submitFlag.find('.button-text').removeClass('d-none');
            $submitFlag.find('.spinner-border').addClass('d-none');
        }, duration);
    }

    // 显示loading状态
    function showLoading() {
        $submitFlag.prop('disabled', true);
        $submitFlag.find('.button-text').addClass('d-none');
        $submitFlag.find('.button-loading').removeClass('d-none');
    }
    
    // 隐藏loading状态
    function hideLoading() {
        $submitFlag.prop('disabled', false);
        $submitFlag.find('.button-text').removeClass('d-none');
        $submitFlag.find('.button-loading').addClass('d-none');
    }

    function showResult(message, isSuccess) {
        // 在 #result 容器中显示科技感样式的消息 - 简约美观
        var iconClass = isSuccess ? 'fa-check-circle' : 'fa-times-circle';
        var colorClass = isSuccess ? 'success' : 'error';
        $result.html(`
            <div class="tech-flag-result tech-flag-result-${colorClass}">
                <i class="fa ${iconClass}"></i>
                <span>${message}</span>
            </div>
        `);
    }

    function showCreateButton() {
        $('#createContainerBtn').show();
        $('#destroyContainerBtn').hide();
        $('#cancelContainerBtn').hide();
        $('#results').hide();
    }
    
    function clearCountdown() {
        if (countdownInterval) {
            clearInterval(countdownInterval);
            countdownInterval = null;
        }
    }

    // 销毁容器函数（FLAG 正确时自动调用）
    function destroyContainer() {
        // 禁用全局加载动画
        if (typeof disableTechLoadingOnce === 'function') {
            disableTechLoadingOnce();
        }
        
        $.ajax({
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
                // 清除本地存储的容器信息
                localStorage.removeItem(containerStatusKey);
                localStorage.removeItem(containerInfoKey);
                localStorage.removeItem(containerCreatedKey);
                localStorage.removeItem(containerTaskIdKey);
                localStorage.removeItem(containerCreatingKey);
                
                // 更新UI：显示创建按钮，隐藏销毁按钮和容器信息
                showCreateButton();
                clearCountdown();
                
                // 显示成功提示
                showTechSuccess("容器自动销毁成功");
            },
            error: function(xhr, status, error) {
                // 即使销毁失败，也清理本地存储和更新UI
                localStorage.removeItem(containerStatusKey);
                localStorage.removeItem(containerInfoKey);
                localStorage.removeItem(containerCreatedKey);
                localStorage.removeItem(containerTaskIdKey);
                localStorage.removeItem(containerCreatingKey);
                
                showCreateButton();
                clearCountdown();
                
                showTechError("容器销毁失败");
            }
        });
    }

    $submitFlag.click(function () {
        if ($submitFlag.prop('disabled')) {
            return;
        }

        var flag = $flagInput.val().trim();
        if (!flag) {
            showResult('FLAG不能为空', false);
            return;
        }

 
        showLoading();

        // 禁用全局加载动画（使用按钮本身的加载状态）
        if ($submitFlag.hasClass('no-loading') || 
            $submitFlag.attr('data-no-loading') !== undefined || 
            $submitFlag.data('loading') === 'false') {
            if (typeof disableTechLoadingOnce === 'function') {
                disableTechLoadingOnce();
            }
        }

        $.ajax({
            url: verifyFlagUrl,
            type: "POST",
            data: {
                challenge_uuid: challengeUuid,
                flag: flag,
                filedownload: checkFileDownloaded() ? 'true' : 'false'
            },
            headers: {
                "X-CSRFToken": csrfToken
            },
            dataType: 'json',
            showLoading: false,  
            success: function (data) {
                
                hideLoading();
                
                showResult(data.message, data.status === 'success');
                if (data.status === 'success') {
                    $flagInput.val(''); // 清空输入框
                    
                    // flag正确，立即销毁容器
                    if (data.is_docker) {
                        showTechInfo("容器将自动销毁");
                        destroyContainer();
                    }
                }
            },
            error: function (jqXHR, textStatus, errorThrown) {
                // 先恢复按钮状态
                hideLoading();
                
                var errorMessage = '提交过程中发生错误，请稍后再试。';
                try {
                    var response = JSON.parse(jqXHR.responseText);
                    if (response.message) {
                        errorMessage = response.message;
                    }
                } catch (e) {
                    console.error("Error parsing JSON response: ", e);
                }
                showResult(errorMessage, false);
            },
            complete: function() {
                // 请求完成后确保隐藏loading（兜底保护）
                hideLoading();
            }
        });
    });

    // 允许用户按回车键提交
    $flagInput.keypress(function(e) {
        if (e.which == 13) { // 回车键的键码是 13
            $submitFlag.click();
            return false; // 防止表单提交
        }
    });
});


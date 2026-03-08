$(document).ready(function () {
    var $submitFlag = $('#submit-flag');
    var $flagInput = $('#flag-input');
    var $result = $('#result');
    var challengeUuid = $submitFlag.data('challenge-uuid');
    const containerStatusKey = `container_status_${challengeUuid}`;
    const containerInfoKey = `container_info_${challengeUuid}`;
    const containerCreatedKey = `container_created_${challengeUuid}`;
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
        var alertClass = isSuccess ? 'alert-success' : 'alert-danger';
        $result.html(`<div class="alert ${alertClass} f-14 text-center" role="alert">${message}</div>`);
    }

    function showCreateButton() {
        $('#createContainerBtn').show();
        $('#destroyContainerBtn').hide();
        $('#results').hide();
    }
    function clearCountdown() {
        if (countdownInterval) {
            clearInterval(countdownInterval);
            countdownInterval = null;
        }
    }
    // 销毁容器函数
    function destroyContainer() {
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
            success: function(data) {
                
                // 清除本地存储的容器信息
                localStorage.removeItem(containerStatusKey);
                localStorage.removeItem(containerInfoKey);
                localStorage.removeItem(containerCreatedKey);
                localStorage.removeItem(containerCreatingKey);
                showCreateButton();
                clearCountdown();
                showSuccessToast("容器自动销毁成功");
                
            },
            error: function(xhr, status, error) {
                showErrorToast("容器销毁失败");
                localStorage.removeItem(containerStatusKey);
                localStorage.removeItem(containerInfoKey);
                localStorage.removeItem(containerCreatedKey);
                localStorage.removeItem(containerCreatingKey);
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

        

        // 显示loading状态
        showLoading();

        $.ajax({
            url: verifyFlagUrl,
            type: "POST",
            data: {
                challenge_uuid: challengeUuid,
                flag: flag
            },
            headers: {
                "X-CSRFToken": csrfToken
            },
            dataType: 'json',
            success: function (data) {
                showResult(data.message, data.status === 'success');
                if (data.status === 'success') {
                    $flagInput.val(''); // 清空输入框
                    
                    
                    // flag正确，立即销毁容器
                    if (data.is_docker) {
                        showInfoToast("容器将自动摧毁");
                        destroyContainer();
                    }
                }
            },
            error: function (jqXHR, textStatus, errorThrown) {
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
                // 请求完成后隐藏loading
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
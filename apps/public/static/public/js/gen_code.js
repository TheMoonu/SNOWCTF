$(document).ready(function() {
    // CSRF令牌处理函数
    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }
    
    // 设置jQuery的AJAX默认CSRF处理
    $.ajaxSetup({
        beforeSend: function(xhr, settings) {
            if (!/^(GET|HEAD|OPTIONS|TRACE)$/i.test(settings.type) && !this.crossDomain) {
                xhr.setRequestHeader("X-CSRFToken", getCookie('csrftoken'));
            }
        }
    });

    // ===== 关注/取消关注功能 =====
    let currentBtn = null;
    
    $('.follow-btn').click(function() {
        var btn = $(this);
        var isFollowing = btn.text().trim() === '取消关注';
        
        if (isFollowing) {
            currentBtn = btn;  // 保存当前按钮引用
            $('#unfollowConfirmModal').modal('show');
            return;
        }
        
        performFollowAction(btn);
    });
    
    // 确认取消关注
    $('#confirmUnfollow').click(function() {
        $('#unfollowConfirmModal').modal('hide');
        if (currentBtn) {
            performFollowAction(currentBtn);
            currentBtn = null;
        }
    });
    
    function performFollowAction(btn) {
        var userId = btn.data('user-id');
        btn.prop('disabled', true);
        
        $.ajax({
            url: '/accounts/profile/follow/',
            type: 'POST',
            data: {
                'user_id': userId
                // 不需要手动添加CSRF令牌，已通过$.ajaxSetup设置
            },
            success: function(response) {
                if (response.status === 'success') {
                    if (response.data.is_following) {
                        btn.text('取消关注');
                    } else {
                        btn.text('关注');
                    }
                    
                    $('.followers-count').text(response.data.followers_count);
                    $('.following-count').text(response.data.following_count);
                    
                    toastr.success(response.message);
                } else {
                    toastr.error(response.message);
                }
            },
            error: function(xhr, errmsg, err) {
                toastr.error('操作失败，请稍后重试');
            },
            complete: function() {
                btn.prop('disabled', false);
            }
        });
    }

    // ===== 生成邀请码功能 =====
    $('.generate-invite-btn').click(function() {
        var btn = $(this);
        btn.prop('disabled', true);
        
        $.ajax({
            url: '/accounts/profile/generate_invite_code/',
            type: 'POST',
            // 不需要手动添加CSRF令牌，已通过$.ajaxSetup设置
            success: function(response) {
                if (response.status === 'success') {
                    var inviteCodeHtml = `
                         <div class="d-flex flex-column">
                            <div class="mb-2">邀请码：<span class="font-weight-bolder">${response.data.invite_code}</span>&nbsp;&nbsp;<i class="bi bi-clipboard copy-icon text-primary" style="font-size: 0.9rem; cursor: pointer;" data-toggle="tooltip" data-placement="top" title="copy" onclick="copyInviteCode('${response.data.invite_code}')"></i></div>
                            <small class="text-muted">有效期至：${response.data.expires_at}</small>
                        </div>
                    `;
                    $('.invite-code-section').html(inviteCodeHtml);
                    toastr.success(response.message);
                } else {
                    toastr.error(response.message);
                }
            },
            error: function(xhr, errmsg, err) {
                toastr.error('生成邀请码失败，请稍后重试');
            },
            complete: function() {
                btn.prop('disabled', false);
            }
        });
    });
});

// 复制邀请码函数（使用您原有的实现方式）
function copyInviteCode(code) {
    const input = document.createElement('input');
    input.value = code;
    document.body.appendChild(input);
    input.select();
    document.execCommand('copy');
    document.body.removeChild(input);
    
    // 获取复制图标元素
    const copyIcon = document.querySelector('.copy-icon');
    // 保存原始的类名
    const originalClass = copyIcon.className;
    // 更改图标为对勾
    copyIcon.className = 'bi bi-check2 text-primary';
    
    // 2秒后恢复原始图标
    setTimeout(() => {
        copyIcon.className = originalClass;
    }, 2000);
    
    toastr.success('邀请码已复制');
}
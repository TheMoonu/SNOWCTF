/**
 * 管理后台密码重置弹框
 */

// 创建弹框HTML（页面加载时插入）
document.addEventListener('DOMContentLoaded', function() {
    const modalHTML = `
        <div id="passwordResetModal" class="password-reset-modal" style="display: none;">
            <div class="password-reset-overlay"></div>
            <div class="password-reset-content">
                <div class="password-reset-header">
                    <h3>修改用户密码</h3>
                    <button type="button" class="password-reset-close" onclick="closeResetPasswordModal()">&times;</button>
                </div>
                <div class="password-reset-body">
                    <form id="passwordResetForm">
                        <input type="hidden" id="userId" name="user_id">
                        <div class="form-group">
                            <label>用户名：</label>
                            <input type="text" id="username" class="form-control" readonly>
                        </div>
                        <div class="form-group">
                            <label>新密码：<span class="text-danger">*</span></label>
                            <input type="password" id="newPassword" name="new_password" class="form-control" 
                                   placeholder="请输入新密码（至少8位）" required minlength="8">
                        </div>
                        <div class="form-group">
                            <label>确认密码：<span class="text-danger">*</span></label>
                            <input type="password" id="confirmPassword" name="confirm_password" class="form-control" 
                                   placeholder="请再次输入新密码" required minlength="8">
                        </div>
                        <div id="passwordError" class="alert alert-danger" style="display: none;"></div>
                        <div class="password-reset-footer">
                            <button type="button" class="btn btn-secondary" onclick="closeResetPasswordModal()">取消</button>
                            <button type="submit" class="btn btn-primary">确认修改</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    `;
    
    document.body.insertAdjacentHTML('beforeend', modalHTML);
    
    // 表单提交处理
    document.getElementById('passwordResetForm').addEventListener('submit', function(e) {
        e.preventDefault();
        
        const userId = document.getElementById('userId').value;
        const username = document.getElementById('username').value;
        const newPassword = document.getElementById('newPassword').value;
        const confirmPassword = document.getElementById('confirmPassword').value;
        const errorDiv = document.getElementById('passwordError');
        
        // 验证密码
        if (newPassword !== confirmPassword) {
            errorDiv.textContent = '两次输入的密码不一致！';
            errorDiv.style.display = 'block';
            return;
        }
        
        if (newPassword.length < 8) {
            errorDiv.textContent = '密码长度至少8位！';
            errorDiv.style.display = 'block';
            return;
        }
        
        errorDiv.style.display = 'none';
        
        // 发送AJAX请求
        const formData = new FormData();
        formData.append('user_id', userId);
        formData.append('new_password', newPassword);
        formData.append('csrfmiddlewaretoken', document.querySelector('[name=csrfmiddlewaretoken]').value);
        
        fetch('/admin/oauth/reset-password/', {
            method: 'POST',
            body: formData,
            headers: {
                'X-Requested-With': 'XMLHttpRequest'
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // 显示成功提示
                showSuccessMessage('密码修改成功！');
                closeResetPasswordModal();
                // 1秒后刷新页面
                setTimeout(() => location.reload(), 1000);
            } else {
                errorDiv.textContent = data.error || '密码修改失败';
                errorDiv.style.display = 'block';
            }
        })
        .catch(error => {
            errorDiv.textContent = '网络错误，请重试';
            errorDiv.style.display = 'block';
            console.error('Error:', error);
        });
    });
});

// 打开弹框
function openResetPasswordModal(userId, username) {
    document.getElementById('userId').value = userId;
    document.getElementById('username').value = username;
    document.getElementById('newPassword').value = '';
    document.getElementById('confirmPassword').value = '';
    document.getElementById('passwordError').style.display = 'none';
    document.getElementById('passwordResetModal').style.display = 'block';
}

// 关闭弹框
function closeResetPasswordModal() {
    document.getElementById('passwordResetModal').style.display = 'none';
}

// 点击遮罩关闭
document.addEventListener('click', function(e) {
    if (e.target.classList.contains('password-reset-overlay')) {
        closeResetPasswordModal();
    }
});

// 成功提示函数
function showSuccessMessage(message) {
    const toast = document.createElement('div');
    toast.className = 'password-reset-toast success';
    toast.innerHTML = `
        <i class="fa fa-check-circle"></i>
        <span>${message}</span>
    `;
    document.body.appendChild(toast);
    
    // 显示动画
    setTimeout(() => toast.classList.add('show'), 10);
    
    // 3秒后自动消失
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}


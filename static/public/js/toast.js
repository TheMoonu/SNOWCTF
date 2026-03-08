/**
 * 自定义Toast提示框工具库
 * 提供简单易用的提示框功能，替代浏览器原生alert
 */

// 确保DOM加载完成后初始化Toast容器
document.addEventListener('DOMContentLoaded', function() {
    // 如果页面中没有toast容器，则创建一个
    if (!document.querySelector('.custom-toast-container')) {
        const toastContainer = document.createElement('div');
        toastContainer.className = 'custom-toast-container';
        toastContainer.style.position = 'fixed';
        toastContainer.style.top = '20px';
        toastContainer.style.right = '20px';
        toastContainer.style.zIndex = '9999';
        document.body.appendChild(toastContainer);
    }
});

/**
 * 显示Toast提示框
 * @param {string} message - 提示消息内容
 * @param {string} title - 提示框标题
 * @param {number} delay - 显示时间（毫秒）
 * @param {string} type - 提示类型（info, success, warning, danger）
 */
function showToast(message, title = "提示", delay = 3000, type = "info") {
    // 获取或创建toast容器
    let toastContainer = document.querySelector('.custom-toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.className = 'custom-toast-container';
        document.body.appendChild(toastContainer);
    }
    
    // 创建唯一ID
    const toastId = 'toast-' + Date.now() + Math.random().toString(36).substr(2, 9);
    
    // 图标映射
    const iconMap = {
        'success': 'fa-check-circle',
        'warning': 'fa-exclamation-triangle',
        'danger': 'fa-times-circle',
        'info': 'fa-info-circle'
    };
    
    const icon = iconMap[type] || iconMap['info'];
    
    // 创建toast HTML（优化版）
    const toastHtml = `
        <div id="${toastId}" class="custom-toast custom-toast-${type}" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="custom-toast-header ${type !== 'info' ? 'custom-bg-' + type : ''}">
                <strong class="custom-title">
                    <i class="fa ${icon}"></i>${title}
                </strong>
                <div class="custom-header-right">
                    <small class="custom-time">${new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</small>
                    <button type="button" class="custom-close" onclick="handleCloseClick(this)" aria-label="关闭">
                        <span aria-hidden="true">&times;</span>
                    </button>
                </div>
            </div>
            <div class="custom-toast-body">
                ${message}
            </div>
        </div>
    `;
    
    // 添加toast到容器
    toastContainer.insertAdjacentHTML('beforeend', toastHtml);
    
    // 获取刚刚创建的toast元素
    const toastElement = document.getElementById(toastId);
    
    // 确保元素已添加到DOM，使用动画
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            toastElement.classList.add('show');
        });
    });
    
    // 设置自动关闭
    if (delay > 0) {
        setTimeout(() => {
            closeToast(toastElement);
        }, delay);
    }
    
    return toastElement;
}

/**
 * 显示成功提示框
 * @param {string} message - 提示消息内容
 * @param {string} title - 提示框标题
 * @param {number} delay - 显示时间（毫秒）
 */
function showSuccessToast(message, title = "成功", delay = 3000) {
    return showToast(message, title, delay, "success");
}

/**
 * 显示警告提示框
 * @param {string} message - 提示消息内容
 * @param {string} title - 提示框标题
 * @param {number} delay - 显示时间（毫秒）
 */
function showWarningToast(message, title = "警告", delay = 4000) {
    return showToast(message, title, delay, "warning");
}

/**
 * 显示错误提示框
 * @param {string} message - 提示消息内容
 * @param {string} title - 提示框标题
 * @param {number} delay - 显示时间（毫秒）
 */
function showErrorToast(message, title = "错误", delay = 4000) {
    return showToast(message, title, delay, "danger");
}

/**
 * 显示信息提示框
 * @param {string} message - 提示消息内容
 * @param {string} title - 提示框标题
 * @param {number} delay - 显示时间（毫秒）
 */
function showInfoToast(message, title = "提示", delay = 3000) {
    return showToast(message, title, delay, "info");
}

/**
 * 替代原生alert的函数
 * @param {string} message - 提示消息内容
 */
function toast(message) {
    return showToast(message, "提示", 3000, "info");
}

// 关闭函数 - 优化版
function closeToast(element) {
    const toast = element.closest ? element.closest('.custom-toast') : element;
    if (!toast || toast.classList.contains('removing')) return;
    
    // 添加removing类触发动画
    toast.classList.add('removing');
    toast.classList.remove('show');
    
    // 等待动画完成后移除元素
    const handleAnimationEnd = () => {
        toast.removeEventListener('animationend', handleAnimationEnd);
        if (toast.parentNode) {
            toast.remove();
        }
    };
    
    toast.addEventListener('animationend', handleAnimationEnd);
    
    // 备用：如果动画事件没触发，400ms后强制移除
    setTimeout(() => {
        if (toast.parentNode) {
            toast.remove();
        }
    }, 400);
}

// 优化关闭按钮点击处理
function handleCloseClick(button) {
    const toast = button.closest('.custom-toast');
    if (toast) {
        closeToast(toast);
    }
}



// ==================== 科技感提示框（全局函数） ====================

/**
 * 显示科技感成功提示
 */
function showTechSuccess(message) {
    showTechToast(message, 'success', '成功');
}

/**
 * 显示科技感错误提示
 */
function showTechError(message) {
    showTechToast(message, 'error', '错误');
}

/**
 * 显示科技感信息提示
 */
function showTechInfo(message) {
    showTechToast(message, 'info', '提示');
}

/**
 * 显示科技感警告提示
 */
function showTechWarning(message) {
    showTechToast(message, 'warning', '警告');
}

/**
 * 统一的 Toast 显示函数
 */
function showTechToast(message, type, title) {
    // 移除现有的 toast
    $('.tech-toast').remove();
    
    // 类型映射：将 danger 映射为 error
    if (type === 'danger') {
        type = 'error';
    }
    
    // 图标映射
    const iconMap = {
        'success': 'fa-check-circle',
        'error': 'fa-times-circle',
        'info': 'fa-info-circle',
        'warning': 'fa-exclamation-triangle'
    };
    
    const icon = iconMap[type] || 'fa-info-circle';
    
    // 创建 toast HTML
    const toast = $(`
        <div class="tech-toast tech-toast-${type}">
            <div class="tech-toast-icon">
                <i class="fa ${icon}"></i>
            </div>
            <div class="tech-toast-content">
                <div class="tech-toast-title">${title}</div>
                <div class="tech-toast-message">${message}</div>
            </div>
        </div>
    `);
    
    // 添加到页面
    $('body').append(toast);
    
    // 触发显示动画
    setTimeout(() => {
        toast.addClass('tech-toast-show');
    }, 50);
    
    // 5秒后自动隐藏
    setTimeout(() => {
        toast.removeClass('tech-toast-show');
        setTimeout(() => {
            toast.remove();
        }, 300);
    }, 5000);
}
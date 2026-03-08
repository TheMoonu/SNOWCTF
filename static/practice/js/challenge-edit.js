const ChallengeEdit = {
    openEditModal(uuid, title, description, hint) {
        // 填充表单数据
        document.getElementById('edit-challenge-uuid').value = uuid;
        document.getElementById('challenge-title').value = title;
        document.getElementById('challenge-description').value = description;
        document.getElementById('challenge-hint').value = hint;
        
        // 显示模态框
        $('#editChallengeModal').modal('show');
    },

    init() {
        const editForm = document.getElementById('edit-challenge-form');
        if (editForm) {
            editForm.addEventListener('submit', function(e) {
                e.preventDefault();
                const formData = new FormData(this);
                
                $.ajax({
                    url: '/snowlab/api/v1/challenge/edit/',
                    type: 'POST',
                    data: formData,
                    processData: false,
                    contentType: false,
                    headers: {
                        'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value,
                        'X-Requested-With': 'XMLHttpRequest'
                    },
                    success: function(response) {
                        // 获取重定向URL并跳转
                        
                        if (response.redirect) {
                            window.location.href = response.redirect
                        } else {
                            window.location.reload(); // 如果没有重定向URL则刷新
                        }
                    },
                    error: function(xhr) {
                        // 同样跟随重定向
                        const redirectUrl = xhr.getResponseHeader('Location');
                        if (redirectUrl) {
                            window.location.href = redirectUrl;
                        } else {
                            window.location.reload();
                        }
                    }
                });
            });
        }
    }
};

// 当文档加载完成后初始化
$(document).ready(function() {
    ChallengeEdit.init();
});
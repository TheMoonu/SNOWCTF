$(function () {
    // 标为已读和删除操作
    $(document).on('click', '.btn-read, .btn-delete', function (e) {
        e.preventDefault();
        
        const $btn = $(this);
        const id = $btn.data('id');
        const tag = $btn.data('tag');
        const CSRF = $btn.data('csrf');
        const URL = $btn.data('url');
        
        // 禁用按钮防止重复点击
        $btn.prop('disabled', true);
        
        $.ajax({
            type: 'POST',
            url: URL,
            data: {
                'id': id,
                'tag': tag,
                'csrfmiddlewaretoken': CSRF
            },
            dataType: 'json',
            headers: {
                'X-Requested-With': 'XMLHttpRequest'
            },
            success: function (ret) {
                if (ret.code === 0) {
                    // 操作成功，刷新页面
                    window.location.reload();
                } else {
                    showWarningToast(ret.msg || '操作失败');
                    $btn.prop('disabled', false);
                }
            },
            error: function (xhr, status, error) {
                console.error('操作失败:', error);
                showWarningToast('操作失败，请重试');
                $btn.prop('disabled', false);
            }
        });
    });
});

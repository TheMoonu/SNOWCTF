function article_update_save(csrf, api_url, article_slug) {
    const article_body = testEditor.getMarkdown();
    const article_img_link = $.trim($('#article-img-link').val());
    const change_img_link_flag = $('#change-img-link-flag').prop('checked');
    $.ajaxSetup({
        data: {
            csrfmiddlewaretoken: csrf
        }
    });
    $.ajax({
        type: 'post',
        url: api_url,
        data: {
            'article_slug': article_slug,
            'article_img_link': article_img_link,
            'change_img_link_flag': change_img_link_flag,
            'article_body': article_body
        },
        dataType: 'json',
        success: function (data) {
            if (data.code === 0) {
                window.location.href = data.data.callback
            }
        },
    })
}

function friend_post() {
    const regex = /^https:\/\/([\w.-]+)\.([a-z]{2,})(\/\S*)?$/i;
    const friend_name = $.trim($('#link-name').val());
    const friend_link = $.trim($('#link-link').val());
    const friend_description = $.trim($('#link-description').val());
    const btn = $('#friend-send');
    const csrf = btn.data('csrf');
    const api_url = btn.data('api-url');

    if (friend_name === '' || friend_link === '' || friend_description === '') {
        // 如果有任何一个参数为空直接不请求
        showErrorToast("🤔️有空值，不允许提交！！！");
        return;
    }

    if (!regex.test(friend_link)) {
        showErrorToast("非合法https地址，不允许提交！！！");
        return;
    }

    $.ajaxSetup({
        data: {
            csrfmiddlewaretoken: csrf
        }
    });
    $.ajax({
        type: 'post',
        url: api_url,
        data: {
            'name': friend_name,
            'link': friend_link,
            'description': friend_description
        },
        dataType: 'json',
        success: function (data) {
            if (data.code === 0) {
                $('#friendModal').modal('hide')
                setTimeout(function () {
                    showSuccessToast("😊提交成功，已通知管理员审核！\n请勿重复提交，谢谢合作🙏");
                }, 500);
            } else {
                showErrorToast('😭提交失败，请检查格式重试！');
            }
        },
    })
}
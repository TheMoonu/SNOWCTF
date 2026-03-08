// 简化版评论编辑器 - 文章版（不使用 SimpleMDE）
$(function() {
    var commentForm = $('#comment-form');
    
    // 表情点击事件
    $('.emoji-btn').click(function(e) {
        e.preventDefault();
        var emoji = $(this).find('img').data('emoji');
        var textarea = document.getElementById('comment-form');
        var cursorPos = textarea.selectionStart;
        var textBefore = textarea.value.substring(0, cursorPos);
        var textAfter = textarea.value.substring(cursorPos);
        
        // 插入表情
        textarea.value = textBefore + emoji + ' ' + textAfter;
        
        // 设置光标位置
        var newCursorPos = cursorPos + emoji.length + 1;
        textarea.setSelectionRange(newCursorPos, newCursorPos);
        textarea.focus();
        
        // 关闭下拉菜单
        $('#emoji-list').parent().removeClass('show');
        $('#emoji-list').removeClass('show');
    });
    
    // 回复功能
    $(".rep-btn").click(function() {
        commentForm.val('');
        var u = $(this).data('repuser');
        var i = $(this).data('repid');
        sessionStorage.setItem('rep_id', i);
        $("#rep-to").text("回复 @" + u).removeClass('hidden');
        $("#no-rep").removeClass('hidden');
        $(".rep-btn").css("color", "#868e96");
        $(this).css("color", "red");
        $('html, body').animate({
            scrollTop: $($.attr(this, 'href')).offset().top - 55
        }, 500);
    });
    
    // 取消回复
    $("#no-rep").click(function() {
        commentForm.val('');
        sessionStorage.removeItem('rep_id');
        $("#rep-to").text('').addClass('hidden');
        $("#no-rep").addClass('hidden');
        $(".rep-btn").css("color", "#868e96");
    });
    
    // 提交评论
    $("#push-com").click(function() {
        var content = commentForm.val().trim();
        
        if (content.length === 0) {
            showWarningToast("评论内容不能为空！");
            return;
        }
        
        if (content.length > 1048) {
            showWarningToast("评论字数(含空格)为：" + content.length + "，超过1048，请精简后再提交！");
            return;
        }
        
        // 评论频率限制
        var base_t = sessionStorage.getItem('base_t');
        var now_t = Date.parse(new Date());
        if (base_t) {
            var tt = now_t - base_t;
            if (tt < 40000) {
                showWarningToast('两次评论时间间隔必须大于40秒，还需等待' + (40 - parseInt(tt / 1000)) + '秒');
                return;
            } else {
                sessionStorage.setItem('base_t', now_t);
            }
        } else {
            sessionStorage.setItem('base_t', now_t);
        }
        
        var csrf = $(this).data('csrf');
        var article_id = $(this).data('article-id');
        var URL = $(this).data('ajax-url');
        var rep_id = sessionStorage.getItem('rep_id');
        
        $.ajaxSetup({
            data: {
                'csrfmiddlewaretoken': csrf
            }
        });
        
        $.ajax({
            type: 'post',
            url: URL,
            data: {
                'rep_id': rep_id,
                'content': content,
                'article_id': article_id
            },
            dataType: 'json',
            success: function(ret) {
                commentForm.val('');
                sessionStorage.removeItem('rep_id');
                
                if (ret.new_point) {
                    sessionStorage.setItem('new_point', ret.new_point);
                    window.location.reload();
                } else {
                    $("#rep-to").text('').addClass('hidden');
                    $("#no-rep").addClass('hidden');
                    $(".rep-btn").css("color", "#868e96");
                    
                    showSuccessToast("评论发表成功！");
                    
                    // 刷新页面以显示新评论
                    setTimeout(function() {
                        window.location.reload();
                    }, 1000);
                }
            },
            error: function(xhr) {
                var msg = '评论发表失败';
                if (xhr.responseJSON && xhr.responseJSON.msg) {
                    msg = xhr.responseJSON.msg;
                } else if (xhr.responseJSON && xhr.responseJSON.error) {
                    msg = xhr.responseJSON.error;
                }
                showWarningToast(msg);
            }
        });
    });
    
    // 高亮新评论
    if (sessionStorage.getItem('new_point')) {
        var target = sessionStorage.getItem('new_point');
        var $target = $(target);
        
        if ($target.length) {
            setTimeout(function() {
                var top = $target.offset().top - 100;
                $('body,html').animate({
                    scrollTop: top
                }, 200);
                
                $target.addClass('comment-highlight');
                setTimeout(function() {
                    $target.removeClass('comment-highlight');
                }, 2000);
                
                if (history.pushState) {
                    history.pushState(null, null, target);
                } else {
                    window.location.hash = target;
                }
                
                sessionStorage.removeItem('new_point');
            }, 300);
        } else {
            sessionStorage.removeItem('new_point');
        }
    }
    
    sessionStorage.removeItem('rep_id');
    $(".comment-body a").attr("target", "_blank");
    
    // Ctrl/Cmd + Enter 快捷键提交
    commentForm.keydown(function(e) {
        if ((e.ctrlKey || e.metaKey) && e.keyCode === 13) {
            $("#push-com").click();
        }
    });
});


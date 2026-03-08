$(function() {
    // 检查评论表单元素是否存在
    var commentFormElement = document.getElementById("comment-form");
    if (!commentFormElement) {
        console.log('Comment form not found, skipping SimpleMDE initialization');
        return;
    }
    
    var simplemde = new SimpleMDE({
      element: commentFormElement,
      autoDownloadFontAwesome: false,
      insertTexts: {
        horizontalRule: ["", "\n\n-----\n\n"],
        image: ["![图片Alt](http://", ")"],
        link: ["[链接描述](http://", ")"],
        table: ["", "\n\n| Column 1 | Column 2 | Column 3 |\n| -------- | -------- | -------- |\n| Text     | Text      | Text     |\n\n"],
      },
      toolbar: [
        {
          name: "bold",
          action: SimpleMDE.toggleBold,
          className: "fa fa-bold",
          title: "粗体",
          "default": true
        },
        {
          name: "italic",
          action: SimpleMDE.toggleItalic,
          className: "fa fa-italic",
          title: "斜体",
          "default": true
        },
        {
          name: "quote",
          action: SimpleMDE.toggleBlockquote,
          className: "fa fa-quote-left",
          title: "引用",
          "default": true
        },
        {
          name: "code",
          action: SimpleMDE.toggleCodeBlock,
          className: "fa fa-code",
          title: "代码"
        },
        {
          name: "link",
          action: SimpleMDE.drawLink,
          className: "fa fa-link",
          title: "插入链接",
          "default": true
        },
        {
          name: "image",
          action: SimpleMDE.drawImage,
          className: "fa fa-picture-o",
          title: "插入图片",
          "default": true
        },
        {
          name: "preview",
          action: SimpleMDE.togglePreview,
          className: "fa fa-eye no-disable",
          title: "预览",
          "default": true
        }
      ],
    });
  
    $(".editor-statusbar").append("<span class='float-left text-info ml-0 hidden' id='rep-to'></span>");
    $("#editor-footer").append("<button type='button' class='btn btn-danger btn-sm float-right mr-4 f-14 hidden' id='no-rep'>取消回复</button>");
  
    var emoji_tag = $("#emoji-list img");
    emoji_tag.click(function() {
      var e = $(this).data('emoji');
      simplemde.value(simplemde.value() + e);
    });
  
    $(".rep-btn").click(function() {
      simplemde.value('');
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
  
    $("#no-rep").click(function() {
      simplemde.value('');
      sessionStorage.removeItem('rep_id');
      $("#rep-to").text('').addClass('hidden');
      $("#no-rep").addClass('hidden');
      $(".rep-btn").css("color", "#868e96");
    });
  
    $("#push-com").click(function() {
      var content = simplemde.value();
      if (content.length === 0) {
        showWarningToast("评论内容不能为空！");
        return;
      }
      if (content.length > 1048) {
        showWarningToast("评论字数(含空格)为：" + content.length + "，超过1048，请精简后再提交！");
        return;
      }
  
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
      var challenge_id = $(this).data('challenge-id');
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
          'challenge_id': challenge_id
        },
        dataType: 'json',
        success: function(ret) {
          simplemde.value('');
          sessionStorage.removeItem('rep_id');
          
          if (ret.new_point) {
            sessionStorage.setItem('new_point', ret.new_point);
            window.location.reload();
          } else {
            $("#rep-to").text('').addClass('hidden');
            $("#no-rep").addClass('hidden');
            $(".rep-btn").css("color", "#868e96");
            
            showSuccessToast("评论发表成功！");
          }
        },
        error: function(ret) {
          showWarningToast(ret.msg);
        }
      });
    });
  
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
  });
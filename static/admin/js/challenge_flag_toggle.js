/**
 * Challenge Admin - Flag Type Toggle
 * 根据Flag类型动态显示/隐藏相关字段
 */

(function($) {
    'use strict';
    
    $(document).ready(function() {
        var flagTypeField = $('#id_flag_type');
        var flagTemplateRow = $('.form-row.field-flag_template');
        var flagCountRow = $('.form-row.field-flag_count');
        var flagCountDisplayRow = $('.form-row.field-flag_count_display');
        
        // 如果字段不存在，退出
        if (flagTypeField.length === 0) {
            return;
        }
        
        function toggleFlagFields() {
            var flagType = flagTypeField.val();
            
            if (flagType === 'STATIC') {
                // 静态Flag：显示flag_template，隐藏flag_count，显示flag_count_display
                flagTemplateRow.show();
                flagCountRow.hide();
                flagCountDisplayRow.show();
                
                // 更新说明文字
                updateDescription('static');
                
            } else if (flagType === 'DYNAMIC') {
                // 动态Flag：隐藏flag_template，显示flag_count，隐藏flag_count_display
                flagTemplateRow.hide();
                flagCountRow.show();
                flagCountDisplayRow.hide();
                
                // 更新说明文字
                updateDescription('dynamic');
                
            } else {
                // 未选择或其他：显示所有
                flagTemplateRow.show();
                flagCountRow.show();
                flagCountDisplayRow.hide();
            }
        }
        
        function updateDescription(type) {
            var flagConfigSection = $('.module').find('h2:contains("Flag配置")').parent();
            var description = flagConfigSection.find('.description');
            
            if (description.length === 0) {
                return;
            }
            
            if (type === 'static') {
                description.html(
                    '<b>📌 静态Flag配置：</b><br>' +
                    '• 在Flag值中输入多个flag，用<b>英文逗号</b>分隔<br>' +
                    '• Flag数量会<b>自动检测</b>，无需手动设置<br>' +
                    '• 示例：<code>flag{answer1},flag{answer2},flag{answer3}</code><br>' +
                    '• <b>分数配置</b>：留空自动平均分配，或手动配置JSON数组（总和必须=题目总分）'
                );
            } else if (type === 'dynamic') {
                description.html(
                    '<b>🎲 动态Flag配置：</b><br>' +
                    '• 设置Flag数量（1-10个），系统会为<b>每个用户</b>生成唯一的flag<br>' +
                    '• 每个flag格式：<code>flag{8位前缀_20位哈希}</code><br>' +
                    '• 自动注入到容器环境变量（SNOW_FLAG, SNOW_FLAGS等）<br>' +
                    '• <b>分数配置</b>：留空自动平均分配，或手动配置JSON数组（总和必须=题目总分）'
                );
            }
        }
        
        // 页面加载时初始化
        toggleFlagFields();
        
        // 监听flag_type变化
        flagTypeField.on('change', toggleFlagFields);
    });
    
})(django.jQuery);


# -*- coding: utf-8 -*-
from django import forms
from django.urls import get_resolver
from .models import Tool, ToolCategory


def get_tool_url_choices():
    """从 urls.py 中提取所有工具相关的路由"""
    from django.urls import reverse
    
    # 定义工具路由和对应的显示名称
    tool_routes = {
        'tool:baidu_push': '百度主动推送',
        'tool:baidu_push_site': 'Sitemap主动推送',
        'tool:regex': '在线正则表达式',
        'tool:useragent': 'User-Agent生成器',
        'tool:html_characters': 'HTML特殊字符查询',
        'tool:docker_search': 'Docker镜像查询',
        'tool:markdown_editor': 'Markdown编辑器',
        'tool:word_cloud': '词云图',
        'tool:json2go': 'JSON转Go工具',
        'tool:tax': '综合所得年度汇算',
        'tool:ip': 'IP地址查询',
        'tool:file_upload': '图床工具',
    }
    
    # 验证路由是否有效，并生成选择项
    choices = [('', '--- 请选择内部工具路由 ---')]
    
    for route, name in tool_routes.items():
        try:
            # 尝试反向解析，验证路由是否存在
            reverse(route)
            choices.append((route, f'{name} ({route})'))
        except:
            # 如果路由不存在，跳过
            pass
    
    return choices


class ToolAdminForm(forms.ModelForm):
    """工具管理表单"""
    
    class Meta:
        model = Tool
        fields = '__all__'
        widgets = {
            'description': forms.Textarea(attrs={
                'rows': 3,
                'placeholder': '请输入工具描述，最多200字'
            }),
            'icon': forms.TextInput(attrs={
                'placeholder': '例如：fa fa-code'
            }),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # 根据工具类型动态设置字段
        tool_type = self.instance.tool_type if self.instance.pk else 'internal'
        
        # 为内部工具的 url_name 字段添加选择项
        if 'url_name' in self.fields:
            self.fields['url_name'].widget = forms.Select(
                choices=get_tool_url_choices(),
                attrs={
                    'class': 'url-name-select',
                }
            )
            self.fields['url_name'].help_text = (
                '选择内部工具的路由名称。如果列表中没有你需要的路由，'
                '请先在 urls.py 中添加对应的路由。'
            )
        
        # 为外部链接字段添加样式
        if 'external_url' in self.fields:
            self.fields['external_url'].widget.attrs.update({
                'placeholder': ''
            })
    
    def clean(self):
        cleaned_data = super().clean()
        tool_type = cleaned_data.get('tool_type')
        url_name = cleaned_data.get('url_name')
        external_url = cleaned_data.get('external_url')
        
        # 验证：内部工具必须有 url_name
        if tool_type == 'internal' and not url_name:
            raise forms.ValidationError('内部工具必须选择一个URL路由名称！')
        
        # 验证：外部工具必须有 external_url
        if tool_type == 'external' and not external_url:
            raise forms.ValidationError('外部工具必须填写外部链接地址！')
        
        # 验证：内部工具不应该有 external_url
        if tool_type == 'internal' and external_url:
            self.add_error('external_url', '内部工具不需要填写外部链接，请清空此字段。')
        
        # 验证：外部工具不应该有 url_name
        if tool_type == 'external' and url_name:
            self.add_error('url_name', '外部工具不需要选择URL路由，请清空此字段。')
        
        return cleaned_data
    
    class Media:
        js = ('tool/js/admin_tool_form.js',)


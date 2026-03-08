import json
import re
import markdown as md
from utils.markdown_ext import IconExtension, AlertExtension, DelExtension
from django import template
import markdown as md_lib
from markdown.extensions.codehilite import CodeHiliteExtension
from markdown.extensions.toc import TocExtension
from pygments.formatters.html import HtmlFormatter
from django.utils.text import slugify
from django.utils.html import mark_safe
# 导入自定义 Markdown 扩展
from utils.markdown_ext import (
    DelExtension,
    IconExtension,
    AlertExtension,
    CodeItemExtension,
    CodeGroupExtension
)

register = template.Library()
def make_markdown():
    """创建统一的 Markdown 解析器（与博客模块一致）"""
    md = md_lib.Markdown(extensions=[
        'markdown.extensions.extra',
        'markdown_checklist.extension',
        CodeHiliteExtension(pygments_formatter=CustomHtmlFormatter),
        TocExtension(slugify=slugify),
        DelExtension(),
        IconExtension(),
        AlertExtension(),
        CodeItemExtension(),
        CodeGroupExtension()
    ])
    return md
# 自定义代码高亮格式化器
class CustomHtmlFormatter(HtmlFormatter):
    def __init__(self, lang_str='', **options):
        super().__init__(**options)
        self.lang_str = lang_str

    def _wrap_code(self, source):
        yield 0, f'<code class="{self.lang_str}">'
        yield from source
        yield 0, '</code>'

@register.filter
def markdown(value):
    """Markdown 过滤器 - 渲染 Markdown 文本为 HTML（防XSS）"""
    if not value:
        return ''
    
    import bleach
    from bleach.css_sanitizer import CSSSanitizer
    
    # 1. 进行 Markdown 渲染
    md = make_markdown()
    html_output = md.convert(value)
    
    # 2. 使用 bleach 清理 HTML，只允许安全的标签和属性
    # 允许的 HTML 标签
    allowed_tags = [
        'p', 'br', 'strong', 'em', 'u', 'del', 's', 'i',  # 添加 i 标签支持图标
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'blockquote', 'code', 'pre',
        'ul', 'ol', 'li',
        'a', 'img',
        'table', 'thead', 'tbody', 'tr', 'th', 'td',
        'div', 'span',
        'hr',
        'sup', 'sub',
        'dl', 'dt', 'dd'
    ]
    
    # 允许的 HTML 属性
    allowed_attributes = {
        '*': ['class', 'id'],
        'a': ['href', 'title', 'target', 'rel'],
        'img': ['src', 'alt', 'title', 'width', 'height'],
        'code': ['class'],
        'pre': ['class'],
        'div': ['class', 'style'],
        'span': ['class', 'style'],
        'td': ['colspan', 'rowspan'],
        'th': ['colspan', 'rowspan'],
    }
    
    # 允许的 CSS 属性（用于代码高亮等）
    css_sanitizer = CSSSanitizer(allowed_css_properties=[
        'color', 'background-color', 'font-weight', 'text-decoration',
        'padding', 'margin', 'border', 'border-left'
    ])
    
    # 使用 bleach 清理 HTML
    # strip=False: 转义不允许的标签而不是删除，这样可以显示 <script> 等代码示例但不会执行
    safe_html = bleach.clean(
        html_output,
        tags=allowed_tags,
        attributes=allowed_attributes,
        css_sanitizer=css_sanitizer,
        strip=False  # 转义不允许的标签，显示但不执行
    )
    
    return mark_safe(safe_html)


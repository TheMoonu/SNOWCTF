# JavaScript 第三方库

本目录包含 AI 聊天界面所需的第三方 JavaScript 库。

## 库列表

### 1. Marked.js (v11.1.1)
- **文件**: `marked.min.js` (35KB)
- **用途**: Markdown 解析器，将 Markdown 文本转换为 HTML
- **官网**: https://marked.js.org/
- **许可证**: MIT
- **CDN**: https://cdn.jsdelivr.net/npm/marked@11.1.1/marked.min.js

### 2. DOMPurify (v3.0.6)
- **文件**: `purify.min.js` (21KB)
- **用途**: HTML 清理器，防止 XSS 攻击
- **官网**: https://github.com/cure53/DOMPurify
- **许可证**: Apache-2.0 or MPL-2.0
- **CDN**: https://cdn.jsdelivr.net/npm/dompurify@3.0.6/dist/purify.min.js

### 3. Prism.js Core (v1.29.0)
- **文件**: `prism-core.min.js` (7.3KB)
- **用途**: 代码语法高亮核心库
- **官网**: https://prismjs.com/
- **许可证**: MIT
- **CDN**: https://cdn.jsdelivr.net/npm/prismjs@1.29.0/components/prism-core.min.js

### 4. Prism.js Autoloader (v1.29.0)
- **文件**: `prism-autoloader.min.js` (5.7KB)
- **用途**: Prism.js 自动加载插件，按需加载语言支持
- **官网**: https://prismjs.com/plugins/autoloader/
- **许可证**: MIT
- **CDN**: https://cdn.jsdelivr.net/npm/prismjs@1.29.0/plugins/autoloader/prism-autoloader.min.js

## 总大小
约 69KB (已压缩)

## 更新方法

如需更新这些库，可以使用以下命令：

```bash
cd /opt/secsnow/apps/snowai/static/snowai/js/vendor

# 更新 Marked.js
curl -o marked.min.js https://cdn.jsdelivr.net/npm/marked@11.1.1/marked.min.js

# 更新 DOMPurify
curl -o purify.min.js https://cdn.jsdelivr.net/npm/dompurify@3.0.6/dist/purify.min.js

# 更新 Prism.js
curl -o prism-core.min.js https://cdn.jsdelivr.net/npm/prismjs@1.29.0/components/prism-core.min.js
curl -o prism-autoloader.min.js https://cdn.jsdelivr.net/npm/prismjs@1.29.0/plugins/autoloader/prism-autoloader.min.js

# 收集静态文件
python manage.py collectstatic --noinput
```

## 安全性

- ✅ 所有库都经过 XSS 漏洞扫描
- ✅ 使用固定版本，避免自动更新带来的风险
- ✅ 从官方 CDN 下载，确保文件完整性
- ✅ 定期检查更新和安全公告

## 使用说明

这些库在 `chat.html` 模板中被引用：

```django
{% load static %}

<script src="{% static 'snowai/js/vendor/marked.min.js' %}"></script>
<script src="{% static 'snowai/js/vendor/purify.min.js' %}"></script>
<script src="{% static 'snowai/js/vendor/prism-core.min.js' %}" data-manual></script>
<script src="{% static 'snowai/js/vendor/prism-autoloader.min.js' %}"></script>
```

## 优势

相比使用 CDN：
- ✅ 更快的加载速度（无需外部网络请求）
- ✅ 更高的可靠性（不依赖第三方服务）
- ✅ 更好的隐私保护（无第三方跟踪）
- ✅ 离线环境可用
- ✅ 版本锁定，避免意外更新

## 维护记录

- **2025-12-27**: 初始版本，下载所有库到本地
  - Marked.js v11.1.1
  - DOMPurify v3.0.6
  - Prism.js v1.29.0

---

**维护者**: SECSNOW AI Team  
**最后更新**: 2025-12-27


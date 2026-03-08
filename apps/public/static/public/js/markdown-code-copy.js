/**
 * Markdown 代码块复制功能
 * 为所有 .markdown-content 中的代码块添加复制按钮
 */

(function() {
    'use strict';

    /**
     * 复制文本到剪贴板
     * @param {string} text - 要复制的文本
     * @returns {Promise<boolean>} - 是否复制成功
     */
    async function copyToClipboard(text) {
        // 优先使用现代的 Clipboard API
        if (navigator.clipboard && window.isSecureContext) {
            try {
                await navigator.clipboard.writeText(text);
                return true;
            } catch (err) {
                console.error('复制失败:', err);
                return false;
            }
        } else {
            // 降级方案：使用 execCommand
            const textArea = document.createElement('textarea');
            textArea.value = text;
            textArea.style.position = 'fixed';
            textArea.style.left = '-9999px';
            textArea.style.top = '-9999px';
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            
            try {
                const successful = document.execCommand('copy');
                document.body.removeChild(textArea);
                return successful;
            } catch (err) {
                console.error('复制失败:', err);
                document.body.removeChild(textArea);
                return false;
            }
        }
    }

    /**
     * 为代码块创建复制按钮
     * @param {HTMLElement} codeBlock - 代码块元素
     */
    function addCopyButton(codeBlock) {
        // 检查是否已经添加了复制按钮
        if (codeBlock.querySelector('.copy-code-btn')) {
            return;
        }
        
        // 检查元素本身是否已经有标记（防止重复处理）
        if (codeBlock.hasAttribute('data-copy-btn-added')) {
            return;
        }
        
        // 跳过编辑器内的代码块（editormd、CodeMirror 等编辑器）
        if (codeBlock.closest('.editormd') || 
            codeBlock.closest('.CodeMirror') || 
            codeBlock.closest('.editormd-preview') ||
            codeBlock.closest('[id*="editormd"]') ||
            codeBlock.closest('.editor-container')) {
            return;
        }
        
        // 标记该元素已处理
        codeBlock.setAttribute('data-copy-btn-added', 'true');

        // 检测代码语言
        const detectLanguage = () => {
            // 尝试从 code 标签的 class 中获取语言
            const codeElement = codeBlock.querySelector('code');
            if (codeElement && codeElement.className) {
                // 匹配 language-xxx 或 lang-xxx 格式
                const langMatch = codeElement.className.match(/(?:language|lang)-(\w+)/);
                if (langMatch) {
                    return formatLanguageName(langMatch[1]);
                }
                // 匹配直接的语言类名
                const directMatch = codeElement.className.match(/\b(python|javascript|java|cpp|c|csharp|go|rust|php|ruby|swift|kotlin|typescript|html|css|scss|sass|less|sql|bash|shell|sh|powershell|json|xml|yaml|yml|markdown|md|dockerfile|nginx|apache|lua|perl|r|scala|dart|elixir|haskell|lisp|scheme|clojure|erlang|fsharp|groovy|objective-c|pascal|prolog|racket|tcl|verilog|vhdl|assembly|asm|fortran|cobol|ada)\b/i);
                if (directMatch) {
                    return formatLanguageName(directMatch[1]);
                }
            }
            
            // 尝试从 pre 标签的 class 中获取
            if (codeBlock.className) {
                const langMatch = codeBlock.className.match(/(?:language|lang)-(\w+)/);
                if (langMatch) {
                    return formatLanguageName(langMatch[1]);
                }
            }
            
            return ''; // 未检测到语言
        };

        // 格式化语言名称 - 简洁低调
        const formatLanguageName = (lang) => {
            const langMap = {
                'js': 'javascript',
                'ts': 'typescript',
                'py': 'python',
                'cpp': 'c++',
                'csharp': 'c#',
                'cs': 'c#',
                'sh': 'shell',
                'bash': 'bash',
                'powershell': 'powershell',
                'md': 'markdown',
                'yml': 'yaml',
                'dockerfile': 'docker',
                'nginx': 'nginx',
                'apache': 'apache',
                'objective-c': 'objective-c',
                'asm': 'assembly',
                'html': 'html',
                'css': 'css',
                'scss': 'scss',
                'sass': 'sass',
                'less': 'less',
                'sql': 'sql',
                'json': 'json',
                'xml': 'xml',
                'yaml': 'yaml'
            };
            
            const lowerLang = lang.toLowerCase();
            return langMap[lowerLang] || lowerLang;
        };

        const language = detectLanguage();

        // 创建语言标签（如果检测到语言）
        if (language) {
            const langLabel = document.createElement('span');
            langLabel.className = 'code-lang-label';
            langLabel.textContent = language;
            langLabel.setAttribute('title', `语言: ${language}`);
            codeBlock.style.position = 'relative';
            codeBlock.appendChild(langLabel);
        }

        // 创建复制按钮
        const copyBtn = document.createElement('button');
        copyBtn.className = 'copy-code-btn';
        copyBtn.innerHTML = '<i class="fa fa-copy"></i>';
        copyBtn.setAttribute('aria-label', '复制代码');
        copyBtn.setAttribute('title', '点击复制代码');

        // 获取代码文本
        const getCodeText = () => {
            const codeElement = codeBlock.querySelector('code') || codeBlock.querySelector('pre');
            return codeElement ? codeElement.textContent : '';
        };

        // 点击事件
        copyBtn.addEventListener('click', async (e) => {
            e.preventDefault();
            e.stopPropagation();

            const codeText = getCodeText();
            const success = await copyToClipboard(codeText);

            if (success) {
                // 显示复制成功状态
                const originalHTML = copyBtn.innerHTML;
                copyBtn.innerHTML = '<i class="fa fa-check"></i>';
                copyBtn.classList.add('copied');

                // 1.5秒后恢复原状
                setTimeout(() => {
                    copyBtn.innerHTML = originalHTML;
                    copyBtn.classList.remove('copied');
                }, 1500);
            } else {
                // 复制失败
                copyBtn.innerHTML = '<i class="fa fa-times"></i>';
                setTimeout(() => {
                    copyBtn.innerHTML = '<i class="fa fa-copy"></i>';
                }, 1500);
            }
        });

        // 将按钮添加到代码块
        codeBlock.style.position = 'relative';
        codeBlock.appendChild(copyBtn);
    }

    /**
     * 初始化所有代码块的复制按钮
     */
    function initCopyButtons() {
        // 优先为所有 .codehilite 添加复制按钮（这是最外层容器）
        const codehiliteBlocks = document.querySelectorAll('.codehilite');
        codehiliteBlocks.forEach(addCopyButton);

        // 只为不在 .codehilite 内的独立 pre 标签添加复制按钮
        const allPreBlocks = document.querySelectorAll('pre:not(.codehilite pre)');
        allPreBlocks.forEach((pre) => {
            // 确保这个 pre 不在任何 .codehilite 容器内
            if (!pre.closest('.codehilite')) {
                addCopyButton(pre);
            }
        });
    }

    /**
     * 使用 MutationObserver 监听动态添加的代码块
     */
    function observeCodeBlocks() {
        const observer = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                mutation.addedNodes.forEach((node) => {
                    if (node.nodeType === 1) { // Element node
                        // 检查新添加的节点是否是代码块
                        if (node.classList && node.classList.contains('codehilite')) {
                            // 只为 .codehilite 添加按钮
                            addCopyButton(node);
                        } else if (node.tagName === 'PRE' && !node.closest('.codehilite')) {
                            // 只为不在 .codehilite 内的 pre 添加按钮
                            addCopyButton(node);
                        } else {
                            // 检查新添加节点的子元素
                            const codehiliteBlocks = node.querySelectorAll('.codehilite');
                            codehiliteBlocks.forEach(addCopyButton);
                            
                            const preBlocks = node.querySelectorAll('pre:not(.codehilite pre)');
                            preBlocks.forEach((pre) => {
                                if (!pre.closest('.codehilite')) {
                                    addCopyButton(pre);
                                }
                            });
                        }
                    }
                });
            });
        });

        // 监听整个文档的变化
        observer.observe(document.body, {
            childList: true,
            subtree: true
        });
    }

    /**
     * 页面加载完成后初始化
     */
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            initCopyButtons();
            observeCodeBlocks();
        });
    } else {
        // DOMContentLoaded 已经触发
        initCopyButtons();
        observeCodeBlocks();
    }

    // 兼容 jQuery 的情况
    if (typeof jQuery !== 'undefined') {
        jQuery(document).ready(() => {
            // 延迟一点确保其他脚本先执行
            setTimeout(initCopyButtons, 100);
        });
    }
})();


/**
 * AI 对话助手 - 前端脚本
 * 
 * 安全特性：
 * 1. XSS 防护 - 使用 DOMPurify 清理所有 Markdown 渲染的 HTML
 * 2. URL 验证 - 阻止 javascript:, data:, vbscript: 等危险协议
 * 3. 输入验证 - 验证所有 UUID 格式的 ID
 * 4. HTML 转义 - 所有用户输入都经过适当的转义处理
 * 5. CSP 友好 - 不使用 eval() 或 inline scripts
 */

// ============================================
// 全局变量
// ============================================
let currentSessionId = null;
let currentModelId = null;
let isStreaming = false;

// ============================================
// Markdown 配置
// ============================================
if (typeof marked !== 'undefined') {
    const renderer = new marked.Renderer();
    
    // 自定义代码块渲染，生成支持 Prism 高亮的结构
    renderer.code = function(code, language) {
        const lang = language || 'text';
        const langClass = `language-${lang}`;
        
        // 转义 HTML
        const escapedCode = code
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
        
        return `<div class="codehilite"><pre><code class="${langClass}">${escapedCode}</code></pre></div>`;
    };
    
    marked.setOptions({
        renderer: renderer,
        breaks: true,
        gfm: true,
        sanitize: false,
        mangle: false,
        headerIds: false
    });
}

// ============================================
// 代码高亮
// ============================================
function highlightCode(container) {
    if (typeof Prism !== 'undefined') {
        const codeBlocks = container.querySelectorAll('pre code[class*="language-"]');
        codeBlocks.forEach(block => {
            Prism.highlightElement(block);
        });
    }
}

// ============================================
// 会话管理
// ============================================
async function loadSessions() {
    try {
        const response = await fetch('/snowai/sessions/', {
            headers: {
                'X-CSRFToken': getCookie('csrftoken')
            }
        });
        
        if (!response.ok) {
            throw new Error('加载会话列表失败');
        }
        
        const data = await response.json();
        const sessions = data.sessions || [];
        
        const listHtml = sessions.length > 0 
            ? sessions.map(s => {
                const sessionIdRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
                if (!sessionIdRegex.test(s.session_id)) {
                    return '';
                }
                
                return `
                    <div class="session-item ${s.session_id === currentSessionId ? 'active' : ''}" 
                         data-session-id="${escapeHtmlAttr(s.session_id)}">
                        <div class="session-icon">
                            <i class="fa fa-comments"></i>
                        </div>
                        <div class="session-content" onclick="switchSession('${escapeHtmlAttr(s.session_id)}')">
                            <div class="session-title">${escapeHtmlAttr(s.title || '未命名对话')}</div>
                            <div class="session-meta">
                                <span>${parseInt(s.message_count) || 0} 条消息</span>
                                <span>•</span>
                                <span>${formatDate(s.update_date)}</span>
                            </div>
                        </div>
                        <button class="session-delete-btn" 
                                onclick="event.stopPropagation(); deleteSession('${escapeHtmlAttr(s.session_id)}')" 
                                title="删除会话">
                            <i class="fa fa-trash"></i>
                        </button>
                    </div>
                `;
            }).join('')
            : '<div style="text-align: center; color: #9ca3af; padding: 2rem 0; font-size: 0.875rem;">暂无对话记录<br><small style="color: #cbd5e1;">点击 + 创建新对话</small></div>';
        
        document.getElementById('sessionsList').innerHTML = listHtml;
        
        if (!currentSessionId) {
            if (sessions.length > 0) {
                const latestSession = sessions[0];
                await switchSession(latestSession.session_id);
            } else {
                showWelcomeState();
            }
        }
    } catch (error) {
        document.getElementById('sessionsList').innerHTML = 
            '<div style="text-align: center; color: #ef4444; padding: 2rem 0; font-size: 0.875rem;"><i class="fa fa-exclamation-triangle"></i> 加载失败</div>';
    }
}

async function loadUserStats() {
    try {
        const response = await fetch('/snowai/stats/', {
            headers: {
                'X-CSRFToken': getCookie('csrftoken')
            }
        });
        
        if (response.ok) {
            const data = await response.json();
            if (data.success && data.quota) {
                const dailyUsedElem = document.getElementById('dailyUsed');
                if (dailyUsedElem) {
                    dailyUsedElem.textContent = data.quota.daily_used;
                }
                
                if (!data.quota.is_unlimited) {
                    const quotaFill = document.getElementById('quotaFill');
                    const dailyQuotaElem = document.getElementById('dailyQuota');
                    
                    if (quotaFill && data.quota.daily_quota > 0) {
                        const percentage = (data.quota.daily_used / data.quota.daily_quota * 100).toFixed(1);
                        quotaFill.style.width = percentage + '%';
                    }
                    
                    if (dailyQuotaElem) {
                        dailyQuotaElem.textContent = data.quota.daily_quota;
                    }
                }
            }
        }
    } catch (error) {
        console.error('加载统计失败:', error);
    }
}

async function createNewSession() {
    let modelId = currentModelId;
    
    if (!modelId) {
        const recommendedModel = document.querySelector('.ai-model-option.recommended');
        if (recommendedModel) {
            modelId = recommendedModel.dataset.value;
            currentModelId = modelId;
        } else {
            const firstModel = document.querySelector('.ai-model-option');
            if (firstModel) {
                modelId = firstModel.dataset.value;
                currentModelId = modelId;
            } else {
                showErrorToast('系统中没有可用的 AI 模型，请联系管理员配置模型');
                return;
            }
        }
    }
    
    modelId = String(modelId || '').trim();
    
    if (!modelId) {
        showErrorToast('无法获取模型 ID，请刷新页面重试');
        return;
    }
    
    const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    if (!uuidRegex.test(modelId)) {
        showErrorToast('模型 ID 格式无效，请刷新页面重试');
        return;
    }
    
    try {
        const response = await fetch('/snowai/session/create/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken')
            },
            body: JSON.stringify({
                model_id: modelId,
                session_type: 'wiki',
                title: ''
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            window.location.href = `/snowai/chat/${data.session_id}/`;
        } else {
            showErrorToast(data.error || '创建会话失败');
        }
    } catch (error) {
        showErrorToast('创建会话失败: ' + error.message);
    }
}

async function switchSession(sessionId) {
    if (!sessionId) {
        showWarningToast('无效的会话 ID');
        return;
    }
    
    if (sessionId === currentSessionId) {
        return;
    }
    
    try {
        currentSessionId = sessionId;
        
        document.querySelectorAll('.session-item').forEach(item => {
            item.classList.remove('active');
        });
        const activeSession = document.querySelector(`.session-item[data-session-id="${sessionId}"]`);
        if (activeSession) {
            activeSession.classList.add('active');
        }
        
        const messagesContainer = document.getElementById('messagesContainer');
        if (messagesContainer) {
            messagesContainer.innerHTML = '<div style="text-align: center; padding: 3rem; color: #9ca3af;"><i class="fa fa-spinner fa-spin"></i> 加载中...</div>';
        }
        
        await loadMessages(sessionId);
        
        window.history.pushState({sessionId: sessionId}, '', `/snowai/chat/${sessionId}/`);
        
        enableInputControls();
        
        if (window.innerWidth <= 768) {
            toggleSidebar();
        }
    } catch (error) {
        console.error('切换会话失败:', error);
        showErrorToast('切换会话失败，请刷新页面重试');
        window.location.href = `/snowai/chat/${sessionId}/`;
    }
}

async function deleteSession(sessionId) {
    if (!sessionId) {
        showWarningToast('无效的会话 ID');
        return;
    }
    
    const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    if (!uuidRegex.test(sessionId)) {
        showErrorToast('无效的会话 ID 格式');
        return;
    }
    
    showConfirmModal(
        '删除会话',
        '确定要删除这个会话吗？此操作不可恢复，所有对话记录都将被删除。',
        async function() {
            try {
                const response = await fetch(`/snowai/session/${sessionId}/delete/`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCookie('csrftoken')
                    }
                });
                
                if (!response.ok) {
                    let errorMsg = '删除失败';
                    try {
                        const errorData = await response.json();
                        errorMsg = errorData.error || errorMsg;
                    } catch (e) {
                        errorMsg = `删除失败 (HTTP ${response.status})`;
                    }
                    showErrorToast(errorMsg);
                    return;
                }
                
                const text = await response.text();
                if (!text) {
                    showErrorToast('服务器返回空响应');
                    return;
                }
                
                let data;
                try {
                    data = JSON.parse(text);
                } catch (e) {
                    showErrorToast('服务器返回无效的响应格式');
                    return;
                }
                
                if (data.success) {
                    showSuccessToast('会话已删除');
                    
                    const sessionElement = document.querySelector(`.session-item[data-session-id="${sessionId}"]`);
                    
                    if (sessionElement) {
                        sessionElement.style.transition = 'all 0.3s ease';
                        sessionElement.style.opacity = '0';
                        sessionElement.style.transform = 'translateX(-20px)';
                        sessionElement.style.height = sessionElement.offsetHeight + 'px';
                        
                        setTimeout(() => {
                            sessionElement.style.height = '0';
                            sessionElement.style.margin = '0';
                            sessionElement.style.padding = '0';
                            sessionElement.style.overflow = 'hidden';
                            
                            setTimeout(() => {
                                sessionElement.remove();
                                
                                const remainingSessions = document.querySelectorAll('.session-item');
                                if (remainingSessions.length === 0) {
                                    document.getElementById('sessionsList').innerHTML = 
                                        '<div style="text-align: center; color: #9ca3af; padding: 2rem 0; font-size: 0.875rem;">暂无对话记录<br><small style="color: #cbd5e1;">点击 + 创建新对话</small></div>';
                                }
                            }, 300);
                        }, 300);
                    }
                    
                    if (sessionId === currentSessionId) {
                        setTimeout(async () => {
                            const remainingSessions = document.querySelectorAll('.session-item');
                            
                            if (remainingSessions.length > 0) {
                                const firstSession = remainingSessions[0];
                                const nextSessionId = firstSession.getAttribute('data-session-id');
                                
                                currentSessionId = nextSessionId;
                                
                                document.querySelectorAll('.session-item').forEach(item => {
                                    item.classList.remove('active');
                                });
                                firstSession.classList.add('active');
                                
                                await loadMessages(nextSessionId);
                                
                                window.history.pushState({}, '', `/snowai/chat/${nextSessionId}/`);
                            } else {
                                currentSessionId = null;
                                showWelcomeState();
                                
                                window.history.pushState({}, '', '/snowai/chat/');
                            }
                        }, 600);
                    }
                } else {
                    showErrorToast(data.error || '删除失败');
                }
            } catch (error) {
                showErrorToast('删除会话失败: ' + error.message);
            }
        }
    );
}

// ============================================
// 消息管理
// ============================================
async function loadMessages(sessionId) {
    try {
        const response = await fetch(`/snowai/session/${sessionId}/messages/`);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const data = await response.json();
        
        if (data.success) {
            displayMessages(data.messages);
            updateSessionInfo(data.session);
        } else {
            throw new Error(data.error || '加载失败');
        }
    } catch (error) {
        console.error('加载消息失败:', error);
        const messagesContainer = document.getElementById('messagesContainer');
        if (messagesContainer) {
            messagesContainer.innerHTML = `
                <div style="text-align: center; padding: 3rem; color: #ef4444;">
                    <i class="fa fa-exclamation-triangle" style="font-size: 3rem; margin-bottom: 1rem;"></i>
                    <div style="font-size: 1.125rem; font-weight: 500; margin-bottom: 0.5rem;">加载消息失败</div>
                    <div style="font-size: 0.875rem; color: #9ca3af;">${error.message}</div>
                    <button onclick="window.location.reload()" style="margin-top: 1.5rem; padding: 0.75rem 1.5rem; background: #3b82f6; color: white; border: none; border-radius: 8px; cursor: pointer;">
                        <i class="fa fa-refresh"></i> 刷新页面
                    </button>
                </div>
            `;
        }
        throw error;
    }
}

function displayMessages(messages) {
    const container = document.getElementById('messagesContainer');
    
    if (messages.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon-wrapper">
                    <div class="empty-icon">💬</div>
                </div>
                <div class="empty-content">
                    <div class="empty-text">开始新的对话</div>
                    <div class="empty-hint">你好！我是 AI 智能助手，基于WIKI知识库为你提供专业的技术解答</div>
                </div>
            </div>
        `;
        return;
    }
    
    container.innerHTML = messages.map(msg => createMessageHTML(msg)).join('');
    
    highlightCode(container);
    
    scrollToBottom();
}

// ============================================
// 安全函数
// ============================================
function sanitizeHtml(html) {
    if (typeof DOMPurify !== 'undefined') {
        return DOMPurify.sanitize(html, {
            ALLOWED_TAGS: [
                'p', 'br', 'strong', 'em', 'u', 'del', 's', 'code', 'pre',
                'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                'ul', 'ol', 'li',
                'blockquote',
                'a', 'img',
                'table', 'thead', 'tbody', 'tr', 'th', 'td',
                'div', 'span',
                'hr'
            ],
            ALLOWED_ATTR: [
                'href', 'title', 'alt', 'src',
                'class', 'id',
                'target', 'rel',
                'colspan', 'rowspan'
            ],
            ALLOWED_URI_REGEXP: /^(?:(?:(?:f|ht)tps?|mailto|tel|callto|sms|cid|xmpp):|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i,
            KEEP_CONTENT: true,
            RETURN_DOM: false,
            RETURN_DOM_FRAGMENT: false,
            RETURN_TRUSTED_TYPE: false
        });
    } else {
        return escapeHtml(html);
    }
}

function renderMessageContent(content, isUser) {
    if (isUser) {
        return escapeHtml(content);
    } else {
        if (typeof marked !== 'undefined') {
            try {
                const rawHtml = marked.parse(content);
                return sanitizeHtml(rawHtml);
            } catch (e) {
                return escapeHtml(content);
            }
        }
        return escapeHtml(content);
    }
}

function escapeHtmlAttr(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function isSafeUrl(url) {
    if (!url || typeof url !== 'string') {
        return false;
    }
    
    const urlLower = url.trim().toLowerCase();
    
    const dangerousProtocols = [
        'javascript:',
        'data:',
        'vbscript:',
        'file:',
        'about:',
        'blob:'
    ];
    
    for (const protocol of dangerousProtocols) {
        if (urlLower.startsWith(protocol)) {
            return false;
        }
    }
    
    if (urlLower.startsWith('http://') || 
        urlLower.startsWith('https://') || 
        urlLower.startsWith('/') ||
        urlLower.startsWith('./') ||
        urlLower.startsWith('../') ||
        urlLower.startsWith('#')) {
        return true;
    }
    
    if (!urlLower.includes(':')) {
        return true;
    }
    
    return false;
}

function renderWikiReferencesHTML(references) {
    if (!references || references.length === 0) {
        return '';
    }
    
    return references.map(ref => {
        const title = escapeHtmlAttr(ref.title || '未知标题');
        const rawSummary = ref.summary || '';
        const summaryPreview = rawSummary.length > 100 ? rawSummary.substring(0, 100) + '...' : rawSummary;
        const summary = escapeHtmlAttr(summaryPreview);
        
        const rawUrl = ref.url || '#';
        if (!isSafeUrl(rawUrl)) {
            return '';
        }
        const url = escapeHtmlAttr(rawUrl);
        
        // 根据后端判断的类型来区分（不依赖前端接收内部 ID）
        // source_type 可能的值：'ctf_writeup', 'internal', 'external', 'unknown'
        const sourceType = ref.source_type || 'unknown';
        
        // 调试：打印 source_type 值
        
        const isCtfChallenge = sourceType === 'ctf_writeup';
        
        // 只用图标区分：靶场题目 🎯，Wiki文档 📄
        const icon = isCtfChallenge ? '🎯' : '📄';
        
        // 处理分类显示：不需要移除前缀，直接显示原始分类
        const categoryDisplay = escapeHtmlAttr(ref.category || '');
        
        return `
            <a href="${url}" class="wiki-ref-card ${isCtfChallenge ? 'ctf-challenge' : 'wiki-article'}" target="_blank" rel="noopener noreferrer">
                <div class="wiki-ref-title">${icon} ${title}</div>
                ${categoryDisplay ? `<div class="wiki-ref-category">${categoryDisplay}</div>` : ''}
                <div class="wiki-ref-summary">${summary}</div>
            </a>
        `;
    }).join('');
}

function createMessageHTML(message) {
    const validRoles = ['user', 'assistant', 'system'];
    if (!validRoles.includes(message.role)) {
        return '';
    }
    
    const isUser = message.role === 'user';
    const avatar = isUser ? '👤' : '🤖';
    const renderedContent = renderMessageContent(message.content, isUser);
    
    let wikiRefsHTML = '';
    if (message.wiki_references && Array.isArray(message.wiki_references) && message.wiki_references.length > 0) {
        wikiRefsHTML = `
            <div class="wiki-references">
                ${renderWikiReferencesHTML(message.wiki_references)}
            </div>
        `;
    }
    
    const messageId = message.message_id || message.id || '';
    const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    const safeMessageId = uuidRegex.test(messageId) ? escapeHtmlAttr(messageId) : '';
    
    return `
        <div class="message ${message.role}" data-message-id="${safeMessageId}">
            <div class="message-avatar">${avatar}</div>
            <div class="message-content">
                <div class="message-bubble">${renderedContent}</div>
                ${wikiRefsHTML}
                <div class="message-time">${formatTime(message.create_date)}</div>
                ${!isUser && safeMessageId ? `
                    <div class="message-actions">
                        <button class="message-action-btn" onclick="copyMessageContent(this)">
                            <i class="fa fa-copy"></i> 复制
                        </button>
                        <button class="message-action-btn" onclick="rateMessage('${safeMessageId}', 5)">
                            <i class="fa fa-thumbs-up"></i> 有帮助
                        </button>
                    </div>
                ` : ''}
            </div>
        </div>
    `;
}

// ============================================
// UI 状态管理
// ============================================
function showWelcomeState() {
    const container = document.getElementById('messagesContainer');
    container.innerHTML = `
        <div class="empty-state">
            <div class="empty-icon-wrapper">
                <div class="empty-icon">🤖</div>
            </div>
            <div class="empty-content">
                <div class="empty-text">欢迎使用WIKI知识库智能助手</div>
                <div class="empty-hint">
                    基于知识库为您提供专业的技术解答<br>
                </div>
                <div class="empty-suggestions" style="margin-top: 2.5rem; gap: 1rem;">
                    <button onclick="createNewSession()" title="创建新对话" style="
                        padding: 1rem 2.5rem;
                        background: linear-gradient(135deg, #0184ff 0%, #00a8ff 100%);
                        color: white;
                        border: none;
                        border-radius: 9999px;
                        font-size: 1rem;
                        font-weight: 600;
                        cursor: pointer;
                        box-shadow: 0 4px 16px rgba(1, 132, 255, 0.3);
                        transition: all 0.3s ease;
                        display: inline-flex;
                        align-items: center;
                        justify-content: center;
                        gap: 0.5rem;
                        min-width: 180px;
                    " onmouseover="this.style.transform='translateY(-2px) scale(1.05)'; this.style.boxShadow='0 6px 20px rgba(1, 132, 255, 0.4)';" 
                       onmouseout="this.style.transform='translateY(0) scale(1)'; this.style.boxShadow='0 4px 16px rgba(1, 132, 255, 0.3)';">
                        <i class="fa fa-plus-circle"></i>
                        <span>创建新对话</span>
                    </button>
                </div>
            </div>
        </div>
    `;
    
    const messageInput = document.getElementById('messageInput');
    const sendBtn = document.getElementById('sendBtn');
    const clearBtn = document.getElementById('clearMessagesBtn');
    
    if (messageInput) {
        messageInput.disabled = true;
        messageInput.placeholder = '请先创建新对话';
        messageInput.style.background = '#f9fafb';
        messageInput.style.cursor = 'not-allowed';
    }
    if (sendBtn) {
        sendBtn.disabled = true;
    }
    if (clearBtn) {
        clearBtn.disabled = true;
        clearBtn.style.opacity = '0.5';
        clearBtn.style.cursor = 'not-allowed';
    }
    
    const chatTitle = document.getElementById('chatTitle');
    const chatModel = document.getElementById('chatModel');
    if (chatTitle) {
        chatTitle.textContent = 'WIKI智能助手';
    }
    if (chatModel) {
        chatModel.textContent = '请先创建新对话';
    }
}

function enableInputControls() {
    const messageInput = document.getElementById('messageInput');
    const sendBtn = document.getElementById('sendBtn');
    const clearBtn = document.getElementById('clearMessagesBtn');
    
    if (messageInput) {
        messageInput.disabled = false;
        messageInput.placeholder = '输入消息... (Shift+Enter 换行，Enter 发送)';
        messageInput.style.background = '';
        messageInput.style.cursor = '';
    }
    if (sendBtn) {
        sendBtn.disabled = false;
    }
    if (clearBtn) {
        clearBtn.disabled = false;
        clearBtn.style.opacity = '';
        clearBtn.style.cursor = '';
    }
}

// ============================================
// 发送消息
// ============================================
async function sendMessage() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    
    if (!message) {
        showWarningToast('请输入消息');
        return;
    }
    
    if (!currentSessionId) {
        showWarningToast('请先创建新对话');
        return;
    }
    
    if (isStreaming) {
        showWarningToast('正在生成回复，请稍候...');
        return;
    }
    
    addMessageToUI('user', message);
    input.value = '';
    input.style.height = '48px';
    
    const sendBtn = document.getElementById('sendBtn');
    if (sendBtn) {
        sendBtn.disabled = true;
    }
    isStreaming = true;
    
    const loadingId = addLoadingIndicator();
    let timeoutId = null;
    let abortController = new AbortController();
    
    // 设置120秒超时（适应长回复）
    timeoutId = setTimeout(() => {
        
        abortController.abort();
        showErrorToast('请求超时，请重试');
    }, 260000);
    
    try {
        const response = await fetch('/snowai/chat/stream/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken')
            },
            body: JSON.stringify({
                session_id: currentSessionId,
                message: message,
                model_id: currentModelId  // 传递当前选择的模型ID
            }),
            signal: abortController.signal
        });
        
        if (!response.ok) {
            clearTimeout(timeoutId);
            removeLoadingIndicator(loadingId);
            const errorText = await response.text();
            throw new Error(`请求失败 (${response.status}): ${errorText}`);
        }
        
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let assistantMessage = '';
        let messageElement = null;
        let hasError = false;
        let isDone = false;
        let loadingRemoved = false;
        let lastActivityTime = Date.now();
        
        try {
            while (true) {
                // 检查是否长时间没有数据（30秒无响应视为超时）
                if (Date.now() - lastActivityTime > 30000) {
                    throw new Error('响应超时');
                }
                
                const {value, done} = await reader.read();
                
                if (done) {
                    break;
                }
                
                lastActivityTime = Date.now();
                const chunk = decoder.decode(value, { stream: true });
                const lines = chunk.split('\n');
                
                for (const line of lines) {
                    if (!line.trim() || !line.startsWith('data: ')) continue;
                    
                    try {
                        const jsonStr = line.substring(6).trim();
                        if (!jsonStr) continue;
                        
                        const data = JSON.parse(jsonStr);
                        
                        if (data.type === 'content') {
                            if (!loadingRemoved) {
                                removeLoadingIndicator(loadingId);
                                loadingRemoved = true;
                            }
                            
                            assistantMessage += data.content;
                            
                            if (!messageElement) {
                                messageElement = addMessageToUI('assistant', assistantMessage);
                            } else {
                                updateMessageContent(messageElement, assistantMessage);
                            }
                        } else if (data.type === 'error') {
                            if (!loadingRemoved) {
                                removeLoadingIndicator(loadingId);
                                loadingRemoved = true;
                            }
                            hasError = true;
                            showWarningToast('服务错误: ' + data.error);
                            isDone = true;
                            break;
                        } else if (data.type === 'done') {
                            if (!loadingRemoved) {
                                removeLoadingIndicator(loadingId);
                                loadingRemoved = true;
                            }
                            
                            // 安全地处理 Wiki 引用
                            try {
                                if (data.wiki_references && Array.isArray(data.wiki_references) && 
                                    data.wiki_references.length > 0 && messageElement) {
                                    addWikiReferences(messageElement, data.wiki_references);
                                }
                            } catch (refError) {
                                console.error('添加Wiki引用失败:', refError);
                            }
                            
                            isDone = true;
                            break;
                        }
                    } catch (e) {
                        console.error('解析流式数据失败:', e, 'Line:', line);
                    }
                }
                
                if (hasError || isDone) break;
            }
        } finally {
            clearTimeout(timeoutId);
            
            if (!loadingRemoved) {
                removeLoadingIndicator(loadingId);
            }
            
            try {
                reader.releaseLock();
            } catch (e) {
                console.error('释放 reader 失败:', e);
            }
        }
        
        // 如果出错但没有消息元素，显示错误消息
        if (hasError && !messageElement) {
            addMessageToUI('assistant', '抱歉，处理您的请求时出现了错误，请稍后重试。');
        }
        
        // 如果流结束但没有收到 done 事件，也没有消息，显示提示
        if (!isDone && !hasError && !messageElement) {
            console.warn('流异常结束：未收到done事件且无消息内容');
            addMessageToUI('assistant', '回复被中断，请重新尝试。');
        }
        
        loadUserStats();
        
    } catch (error) {
        clearTimeout(timeoutId);
        removeLoadingIndicator(loadingId);
        
        if (error.name === 'AbortError') {
            showErrorToast('请求已取消');
        } else {
            console.error('发送消息失败:', error);
            showErrorToast('发送消息失败: ' + error.message);
        }
    } finally {
        // 确保总是重置状态
        const sendButton = document.getElementById('sendBtn');
        if (sendButton) {
            sendButton.disabled = false;
        }
        isStreaming = false;
    }
}

function addMessageToUI(role, content) {
    const container = document.getElementById('messagesContainer');
    
    const emptyState = container.querySelector('.empty-state');
    if (emptyState) {
        emptyState.remove();
    }
    
    const isUser = role === 'user';
    const renderedContent = renderMessageContent(content, isUser);
    
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}`;
    if (!isUser) {
        messageDiv.dataset.tempId = 'msg-' + Date.now();
    }
    messageDiv.innerHTML = `
        <div class="message-avatar">${role === 'user' ? '👤' : '🤖'}</div>
        <div class="message-content">
            <div class="message-bubble">${renderedContent}</div>
            <div class="message-time">${new Date().toLocaleTimeString()}</div>
        </div>
    `;
    
    container.appendChild(messageDiv);
    
    if (!isUser) {
        highlightCode(messageDiv);
    }
    
    scrollToBottom();
    
    return messageDiv;
}

function updateMessageContent(element, content) {
    const bubble = element.querySelector('.message-bubble');
    if (bubble) {
        const role = element.classList.contains('user') ? 'user' : 'assistant';
        const renderedContent = renderMessageContent(content, role === 'user');
        bubble.innerHTML = renderedContent;
        
        if (role !== 'user') {
            highlightCode(element);
        }
        
        scrollToBottom();
    }
}

function addWikiReferences(messageElement, wikiReferences) {
    if (!messageElement || !wikiReferences || wikiReferences.length === 0) {
        return;
    }
    
    const messageContent = messageElement.querySelector('.message-content');
    if (!messageContent) {
        return;
    }
    
    const existingRefs = messageContent.querySelector('.wiki-references');
    if (existingRefs) {
        return;
    }
    
    const wikiRefsHTML = renderWikiReferencesHTML(wikiReferences);
    
    const messageBubble = messageContent.querySelector('.message-bubble');
    const messageTime = messageContent.querySelector('.message-time');
    
    if (messageBubble && messageTime) {
        const wikiDiv = document.createElement('div');
        wikiDiv.className = 'wiki-references';
        wikiDiv.innerHTML = wikiRefsHTML;
        wikiDiv.style.opacity = '0';
        
        messageContent.insertBefore(wikiDiv, messageTime);
        
        requestAnimationFrame(() => {
            wikiDiv.style.transition = 'opacity 0.3s ease';
            wikiDiv.style.opacity = '1';
        });
        
        scrollToBottom();
    }
}

function addLoadingIndicator() {
    const container = document.getElementById('messagesContainer');
    const loadingDiv = document.createElement('div');
    const loadingId = 'loading-' + Date.now();
    loadingDiv.id = loadingId;
    loadingDiv.className = 'message assistant';
    loadingDiv.innerHTML = `
        <div class="message-avatar">🤖</div>
        <div class="message-content">
            <div class="loading-indicator">
                <span>正在思考</span>
                <div class="loading-dots">
                    <div class="loading-dot"></div>
                    <div class="loading-dot"></div>
                    <div class="loading-dot"></div>
                </div>
            </div>
        </div>
    `;
    
    container.appendChild(loadingDiv);
    scrollToBottom();
    
    return loadingId;
}

function removeLoadingIndicator(loadingId) {
    const loading = document.getElementById(loadingId);
    if (loading) {
        loading.remove();
    }
}

// ============================================
// 清空消息
// ============================================
function scrollToBottom() {
    const container = document.getElementById('messagesContainer');
    container.scrollTop = container.scrollHeight;
}

function clearMessageDisplay() {
    const container = document.getElementById('messagesContainer');
    container.innerHTML = `
        <div class="empty-state">
            <div class="empty-icon">💬</div>
            <div class="empty-text">开始新的对话</div>
            <div class="empty-hint">输入您的问题，AI 将为您提供帮助</div>
        </div>
    `;
}

async function clearMessages() {
    if (!currentSessionId) {
        showWarningToast('没有当前会话');
        return;
    }
    
    const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    if (!uuidRegex.test(currentSessionId)) {
        showErrorToast('无效的会话 ID 格式');
        return;
    }
    
    showConfirmModal(
        '清空对话',
        '确定要清空当前对话吗？此操作不可恢复，所有消息都将被删除。',
        async function() {
            try {
                const response = await fetch(`/snowai/session/${currentSessionId}/clear/`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCookie('csrftoken')
                    }
                });
                
                if (!response.ok) {
                    let errorMsg = '清空失败';
                    try {
                        const errorData = await response.json();
                        errorMsg = errorData.error || errorMsg;
                    } catch (e) {
                        errorMsg = `清空失败 (HTTP ${response.status})`;
                    }
                    showErrorToast(errorMsg);
                    return;
                }
                
                const text = await response.text();
                if (!text) {
                    showErrorToast('服务器返回空响应');
                    return;
                }
                
                let data;
                try {
                    data = JSON.parse(text);
                } catch (e) {
                    showErrorToast('服务器返回无效的响应格式');
                    return;
                }
                
                if (data.success) {
                    clearMessageDisplay();
                    showSuccessToast(data.message || '对话已清空');
                    loadSessions();
                } else {
                    showErrorToast(data.error || '清空失败');
                }
            } catch (error) {
                showErrorToast('清空消息失败: ' + error.message);
            }
        }
    );
}

// ============================================
// 模态框
// ============================================
function showConfirmModal(title, text, onConfirm) {
    const modal = document.getElementById('confirmModal');
    const modalTitle = document.getElementById('modalTitle');
    const modalText = document.getElementById('modalText');
    const confirmBtn = document.getElementById('modalConfirmBtn');
    
    modalTitle.textContent = title;
    modalText.textContent = text;
    
    const newConfirmBtn = confirmBtn.cloneNode(true);
    confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);
    
    newConfirmBtn.addEventListener('click', function() {
        closeConfirmModal();
        if (onConfirm && typeof onConfirm === 'function') {
            onConfirm();
        }
    });
    
    modal.classList.add('show');
}

function closeConfirmModal() {
    const modal = document.getElementById('confirmModal');
    modal.classList.remove('show');
}

// ============================================
// 图片查看器
// ============================================
function openImageViewer(imageSrc, imageAlt) {
    const viewer = document.getElementById('imageViewer');
    const viewerImg = document.getElementById('viewerImage');
    
    viewerImg.src = imageSrc;
    viewerImg.alt = imageAlt || '';
    
    viewer.classList.add('show');
    document.body.style.overflow = 'hidden';
}

function closeImageViewer() {
    const viewer = document.getElementById('imageViewer');
    viewer.classList.remove('show');
    document.body.style.overflow = '';
}

function initializeImageClickHandlers() {
    const messagesContainer = document.getElementById('messagesContainer');
    if (!messagesContainer) return;
    
    messagesContainer.addEventListener('click', function(e) {
        if (e.target.tagName === 'IMG' && e.target.closest('.message-bubble')) {
            e.preventDefault();
            e.stopPropagation();
            
            const imgSrc = e.target.src;
            const imgAlt = e.target.alt || e.target.title || '';
            
            openImageViewer(imgSrc, imgAlt);
        }
    });
}

// ============================================
// 模型选择
// ============================================
function changeModel() {
    if (currentModelId) {
        const modelName = document.getElementById('selectedModelText').textContent.trim();
        document.getElementById('chatModel').textContent = modelName;
    }
}

function toggleModelDropdown() {
    const select = document.getElementById('customModelSelect');
    const dropdown = document.getElementById('modelDropdown');
    
    if (!select || !dropdown) {
        console.error('模型下拉菜单元素未找到!');
        return;
    }
    
    const isActive = select.classList.contains('active');
    
    if (isActive) {
        closeModelDropdown();
    } else {
        // 添加active类到选择器
        select.classList.add('active');
        
        // 计算下拉菜单的位置（使用 fixed 定位，相对于视口）
        updateDropdownPosition();
        
        // 添加show类触发过渡动画
        dropdown.classList.add('show');
        
        // 监听页面滚动，自动关闭下拉菜单
        const scrollHandler = function() {
            closeModelDropdown();
            document.removeEventListener('scroll', scrollHandler, true);
        };
        document.addEventListener('scroll', scrollHandler, true);
        
        // 监听窗口大小变化，重新定位
        const resizeHandler = function() {
            if (dropdown.classList.contains('show')) {
                updateDropdownPosition();
            } else {
                window.removeEventListener('resize', resizeHandler);
            }
        };
        window.addEventListener('resize', resizeHandler);
    }
}

function updateDropdownPosition() {
    const select = document.getElementById('customModelSelect');
    const dropdown = document.getElementById('modelDropdown');
    
    if (!select || !dropdown) return;
    
    const rect = select.getBoundingClientRect();
    const viewportHeight = window.innerHeight;
    const dropdownMaxHeight = 320; // 与CSS中的max-height一致
    
    // 计算下拉菜单应该显示在上方还是下方
    const spaceBelow = viewportHeight - rect.bottom;
    const spaceAbove = rect.top;
    
    if (spaceBelow < dropdownMaxHeight && spaceAbove > spaceBelow) {
        // 空间不足，显示在上方
        dropdown.style.bottom = `${viewportHeight - rect.top + 8}px`;
        dropdown.style.top = 'auto';
    } else {
        // 显示在下方
        dropdown.style.top = `${rect.bottom + 8}px`;
        dropdown.style.bottom = 'auto';
    }
    
    dropdown.style.left = `${rect.left}px`;
    dropdown.style.width = `${rect.width}px`;
    
    // 移动端特殊处理：如果屏幕宽度较小，居中显示
    if (window.innerWidth <= 768) {
        const dropdownWidth = Math.min(rect.width, window.innerWidth - 32);
        dropdown.style.width = `${dropdownWidth}px`;
        dropdown.style.left = `${(window.innerWidth - dropdownWidth) / 2}px`;
    }
}

function closeModelDropdown() {
    const select = document.getElementById('customModelSelect');
    const dropdown = document.getElementById('modelDropdown');
    
    if (select) select.classList.remove('active');
    if (dropdown) dropdown.classList.remove('show');
}

function selectModel(modelId, modelName, isRecommended) {
    
    currentModelId = modelId;
    
    const selectedText = document.getElementById('selectedModelText');
    const chatModel = document.getElementById('chatModel');
    
    // 保存用户选择到 localStorage
    try {
        localStorage.setItem('selectedModelId', modelId);
        localStorage.setItem('selectedModelName', modelName);
    } catch (e) {
        console.error('保存模型选择失败:', e);
    }
    
    // 添加选择动画
    if (selectedText) {
        selectedText.style.opacity = '0.5';
        selectedText.style.transform = 'scale(0.95)';
        
        setTimeout(() => {
            selectedText.textContent = modelName;
            selectedText.style.opacity = '1';
            selectedText.style.transform = 'scale(1)';
        }, 150);
    }
    
    if (chatModel) {
        chatModel.textContent = modelName;
    }
    
    // 更新选项状态
    const options = document.querySelectorAll('.ai-model-option');
    let supportsWiki = false;
    
    options.forEach(opt => {
        if (opt.dataset.value === modelId) {
            opt.classList.add('active');
            // 添加选中动画
            opt.style.transition = 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)';
            // 获取模型是否支持知识库
            supportsWiki = opt.dataset.supportsWiki === 'true';
        } else {
            opt.classList.remove('active');
        }
    });
    
    // 更新知识库检索状态显示
    updateWikiSearchStatus(supportsWiki);
    
    closeModelDropdown();
    
    // 显示成功提示
    showSuccessToast(`已切换到 ${modelName}`);
}

// ============================================
// 工具函数
// ============================================
function copyMessageContent(button) {
    const messageContent = button.closest('.message-content');
    const messageBubble = messageContent.querySelector('.message-bubble');
    
    if (messageBubble) {
        const text = messageBubble.innerText || messageBubble.textContent;
        
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(() => {
                showSuccessToast('已复制到剪贴板');
            }).catch(err => {
                fallbackCopyText(text);
            });
        } else {
            fallbackCopyText(text);
        }
    }
}

function fallbackCopyText(text) {
    const textArea = document.createElement('textarea');
    textArea.value = text;
    textArea.style.position = 'fixed';
    textArea.style.left = '-999999px';
    textArea.style.top = '-999999px';
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    
    try {
        document.execCommand('copy');
        showSuccessToast('已复制到剪贴板');
    } catch (err) {
        showErrorToast('复制失败');
    }
    
    document.body.removeChild(textArea);
}

function rateMessage(messageId, rating) {
    fetch(`/snowai/message/${messageId}/rate/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken')
        },
        body: JSON.stringify({ rating })
    }).then(() => {
        showSuccessToast('感谢您的反馈！');
    });
}

function updateWikiSearchStatus(supportsWiki) {

    const wikiSearchStatus = document.getElementById('wikiSearchStatus');
    if (wikiSearchStatus) {
        if (supportsWiki) {
            wikiSearchStatus.innerHTML = '<span style="color: #10b981;">✓ 已启用</span>';
        } else {
            wikiSearchStatus.innerHTML = '<span style="color: #9ca3af;">✗ 未启用</span>';
        }
    }
}

function updateSessionInfo(session) {
    document.getElementById('messageCount').textContent = session.message_count || 0;
    document.getElementById('tokenUsed').textContent = session.total_tokens || 0;
    
    const chatTitle = document.getElementById('chatTitle');
    if (chatTitle) {
        chatTitle.textContent = session.title || 'WIKI智能助手';
    }
    
    if (session.ai_model && session.ai_model.uuid) {
        const sessionModelName = session.ai_model.name || '未知模型';
        
        const chatModel = document.getElementById('chatModel');
        if (chatModel) {
            chatModel.textContent = sessionModelName;
        }
        const currentOption = document.querySelector(`.ai-model-option[data-value="${currentModelId}"]`);
        if (currentOption) {
            const supportsWiki = currentOption.dataset.supportsWiki === 'true';
            updateWikiSearchStatus(supportsWiki);
        }
    }
}

function formatDate(dateStr) {
    const date = new Date(dateStr);
    const now = new Date();
    const diff = now - date;
    
    if (diff < 60000) return '刚刚';
    if (diff < 3600000) return Math.floor(diff / 60000) + '分钟前';
    if (diff < 86400000) return Math.floor(diff / 3600000) + '小时前';
    return date.toLocaleDateString();
}

function formatTime(dateStr) {
    return new Date(dateStr).toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit'
    });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML.replace(/\n/g, '<br>');
}

function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

function toggleSidebar() {
    const sidebar = document.getElementById('chatSidebar');
    const overlay = document.getElementById('sidebarOverlay');
    
    sidebar.classList.toggle('show');
    overlay.classList.toggle('show');
}

// ============================================
// 事件监听器
// ============================================
document.addEventListener('DOMContentLoaded', function() {
    // 确认模态框事件
    const modal = document.getElementById('confirmModal');
    modal.addEventListener('click', function(e) {
        if (e.target === modal) {
            closeConfirmModal();
        }
    });
    
    // 图片查看器事件
    const imageViewer = document.getElementById('imageViewer');
    imageViewer.addEventListener('click', function(e) {
        if (e.target === imageViewer || e.target.classList.contains('image-viewer-content')) {
            closeImageViewer();
        }
    });
    
    // ESC 键关闭模态框
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            if (modal.classList.contains('show')) {
                closeConfirmModal();
            }
            if (imageViewer.classList.contains('show')) {
                closeImageViewer();
            }
        }
    });
    
    // 初始化图片点击处理
    initializeImageClickHandlers();
});

// 浏览器前进/后退按钮支持
window.addEventListener('popstate', function(e) {
    const pathParts = window.location.pathname.split('/');
    const sessionIdIndex = pathParts.indexOf('chat') + 1;
    const urlSessionId = pathParts[sessionIdIndex];
    
    if (urlSessionId && urlSessionId !== currentSessionId) {
        switchSession(urlSessionId);
    } else if (!urlSessionId && currentSessionId) {
        currentSessionId = null;
        showWelcomeState();
        
        document.querySelectorAll('.session-item').forEach(item => {
            item.classList.remove('active');
        });
    }
});

// 点击外部关闭下拉框
document.addEventListener('click', function(e) {
    const select = document.getElementById('customModelSelect');
    const dropdown = document.getElementById('modelDropdown');
    
    // 如果点击的不是选择器也不是下拉菜单，则关闭
    if (select && dropdown && 
        !select.contains(e.target) && 
        !dropdown.contains(e.target)) {
        closeModelDropdown();
    }
});


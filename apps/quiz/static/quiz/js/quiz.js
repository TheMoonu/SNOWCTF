

(function() {
    'use strict';

    // ==================== 工具函数 ====================
    function getCsrfToken() {
        const name = 'csrftoken';
        if (document.cookie) {
            const cookies = document.cookie.split(';');
            for (let cookie of cookies) {
                cookie = cookie.trim();
                if (cookie.startsWith(name + '=')) {
                    return decodeURIComponent(cookie.substring(name.length + 1));
                }
            }
        }
        // 备用：从表单获取
        const input = document.querySelector('[name=csrfmiddlewaretoken]');
        return input ? input.value : '';
    }

    function safeShowToast(message, title, duration, type) {
        if (typeof showToast === 'function') {
            showToast(message, title, duration, type);
        } else {
            console.log(`[Toast] ${title}: ${message}`);
        }
    }

    // ==================== 答案缓存系统 ====================
    class AnswerCache {
        constructor(recordUuid) {
            this.recordUuid = recordUuid;
            this.cacheKey = `quiz_answers_${recordUuid}`;
            this.answers = this._load();
        }

        _load() {
            try {
                const data = JSON.parse(localStorage.getItem(this.cacheKey) || '{}');
                return data.recordUuid === this.recordUuid ? (data.answers || {}) : {};
            } catch (e) {
                return {};
            }
        }

        _save() {
            try {
                localStorage.setItem(this.cacheKey, JSON.stringify({
                    recordUuid: this.recordUuid,
                    answers: this.answers,
                    timestamp: Date.now()
                }));
            } catch (e) {
                console.warn('[缓存] 保存失败:', e);
            }
        }

        set(questionId, optionIds, textAnswer = null) {
            this.answers[questionId] = {
                optionIds: Array.isArray(optionIds) ? optionIds : [optionIds],
                textAnswer: textAnswer,
                timestamp: Date.now()
            };
            this._save();
        }

        get(questionId) {
            return this.answers[questionId]?.optionIds || [];
        }
        
        getText(questionId) {
            return this.answers[questionId]?.textAnswer || '';
        }

        has(questionId) {
            return !!this.answers[questionId];
        }

        count() {
            return Object.keys(this.answers).length;
        }

        getAll() {
            return this.answers;
        }

        clear() {
            localStorage.removeItem(this.cacheKey);
            this.answers = {};
        }

        // 保存到服务器
        async saveToServer() {
            if (Object.keys(this.answers).length === 0) {
                return { success: true };
            }

            try {
                const response = await fetch(`/quiz/answer/${this.recordUuid}/batch-save/`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken()
                    },
                    body: JSON.stringify({ answers: this.answers })
                });
                return await response.json();
            } catch (error) {
                console.error('[缓存] 保存到服务器失败:', error);
                return { success: false, error: error.message };
            }
        }

        // 从服务器恢复
        async restoreFromServer() {
            try {
                const response = await fetch(`/quiz/answer/${this.recordUuid}/get-answers/`);
                const result = await response.json();
                if (result.success && result.answers) {
                    for (const qid in result.answers) {
                        if (!this.answers[qid]) {
                            this.answers[qid] = result.answers[qid];
                        }
                    }
                    this._save();
                    return true;
                }
            } catch (e) {
                console.warn('[缓存] 从服务器恢复失败:', e);
            }
            return Object.keys(this.answers).length > 0;
        }
    }

    // ==================== 异步提交系统 ====================
    class QuizSubmitter {
        constructor(recordUuid, answerCache) {
            this.recordUuid = recordUuid;
            this.answerCache = answerCache;
            this.isSubmitting = false;
        }

        async submit() {
            if (this.isSubmitting) {
                return { success: false, error: '正在提交中，请稍候' };
            }

            this.isSubmitting = true;

            try {
                // 1. 保存答案
                const saveResult = await this.answerCache.saveToServer();
                if (!saveResult.success) {
                    throw new Error('保存答案失败');
                }

                // 2. 创建提交任务
                const taskResult = await this._createTask();
                if (!taskResult.success) {
                    throw new Error(taskResult.error || '创建任务失败');
                }

                // 3. 轮询任务状态
                const finalResult = await this._pollTask(taskResult.task_id);

                if (finalResult.success) {
                    this.answerCache.clear();
                }

                return finalResult;

            } catch (error) {
                return { success: false, error: error.message };
            } finally {
                this.isSubmitting = false;
            }
        }

        async _createTask() {
            const response = await fetch(`/quiz/answer/${this.recordUuid}/submit/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest',
                    'X-CSRFToken': getCsrfToken()
                }
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            return await response.json();
        }

        async _pollTask(taskId, maxAttempts = 30, interval = 1000) {
            for (let i = 1; i <= maxAttempts; i++) {
                try {
                    const response = await fetch(`/quiz/submit-task/${taskId}/`, {
                        headers: { 'X-Requested-With': 'XMLHttpRequest' }
                    });

                    if (!response.ok) {
                        if (response.status === 404) {
                            return { success: false, error: '任务不存在' };
                        }
                        throw new Error(`HTTP ${response.status}`);
                    }

                    const result = await response.json();
                    if (!result.success) {
                        return { success: false, error: result.error };
                    }

                    const info = result.task_info;

                    if (info.status === 'success') {
                        return { success: true, data: info.data };
                    }
                    if (info.status === 'failed') {
                        return { success: false, error: info.error };
                    }

                    await new Promise(r => setTimeout(r, interval));

                } catch (e) {
                    console.warn(`[提交] 轮询 ${i} 失败:`, e);
                    if (i === maxAttempts) {
                        return { success: false, error: '查询超时' };
                    }
                    await new Promise(r => setTimeout(r, interval));
                }
            }

            return { success: false, error: '提交超时' };
        }
    }

    // ==================== 倒计时系统 ====================
    class QuizTimer {
        constructor(recordUuid, seconds, onTimeout) {
            this.recordUuid = recordUuid;
            this.seconds = seconds;
            this.onTimeout = onTimeout;
            this.element = document.getElementById('quiz-timer');
            this.intervalId = null;
        }

        start() {
            if (!this.element) {
                console.error('[倒计时] 找不到 #quiz-timer');
                return;
            }

            this._update();
            this.intervalId = setInterval(() => {
                this.seconds--;
                this._update();

                if (this.seconds <= 0) {
                    this.stop();
                    if (this.onTimeout) this.onTimeout();
                }
            }, 1000);

        }

        stop() {
            if (this.intervalId) {
                clearInterval(this.intervalId);
                this.intervalId = null;
            }
        }

        _update() {
            if (!this.element) return;

            const h = Math.floor(this.seconds / 3600);
            const m = Math.floor((this.seconds % 3600) / 60);
            const s = this.seconds % 60;

            this.element.textContent = h > 0
                ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
                : `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;

            // 最后5分钟警告
            const parent = this.element.parentElement;
            if (parent && this.seconds <= 300) {
                parent.style.background = '#dc3545';
                parent.style.color = 'white';
            }
        }
    }

    // ==================== 防作弊系统 ====================
    class AntiCheat {
        constructor(recordUuid, config) {
            this.recordUuid = recordUuid;
            this.violationCount = config.violationCount || 0;
            this.maxViolations = config.maxViolations || 5;
            this.allowLeave = false;
            this.lastViolationTime = 0;
        }

        init() {
            this._setupFullscreen();
            this._setupFocusMonitor();
            this._setupLeaveProtection();
            this._setupKeyboardProtection();
        }

        _setupFullscreen() {
            const enter = () => {
                if (this.allowLeave) return;
                const el = document.documentElement;
                (el.requestFullscreen || el.webkitRequestFullscreen || el.mozRequestFullScreen)?.call(el);
            };

            setTimeout(enter, 500);
            document.addEventListener('click', enter, { once: true });

            const onChange = () => {
                const isFullscreen = !!(document.fullscreenElement || document.webkitFullscreenElement);
                if (!isFullscreen && !this.allowLeave) {
                    this._recordViolation('退出全屏');
                    if (typeof $ !== 'undefined') {
                        $('#exitFullscreenModal').modal('show');
                    }
                } else if (isFullscreen) {
                    // 进入全屏后，关闭模态框
                    if (typeof $ !== 'undefined') {
                        $('#exitFullscreenModal').modal('hide');
                    }
                }
            };

            document.addEventListener('fullscreenchange', onChange);
            document.addEventListener('webkitfullscreenchange', onChange);

            // 重新进入全屏按钮
            document.getElementById('reenterFullscreen')?.addEventListener('click', () => {
                const el = document.documentElement;
                (el.requestFullscreen || el.webkitRequestFullscreen || el.mozRequestFullScreen)?.call(el);
            });
        }

        _setupFocusMonitor() {
            window.addEventListener('blur', () => {
                if (!this.allowLeave) {
                    this._recordViolation('切换窗口');
                }
            });

            document.addEventListener('visibilitychange', () => {
                if (document.hidden && !this.allowLeave) {
                    this._recordViolation('页面隐藏');
                }
            });
        }

        _setupLeaveProtection() {
            window.addEventListener('beforeunload', (e) => {
                if (!this.allowLeave) {
                    e.preventDefault();
                    e.returnValue = '';
                    return '';
                }
            });
        }

        _setupKeyboardProtection() {
            document.addEventListener('keydown', (e) => {
                // F12, Ctrl+Shift+I, Ctrl+Shift+C, Ctrl+U
                if (e.key === 'F12' ||
                    (e.ctrlKey && e.shiftKey && (e.key === 'I' || e.key === 'C')) ||
                    (e.ctrlKey && e.key === 'u')) {
                    e.preventDefault();
                    this._recordViolation('尝试打开开发工具');
                }
            });

            document.addEventListener('contextmenu', (e) => e.preventDefault());
        }

        _recordViolation(type) {
            const now = Date.now();
            if (now - this.lastViolationTime < 2000) return;
            this.lastViolationTime = now;
            this.violationCount++;


            fetch(`/quiz/answer/${this.recordUuid}/violation/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken()
                },
                body: JSON.stringify({ type, time: new Date().toLocaleTimeString() })
            })
            .then(r => r.json())
            .then(data => {
                if (data.force_submit) {
                    this.allowLeave = true;
                    if (typeof $ !== 'undefined') {
                        $('#forceSubmitModal').modal('show');
                    }
                    document.getElementById('goToResult')?.addEventListener('click', () => {
                        window.location.href = `/quiz/result/${this.recordUuid}/`;
                    });
                } else {
                    this._updateBadge();
                }
            })
            .catch(e => console.warn('[防作弊] 记录失败:', e));
        }

        _updateBadge() {
            const badge = document.getElementById('violation-badge');
            const text = document.getElementById('violation-count-text');
            if (badge && text) {
                text.textContent = `违规: ${this.violationCount}/${this.maxViolations}`;
                badge.style.display = 'block';
                badge.style.background = this.violationCount >= 4 ? '#f8d7da' :
                                         this.violationCount >= 2 ? '#fff3cd' : '#d4edda';
                badge.style.color = this.violationCount >= 4 ? '#721c24' :
                                    this.violationCount >= 2 ? '#856404' : '#155724';
            }
        }
    }

    // ==================== 答题交互系统 ====================
    class QuizUI {
        constructor(answerCache) {
            this.answerCache = answerCache;
            this.cards = document.querySelectorAll('.quiz-question-card');
            this.navItems = document.querySelectorAll('.quiz-nav-item');
            this.currentIndex = 0;
        }

        init() {
            this._bindNavigation();
            this._bindOptions();
            this._restoreAnswers();
            this._updateProgress();
        }

        _bindNavigation() {
            // 上一题/下一题
            document.querySelectorAll('.quiz-prev-btn').forEach(btn => {
                btn.addEventListener('click', () => this._goto(this.currentIndex - 1));
            });
            document.querySelectorAll('.quiz-next-btn').forEach(btn => {
                btn.addEventListener('click', () => this._goto(this.currentIndex + 1));
            });

            // 答题卡
            this.navItems.forEach((item, i) => {
                item.addEventListener('click', () => this._goto(i));
            });
        }

        _goto(index) {
            if (index < 0 || index >= this.cards.length) return;

            this.cards[this.currentIndex].style.display = 'none';
            this.navItems[this.currentIndex].classList.remove('current');

            this.cards[index].style.display = 'block';
            this.navItems[index].classList.add('current');

            this.currentIndex = index;
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }

        _bindOptions() {
            // 绑定选择题/判断题的选项
            document.querySelectorAll('.quiz-option').forEach(div => {
                div.addEventListener('click', () => {
                    const input = div.querySelector('input');
                    if (!input) return;

                    if (input.type === 'radio') {
                        document.querySelectorAll(`input[name="${input.name}"]`).forEach(r => {
                            r.closest('.quiz-option')?.classList.remove('selected');
                        });
                        input.checked = true;
                        div.classList.add('selected');
                    } else {
                        input.checked = !input.checked;
                        div.classList.toggle('selected');
                    }

                    this._saveAnswer(input);
                });
            });
            
            // 绑定填空题输入框
            document.querySelectorAll('.quiz-text-input').forEach(input => {
                input.addEventListener('input', () => {
                    this._saveTextAnswer(input);
                });
                input.addEventListener('blur', () => {
                    this._saveTextAnswer(input);
                });
            });
            
            // 绑定简答题输入框
            document.querySelectorAll('.quiz-textarea-input').forEach(textarea => {
                textarea.addEventListener('input', () => {
                    this._saveTextAnswer(textarea);
                });
                textarea.addEventListener('blur', () => {
                    this._saveTextAnswer(textarea);
                });
            });
        }

        _saveAnswer(input) {
            const qid = input.name.replace('question_', '');
            let values;

            if (input.type === 'radio') {
                values = [parseInt(input.value)];
            } else {
                values = Array.from(document.querySelectorAll(`input[name="${input.name}"]:checked`))
                    .map(cb => parseInt(cb.value));
            }

            this.answerCache.set(qid, values);
            this._updateProgress();
            this._updateNavItem(qid);
        }
        
        _saveTextAnswer(input) {
            const qid = input.name.replace('question_', '').replace('_text', '');
            const textAnswer = input.value.trim();
            
            // 保存文本答案（不需要选项ID）
            this.answerCache.set(qid, [], textAnswer);
            this._updateProgress();
            this._updateNavItem(qid);
        }

        _restoreAnswers() {
            const all = this.answerCache.getAll();
            for (const qid in all) {
                const answerData = all[qid];
                
                // 恢复选择题/判断题的选项
                const ids = answerData.optionIds || [];
                ids.forEach(id => {
                    const input = document.querySelector(`input[value="${id}"]`);
                    if (input) {
                        input.checked = true;
                        input.closest('.quiz-option')?.classList.add('selected');
                    }
                });
                
                // 恢复填空题/简答题的文本答案
                if (answerData.textAnswer) {
                    const textInput = document.querySelector(`input[name="question_${qid}_text"]`);
                    const textArea = document.querySelector(`textarea[name="question_${qid}_text"]`);
                    
                    if (textInput) {
                        textInput.value = answerData.textAnswer;
                    } else if (textArea) {
                        textArea.value = answerData.textAnswer;
                    }
                }
                
                this._updateNavItem(qid);
            }
        }

        _updateProgress() {
            const total = this.cards.length;
            const answered = this.answerCache.count();

            const text = document.getElementById('quiz-progress-text');
            if (text) text.textContent = `已答 ${answered} / ${total} 题`;

            const bar = document.getElementById('quiz-progress-bar');
            if (bar) bar.style.width = `${(answered / total) * 100}%`;
        }

        _updateNavItem(qid) {
            const card = document.querySelector(`.quiz-question-card[data-question-id="${qid}"]`);
            if (!card) return;

            const index = Array.from(this.cards).indexOf(card);
            const navItem = this.navItems[index];
            if (navItem && this.answerCache.has(qid)) {
                navItem.classList.remove('unanswered');
                navItem.classList.add('answered');
            }
        }

        getUnansweredCount() {
            return document.querySelectorAll('.quiz-nav-item.unanswered').length;
        }
    }

    // ==================== 主控制器 ====================
    class QuizController {
        constructor(config) {
            this.config = config;
            this.answerCache = new AnswerCache(config.recordUuid);
            this.submitter = new QuizSubmitter(config.recordUuid, this.answerCache);
            this.ui = new QuizUI(this.answerCache);
            this.antiCheat = null;
            this.timer = null;
        }

        async init() {

            try {
                // 1. 恢复答案
                await this.answerCache.restoreFromServer();

                // 2. 初始化UI
                this.ui.init();

                // 3. 绑定提交按钮
                this._bindSubmitButton();

                // 4. 防作弊系统
                if (this.config.enableAntiCheat === true) {
                    this.antiCheat = new AntiCheat(this.config.recordUuid, {
                        violationCount: this.config.violationCount,
                        maxViolations: this.config.maxViolations
                    });
                    this.antiCheat.init();
                }

                // 5. 倒计时
                if (this.config.remainingSeconds > 0) {
                    this.timer = new QuizTimer(
                        this.config.recordUuid,
                        this.config.remainingSeconds,
                        () => this._handleTimeout()
                    );
                    this.timer.start();
                }

                // 暴露全局对象
                window.QUIZ = {
                    cache: this.answerCache,
                    submitter: this.submitter,
                    ui: this.ui,
                    antiCheat: this.antiCheat,
                    timer: this.timer
                };


            } catch (error) {
                console.error('[系统] 初始化失败:', error);
            }
        }

        _bindSubmitButton() {
            const submitBtn = document.getElementById('quiz-submit-btn');
            const confirmBtn = document.getElementById('confirm-submit-btn');

            if (submitBtn) {
                submitBtn.addEventListener('click', () => {
                    const count = this.ui.getUnansweredCount();
                    const warning = document.getElementById('unanswered-warning');
                    const countSpan = document.getElementById('unanswered-count');

                    if (count > 0 && warning && countSpan) {
                        countSpan.textContent = `还有 ${count} 题未作答`;
                        warning.style.display = 'block';
                    } else if (warning) {
                        warning.style.display = 'none';
                    }

                    if (typeof $ !== 'undefined') {
                        $('#confirmSubmitModal').modal('show');
                    }
                });
            }

            if (confirmBtn) {
                confirmBtn.addEventListener('click', () => this._doSubmit(confirmBtn));
            }
        }

        async _doSubmit(btn) {
            const originalHtml = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<i class="bi bi-hourglass-split mr-1"></i>提交中...';

            if (this.antiCheat) {
                this.antiCheat.allowLeave = true;
            }

            try {
                const result = await this.submitter.submit();

                if (result.success) {
                    safeShowToast('提交成功！正在跳转...', '成功', 1500, 'success');

                    if (typeof $ !== 'undefined') {
                        $('#confirmSubmitModal').modal('hide');
                    }

                    setTimeout(() => {
                        const uuid = result.data?.record_uuid || this.config.recordUuid;
                        window.location.href = `/quiz/result/${uuid}/`;
                    }, 1500);
                } else {
                    safeShowToast(`提交失败: ${result.error}`, '错误', 5000, 'error');
                    btn.disabled = false;
                    btn.innerHTML = originalHtml;
                }
            } catch (error) {
                safeShowToast('提交异常，请重试', '错误', 5000, 'error');
                btn.disabled = false;
                btn.innerHTML = originalHtml;
            }
        }

        async _handleTimeout() {
            safeShowToast('时间到，正在自动提交...', '提示', 0, 'warning');

            if (this.antiCheat) {
                this.antiCheat.allowLeave = true;
            }

            try {
                const result = await this.submitter.submit();

                if (result.success) {
                    safeShowToast('提交成功！', '成功', 1500, 'success');
                    setTimeout(() => {
                        const uuid = result.data?.record_uuid || this.config.recordUuid;
                        window.location.href = `/quiz/result/${uuid}/`;
                    }, 1500);
                } else {
                    safeShowToast(`自动提交失败: ${result.error}`, '错误', 5000, 'error');
                    setTimeout(() => {
                        window.location.href = '/quiz/my-records/';
                    }, 5000);
                }
            } catch (error) {
                console.error('[自动提交] 异常:', error);
                setTimeout(() => {
                    window.location.href = '/quiz/my-records/';
                }, 3000);
            }
        }
    }

    // ==================== 全局初始化函数 ====================
    window.initQuizSystem = function(config) {
        const controller = new QuizController(config);
        controller.init();
    };

})();

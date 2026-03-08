document.addEventListener('DOMContentLoaded', function() {
    const challengeContainer = document.getElementById('challenge-container');
    const challengeList = document.getElementById('challenge-list');
    const filterLinks = document.querySelectorAll('[data-filter]');

    function updateActiveStatus(filter, value) {
        const links = document.querySelectorAll(`[data-filter="${filter}"]`);
        links.forEach(link => {
            if (link.dataset.value === value) {
                link.classList.add('active-type');
            } else {
                link.classList.remove('active-type');
            }
        });
    }

    function updateUI(url) {
        const params = new URLSearchParams(url.search);
        updateActiveStatus('type', params.get('type') || '');
        updateActiveStatus('difficulty', params.get('difficulty') || '');
        updateActiveStatus('status', params.get('status') || '');
        updateActiveStatus('author', params.get('author') || '');
    }

    filterLinks.forEach(link => {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            const filter = this.dataset.filter;
            const value = this.dataset.value;

            // 构建查询 URL
            const url = new URL(window.location);
            
            // 删除搜索查询和标签参数
            url.searchParams.delete('q');
            url.searchParams.delete('tag');

            if (value === '') {
                url.searchParams.delete(filter);
            } else {
                url.searchParams.set(filter, value);
            }

            // 发送 AJAX 请求
            fetch(url)
                .then(response => response.text())
                .then(html => {
                    // 解析返回的 HTML
                    const parser = new DOMParser();
                    const doc = parser.parseFromString(html, 'text/html');
                    const newChallengeList = doc.getElementById('challenge-list');

                    // 更新挑战列表
                    if (challengeList && newChallengeList) {
                        challengeList.innerHTML = newChallengeList.innerHTML;
                    }

                    // 更新 URL，但不重新加载页面
                    history.pushState({}, '', url);

                    // 更新 UI 活动状态
                    updateUI(url);
                })
                .catch(error => {
                    console.error('Error:', error);
                });
        });
    });
});
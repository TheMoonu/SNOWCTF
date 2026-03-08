document.addEventListener('DOMContentLoaded', function() {
    const challengeContainer = document.getElementById('challenge-container');
    const challengeContent = document.getElementById('challenge-content');
    const challengeList = document.getElementById('challenge-list');
    const filterLinks = document.querySelectorAll('[data-filter]');

    // 平滑滚动到题目列表区域（用于分页链接）
    // 检查URL中是否有 #challenge-list 锚点
    if (window.location.hash === '#challenge-list' && challengeList) {
        setTimeout(function() {
            const yOffset = -20; // 顶部偏移量（像素）
            const element = challengeList;
            const y = element.getBoundingClientRect().top + window.pageYOffset + yOffset;
            window.scrollTo({top: y, behavior: 'smooth'});
        }, 100);
    }

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

            const url = new URL(window.location);
            
            url.searchParams.delete('q');
            url.searchParams.delete('tag');
            // 改变过滤条件时重置到第1页
            url.searchParams.delete('page');

            if (value === '') {
                url.searchParams.delete(filter);
            } else {
                url.searchParams.set(filter, value);
            }
            fetch(url)
                .then(response => response.text())
                .then(html => {
                    const parser = new DOMParser();
                    const doc = parser.parseFromString(html, 'text/html');
                    const newChallengeContent = doc.getElementById('challenge-content');

                    // 更新整个内容区域（包含题目列表和分页）
                    if (challengeContent && newChallengeContent) {
                        challengeContent.innerHTML = newChallengeContent.innerHTML;
                    }
                    
                    history.pushState({}, '', url);
                    updateUI(url);
                })
                .catch(error => {
                    console.error('Error:', error);
                });
        });
    });
});
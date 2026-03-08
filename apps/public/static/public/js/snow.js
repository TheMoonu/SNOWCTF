document.addEventListener('DOMContentLoaded', () => {
  const container = document.querySelector('#masonry-container');
  if (!container) return;

  // 给已有卡片打上 masonry 标记
  container.querySelectorAll('.snow-waterfall .col-sm-6')
           .forEach(el => el.classList.add('grid-item'));

  const msnry = new Masonry(container, {
    itemSelector: '.grid-item',
    percentPosition: true,
    transitionDuration: '0.3s',
    initLayout: true
  });

  /* 分页状态 */
  let page = 2;                // 下一次要请求的页码（第 1 页已在 html 里）
  let loading = false;
  let hasNext = typeof window.initialHasNext !== 'undefined' ? window.initialHasNext : true;  // 从模板初始化，或默认 true

  /* DOM 引用 */
  const loaderDots = document.querySelector('.snow-loading-dots');
  const noMore      = document.querySelector('.snow-no-more');
  const trigger     = document.querySelector('#infinite-scroll-trigger');

  /* 核心：加载一页 */
  function loadMore() {
    if (loading || !hasNext) return;
    loading = true;
    loaderDots.style.display = 'flex';
    if (trigger) trigger.style.display = 'none';

    fetch(`${window.location.pathname}?page=${page}`, {
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
      .then(res => res.json())
      .then(data => {
        // 服务器没返回内容 => 到底了
        if (!data.html || data.html.trim() === '') {
          hasNext = false;
          if (trigger) trigger.style.display = 'none';
          if (noMore) noMore.style.display = 'block';
          return;
        }

        // 1. 插节点
        const frag  = document.createRange().createContextualFragment(data.html);
        const items = Array.from(frag.querySelectorAll('.col-sm-6'));
        items.forEach(el => {
          el.classList.add('grid-item');
          container.appendChild(el);
        });

        // 2. 通知 masonry
        msnry.appended(items);
        msnry.layout();

        // 3. 更新分页状态
        hasNext = data.has_next;      // 服务器告诉能不能继续
        if (hasNext) {
          page += 1;                  // 只有还有才继续往下翻
        } else {
          if (trigger) trigger.style.display = 'none';  // 没有下一页时隐藏触发器
          if (noMore) noMore.style.display = 'block';   // 显示"没有更多"
        }

        // 4. 可选：剩余篇数提示
        const rest = data.total - (page - 1) * (data.per_page || 3);
        console.log(`剩余 ${Math.max(0, rest)} 篇`);
      })
      .catch(err => console.error('加载失败:', err))
      .finally(() => {
        loading = false;
        loaderDots.style.display = 'none';
        if (trigger && hasNext) {
          trigger.style.display = 'block';
        } else if (trigger) {
          trigger.style.display = 'none';
        }
      });
  }

  /* 滚动触发（简单节流） */
  let throttleTimeout;
  const scrollHandler = () => {
    if (!trigger || !hasNext || loading) return;
    const rect = trigger.getBoundingClientRect();
    if (rect.top < window.innerHeight + 100 && !throttleTimeout) {
      throttleTimeout = setTimeout(() => {
        loadMore();
        throttleTimeout = null;
      }, 200);
    }
  };
  window.addEventListener('scroll', scrollHandler, { passive: true });
});
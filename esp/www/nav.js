// /www/nav.js — E6 底部导航统一注入
// 用法：页面 </body> 前加 <script src="/nav.js"></script>
//       可选 data-nav-layout="fixed" | "scroll"（默认 scroll 自动留底部空间）
(function(){
  if (window.__e6_nav_injected__) return;
  window.__e6_nav_injected__ = true;

  var NAV = [
    { href: '/',        key: 'home',    icon: '🏠', label: '主页' },
    { href: '/convert', key: 'convert', icon: '🖼',  label: '转换' },
    { href: '/edit',    key: 'edit',    icon: '✏️', label: '编辑' },
    { href: '/text',    key: 'text',    icon: '🅰️', label: '文字' },
    { href: '/mosaic',  key: 'mosaic',  icon: '🧩', label: '拼图' },
    { href: '/#upload', key: 'upload',  icon: '📤', label: '上传' },
    { href: '/wifi',    key: 'wifi',    icon: '📶', label: '网络' }
  ];

  var layout = (document.body && document.body.getAttribute('data-nav-layout')) || 'scroll';

  // 暗色主题检测
  var isDark = false;
  try {
    isDark = getComputedStyle(document.documentElement)
              .getPropertyValue('--panel').trim() !== '';
  } catch (e) {}

  var S = {
    bg:     isDark ? 'rgba(30,30,28,0.96)'      : 'rgba(255,255,255,0.96)',
    border: isDark ? 'rgba(255,255,255,0.08)'   : 'rgba(0,0,0,0.08)',
    shadow: isDark ? '0 -1px 6px rgba(0,0,0,0.2)' : '0 -1px 6px rgba(0,0,0,0.04)',
    idle:   isDark ? '#8a8a85' : '#888',
    active: isDark ? '#f2f2ed' : '#06c'
  };

  // 关键改动：
  //  - .e6-bottom-nav  overflow-x:auto + 隐藏滚动条
  //  - .e6-nav-item    flex:1 0 58px  → 空间足够时平分，空间不足时每项固定 58px
  var css =
    '.e6-bottom-nav{position:fixed;bottom:0;left:0;right:0;height:60px;display:flex;' +
    'flex-wrap:nowrap;overflow-x:auto;overflow-y:hidden;' +
    'background:' + S.bg + ';' +
    '-webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px);' +
    'border-top:1px solid ' + S.border + ';' +
    'box-shadow:' + S.shadow + ';z-index:900;' +
    'padding-bottom:env(safe-area-inset-bottom,0px);box-sizing:content-box;' +
    '-webkit-overflow-scrolling:touch;' +
    'scrollbar-width:none;-ms-overflow-style:none;' +
    'font-family:-apple-system,system-ui,"Segoe UI",sans-serif}' +
    '.e6-bottom-nav::-webkit-scrollbar{display:none}' +
    '.e6-nav-item{flex:1 0 58px;display:flex;flex-direction:column;align-items:center;' +
    'justify-content:center;text-decoration:none;color:' + S.idle + ';' +
    'font-size:11px;font-weight:500;gap:1px;padding:0 2px;min-width:58px;' +
    'box-sizing:border-box;' +
    '-webkit-tap-highlight-color:transparent;transition:color .15s;' +
    'scroll-snap-align:center}' +
    '.e6-nav-item.active,.e6-nav-item:hover{color:' + S.active + '}' +
    '.e6-nav-icon{font-size:19px;line-height:1}' +
    '.e6-nav-label{font-size:10.5px;line-height:1.2;white-space:nowrap;' +
    'overflow:hidden;text-overflow:ellipsis;max-width:100%}' +
    'body[data-nav-layout="scroll"]{' +
    'padding-bottom:calc(60px + env(safe-area-inset-bottom,0px) + 12px)!important}';

  var st = document.createElement('style');
  st.textContent = css;
  document.head.appendChild(st);

  // 决定当前应该高亮哪一项
  function computeCurrentKey(){
    var p = (location.pathname || '/').replace(/\/+$/, '') || '/';
    var hash = (location.hash || '').replace(/^#/, '');

    if (p === '/'){
      if (hash === 'upload') return 'upload';
      return 'home';
    }
    for (var i = 0; i < NAV.length; i++){
      if (NAV[i].href.indexOf('#') < 0 && NAV[i].href === p){
        return NAV[i].key;
      }
    }
    return 'home';
  }

  var nav = document.createElement('nav');
  nav.className = 'e6-bottom-nav';
  nav.setAttribute('aria-label', '主导航');
  nav.innerHTML = NAV.map(function (n) {
    return '<a href="' + n.href + '" class="e6-nav-item" data-key="' + n.key + '">' +
           '<span class="e6-nav-icon">' + n.icon + '</span>' +
           '<span class="e6-nav-label">' + n.label + '</span>' +
           '</a>';
  }).join('');
  document.body.appendChild(nav);

  function scrollActiveIntoView(behavior){
    var active = nav.querySelector('a.e6-nav-item.active');
    if (!active) return;
    // 只有真正溢出时才滚动，避免桌面端“每次加载都轻微横向跳动”
    if (nav.scrollWidth <= nav.clientWidth + 2) return;
    try {
      active.scrollIntoView({
        behavior: behavior || 'auto',
        block: 'nearest',
        inline: 'center'
      });
    } catch (e) {
      // 老浏览器没有 options 参数，退化到直接设置 scrollLeft
      var target = active.offsetLeft - (nav.clientWidth - active.offsetWidth) / 2;
      nav.scrollLeft = target;
    }
  }

  var lastKey = null;

  function updateActive(){
    var curKey = computeCurrentKey();
    var links = nav.querySelectorAll('a.e6-nav-item');
    for (var i = 0; i < links.length; i++){
      var k = links[i].getAttribute('data-key');
      if (k === curKey) links[i].classList.add('active');
      else links[i].classList.remove('active');
    }
    // 只在 key 变化时滚动，避免无意义滚动
    if (curKey !== lastKey){
      lastKey = curKey;
      // 首次用 auto，切页用 smooth
      scrollActiveIntoView(lastKey === curKey ? 'smooth' : 'auto');
    }
  }

  // 桌面端：鼠标纵向滚轮映射为横向滚动（仅当 nav 溢出时）
  nav.addEventListener('wheel', function(e){
    if (nav.scrollWidth <= nav.clientWidth + 2) return;
    if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
    e.preventDefault();
    nav.scrollLeft += e.deltaY;
  }, { passive: false });

  // 触屏：允许左右拖动
  var touchStartX = 0, touchStartScroll = 0, touching = false;
  nav.addEventListener('touchstart', function(e){
    if (e.touches.length !== 1) return;
    if (nav.scrollWidth <= nav.clientWidth + 2) return;
    touching = true;
    touchStartX = e.touches[0].clientX;
    touchStartScroll = nav.scrollLeft;
  }, { passive: true });
  nav.addEventListener('touchmove', function(e){
    if (!touching || e.touches.length !== 1) return;
    var dx = e.touches[0].clientX - touchStartX;
    if (Math.abs(dx) > 4){
      // 阻止父页面滚动
      e.preventDefault();
      nav.scrollLeft = touchStartScroll - dx;
    }
  }, { passive: false });
  nav.addEventListener('touchend', function(){ touching = false; });
  nav.addEventListener('touchcancel', function(){ touching = false; });

  // hashchange 时同步高亮
  window.addEventListener('hashchange', updateActive);
  window.addEventListener('popstate', updateActive);

  // 初次调用
  updateActive();

  // 首次加载时用 auto 滚动，避免动画干扰
  setTimeout(function(){ scrollActiveIntoView('auto'); }, 60);

  // 窗口尺寸变化时重新计算
  var resizeTimer = 0;
  window.addEventListener('resize', function(){
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function(){
      scrollActiveIntoView('auto');
    }, 150);
  });
})();
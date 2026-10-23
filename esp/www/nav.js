// /nav.js — 公共顶部导航注入
// 用法：页面的 header 里放 <nav id="nav"></nav>
(function(){
'use strict';

var NAV = [
  { href: '/',     icon: '📚', label: '书架', key: 'home' },
  { href: '/wifi', icon: '📶', label: '网络', key: 'wifi' }
];

var path = (location.pathname || '/').replace(/\/+$/, '') || '/';
var current = 'home';
for (var i = 0; i < NAV.length; i++) {
  if (NAV[i].href === path) { current = NAV[i].key; break; }
}

var nav = document.getElementById('nav');
if (!nav) return;

var html = '';
for (var j = 0; j < NAV.length; j++) {
  var n = NAV[j];
  html += '<a href="' + n.href + '"'
       + (n.key === current ? ' class="active"' : '')
       + '>' + n.icon + ' ' + n.label + '</a>';
}
nav.innerHTML = html;
})();
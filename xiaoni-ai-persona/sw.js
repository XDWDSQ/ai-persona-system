/* 小拟 PWA Service Worker —— 应用壳缓存，替代原 APK 的秒开体验。
   策略：导航请求网络优先（更新即时生效，离线回退缓存）；
   静态资源缓存优先 + 后台刷新；API 与上传目录一律不缓存。 */
var CACHE = 'xiaoni-shell-v7';
var SHELL = [
  '/pages/chat.html',
  '/pages/css/chat.css',
  '/pages/js/app.js',
  '/pages/pet.js',
  '/pages/favicon.svg',
  '/pages/bg-ambient.webp',
  '/pages/avatar_dashuai_64.webp',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/apple-touch-icon.png'
];

self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(CACHE)
      .then(function (c) { return c.addAll(SHELL); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(keys.filter(function (k) { return k !== CACHE; })
          .map(function (k) { return caches.delete(k); }));
      })
      .then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return;
  var url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.indexOf('/api/') === 0 || url.pathname.indexOf('/uploads/') === 0) return;

  if (req.mode === 'navigate') {
    /* 导航：网络优先，成功则回写缓存；离线时回退缓存副本 */
    e.respondWith(
      fetch(req).then(function (res) {
        /* 只回写 2xx：Cache API 拒绝 5xx/4xx，裸 c.put 会产生未捕获的 reject */
        if (res && res.status === 200) {
          var copy = res.clone();
          caches.open(CACHE).then(function (c) { c.put(req, copy); });
        }
        return res;
      }).catch(function () {
        return caches.match(req).then(function (hit) {
          return hit || caches.match('/pages/chat.html');
        });
      })
    );
    return;
  }

  /* 静态资源：缓存优先 + 后台更新（stale-while-revalidate） */
  e.respondWith(
    caches.match(req).then(function (hit) {
      var fetching = fetch(req).then(function (res) {
        if (res && res.status === 200) {
          var copy = res.clone();
          caches.open(CACHE).then(function (c) { c.put(req, copy); });
        }
        return res;
      }).catch(function () { return hit; });
      return hit || fetching;
    })
  );
});

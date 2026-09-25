/* 小拟 PWA Service Worker —— 应用壳缓存，替代原 APK 的秒开体验。
   策略：导航请求网络优先（更新即时生效，离线回退缓存）；
   静态资源缓存优先 + 后台刷新；API 与上传目录一律不缓存。 */
/* 第八轮前端优化（拖拽文件发送/灯箱左右切换+下载/弹窗焦点陷阱/浅色对比度/defer/theme-color）
   随壳缓存换版：不 bump 的话 SWR 策略要等用户第二次刷新才拿到新文件 */
var CACHE = 'xiaoni-shell-v17';
var SHELL = [
  '/pages/chat.html',
  '/pages/story.html',
  '/pages/css/chat.css',
  '/pages/js/app.js',
  '/pages/pet.js',
  '/pages/favicon.svg',
  '/pages/bg_ambient_v2.webp',
  '/pages/avatar_dashuai_256.webp',
  '/pages/avatar_dashuai_64.webp',
  '/pages/welcome-bg.mp4',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/apple-touch-icon.png'
];

self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(CACHE)
      /* 逐项容错：addAll 是全有或全无，发版瞬间任一资源 404/隧道抖动都会让
         整个 SW 安装失败、永不激活；失败项交给运行时 SWR 首次访问时自动补齐 */
      .then(function (c) {
        return Promise.allSettled(SHELL.map(function (u) { return c.add(u); }));
      })
      .then(function () { return self.skipWaiting(); })
  );
});

/* 桌宠素材按 ?v=N 做版本键：bump ASSET_VERSION 后，同一路径的旧条目在同一个
   CACHE 里再无人引用，激活时按路径分组、每组只留 v 最大的一条 */
function pruneOldPetAssets(cache) {
  return cache.keys().then(function (keys) {
    var groups = {};
    keys.forEach(function (rq) {
      var u = new URL(rq.url);
      if (/\/pages\/pet\/[^/]+\.webp$/i.test(u.pathname)) {
        (groups[u.pathname] = groups[u.pathname] || []).push(rq);
      }
    });
    return Promise.all(Object.keys(groups).map(function (p) {
      var sorted = groups[p].sort(function (a, b) {
        var va = parseInt(((new URL(a.url).search.match(/v=(\d+)/) || [])[1]) || '0', 10);
        var vb = parseInt(((new URL(b.url).search.match(/v=(\d+)/) || [])[1]) || '0', 10);
        return vb - va;
      });
      return Promise.all(sorted.slice(1).map(function (rq) { return cache.delete(rq); }));
    }));
  });
}

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(keys.filter(function (k) { return k !== CACHE; })
          .map(function (k) { return caches.delete(k); }));
      })
      .then(function () { return caches.open(CACHE); })
      .then(function (cache) { return pruneOldPetAssets(cache); })
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
        /* 只回写 2xx：Cache API 拒绝 5xx/4xx，裸 c.put 会产生未捕获的 reject。
           还必须排除"被重定向过"的响应：未登录时服务端对页面返回 302 -> /login，
           fetch 默认跟随重定向，最终拿到的是一个 status 200 的**登录页** —— 若照常
           回写，它就会冒充聊天页进缓存，用户之后离线启动 PWA 看到的是登录页。 */
        var samePath = false;
        try { samePath = !!res && !!res.url && new URL(res.url).pathname === url.pathname; } catch (e) {}
        if (res && res.status === 200 && !res.redirected && samePath) {
          var copy = res.clone();
          caches.open(CACHE).then(function (c) { return c.put(req, copy); }).catch(function () {});
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
          caches.open(CACHE).then(function (c) { return c.put(req, copy); }).catch(function () {});
        }
        return res;
      }).catch(function () { return hit; });
      return hit || fetching;
    })
  );
});

/* 宝可梦番茄钟 · Service Worker
   **只缓存应用外壳，不缓存宝可梦图片。**
   图片走图鉴页 CDN 自带的 HTTP 缓存 —— 把上千张 webp 塞进 SW 缓存
   会把手机存储吃掉几十 MB，而且图鉴更新后还得手动清缓存。
*/
const CACHE = 'pomodoro-shell-v2';
const SHELL = [
  './',
  './index.html',
  './app.js?v=1',   // ★ 版本号要跟 index.html 里的一致
  './style.css',
  './manifest.webmanifest',
  './icon.svg',
  './pokedex-lite.json',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  // 图片永远不进 SW 缓存
  if (/\.(webp|png|jpg|jpeg|gif|svg)$/i.test(url.pathname) && url.pathname.indexOf('/img/') >= 0) return;
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).catch(() => caches.match('./index.html')));
    return;
  }
  e.respondWith(
    caches.match(req).then(hit => hit || fetch(req).then(res => {
      if (res.ok && url.origin === location.origin) {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(req, copy));
      }
      return res;
    }).catch(() => hit))
  );
});

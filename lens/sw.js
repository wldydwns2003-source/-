// 공유 대상(Share Target)으로 받은 이미지를 캐시에 잠깐 보관했다가 페이지로 넘겨줍니다.
const SHARE_CACHE = 'shared-image';
const APP_CACHE = 'sns-lens-v3';
const APP_FILES = ['./', './index.html', './manifest.webmanifest', './icon-192.png', './icon-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(APP_CACHE).then((c) => c.addAll(APP_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k !== APP_CACHE && k !== SHARE_CACHE) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);

  if (e.request.method === 'POST' && url.pathname.endsWith('/share-target')) {
    e.respondWith((async () => {
      const form = await e.request.formData();
      const file = form.get('image');
      const cache = await caches.open(SHARE_CACHE);
      if (file && typeof file !== 'string') {
        await cache.put('./shared-image', new Response(file, { headers: { 'content-type': file.type || 'image/png' } }));
      }
      return Response.redirect('./?shared=1', 303);
    })());
    return;
  }

  if (e.request.method !== 'GET' || url.origin !== location.origin) return;

  // 네트워크 우선, 실패하면 캐시 (오프라인에서도 앱 화면은 열리도록)
  e.respondWith((async () => {
    try {
      const res = await fetch(e.request);
      if (res.ok && APP_FILES.some((f) => url.pathname.endsWith(f.replace('./', '/')))) {
        (await caches.open(APP_CACHE)).put(e.request, res.clone());
      }
      return res;
    } catch {
      return (await caches.match(e.request, { ignoreSearch: true })) || (await caches.match('./index.html'));
    }
  })());
});

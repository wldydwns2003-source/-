// 홈 화면 설치(PWA)용 최소 서비스워커. 다운로드는 항상 네트워크로.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});

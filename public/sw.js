// Service Worker for PWA
const CACHE_NAME = 'keiba-ai-v1';
const urlsToCache = [
  '/',
  '/dashboard',
  '/train',
  '/data-collection',
  '/predict-batch'
];

// インストール時
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => {
        console.log('Opened cache');
        return cache.addAll(urlsToCache);
      })
  );
});

// フェッチ時（ネットワーク優先、フォールバックでキャッシュ）
self.addEventListener('fetch', (event) => {
  // POSTやHEADリクエストはキャッシュしない
  if (event.request.method !== 'GET') {
    return;  // Service Workerをバイパス
  }

  // 開発環境の特殊なリクエストはキャッシュしない
  const url = new URL(event.request.url);
  
  // Next.js HMR、WebSocket、開発サーバーのリクエストは無視
  if (url.pathname.includes('/_next/webpack-hmr') ||
      url.pathname.includes('/_next/static/webpack') ||
      url.protocol === 'ws:' ||
      url.protocol === 'wss:') {
    return;  // Service Workerをバイパス
  }

  // APIリクエストはキャッシュしない
  if (url.pathname.includes('/api/')) {
    return;  // Service Workerをバイパス
  }

  // 外部リクエスト（localhost:8001等）はキャッシュしない
  if (url.hostname !== self.location.hostname) {
    return;  // Service Workerをバイパス
  }

  // 静的ファイルのみキャッシュ戦略を適用
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        // 成功したレスポンスのみキャッシュ
        if (!response || response.status !== 200 || response.type === 'error') {
          return response;
        }

        // レスポンスをクローンしてキャッシュに保存
        const responseToCache = response.clone();
        caches.open(CACHE_NAME)
          .then((cache) => {
            cache.put(event.request, responseToCache);
          });
        return response;
      })
      .catch(() => {
        // ネットワークエラー時はキャッシュから返す（オフライン対応）
        return caches.match(event.request);
      })
  );
});

// アクティベート時（古いキャッシュを削除）
self.addEventListener('activate', (event) => {
  const cacheWhitelist = [CACHE_NAME];
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheWhitelist.indexOf(cacheName) === -1) {
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
});

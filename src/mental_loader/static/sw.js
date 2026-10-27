// No transcript or API caching: private household data stays out of shared caches.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data?.json() || {}; } catch {}
  event.waitUntil(self.registration.showNotification(data.title || 'Mental Loader', {
    body: data.body || 'A household reminder is ready.',
    icon: '/static/icon-192.png',
    tag: data.tag || 'household-reminder',
    data: { url: data.url || '/' },
  }));
});
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || '/', self.location.origin);
  if (url.origin !== self.location.origin) return;
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(async (clients) => {
    for (const client of clients) {
      if ('navigate' in client) await client.navigate(url.href);
      return client.focus();
    }
    return self.clients.openWindow(url.href);
  }));
});

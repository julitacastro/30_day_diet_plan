// Jhuls Health Tracker — Service Worker
// Handles background water reminder notifications

const CACHE_NAME = 'jhuls-tracker-v1';

self.addEventListener('install', e => {
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  clients.claim();
});

// Handle notification click — open the app
self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clientList => {
      for (const client of clientList) {
        if (client.url.includes('health-tracker') && 'focus' in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow('/');
      }
    })
  );
});

// Handle push messages from server (future use)
self.addEventListener('push', e => {
  const data = e.data ? e.data.json() : {};
  e.waitUntil(
    self.registration.showNotification(data.title || '💧 Water time, Jhuls!', {
      body: data.body || "Time to drink a glass of water. Your calves and eyes will thank you.",
      icon: '/static/icon.png',
      badge: '/static/icon.png',
      vibrate: [200, 100, 200],
      tag: 'water-reminder',
      renotify: true,
      actions: [
        { action: 'drank', title: '💧 I drank it!' },
        { action: 'snooze', title: '⏰ 15 min' }
      ]
    })
  );
});

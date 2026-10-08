self.addEventListener('install', event=>self.skipWaiting());
self.addEventListener('activate', event=>event.waitUntil(self.clients.claim()));
// No offline cache: personal archive metadata must never be served stale.
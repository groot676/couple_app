/* Minimal service worker: satisfies Android Chrome's installability check.
   Pure network passthrough — no caching, the surface is always live. */
self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener("fetch", function () { /* default network fetch */ });

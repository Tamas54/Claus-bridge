// Bridge UI v2 — service worker: a kezdőképernyőre tehetőséghez. Szándékosan NINCS offline gyorsítótár: a Bridge élő
// adat (jóváhagyás, költés, beszélgetés), egy régi, gyorsítótárból mutatott állapot félrevezetne.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});

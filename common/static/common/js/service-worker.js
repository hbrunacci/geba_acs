// Service worker de la PWA.
//
// QUÉ HACÍA MAL LA VERSIÓN ANTERIOR
// ---------------------------------
// Interceptaba TODO GET del mismo origen y respondía cache-first
// (`return cachedResponse || networkResponse`). Eso traía tres problemas:
//
//   1. Rompía las descargas. Bajar el Excel del tablero es una navegación a
//      /api/xsys/tableros/deuda/excel/; el worker la contestaba desde su cache,
//      el navegador cancelaba la navegación y el usuario veía la página
//      recargarse sin que pasara nada. Sólo fallaba en las máquinas donde el
//      worker estaba registrado, que es lo que hacía difícil de encontrar.
//
//   2. Guardaba datos de socios en el disco del cliente. Las respuestas de
//      /api/ —fichas, cuenta corriente, deuda, documentos— entraban al Cache
//      Storage del navegador y quedaban ahí después de cerrar sesión, en
//      máquinas de mostrador que usa más de una persona.
//
//   3. Mostraba datos viejos. Al devolver el cache primero, un tablero podía
//      quedar mostrando la foto de anteayer sin ninguna señal.
//
// QUÉ HACE AHORA
// --------------
//   /static/...   cache-first. Son archivos versionados que no cambian solos y
//                 es lo único que conviene servir sin tocar la red.
//   navegaciones  red primero, y sólo si la red falla se cae al shell cacheado,
//                 para que la app abra algo con la VPN caída.
//   todo lo demás (incluido /api/) ni se toca: pasa derecho a la red.
//
// El nombre del cache subió a v2 a propósito: `activate` borra todo cache cuyo
// nombre no sea el actual, así que al actualizarse el worker se lleva puesto el
// cache viejo con los datos de socios que nunca debieron guardarse ahí.

const CACHE_NAME = "acs-pwa-v2";
const SHELL = "/";
const APP_SHELL = [
  SHELL,
  "/static/common/css/dashboard.css",
  "/static/common/js/dashboard.js",
  "/static/common/manifest.webmanifest",
  "/static/common/img/pwa-icon.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(APP_SHELL))
      .catch(() => undefined)
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((cacheNames) =>
        Promise.all(
          cacheNames
            .filter((cacheName) => cacheName !== CACHE_NAME)
            .map((cacheName) => caches.delete(cacheName))
        )
      )
      .then(() => self.clients.claim())
  );
});

function esEstatico(url) {
  return url.pathname.startsWith("/static/");
}

self.addEventListener("fetch", (event) => {
  const { request } = event;

  if (request.method !== "GET") {
    return;
  }

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) {
    return;
  }

  // Estáticos: del cache si están, y se guardan al pasar por la red.
  if (esEstatico(url)) {
    event.respondWith(
      caches.match(request).then((cacheado) => {
        if (cacheado) {
          return cacheado;
        }
        return fetch(request).then((respuesta) => {
          if (respuesta && respuesta.ok) {
            const copia = respuesta.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copia));
          }
          return respuesta;
        });
      })
    );
    return;
  }

  // Navegaciones: siempre la red. El cache es sólo el paracaídas para que la
  // app abra si el servidor no responde.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() => caches.match(SHELL))
    );
    return;
  }

  // El resto —/api/, descargas, cualquier cosa con datos— no se intercepta:
  // sin `respondWith`, el navegador la resuelve por su cuenta contra la red.
});

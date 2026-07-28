"use strict";

const CACHE = "staff-shell-v1";
const SHELL = ["/staff/", "/staff/styles.css", "/staff/app.js", "/staff/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))));
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET" || !new URL(event.request.url).pathname.startsWith("/staff/")) return;
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
});

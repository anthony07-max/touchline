/* Kickvana service worker: push notifications + offline support. */
const V='kv-v5',SHELL=['/offline','/static/brand/theme.css','/static/brand/a11y.js','/static/brand/gen.js','/static/brand/mark.svg','/static/brand/logo.svg','/static/brand/favicon.svg'];
const API_OK=/^\/api\/(career$|career\/(summary|squad|table|fixtures|tactics|academy|clubs)|progress|weekly$|social\/(duels|leaderboard)|centre\/[^/]+(\/timeline)?$|live\/match|account$|me$)/;
self.addEventListener('install',e=>e.waitUntil(caches.open(V).then(c=>c.addAll(SHELL)).then(()=>self.skipWaiting())));
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==V&&k!=='kv-data').map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
function stamp(r){const h=new Headers(r.headers);h.set('X-KV-Offline','1');return r.blob().then(b=>new Response(b,{status:r.status,headers:h}))}
self.addEventListener('fetch',e=>{const r=e.request;if(r.method!=='GET')return;const u=new URL(r.url);if(u.origin!==location.origin)return;
  if(r.mode==='navigate'){e.respondWith(fetch(r).then(res=>{if(res.ok){const c=res.clone();caches.open(V).then(x=>x.put(r,c))}return res}).catch(()=>caches.match(r).then(m=>m||caches.match('/offline'))));return}
  if(u.pathname.startsWith('/static/')){e.respondWith(caches.match(r).then(m=>{const f=fetch(r).then(res=>{if(res.ok){const c=res.clone();caches.open(V).then(x=>x.put(r,c))}return res}).catch(()=>m);return m||f}));return}
  if(API_OK.test(u.pathname)){
    e.respondWith(fetch(r).then(res=>{if(res.ok){const c=res.clone();caches.open('kv-data').then(x=>x.put(u.href,c))}return res}).catch(()=>caches.open('kv-data').then(x=>x.match(u.href)).then(m=>m?stamp(m):new Response(JSON.stringify({detail:'You are offline'}),{status:503,headers:{'content-type':'application/json'}}))))}});
self.addEventListener('push',e=>{let d={};try{d=e.data.json()}catch(_){d={title:'Kickvana',body:e.data&&e.data.text()}}
 e.waitUntil(self.registration.showNotification(d.title||'Kickvana',{body:d.body||'',icon:'/static/brand/mark.svg',badge:'/static/brand/favicon.svg',tag:d.tag,data:{url:d.url||'/'}}))});
self.addEventListener('notificationclick',e=>{e.notification.close();const u=(e.notification.data||{}).url||'/';
 e.waitUntil(clients.matchAll({type:'window'}).then(ws=>{for(const w of ws){if(w.url.includes(u)&&'focus'in w)return w.focus()}return clients.openWindow(u)}))});

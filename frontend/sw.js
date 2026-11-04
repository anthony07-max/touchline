self.addEventListener('install',e=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
self.addEventListener('push',e=>{let d={};try{d=e.data.json()}catch(_){d={title:'Kickvana',body:e.data&&e.data.text()}}
 e.waitUntil(self.registration.showNotification(d.title||'Kickvana',{body:d.body||'',icon:'/static/brand/mark.svg',badge:'/static/brand/favicon.svg',tag:d.tag,data:{url:d.url||'/'}}))});
self.addEventListener('notificationclick',e=>{e.notification.close();const u=(e.notification.data||{}).url||'/';
 e.waitUntil(clients.matchAll({type:'window'}).then(ws=>{for(const w of ws){if(w.url.includes(u)&&'focus'in w)return w.focus()}return clients.openWindow(u)}))});

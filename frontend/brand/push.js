// Kickvana web push helper: KVPush.enable({fixtures:[id], all_elite:true}) -> subscription status
window.KVPush={supported:()=>'serviceWorker'in navigator&&'PushManager'in window&&'Notification'in window,
 async reg(){return navigator.serviceWorker.register('/sw.js',{scope:'/'})},
 async current(){if(!this.supported())return null;const r=await this.reg();return r.pushManager.getSubscription()},
 async enable(opt={}){if(!this.supported())throw new Error('This browser does not support web push. On iPhone, add Kickvana to your Home Screen first.');
  const perm=await Notification.requestPermission();if(perm!=='granted')throw new Error('Notifications were blocked in the browser settings.');
  const r=await this.reg();const k=await (await fetch('/api/webpush/key')).json();
  const key=Uint8Array.from(atob(k.public_key.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-k.public_key.length%4)%4)),c=>c.charCodeAt(0));
  let s=await r.pushManager.getSubscription();if(!s)s=await r.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:key});
  const t=localStorage.tl_token;const res=await fetch('/api/webpush/subscribe',{method:'POST',headers:{'content-type':'application/json',...(t?{Authorization:'Bearer '+t}:{})},body:JSON.stringify({subscription:s.toJSON(),fixtures:opt.fixtures||[],all_elite:opt.all_elite!==false})});
  return res.json()},
 async test(){const s=await this.current();if(!s)throw new Error('Turn alerts on first');return (await fetch('/api/webpush/test',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({endpoint:s.endpoint})})).json()},
 async off(){const s=await this.current();if(s){await fetch('/api/webpush/unsubscribe',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({endpoint:s.endpoint})});await s.unsubscribe()}}};
// Kickvana share helper
window.KVShare=async function(card){const d=card;if(navigator.share){try{await navigator.share({title:'Kickvana',text:d.text,url:d.url});return}catch(e){}}
 return d};

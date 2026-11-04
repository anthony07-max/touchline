/* Kickvana core: accessibility helpers, text scaling, reduced motion, offline service worker. */
(function(){
  const H=document.documentElement,LS=localStorage;
  const ts=+LS.kv_text||0; if(ts)H.classList.add('ts-'+ts);
  const rm=LS.kv_motion==='reduce'||matchMedia('(prefers-reduced-motion: reduce)').matches; if(rm)H.classList.add('rm');
  window.KV_REDUCED_MOTION=rm;
  function fix(root){
    root.querySelectorAll('[onclick]:not(button):not(a):not(input):not([tabindex])').forEach(e=>{e.tabIndex=0;if(!e.getAttribute('role'))e.setAttribute('role','button')});
    root.querySelectorAll('button:not([aria-label]),a:not([aria-label])').forEach(b=>{const t=(b.textContent||'').trim();if(!t||/^[^\w\s]{1,3}$/u.test(t)){const l=b.title||b.dataset.label||({'🔔':'Match alerts','✕':'Close','×':'Close','✎':'Rename','←':'Back','⟵':'Back','☰':'Menu','▶':'Play','⏸':'Pause'}[t])||'';if(l)b.setAttribute('aria-label',l)}});
    root.querySelectorAll('img:not([alt])').forEach(i=>i.alt='');
    root.querySelectorAll('img:not([loading])').forEach((i,n)=>{if(n>1)i.loading='lazy';i.decoding='async'});
    root.querySelectorAll('canvas:not([role])').forEach(c=>{c.setAttribute('role','img');if(!c.getAttribute('aria-label'))c.setAttribute('aria-label',c.dataset.label||'Match visualisation')});
    root.querySelectorAll('input:not([aria-label])').forEach(i=>{if(!i.id||!document.querySelector(`label[for="${i.id}"]`)){const p=i.placeholder||i.name;if(p)i.setAttribute('aria-label',p)}});
  }
  document.addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target.getAttribute&&e.target.getAttribute('role')==='button'&&e.target.tagName!=='BUTTON'){e.preventDefault();e.target.click()}});
  function bar(){if(document.querySelector('.kv-a11y')||H.dataset.noA11yBar!==undefined)return;const d=document.createElement('div');d.className='kv-a11y';d.setAttribute('role','group');d.setAttribute('aria-label','Display settings');
    d.innerHTML=`<button type="button" id="kvTs" aria-label="Text size: ${['normal','large','larger'][ts]}" title="Text size">A<span style="font-size:16px">A</span></button><button type="button" id="kvRm" aria-pressed="${rm}" aria-label="Reduce motion" title="Reduce motion">≋</button>`;
    document.body.appendChild(d);
    d.querySelector('#kvTs').onclick=()=>{const n=((+LS.kv_text||0)+1)%3;LS.kv_text=n;H.classList.remove('ts-1','ts-2');if(n)H.classList.add('ts-'+n);d.querySelector('#kvTs').setAttribute('aria-label','Text size: '+['normal','large','larger'][n])};
    d.querySelector('#kvRm').onclick=()=>{const on=!H.classList.contains('rm');H.classList.toggle('rm',on);LS.kv_motion=on?'reduce':'';window.KV_REDUCED_MOTION=on;d.querySelector('#kvRm').setAttribute('aria-pressed',on)}}
  function init(){if(!document.querySelector('main,[role=main]')){const m=document.querySelector('.in,.wrap,#app,body>div');if(m&&!m.id)m.id='main';if(m)m.setAttribute('role','main')}
    if(!document.querySelector('.skip')){const a=document.createElement('a');a.className='skip';a.href='#'+((document.querySelector('main,[role=main]')||{}).id||'main');a.textContent='Skip to content';document.body.prepend(a)}
    fix(document);bar();new MutationObserver(ms=>ms.forEach(m=>m.addedNodes.forEach(n=>n.nodeType===1&&fix(n.parentNode||n)))).observe(document.body,{childList:true,subtree:true})}
  function net(){let b=document.getElementById('kvNet');if(navigator.onLine){if(b)b.remove();return}if(!b){b=document.createElement('div');b.id='kvNet';b.setAttribute('role','status');b.style.cssText='position:fixed;left:50%;transform:translateX(-50%);top:8px;z-index:90;background:#FFB547;color:#1a1206;border-radius:999px;padding:7px 14px;font:700 12px Inter';b.textContent='Offline · showing your last saved data';document.body.appendChild(b)}}
  addEventListener('online',net);addEventListener('offline',net);addEventListener('load',net);
  document.readyState==='loading'?document.addEventListener('DOMContentLoaded',init):init();
  if('serviceWorker' in navigator&&location.protocol!=='file:')navigator.serviceWorker.register('/sw.js',{scope:'/'}).catch(()=>{});
})();

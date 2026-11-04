"""Store screenshots from the live site: python store/make_screens.py <base_url> <out_dir>"""
import asyncio, sys, os
from playwright.async_api import async_playwright
BASE, OUT = sys.argv[1].rstrip('/'), sys.argv[2]; os.makedirs(OUT, exist_ok=True)
SIZES = {'ios-6.9': (440, 956, 3), 'ios-6.5': (428, 926, 3), 'android': (360, 640, 3)}
CAP = {'1-career': 'Run your club', '2-tactics': 'Drag-and-drop tactics', '3-tutorial': 'Learn in 60 seconds', '4-lowerleague': 'Lower League Mode', '5-clubhouse': 'Duels, leagues & weekly challenges', '6-squad': 'Original players, kits & badges'}
async def caption(pg, text):
    await pg.evaluate("""t=>{const d=document.createElement('div');d.style.cssText='position:fixed;left:0;right:0;top:0;z-index:9999;padding:14px 18px 16px;background:linear-gradient(180deg,#05070Dee,#05070D00);font:800 22px Space Grotesk,Inter;color:#fff;text-align:center;pointer-events:none';d.textContent=t;document.body.appendChild(d);document.querySelectorAll('.kv-a11y').forEach(e=>e.style.display='none')}""", text)
async def run(b, name, w, h, s):
    ctx = await b.new_context(viewport={'width': w, 'height': h}, device_scale_factor=s); pg = await ctx.new_page()
    async def snap(key): await caption(pg, CAP[key]); await pg.wait_for_timeout(300); await pg.screenshot(path=f"{OUT}/{name}-{key}.png")
    await pg.goto(BASE + '/career'); await pg.wait_for_selector('.c[data-id=c4]', timeout=60000); await pg.click('#startBtn'); await pg.wait_for_function("document.body.textContent.includes('Board confidence')", timeout=60000); await pg.wait_for_timeout(600); await snap('1-career')
    await pg.click('button[data-p=tactics]'); await pg.wait_for_timeout(3000); await snap('2-tactics')
    await pg.click('button[data-p=squad]'); await pg.wait_for_selector('#pg-squad table', timeout=60000); await pg.wait_for_timeout(800); await snap('6-squad')
    await pg.goto(BASE + '/tutorial'); await pg.wait_for_selector('#f4-3-3', timeout=60000); await pg.click('#f4-3-3'); await pg.click('#tE'); await pg.wait_for_selector('#pH', timeout=60000); await snap('3-tutorial')
    await pg.goto(BASE + '/career?league=l2'); await pg.evaluate("localStorage.removeItem('kv_career')"); await pg.goto(BASE + '/career?league=l2'); await pg.wait_for_selector('.c[data-id=c0]', timeout=60000); await pg.evaluate("document.querySelector('.c[data-id=c0]').scrollIntoView({block:'center'})"); await snap('4-lowerleague')
    await pg.goto(BASE + '/social'); await pg.wait_for_timeout(2000); await pg.click('.tabs button:has-text("Duels")'); await pg.wait_for_timeout(1500); nb = await pg.query_selector('#pg-duels button.b')
    if nb: await nb.click(); await pg.wait_for_timeout(3000)
    await snap('5-clubhouse')
    await ctx.close()
async def feature(b):
    pg = await b.new_page(viewport={'width': 1024, 'height': 500})
    mark = open(os.path.join(os.path.dirname(__file__), '..', 'frontend', 'brand', 'mark.svg')).read()
    await pg.goto(BASE + '/privacy')
    await pg.evaluate("""m=>{document.body.innerHTML=`<div style="position:fixed;inset:0;background:radial-gradient(700px 400px at 85% 0%,#8B5CF655,transparent 60%),radial-gradient(600px 400px at 0% 100%,#2DE2FF33,transparent 60%),#05070D;display:flex;align-items:center;gap:40px;padding:0 70px;font-family:Space Grotesk,Inter"><div style="width:180px;height:180px">${m.replace('width="64" height="64"','width="180" height="180"')}</div><div><div style="font:800 76px Space Grotesk;color:#fff;letter-spacing:-1px">Kickvana</div><div style="font:600 30px Inter;color:#cfd8f5;margin-top:6px">Manage live. Rewrite results.</div><div style="font:600 18px Inter;color:#00F5A0;margin-top:18px;letter-spacing:.12em">CAREER · LIVE WHAT-IFS · DUELS · LOWER LEAGUE MODE</div></div></div>`}""", mark)
    await pg.wait_for_timeout(500); await pg.screenshot(path=f"{OUT}/feature-graphic.png"); await pg.close()
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        await feature(b)
        for n, (w, h, s) in SIZES.items(): await run(b, n, w, h, s)
        await b.close()
asyncio.run(main())

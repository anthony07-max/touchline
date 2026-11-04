"""Phase 4 screenshots: python scripts_phase4.py <base_url> <out_dir>"""
import asyncio, sys, time, os
from playwright.async_api import async_playwright
BASE, OUT = sys.argv[1].rstrip('/'), sys.argv[2]
os.makedirs(OUT, exist_ok=True)
async def shot(pg, name, full=True): await pg.screenshot(path=f"{OUT}/{name}.png", full_page=full); print('saved', name)
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={'width': 430, 'height': 932}, device_scale_factor=2)
        pg = await ctx.new_page(); errs = []; pg.on('pageerror', lambda e: errs.append(str(e)))
        # tutorial
        await pg.goto(BASE + '/tutorial'); await pg.wait_for_selector('#f4-3-3', timeout=60000); await shot(pg, '01_tutorial_start')
        await pg.click('#f4-4-2'); await pg.click('#tE'); await pg.wait_for_selector('#pH', timeout=60000); await shot(pg, '02_tutorial_read_game')
        await pg.click('#pH'); await pg.wait_for_selector('#sN', timeout=60000); await shot(pg, '03_tutorial_sub')
        await pg.click('#sY' if await pg.query_selector('#sY') else '#sN'); await pg.wait_for_selector('text=Start my career', timeout=60000); await shot(pg, '04_tutorial_done')
        # lower league career
        await pg.goto(BASE + '/career?league=l2'); await pg.evaluate("localStorage.removeItem('kv_career')"); await pg.goto(BASE + '/career?league=l2')
        await pg.wait_for_function("document.body.textContent.includes('Leyton Marshes')", timeout=60000); await shot(pg, '05_lower_league_pick', False)
        await pg.click('.c[data-id=c0]'); await pg.wait_for_timeout(500); await pg.click('#startBtn')
        await pg.wait_for_function("document.body.textContent.includes('Kickvana League Two')", timeout=60000); await pg.wait_for_timeout(800); await shot(pg, '06_l2_club_kits')
        await pg.goto(BASE + '/career#squad'); await pg.wait_for_selector('#pg-squad table', timeout=60000); await pg.wait_for_timeout(600); await shot(pg, '07_squad_avatars', False)
        await pg.click('#pg-squad tr.click'); await pg.wait_for_timeout(900); await shot(pg, '08_player_card_avatar', False)
        await pg.goto(BASE + '/career#league'); await pg.wait_for_timeout(2000); await shot(pg, '09_l2_table_24_clubs')
        # legal & account
        for path, n in (('/privacy', '10_privacy'), ('/terms', '11_terms'), ('/support', '12_support')):
            await pg.goto(BASE + path); await shot(pg, n)
        await pg.goto(BASE + '/account'); await pg.wait_for_selector('#em'); await shot(pg, '13_account_signin_age_gate', False)
        em = f"kvp4{int(time.time())}@example.com"
        await pg.fill('#em', em); await pg.fill('#pw', 'password123'); await pg.click('button[data-m=register]'); await pg.wait_for_timeout(800); await shot(pg, '14_age_gate_refused', False)
        await pg.check('#age'); await pg.click('button[data-m=register]'); await pg.wait_for_selector('#ex', timeout=30000); await shot(pg, '15_account_manage')
        await pg.click('#rs'); await pg.wait_for_timeout(1200); await shot(pg, '16_verify_resend')
        await pg.goto(BASE + '/account/reset'); await shot(pg, '17_password_reset', False)
        # weekly with balanced score
        await pg.goto(BASE + '/social'); await pg.wait_for_timeout(2500); await pg.click('.tabs button:has-text("Weekly")'); await pg.wait_for_selector('text=Submit plan', timeout=120000)
        await pg.select_option('#pg-weekly select >> nth=1', index=2)
        await pg.click('#pg-weekly button:has-text("Submit plan")'); await pg.wait_for_function("document.body.textContent.includes('Scored vs the baseline')", timeout=120000)
        await shot(pg, '18_weekly_balanced_score')
        await pg.click('.tabs button:has-text("Duels")'); await pg.wait_for_timeout(1500); nb = await pg.query_selector('#pg-duels button.b')
        if nb: await nb.click(); await pg.wait_for_timeout(3000)
        await shot(pg, '19_duel_avatars', False)
        # match page analysis (classic)
        await pg.goto(BASE + '/match/9100007'); await pg.wait_for_timeout(9000)
        await pg.evaluate("document.getElementById('anl').scrollIntoView()"); await shot(pg, '20_match_2d_analysis')
        await pg.evaluate("window.scrollTo(0,0)"); await pg.wait_for_timeout(500); await shot(pg, '21_match_2d_names_trail', False)
        # a11y: text size + keyboard focus
        await pg.goto(BASE + '/support'); await pg.click('#kvTs'); await pg.click('#kvTs'); await pg.keyboard.press('Tab'); await pg.keyboard.press('Tab'); await shot(pg, '22_a11y_text_size_focus', False)
        # offline
        await pg.goto(BASE + '/career'); await pg.wait_for_timeout(3000); await ctx.set_offline(True)
        await pg.goto(BASE + '/terms'); await pg.wait_for_timeout(800); await shot(pg, '23_offline_page', False)
        await pg.goto(BASE + '/career'); await pg.wait_for_timeout(3000); await shot(pg, '24_offline_cached_career', False)
        await ctx.set_offline(False)
        print('errors', errs[:5]); await b.close()
asyncio.run(main())

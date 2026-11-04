"""iPhone screenshots for v4. Live/half-time/upcoming states are rendered by mocking the centre API
from the recorded 2025 final (no live match was on while building)."""
import asyncio, json, time, urllib.request
from playwright.async_api import async_playwright
U = "http://localhost:8770"; OUT = "/workspace/touchline/screenshots/v4/"
get = lambda p: json.load(urllib.request.urlopen(U + p))
FT = get("/api/centre/1374897"); RO = get("/api/centre/1635695")

def at(snap, minute, phase, label, short):
    s = json.loads(json.dumps(snap)); L = s["live"]
    L["status"] = {"short": short, "long": label, "elapsed": minute, "extra": None}
    L["events"] = [e for e in L["events"] if (e["minute"] or 0) <= minute]
    h = sum(1 for e in L["events"] if e["type"] == "Goal" and e["team_id"] == L["home"]["id"])
    a = sum(1 for e in L["events"] if e["type"] == "Goal" and e["team_id"] != L["home"]["id"])
    L["score"] = {"home": h, "away": a}
    s["state"] = {"phase": phase, "label": label, "seconds_to_ko": -minute * 60, "playable_live": True, "can_branch": True, "can_replay": False, "ended": False}
    s["recap"] = None; s["fetched_at"] = time.time(); s["next_refresh"] = time.time() + 120; return s

def upcoming(snap):
    s = json.loads(json.dumps(snap)); s["live"]["status"] = {"short": "NS", "long": "Not Started", "elapsed": None, "extra": None}
    s["state"] = {"phase": "upcoming", "label": "Upcoming", "seconds_to_ko": 2 * 3600 + 14 * 60 + 9, "playable_live": False, "can_branch": False, "can_replay": False, "ended": False}
    s["recap"] = None; s["feed_warning"] = None; return s

async def shot(ctx, url, name, mock=None, upto=None, actions=None, wait=2500, full=False):
    pg = await ctx.new_page(); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    if mock:
        await pg.route("**/api/centre/*/stream", lambda r: r.abort())
        async def h(route):
            u = route.request.url
            if "/timeline" in u and upto is not None:
                await route.fulfill(body=json.dumps(get(f"/api/centre/1374897/timeline?upto={upto}")), content_type="application/json")
            elif "/timeline" in u: await route.continue_()
            else: await route.fulfill(body=json.dumps(mock), content_type="application/json")
        await pg.route("**/api/centre/**", h)
    await pg.goto(U + url); await pg.wait_for_timeout(wait)
    if actions: await actions(pg)
    await pg.screenshot(path=OUT + name, full_page=full); print(name, errs); await pg.close()

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True)
        async def ovl(pg):
            for o in ("network", "shots", "cam"): await pg.click(f'#ov .chip[data-o="{o}"]')
            await pg.evaluate("simT=3600;firedIdx=T.events.filter(e=>e.t<simT).length;document.getElementById('spd').value='12'"); await pg.wait_for_timeout(1800)
        await shot(ctx, "/match/1374897", "01_match_2d_overlays.png", actions=ovl, wait=3000)
        async def heat(pg):
            await pg.click('#ov .chip[data-o="heat"]'); await pg.evaluate("simT=4800"); await pg.wait_for_timeout(1200)
        await shot(ctx, "/match/1374897", "02_match_2d_heatmap.png", actions=heat, wait=3000)
        await shot(ctx, "/match/1635695", "03_centre_upcoming.png", mock=upcoming(RO), wait=2500)
        await shot(ctx, "/match/1374897", "04_centre_live.png", mock=at(FT, 67, "live", "Live", "2H"), upto=67, wait=3500)
        async def tick(pg): await pg.click('#tabs button[data-p="centre"]'); await pg.wait_for_timeout(600)
        await shot(ctx, "/match/1374897", "05_centre_live_ticker.png", mock=at(FT, 67, "live", "Live", "2H"), upto=67, actions=tick, full=True)
        await shot(ctx, "/match/1374897", "06_centre_halftime.png", mock=at(FT, 45, "halftime", "Half-time", "HT"), upto=45, wait=3000)
        async def rec(pg): await pg.click('#tabs button[data-p="recap"]'); await pg.wait_for_timeout(700)
        await shot(ctx, "/match/1374897", "07_fulltime_recap.png", actions=rec, full=True)
        await shot(ctx, "/match/1635695", "08_stale_finished_roma.png", wait=3500)
        await shot(ctx, "/live/play?fixture=1635695", "09_whatif_gate.png", wait=3000)
        async def fan(pg):
            ids = await pg.evaluate("""(()=>{const P=POOL.pool,pick=[],c={GK:1,DEF:2,MID:3,FWD:2},s={home:0,away:0};
              for(const g in c){P.filter(p=>p.group===g&&p.likely_starter).sort((a,b)=>a.price-b.price).forEach(p=>{if(c[g]>0&&s[p.side]<4){pick.push(p.id);c[g]--;s[p.side]++}})}
              PICK=pick;CAP=pick[pick.length-1];save();draw();return pick})()""")
            await pg.evaluate("score()"); await pg.wait_for_timeout(2500)
        await shot(ctx, "/fantasy?fixture=1374897", "10_fantasy.png", actions=fan, wait=12000)
        async def modes(pg): await pg.click("#enterBtn"); await pg.wait_for_timeout(1500)
        await shot(ctx, "/", "11_welcome_modes.png", actions=modes, wait=2500)
        async def setup(pg): await pg.goto(U + "/?fixture=1374897#setup"); await pg.wait_for_timeout(2500)
        await shot(ctx, "/", "12_setup_official_xi.png", actions=setup, wait=500, full=True)
        await b.close()
asyncio.run(main())

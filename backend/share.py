"""Shareable what-if cards: server-rendered 1200x630 PNG + /s/{id} landing page with Open Graph / Twitter meta.
Card text is always generated server-side from stored results (never free text from the client)."""
from __future__ import annotations
import html, io, json, os, secrets, time
from typing import Dict, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from . import db, auth, progress

router = APIRouter(tags=["share"])
SCHEMA = """
CREATE TABLE IF NOT EXISTS share_cards (
  id TEXT PRIMARY KEY, user_id INTEGER, kind TEXT NOT NULL, ref TEXT NOT NULL, title TEXT NOT NULL, line1 TEXT NOT NULL,
  line2 TEXT NOT NULL, score TEXT, created_at REAL NOT NULL);
"""
if SCHEMA not in db.SCHEMA: db.SCHEMA += SCHEMA
FONTS = os.path.join(os.path.dirname(__file__), "..", "frontend", "brand", "fonts")
BASE = os.getenv("PUBLIC_BASE_URL", "https://touchline-live.onrender.com")


def _make(kind, ref, title, line1, line2, score, uid):
    ex = db.q("SELECT id FROM share_cards WHERE kind=? AND ref=?", (kind, ref), one=True)
    if ex: return ex["id"]
    cid = secrets.token_urlsafe(7)
    db.q("INSERT INTO share_cards(id,user_id,kind,ref,title,line1,line2,score,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
         (cid, uid, kind, ref, title[:80], line1[:120], line2[:120], score, time.time()))
    progress.record(uid, "share")
    return cid


def _out(cid, request: Optional[Request] = None):
    base = BASE
    url = f"{base}/s/{cid}"; c = db.q("SELECT * FROM share_cards WHERE id=?", (cid,), one=True)
    from urllib.parse import quote
    text = f"{c['title']} {c['line1']} #Kickvana"
    return {"id": cid, "url": url, "image": f"{base}/api/share/{cid}.png", "text": text,
            "x_intent": f"https://twitter.com/intent/tweet?text={quote(text)}&url={quote(url)}",
            "whatsapp": f"https://wa.me/?text={quote(text + ' ' + url)}"}


@router.post("/api/share/shadow/{sid}")
def share_shadow(sid: str, user=Depends(auth.optional_user)):
    r = db.q("SELECT * FROM shadows WHERE id=?", (sid,), one=True)
    if not r or not r["final_json"]: raise HTTPException(409, "finish the timeline first")
    f = json.loads(r["final_json"])
    cid = _make("shadow", sid, f["headline"].split(" (")[0], f"{f['team']} v {f['opponent']}, managed by me on Kickvana",
                f"Reality: {f['real_result']} {f['real'][0]}–{f['real'][1]} · My timeline: {f['alt'][0]}–{f['alt'][1]}",
                f"{f['alt'][0]}–{f['alt'][1]}", user["id"] if user else None)
    return _out(cid)


@router.post("/api/share/weekly")
def share_weekly(user=Depends(auth.require_user)):
    from .whatif import week_id, current_challenge
    w = week_id(); e = db.q("SELECT * FROM weekly_entries WHERE week=? AND user_id=?", (w, user["id"]), one=True)
    if not e: raise HTTPException(409, "enter this week's challenge first")
    ch = current_challenge(w)
    cid = _make("weekly", f"{w}:{user['id']}:{e['score']}", f"I scored {e['score']:.0f}/100", ch["brief"][:118],
                f"Kickvana weekly challenge {w} · win chance {round(e['win'] * 100)}%", f"{e['score']:.0f}", user["id"])
    return _out(cid)


@router.post("/api/share/duel/{did}")
def share_duel(did: int, user=Depends(auth.optional_user)):
    d = db.q("SELECT * FROM duels WHERE id=?", (did,), one=True)
    if not d or not d["result_json"]: raise HTTPException(409, "duel not played yet")
    r = json.loads(d["result_json"])
    cid = _make("duel", str(did), f"{r['winner_name']} wins the duel" if r.get("winner_name") else "Honours even",
                f"{r['a_name']} {r['score'][0]}–{r['score'][1]} {r['b_name']}", "Head-to-head manager duel on Kickvana",
                f"{r['score'][0]}–{r['score'][1]}", user["id"] if user else None)
    return _out(cid)


@router.post("/api/share/career/{fixture_id}")
def share_career(fixture_id: int, x_career_key: Optional[str] = Header(None), user=Depends(auth.optional_user)):
    from .career import _load
    from touchline_engine import career as C
    st = _load(x_career_key); f = next((x for x in st["fixtures"] if x["id"] == fixture_id and x["played"]), None)
    if not f or st["club_id"] not in (f["home"], f["away"]): raise HTTPException(404, "no such played match")
    h, a = C.club_of(st, f["home"]), C.club_of(st, f["away"]); me = st["club_id"] == f["home"]
    i = 0 if me else 1; gf, ga = f["score"][i], f["score"][1 - i]
    word = "won" if gf > ga else "drew" if gf == ga else "lost"
    cid = _make("career", f"{x_career_key[:12]}:{st['season']}:{fixture_id}", f"We {word} {gf}–{ga}",
                f"{h['name']} {f['score'][0]}–{f['score'][1]} {a['name']}", f"Kickvana Manager Career · Season {st['season']}, week {f['week']}",
                f"{f['score'][0]}–{f['score'][1]}", user["id"] if user else None)
    return _out(cid)


def render_png(c: Dict) -> bytes:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    W, H = 1200, 630
    img = Image.new("RGB", (W, H), "#05070D")
    glow = Image.new("RGB", (W, H), "#05070D"); g = ImageDraw.Draw(glow)
    g.ellipse((-200, -260, 520, 420), fill="#0f3a2c"); g.ellipse((760, 260, 1420, 900), fill="#3a0f33")
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(90)), 1.0)
    d = ImageDraw.Draw(img)
    f = lambda name, size: ImageFont.truetype(os.path.join(FONTS, name), size)
    sg, it = "SpaceGrotesk.ttf", "Inter.ttf"
    for x in range(0, W, 60): d.line([(x, 0), (x, H)], fill="#0b1222")
    d.rounded_rectangle((40, 40, W - 40, H - 40), radius=36, outline="#1C2742", width=3)
    d.text((80, 78), "KICKVANA", font=f(sg, 34), fill="#00F5A0")
    d.text((W - 80, 84), "WHAT IF…" if c["kind"] == "shadow" else c["kind"].upper(), font=f(it, 24), fill="#FF3DCB", anchor="ra")
    title = c["title"]; size = 84
    while d.textlength(title, font=f(sg, size)) > W - 160 and size > 40: size -= 4
    d.text((80, 190), title, font=f(sg, size), fill="#FFFFFF")
    d.text((80, 320), c["line1"], font=f(it, 34), fill="#C9D3F0")
    d.text((80, 372), c["line2"], font=f(it, 28), fill="#8592B0")
    d.rounded_rectangle((80, 470, 80 + 12 + d.textlength("Play the what-if at kickvana", font=f(it, 26)) + 30, 530), radius=30, fill="#00F5A0")
    d.text((106, 484), "Play the what-if at kickvana", font=f(it, 26), fill="#05070D")
    d.text((W - 80, 548), "Simulated alternate timeline · real result unchanged", font=f(it, 18), fill="#56607d", anchor="ra")
    buf = io.BytesIO(); img.save(buf, "PNG", optimize=True); return buf.getvalue()


def render_svg(c: Dict) -> str:
    e = html.escape
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630"><rect width="1200" height="630" fill="#05070D"/>
<text x="80" y="110" fill="#00F5A0" font-family="sans-serif" font-size="34" font-weight="700">KICKVANA</text>
<text x="80" y="260" fill="#fff" font-family="sans-serif" font-size="80" font-weight="700">{e(c['title'])}</text>
<text x="80" y="350" fill="#C9D3F0" font-family="sans-serif" font-size="34">{e(c['line1'])}</text>
<text x="80" y="400" fill="#8592B0" font-family="sans-serif" font-size="28">{e(c['line2'])}</text></svg>"""


@router.get("/api/share/{cid}.png")
def card_png(cid: str):
    c = db.q("SELECT * FROM share_cards WHERE id=?", (cid,), one=True)
    if not c: raise HTTPException(404, "card not found")
    try: return Response(render_png(c), media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
    except ImportError: return Response(render_svg(c), media_type="image/svg+xml")


@router.get("/api/share/{cid}")
def card_json(cid: str):
    c = db.q("SELECT * FROM share_cards WHERE id=?", (cid,), one=True)
    if not c: raise HTTPException(404, "card not found")
    return {**{k: c[k] for k in ("kind", "title", "line1", "line2", "score")}, **_out(cid)}


@router.get("/s/{cid}", response_class=HTMLResponse, include_in_schema=False)
def card_page(cid: str):
    c = db.q("SELECT * FROM share_cards WHERE id=?", (cid,), one=True)
    if not c: raise HTTPException(404, "card not found")
    o = _out(cid); e = html.escape
    cta = "/career" if c["kind"] in ("career",) else "/social" if c["kind"] in ("weekly", "duel") else "/"
    return f"""<!doctype html><html lang="en-GB"><head><meta charset="utf-8"><title>{e(c['title'])} · Kickvana</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#05070D">
<meta name="description" content="{e(c['line1'])}">
<meta property="og:type" content="website"><meta property="og:site_name" content="Kickvana"><meta property="og:title" content="{e(c['title'])}">
<meta property="og:description" content="{e(c['line1'])} · {e(c['line2'])}"><meta property="og:url" content="{e(o['url'])}">
<meta property="og:image" content="{e(o['image'])}"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="{e(c['title'])}"><meta name="twitter:description" content="{e(c['line1'])}"><meta name="twitter:image" content="{e(o['image'])}">
<link rel="icon" href="/static/brand/favicon.svg"><link rel="stylesheet" href="/static/brand/theme.css">
<style>body{{display:flex;justify-content:center}}.w{{max-width:720px;padding:24px 16px}}img.card{{width:100%;border-radius:18px;border:1px solid #1C2742}}
.row{{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}}a.b,button.b{{border:0;border-radius:999px;padding:12px 18px;font:700 13px Inter;text-decoration:none;cursor:pointer;color:#05070D;background:#00F5A0}}
a.g,button.g{{background:#ffffff10;color:#EAF0FF;border:1px solid #ffffff22}}</style></head><body><div class="w">
<a href="/"><img src="/static/brand/logo.svg" height="34" alt="Kickvana"></a><h1 style="font:700 30px 'Space Grotesk'">{e(c['title'])}</h1><p style="color:#8592B0">{e(c['line1'])}<br>{e(c['line2'])}</p>
<img class="card" src="/api/share/{cid}.png" alt="{e(c['title'])}">
<div class="row"><button class="b" id="sh">Share</button><a class="b g" href="{e(o['x_intent'])}" target="_blank" rel="noopener">Post on X</a><a class="b g" href="{e(o['whatsapp'])}" target="_blank" rel="noopener">WhatsApp</a><button class="b g" id="cp">Copy link</button></div>
<a class="b" href="{cta}">Play your own what-if on Kickvana →</a>
<p style="color:#56607d;font-size:12px;margin-top:20px">Alternate timelines are simulations. Real results are unchanged.</p></div>
<script>const D={json.dumps({"url": o["url"], "text": o["text"], "title": c["title"]})};
document.getElementById('sh').onclick=async()=>{{if(navigator.share){{try{{await navigator.share({{title:D.title,text:D.text,url:D.url}})}}catch(e){{}}}}else{{navigator.clipboard.writeText(D.url);document.getElementById('sh').textContent='Link copied'}}}};
document.getElementById('cp').onclick=()=>{{navigator.clipboard.writeText(D.url);document.getElementById('cp').textContent='Copied'}};</script></body></html>"""

#!/usr/bin/env python3
"""Build dist/ for mcc-review: fetch all sources, assemble snapshot, inject into template."""
import json, re, sys, time, urllib.request, zipfile, io, os, datetime
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

UA = {"User-Agent": "mcc-refresh/1.0 (static dashboard build)"}
HERE = os.path.dirname(os.path.abspath(__file__))

def get(url, binary=False, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    if data[:2] == b"\x1f\x8b":
        import gzip
        data = gzip.decompress(data)
    return data if binary else data.decode("utf-8", "replace")

def tryf(fn, fallback):
    try:
        return fn()
    except Exception as e:
        print(f"WARN: {fn.__name__} failed: {e}", file=sys.stderr)
        return fallback

def pdate(s):
    s = (s or "").strip()
    try:
        return parsedate_to_datetime(s)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            d = datetime.datetime.strptime(s, fmt)
            return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            continue
    return datetime.datetime.now(datetime.timezone.utc)

def rss_items(url, source, limit=12):
    items = []
    root = ET.fromstring(get(url))
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        date = (it.findtext("pubDate") or it.findtext("date") or "").strip()
        if title and link:
            items.append({"source": source, "title": title, "url": link, "date": date, "_d": pdate(date)})
        if len(items) >= limit:
            break
    return items

def trunc(t, n=150):
    t = " ".join((t or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"

snap = {"generated": datetime.datetime.now(datetime.timezone.utc).isoformat()}

# FX latest + 30d history (Frankfurter / ECB)
def fx_latest():
    d = json.loads(get("https://api.frankfurter.dev/v1/latest?base=EUR&symbols=USD,CAD,GBP,CHF,BRL,CNY"))
    snap["fx_date"] = d["date"]
    return [{"pair": f"EUR/{s}", "price": d["rates"][s]} for s in ["USD", "CAD", "GBP", "CHF", "BRL", "CNY"] if s in d["rates"]]
snap["fx"] = tryf(fx_latest, [])
snap.setdefault("fx_date", "")

def fx_hist():
    start = (datetime.date.today() - datetime.timedelta(days=45)).isoformat()
    d = json.loads(get(f"https://api.frankfurter.dev/v1/{start}..?base=EUR&symbols=USD,CAD"))
    days = sorted(d["rates"].items())[-31:]
    return {"dates": [k for k, _ in days], "usd": [v["USD"] for _, v in days], "cad": [v["CAD"] for _, v in days]}
snap["fxhist"] = tryf(fx_hist, {"dates": [], "usd": [], "cad": []})
time.sleep(1)

# Crypto (CoinGecko)
def crypto():
    d = json.loads(get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true"))
    return [{"name": n.upper(), "usd": round(d[i]["usd"], 2), "chg": round(d[i]["usd_24h_change"], 2)}
            for i, n in [("bitcoin", "bitcoin"), ("ethereum", "ethereum"), ("solana", "solana")]]
snap["crypto"] = tryf(crypto, [])
time.sleep(2)

def coin7(cid):
    d = json.loads(get(f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart?vs_currency=usd&days=7&interval=daily"))
    by_date = {}
    for ts, price in d["prices"]:
        day = datetime.datetime.fromtimestamp(ts / 1000, datetime.timezone.utc).date().isoformat()
        by_date[day] = price  # keep last point per date
    days = sorted(by_date.items())[-7:]
    return [round(p, 2) for _, p in days], [k for k, _ in days]
btc, btcdates = tryf(lambda: coin7("bitcoin"), ([], []))
snap["btc7"], snap["btc7dates"] = btc, btcdates
time.sleep(2)
eth, _ = tryf(lambda: coin7("ethereum"), ([], []))
snap["eth7"] = eth
time.sleep(2)

# News wires
feeds_news = [("https://www.aljazeera.com/xml/rss/all.xml", "Al Jazeera"),
              ("https://feeds.bbci.co.uk/news/world/rss.xml", "BBC World"),
              ("https://rss.dw.com/xml/rss-en-world", "DW")]
allnews = []
for url, src in feeds_news:
    allnews += tryf(lambda u=url, s=src: rss_items(u, s, 8), [])
    time.sleep(1)
allnews.sort(key=lambda x: -x["_d"].timestamp())
snap["news"] = [{k: v for k, v in n.items() if k != "_d"} for n in allnews[:12]]

feeds_ind = [("https://kyivindependent.com/news-archive/rss/", "Kyiv Ind."),
             ("https://meduza.io/rss/en/all", "Meduza"),
             ("https://theins.press/en/feed", "The Insider"),
             ("https://bnonews.com/index.php/feed/", "BNO News"),
             ("https://www.defenseone.com/rss/all/", "Defense One"),
             ("https://www.middleeasteye.net/rss", "MEE")]
allind = []
for url, src in feeds_ind:
    allind += tryf(lambda u=url, s=src: rss_items(u, s, 6), [])
    time.sleep(1)
allind.sort(key=lambda x: -x["_d"].timestamp())
snap["independent"] = [{k: v for k, v in n.items() if k != "_d"} for n in allind[:12]]

# SEC EDGAR Form 4 (Atom)
def edgar():
    url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&owner=include&count=10&output=atom"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (mcc-refresh research; contact@example.com)"})
    root = ET.fromstring(urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace"))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in root.findall("a:entry", ns)[:8]:
        out.append({"title": (e.findtext("a:title", "", ns) or "").strip(),
                    "url": e.find("a:link", ns).get("href", ""),
                    "date": (e.findtext("a:updated", "", ns) or "").strip()})
    return out
snap["edgar"] = tryf(edgar, [])
time.sleep(1)

# Key voices: Bluesky keyless + Barebone RSS + Hedgeye podcast RSS
BSKY = [("wartranslated.bsky.social", "WarTranslated"), ("chriso-wiki.bsky.social", "ChrisO_wiki"),
        ("bellingcat.com", "Bellingcat"), ("unusualwhales.bsky.social", "Unusual Whales")]
voices = []
for handle, name in BSKY:
    def bsky(h=handle, n=name):
        d = json.loads(get(f"https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor={h}&limit=6&filter=posts_no_replies"))
        out = []
        for it in d.get("feed", [])[:2]:
            p = it["post"]
            rkey = p["uri"].rsplit("/", 1)[-1]
            out.append({"source": n, "title": trunc(p["record"].get("text", "")),
                        "url": f"https://bsky.app/profile/{p['author']['handle']}/post/{rkey}",
                        "date": p["record"]["createdAt"], "kind": "post"})
        return out
    voices += tryf(bsky, [])
    time.sleep(1)
def barebone():
    out = []
    for n in rss_items("https://barebone.ai/feed.xml", "Barebone", 2):
        out.append({"source": "Barebone", "title": trunc(n["title"], 110), "url": n["url"], "date": n["date"], "kind": "article"})
    return out
voices += tryf(barebone, [])
time.sleep(1)
def hedgeye():
    out = []
    root = ET.fromstring(get("https://rss.buzzsprout.com/88009.rss"))
    for it in list(root.iter("item"))[:2]:
        enc = it.find("enclosure")
        page = enc.get("url", "")[:-4] if enc is not None and enc.get("url", "").endswith(".mp3") else ""
        title = (it.findtext("title") or "").strip()
        date = (it.findtext("pubDate") or "").strip()
        if title and page:
            out.append({"source": "Hedgeye", "title": "♪ " + trunc(title, 105), "url": page, "date": date, "kind": "podcast"})
    return out
voices += tryf(hedgeye, [])
snap["voices"] = voices

# UCDP static conflict markers (monthly manual refresh)
snap["conflicts"] = json.load(open(os.path.join(HERE, "static", "conflicts.json")))

# GDELT last-24h material conflict points (15-min export batches, spaced)
def gdelt():
    lu = get("https://data.gdeltproject.org/gdeltv2/lastupdate.txt")
    m = re.search(r"(\d{14})\.export\.CSV\.zip", lu)
    start = datetime.datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    evs = []
    for i in range(96):
        ts = start - datetime.timedelta(minutes=15 * i)
        url = f"https://data.gdeltproject.org/gdeltv2/{ts:%Y%m%d%H%M}00.export.CSV.zip"
        try:
            data = get(url, binary=True, timeout=20)
            z = zipfile.ZipFile(io.BytesIO(data))
            text = z.read(z.namelist()[0]).decode("utf-8", "replace")
            for line in text.splitlines():
                f = line.split("\t")
                if len(f) < 61 or f[29] != "4" or f[25] != "1" or not f[56] or not f[57]:
                    continue
                g = float(f[30])
                if g > -7:
                    continue
                evs.append({"id": f[0], "lat": float(f[56]), "lon": float(f[57]), "g": g, "n": int(f[31] or 0)})
        except Exception:
            pass  # tolerate missing batches
        time.sleep(0.2)
    byid = {}
    for e in evs:
        if e["id"] not in byid or e["n"] > byid[e["id"]]["n"]:
            byid[e["id"]] = e
    evs = sorted(byid.values(), key=lambda e: -e["n"])
    seen, pts = set(), []
    for e in evs:
        key = (round(e["lat"] * 2) / 2, round(e["lon"] * 2) / 2)
        if key in seen:
            continue
        seen.add(key)
        pts.append({"lat": round(e["lat"], 2), "lon": round(e["lon"], 2), "g": e["g"], "n": e["n"]})
        if len(pts) >= 150:
            break
    return pts
snap["gdelt"] = tryf(gdelt, [])

# Guards: don't publish a broken snapshot
if len(snap["news"]) < 3 or len(snap["fx"]) < 5:
    print("FATAL: too little news/fx data; refusing to build", file=sys.stderr)
    sys.exit(1)

dist = os.path.join(HERE, "dist")
os.makedirs(dist, exist_ok=True)
html = open(os.path.join(HERE, "template.html")).read()
html = html.replace("__SNAPSHOT__", json.dumps(snap, ensure_ascii=False))
open(os.path.join(dist, "index.html"), "w").write(html)
open(os.path.join(dist, "snapshot.json"), "w").write(json.dumps(snap, ensure_ascii=False))
for f in ("lwc.js", "vazirmatn.woff2", "jps-logo.png"):
    open(os.path.join(dist, f), "wb").write(open(os.path.join(HERE, "static", f), "rb").read())
print(f"OK: news={len(snap['news'])} ind={len(snap['independent'])} voices={len(snap['voices'])} gdelt={len(snap['gdelt'])} edgar={len(snap['edgar'])}")

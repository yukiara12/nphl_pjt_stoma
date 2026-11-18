"""
kensakusystem.jp 系の議事録検索から、ストーマ関連語を含む発言を全年分取得し、
04_classify_council_mentions.py と同じルールで基準額言及（rule_label=A）を判定する。

対象：宇都宮市・大津市（依頼元10自治体のうち kensakusystem 系）

手順：
  1. Search2.exe に POST（キーワード完全一致、全期間・全会議種別）
  2. 該当年ごとに結果一覧を取得し、各ヒットの context（文書ID/発言ID）を集める
  3. GetText3.exe で発言本文を取得（GET、固定URLとして記録）
  4. 04 の classify / make_excerpt で判定

使い方：
  python3 code/collection/06_search_kensakusystem.py               # 宇都宮市・大津市
  python3 code/collection/06_search_kensakusystem.py --tenant otsu

出力：data/raw/stoma_mentions_classified_kensaku.csv（04 と同じ列）
"""
import argparse
import csv
import importlib.util
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "raw" / "stoma_mentions_classified_kensaku.csv"

spec = importlib.util.spec_from_file_location("c04", ROOT / "code" / "collection" / "04_classify_council_mentions.py")
c04 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c04)

TENANTS = {
    "utsunomiya": {"prefecture": "栃木県", "municipality": "宇都宮市議会"},
    "otsu": {"prefecture": "滋賀県", "municipality": "大津市議会"},
}
KEYWORDS = ["ストーマ", "ストマ", "オストメイト", "蓄便袋", "蓄尿袋", "人工肛門", "人工膀胱"]
BASE = "https://www.kensakusystem.jp/{t}/cgi-bin3/"
UA = {"User-Agent": c04.UA}
DELAY = 1.5
ERA = {"令和": 2018, "平成": 1988, "昭和": 1925}


def fetch(url, fields=None):
    time.sleep(DELAY)
    data = None
    if fields is not None:
        data = urllib.parse.urlencode([(k, v.encode("cp932")) for k, v in fields]).encode()
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=UA), timeout=120) as r:
                return r.read().decode("cp932", "replace")
        except Exception:
            if i == 2:
                raise
            time.sleep(5)


def get_code(t):
    html = fetch(f"https://www.kensakusystem.jp/{t}/index.html")
    return re.search(r"Code=([0-9a-z]+)", html).group(1)


def date_range(t, code):
    """全期間の最初と最後の会議名（fromDate/tillDate の値）を得る"""
    html = fetch(BASE.format(t=t) + f"Search2.exe?Code={code}&sTarget=2")
    years = re.findall(r'NAME="fromYear".*?</SELECT>', html, re.S)[0]
    years = re.findall(r'VALUE="([^"]+)"', years)
    oldest, newest = years[-1], years[0]
    f = [("Code", code), ("dMode", "0"), ("fromYear", oldest), ("tillYear", newest)]
    html = fetch(BASE.format(t=t) + "Search2.exe", f)
    fd = re.findall(r'VALUE="([^"]+)"', re.findall(r'NAME="fromDate".*?</SELECT>', html, re.S)[0])[0]
    td = re.findall(r'VALUE="([^"]+)"', re.findall(r'NAME="tillDate".*?</SELECT>', html, re.S)[0])[-1]
    return oldest, fd, newest, td


def hidden_fields(html, form):
    s = html[html.find(f'name="{form}"'):]
    s = s[: s.find("</FORM>")]
    return re.findall(r'name="([^"]+)" value="([^"]*)"', s, re.I)


ROW_RE = re.compile(
    r"<TD nowrap>([^<]+)</TD>\s*<TD nowrap><A[^>]*go\('([^']+)'\)\">([^<]+)</A></TD>\s*"
    r"<TD nowrap>([^<]*)</TD>\s*<TD[^>]*>([^<]*)</TD>\s*<TD nowrap>([^<]*)</TD>", re.S)


def search(t, code, kw, rng):
    fy, fd, ty, td = rng
    f = [("Code", code), ("dMode", "0"), ("KeyWord", kw), ("searchMode", "1"), ("keyMode", "0"),
         ("fromYear", fy), ("fromDate", fd), ("tillYear", ty), ("tillDate", td), ("speaker", ""),
         ("alltarget", "on")] + [("eTarget", str(i)) for i in range(1, 12)] + [("AhitResult", "検索")]
    html = fetch(BASE.format(t=t) + "Search2.exe", f)
    m = re.search(r"class=pink>(\d+)件", html)
    n = int(m.group(1)) if m else 0
    years = re.findall(r"changeyear\('([^']+)'\)", html)
    rows = {}
    chyear = hidden_fields(html, "chyear")
    for y in years:
        f2 = [(k, v) for k, v in chyear if k not in ("treedepth", "FUNC")] + [("treedepth", y), ("FUNC", "")]
        h = fetch(BASE.format(t=t) + "Search2.exe", f2)
        for meet, ctx, day, page, cnt, spk in ROW_RE.findall(h):
            rows[ctx] = {"meeting": (meet.strip() + day.strip()), "speaker": spk.strip()}
    return n, rows


def parse_date(meeting):
    m = re.search(r"(令和|平成|昭和)\s*(元|\d+)年.*?(\d+)月\s*(\d+)日", meeting)
    if not m:
        return "", ""
    y = ERA[m.group(1)] + (1 if m.group(2) == "元" else int(m.group(2)))
    mo, d = int(m.group(3)), int(m.group(4))
    return f"{y:04d}-{mo:02d}-{d:02d}", str(y if mo >= 4 else y - 1)


def get_text(t, code, ctx):
    doc, sid = ctx.split("/")[:2]
    url = BASE.format(t=t) + f"GetText3.exe?{code}/{doc}/{sid}/0/1//1//0"
    html = fetch(url)
    body = re.sub(r"<[^>]+>", " ", html)
    body = re.sub(r"[ \t　]+", " ", body)
    return url, re.sub(r"\s*\n\s*", "\n", body).strip()


def run(t):
    info = TENANTS[t]
    code = get_code(t)
    rng = date_range(t, code)
    print(t, "range:", rng[1], "-", rng[3])
    ctxs = {}
    for kw in KEYWORDS:
        n, rows = search(t, code, kw, rng)
        print(f"  {kw}: {n}件 / 取得 {len(rows)}")
        for c, r in rows.items():
            ctxs.setdefault(c, r)
    out = []
    for ctx, r in ctxs.items():
        url, text = get_text(t, code, ctx)
        kws = c04.find_terms(text, c04.STOMA_TERMS)
        if not kws:
            continue
        d, fy = parse_date(r["meeting"])
        out.append({"tenant": t, "prefecture": info["prefecture"], "municipality": info["municipality"],
                    "meeting": r["meeting"], "date": d, "fiscal_year": fy, "minute_url": url,
                    "minute_id": ctx, "speaker": r["speaker"], "minute_type": "",
                    "keywords": "|".join(kws), "rule_label": c04.classify(text), "meeting_has_A": "",
                    "excerpt": c04.make_excerpt(text)})
    meets_a = {(o["date"], o["meeting"].split("（")[0]) for o in out if o["rule_label"] == "A"}
    for o in out:
        o["meeting_has_A"] = "1" if (o["date"], o["meeting"].split("（")[0]) in meets_a else "0"
    print(f"  発言 {len(out)} / A {sum(o['rule_label'] == 'A' for o in out)}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default=",".join(TENANTS))
    a = ap.parse_args()
    targets = a.tenant.split(",")
    keep = []
    if OUT.exists():
        with open(OUT, encoding="utf-8") as f:
            keep = [r for r in csv.DictReader(f) if r["tenant"] not in targets]
    rows = keep
    for t in targets:
        rows += run(t)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=c04.FIELDS)
        w.writeheader()
        w.writerows(rows)
    print("wrote", OUT, len(rows))


if __name__ == "__main__":
    main()

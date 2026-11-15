"""
ssp.kaigiroku.net の議事録から、ストーマ関連キーワードを含む発言を抽出し、
「基準額（給付上限額・公費負担上限額）への言及」かどうかをルールで判定する。

02_search_council_stoma.py との違い:
  - 画面操作ではなく、MinuteSearch/MinuteView の画面が内部で呼んでいるJSON API
    (https://ssp.kaigiroku.net/dnp/search/...) を直接POSTで呼ぶ。Playwright不要。
  - 検索結果は「年（view_year）ごと」に分割されて返る仕様のため、
    全年分の facet を取得してから年ごとに検索する（= ヒット一覧の全件取得）。
    02 は画面の初期表示（最新年のみ）しか取得していなかった。
  - 会議録本文（発言単位）を取得し、キーワードを含む発言だけを出力する。

判定ルール（notes/council_mention_classifier_design.md で評価）:
  rule_label = "A" ⇔ 同一発言内で、ストーマ語（STOMA_TERMS）の前後 WINDOW(=150) 文字以内に
                     上限額語（CAP_TERMS）が出現する。ただし両語の間に
                     トイレ/備蓄/避難/便座/洗浄 が挟まるペアは数えない。
  会議（＝自治体×年度）単位のフラグは meeting_has_A（会議内に rule_label=A が1件以上）。

使い方:
  python3 scripts/04_classify_council_mentions.py --tenant kitakyushu,saitama
  python3 scripts/04_classify_council_mentions.py --test 5
  python3 scripts/04_classify_council_mentions.py --tenant kitakyushu --fresh   # 当該tenantを再取得

出力:
  data/raw/council_minutes/stoma_mentions_classified.csv   （発言単位、1行=キーワードを含む1発言）
  data/raw/council_minutes/stoma_mentions_progress.csv     （完了tenantの記録、resume用）
"""

import argparse
import csv
import html
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TENANTS_CSV = PROJECT_ROOT / "data" / "master" / "council_search_systems.csv"
OUT_DIR = PROJECT_ROOT / "data" / "raw" / "council_minutes"
OUTPUT_CSV = OUT_DIR / "stoma_mentions_classified.csv"
PROGRESS_CSV = OUT_DIR / "stoma_mentions_progress.csv"

API = "https://ssp.kaigiroku.net/dnp/search/"
TENANT_JS = "https://ssp.kaigiroku.net/tenant/{tenant}/js/tenant.js"
VIEW_URL = ("https://ssp.kaigiroku.net/tenant/{tenant}/MinuteView.html"
            "?council_id={council_id}&schedule_id={schedule_id}")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

SEARCH_KEYWORDS = ["ストーマ", "オストメイト", "蓄便袋", "蓄尿袋"]

# 発言抽出・判定に使う語
STOMA_TERMS = ["ストーマ", "ストマ", "オストメイト", "蓄便袋", "蓄尿袋", "人工肛門", "人工膀胱", "人工ぼうこう"]
CAP_TERMS = [
    "基準額", "上限額", "給付額", "支給額", "公費負担", "自己負担", "給付上限", "基準価格",
    "単価", "増額", "引き上げ", "引上げ", "見直し", "実勢価格", "価格の上昇", "価格高騰", "価格の高騰",
    "引き上", "限度額", "助成額", "補助額", "値上", "物価高", "価格が上昇", "価格上昇", "負担額",
    "月額",
]
WINDOW = 150  # 推奨ルール：ストーマ語の前後150文字以内に上限額語
# ストーマ語と上限額語の間にこれらの語が挟まるペアは数えない（トイレ改修費・備蓄の見直し等の誤検出対策）
EXCLUDE_BETWEEN = re.compile("トイレ|備蓄|避難|便座|洗浄")
FACILITY_AFTER = re.compile(r"[・\s]?(対応|(用|専用)?(トイレ|便座|便房|設備|マーク|簡易|仮設|ベッド|多目的|多機能))")

DELAY = 1.5  # リクエスト間隔（秒）


# ---------------------------------------------------------------- HTTP / API
_last_request = [0.0]


def _wait():
    dt = time.time() - _last_request[0]
    if dt < DELAY:
        time.sleep(DELAY - dt)
    _last_request[0] = time.time()


def http(url, data=None, retries=3):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    for attempt in range(retries):
        _wait()
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8")
        except Exception as e:  # noqa: BLE001
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))
            print(f"    retry ({e})", file=sys.stderr)


def api(endpoint, **params):
    return json.loads(http(API + endpoint, params))


def get_tenant_id(tenant):
    js = http(TENANT_JS.format(tenant=tenant))
    m = re.search(r"tenant_id\s*=\s*(\d+)", js)
    if not m:
        raise RuntimeError(f"tenant_id not found for {tenant}")
    return m.group(1)


def search_hits(tenant_id, keyword):
    """keyword の全ヒット（全年）を返す。[(schedule_dict, [minute_ids])]"""
    base = dict(tenant_id=tenant_id, power_user="false", identical="false", keywords=keyword)
    d = api("minute_searches/search", function="all", **base)
    years = [y["view_year"] for y in d.get("faset", {}).get("view_years", [])]
    out = []
    for y in years:
        dy = api("minute_searches/search", function="year", fq_view_year=y, **base)
        for s in dy.get("document", {}).get("schedules", []):
            out.append((s, [m["minute_id"] for m in s.get("minutes", [])]))
    return d.get("result", {}).get("numFound", 0), out


def get_minute(tenant_id, council_id, schedule_id):
    d = api("minutes/get_minute", tenant_id=tenant_id, power_user="false",
            council_id=council_id, schedule_id=schedule_id)
    return d.get("tenant_minutes", [])


# ---------------------------------------------------------------- text utils
TAG_RE = re.compile(r"<[^>]+>")


def clean_body(body):
    return html.unescape(TAG_RE.sub("", body or "")).strip()


def find_terms(text, terms):
    return sorted({t for t in terms if t in text})


ERA_BASE = {"令和": 2018, "平成": 1988, "昭和": 1925}


def parse_meeting_date(meeting_name, schedule_name, view_year=None):
    """会議名（例『令和　７年　　９月　定例会』）と日程名（例『09月24日－02号』）から
    (YYYY-MM-DD, fiscal_year) を返す。年度は4月〜翌3月。"""
    name = unicodedata.normalize("NFKC", meeting_name or "")
    sched = unicodedata.normalize("NFKC", schedule_name or "")
    year = None
    m = re.search(r"(令和|平成|昭和)\s*(元|\d+)\s*年", name)
    if m:
        n = 1 if m.group(2) == "元" else int(m.group(2))
        year = ERA_BASE[m.group(1)] + n
    elif view_year:
        year = int(view_year)
    else:
        m2 = re.search(r"(19|20)\d{2}", name)
        year = int(m2.group(0)) if m2 else None
    md = re.search(r"(\d{1,2})月\s*(\d{1,2})日", sched)
    mm = re.search(r"年\s*(\d{1,2})\s*月", name)
    if year and not md and mm:
        # 日程名に日付がない場合（例：『議会報』）は会議名の月で近似（日は不明）
        month = int(mm.group(1))
        return f"{year:04d}-{month:02d}", (year if month >= 4 else year - 1)
    if not year or not md:
        return "", ""
    month, day = int(md.group(1)), int(md.group(2))
    # 会議名の月（例：12月定例会）が秋冬で開催日が1〜3月なら翌年
    if mm and int(mm.group(1)) >= 10 and month <= 3:
        year += 1
    fy = year if month >= 4 else year - 1
    return f"{year:04d}-{month:02d}-{day:02d}", fy


# ---------------------------------------------------------------- rules
def _positions(text, terms):
    pos = []
    for t in terms:
        for m in re.finditer(re.escape(t), text):
            pos.append((m.start(), m.end(), t))
    return sorted(pos)


def cap_near_stoma(text, window=WINDOW, cap_terms=CAP_TERMS, stoma_terms=STOMA_TERMS,
                   exclude=EXCLUDE_BETWEEN):
    """ストーマ語の前後 window 文字以内に上限額語があれば (dist, stoma_pos, cap_term) を返す。
    両語の間（両語を含む区間）に exclude の語があるペアは数えない。"""
    # 「オストメイト対応トイレ」「オストメイトトイレ」等の設備の修飾語としての用法は数えない
    sp = [p for p in _positions(text, stoma_terms) if not FACILITY_AFTER.match(text, p[1])]
    cp = _positions(text, cap_terms)
    best = None
    for s0, s1, _ in sp:
        for c0, c1, ct in cp:
            dist = max(0, c0 - s1, s0 - c1)
            if dist > window or (best is not None and dist >= best[0]):
                continue
            if exclude and exclude.search(text[min(s0, c0):max(s1, c1)]):
                continue
            best = (dist, s0, ct)
    return best


def classify(text):
    """推奨ルール。'A' or ''"""
    return "A" if cap_near_stoma(text) else ""


def excerpt(text, center, width=300):
    half = width // 2
    s = max(0, center - half)
    e = min(len(text), s + width)
    s = max(0, e - width)
    return re.sub(r"\s+", " ", text[s:e]).strip()


def make_excerpt(text):
    hit = cap_near_stoma(text)
    if hit:
        return excerpt(text, hit[1])
    sp = _positions(text, STOMA_TERMS)
    return excerpt(text, sp[0][0] if sp else 0)


def extract_speeches(minutes):
    """キーワードを含む発言を返す（名簿・目次など本文以外も含めて判定は文字列のみ）"""
    out = []
    for m in minutes:
        text = clean_body(m.get("body"))
        kws = find_terms(text, STOMA_TERMS)
        if not kws:
            continue
        out.append({"minute_id": m.get("minute_id"), "speaker": (m.get("title") or "").strip(),
                    "minute_type": m.get("minute_type") or "", "text": text, "keywords": kws})
    return out


# ---------------------------------------------------------------- driver
FIELDS = ["tenant", "prefecture", "municipality", "meeting", "date", "fiscal_year",
          "minute_url", "minute_id", "speaker", "minute_type", "keywords",
          "rule_label", "meeting_has_A", "excerpt"]
PROGRESS_FIELDS = ["tenant", "n_meetings", "n_speeches", "n_A", "hits_by_keyword", "status", "run_date"]


def load_tenants(filter_tenants=None):
    seen, out = set(), []
    with open(TENANTS_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t = r["kaigiroku_tenant"]
            if r["system_type"] != "kaigiroku_ssp" or not t or t in seen:
                continue
            if filter_tenants and t not in filter_tenants:
                continue
            seen.add(t)
            out.append({"tenant": t, "prefecture": r["prefecture"],
                        "municipality": re.sub(r"議会$", "", r["municipality"])})
    return out


def load_done():
    if not PROGRESS_CSV.exists():
        return set()
    with open(PROGRESS_CSV, encoding="utf-8") as f:
        return {r["tenant"] for r in csv.DictReader(f) if r["status"] == "ok"}


def append_csv(path, fields, rows):
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


def drop_tenant_rows(tenants):
    """--fresh 用：出力・進捗CSVから該当tenantの行を除く"""
    for path, fields in [(OUTPUT_CSV, FIELDS), (PROGRESS_CSV, PROGRESS_FIELDS)]:
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r["tenant"] not in tenants]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)


def process_tenant(info, keywords):
    tenant = info["tenant"]
    tid = get_tenant_id(tenant)
    meetings = {}  # (council_id, schedule_id) -> schedule dict
    hits_by_kw = {}
    for kw in keywords:
        n, sched = search_hits(tid, kw)
        hits_by_kw[kw] = n
        for s, _ in sched:
            meetings.setdefault((s["council_id"], str(s["schedule_id"])), s)
    rows = []
    for (cid, sid), s in sorted(meetings.items(), key=lambda kv: (kv[1].get("view_year", ""), kv[0])):
        minutes = get_minute(tid, cid, sid)
        speeches = extract_speeches(minutes)
        d, fy = parse_meeting_date(s.get("name"), s.get("schedule_name"), s.get("view_year"))
        labels = [classify(sp["text"]) for sp in speeches]
        has_a = "1" if "A" in labels else "0"
        for sp, lab in zip(speeches, labels):
            rows.append({
                "tenant": tenant, "prefecture": info["prefecture"], "municipality": info["municipality"],
                "meeting": unicodedata.normalize("NFKC", s.get("name", "")).strip(),
                "date": d, "fiscal_year": fy,
                "minute_url": VIEW_URL.format(tenant=tenant, council_id=cid, schedule_id=sid),
                "minute_id": sp["minute_id"], "speaker": sp["speaker"], "minute_type": sp["minute_type"],
                "keywords": "|".join(sp["keywords"]), "rule_label": lab, "meeting_has_A": has_a,
                "excerpt": make_excerpt(sp["text"]),
            })
    prog = {"tenant": tenant, "n_meetings": len(meetings), "n_speeches": len(rows),
            "n_A": sum(r["rule_label"] == "A" for r in rows),
            "hits_by_keyword": json.dumps(hits_by_kw, ensure_ascii=False),
            "status": "ok", "run_date": str(date.today())}
    return rows, prog


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", help="カンマ区切りtenant名")
    ap.add_argument("--test", type=int, help="先頭N tenantのみ")
    ap.add_argument("--fresh", action="store_true", help="指定tenantの既存結果を消して再取得")
    ap.add_argument("--keywords", default=",".join(SEARCH_KEYWORDS), help="検索語（カンマ区切り）")
    ap.add_argument("--delay", type=float, default=1.5, help="リクエスト間隔秒（>=1.5推奨）")
    args = ap.parse_args()

    global DELAY
    DELAY = max(1.0, args.delay)
    filt = set(args.tenant.split(",")) if args.tenant else None
    tenants = load_tenants(filt)
    if args.test:
        tenants = tenants[:args.test]
    if args.fresh:
        drop_tenant_rows({t["tenant"] for t in tenants})
    done = load_done()
    todo = [t for t in tenants if t["tenant"] not in done]
    keywords = args.keywords.split(",")
    print(f"targets={len(tenants)} done={len(tenants) - len(todo)} todo={len(todo)} keywords={keywords}")

    for i, info in enumerate(todo, 1):
        t0 = time.time()
        print(f"[{i}/{len(todo)}] {info['municipality']} ({info['tenant']}) ...", end=" ", flush=True)
        try:
            rows, prog = process_tenant(info, keywords)
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {e}")
            append_csv(PROGRESS_CSV, PROGRESS_FIELDS, [{
                "tenant": info["tenant"], "status": f"error: {str(e)[:80]}", "run_date": str(date.today())}])
            continue
        append_csv(OUTPUT_CSV, FIELDS, rows)
        append_csv(PROGRESS_CSV, PROGRESS_FIELDS, [prog])
        print(f"meetings={prog['n_meetings']} speeches={prog['n_speeches']} A={prog['n_A']} "
              f"({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()

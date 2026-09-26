"""
ssp.kaigiroku.net の議事録検索システムで「ストーマ」「オストメイト」を自動検索するスクリプト。
Playwrightでブラウザを自動操作し、ヒット件数・議事録リンク・発言概要を取得してCSVに保存する。

使い方:
  python3 scripts/02_search_council_stoma.py              # 全tenant実行
  python3 scripts/02_search_council_stoma.py --test 5      # 最初の5件でテスト
  python3 scripts/02_search_council_stoma.py --tenant shinjuku,ageo  # 指定tenantのみ

出力:
  data/raw/council_minutes/stoma_search_results.csv   (テナント×キーワードの集計)
  data/raw/council_minutes/stoma_search_details.csv   (個別ヒットの詳細)

前提: pip install playwright && python -m playwright install chromium
"""

import argparse
import csv
import re
import time
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

PROJECT_ROOT = Path(__file__).parent.parent
TENANTS_CSV = PROJECT_ROOT / "data" / "master" / "council_search_systems.csv"
OUTPUT_CSV = PROJECT_ROOT / "data" / "raw" / "council_minutes" / "stoma_search_results.csv"
DETAIL_CSV = PROJECT_ROOT / "data" / "raw" / "council_minutes" / "stoma_search_details.csv"

KEYWORDS = ["ストーマ", "オストメイト", "蓄便袋", "蓄尿袋"]
SEARCH_URL_TEMPLATE = "https://ssp.kaigiroku.net/tenant/{tenant}/MinuteSearch.html"
BASE_URL = "https://ssp.kaigiroku.net/tenant/{tenant}/"


def load_tenants(tenants_csv, filter_tenants=None):
    tenants = []
    seen = set()
    with open(tenants_csv, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["system_type"] != "kaigiroku_ssp":
                continue
            tenant = row["kaigiroku_tenant"]
            if not tenant or tenant in seen:
                continue
            if filter_tenants and tenant not in filter_tenants:
                continue
            seen.add(tenant)
            tenants.append({
                "tenant": tenant,
                "prefecture": row["prefecture"],
                "municipality": row["municipality"],
            })
    return tenants


def extract_hit_details(page, tenant, keyword):
    """
    検索結果ページから個別ヒットの詳細（会議名、日付、議事録URL）を抽出する。
    URLはクリック→遷移先URL取得→再検索のループで取得する。
    a.link-minuteには議事録リンクとページリンクが交互に存在するため、
    tr.hit-schedule内のtd[2]のリンクだけを対象にする。
    """
    # First: extract row data via evaluate (fast, no navigation)
    row_data = page.evaluate('''() => {
        const results = [];
        document.querySelectorAll('tr.hit-schedule').forEach(row => {
            const tds = row.querySelectorAll('td');
            const link = tds[2]?.querySelector('a.link-minute');
            if (!link) return;
            results.push({
                meeting: tds[1]?.innerText?.trim() || '',
                date: link?.innerText?.trim() || '',
                hits: parseInt((tds[3]?.innerText?.match(/(\d+)/) || [0,0])[1]),
            });
        });
        return results;
    }''')

    if not row_data:
        return []

    # Second: get URL for each row by re-searching and clicking the nth link
    search_url = SEARCH_URL_TEMPLATE.format(tenant=tenant)
    details = []

    for i, rd in enumerate(row_data):
        minute_url = ""
        try:
            # Re-search to get a fresh page
            page.goto(search_url, timeout=20000, wait_until="domcontentloaded")
            time.sleep(1)
            inp = page.query_selector('input#se-keyword-value')
            if inp:
                inp.fill(keyword)
                btn = page.query_selector('button#btn-search')
                if btn:
                    btn.click()
                    time.sleep(3)

                    # Click the ith visible link (td[2] links only)
                    # Use evaluate to find the correct link index in all a.link-minute
                    idx = page.evaluate(f'''() => {{
                        const rows = document.querySelectorAll('tr.hit-schedule');
                        if ({i} >= rows.length) return -1;
                        const link = rows[{i}].querySelector('td:nth-child(3) a.link-minute');
                        if (!link) return -1;
                        const allLinks = Array.from(document.querySelectorAll('a.link-minute'));
                        return allLinks.indexOf(link);
                    }}''')

                    if idx >= 0:
                        locator = page.locator('a.link-minute').nth(idx)
                        locator.click(force=True, timeout=5000)
                        time.sleep(1)
                        if "MinuteView" in page.url:
                            minute_url = page.url
        except Exception:
            pass

        details.append({
            "tenant": tenant,
            "keyword": keyword,
            "meeting": rd["meeting"],
            "date": rd["date"],
            "hits_in_meeting": rd["hits"],
            "minute_url": minute_url,
        })

    return details

    return details


def search_keyword(page, keyword, tenant, collect_details=True):
    """検索実行し、ヒット件数と詳細を返す"""
    try:
        input_el = page.query_selector('input#se-keyword-value')
        if not input_el:
            for sel in ['input[name="keywords"]', 'input#he-input-keyword']:
                input_el = page.query_selector(sel)
                if input_el:
                    break
        if not input_el:
            return -1, "input_not_found", []

        input_el.fill("")
        input_el.fill(keyword)

        btn = page.query_selector('button#btn-search')
        if not btn:
            return -1, "button_not_found", []

        btn.click()
        time.sleep(4)

        body = page.inner_text("body")

        # Extract hit count
        match = re.search(r'検索結果\s*(\d+)\s*件', body)
        if not match:
            match = re.search(r'(\d+)\s*件ヒット', body)
        if not match:
            if any(msg in body for msg in ["該当するデータがありません", "見つかりません"]):
                return 0, "", []
            match = re.search(r'(\d+)\s*件', body)

        hit_count = int(match.group(1)) if match else 0

        # Collect details if there are hits
        details = []
        if hit_count > 0 and collect_details:
            details = extract_hit_details(page, tenant, keyword)

        return hit_count, "", details

    except PWTimeout:
        return -1, "timeout", []
    except Exception as e:
        return -1, str(e)[:100], []


def search_tenant(context, tenant_info, collect_details=True):
    tenant = tenant_info["tenant"]
    url = SEARCH_URL_TEMPLATE.format(tenant=tenant)
    results = []
    all_details = []

    page = context.new_page()
    try:
        page.goto(url, timeout=20000, wait_until="domcontentloaded")
        time.sleep(2)

        if "sorry" in page.url:
            for kw in KEYWORDS:
                results.append(make_result(tenant_info, kw, -1, "server_unavailable", url))
            return results, []

        for kw in KEYWORDS:
            if results:
                page.goto(url, timeout=20000, wait_until="domcontentloaded")
                time.sleep(1)

            hit_count, error, details = search_keyword(page, kw, tenant, collect_details)
            results.append(make_result(tenant_info, kw, hit_count, error, url))
            all_details.extend(details)

    except Exception as e:
        for kw in KEYWORDS:
            if not any(r["keyword"] == kw for r in results):
                results.append(make_result(tenant_info, kw, -1, str(e)[:100], url))
    finally:
        page.close()

    return results, all_details


def make_result(tenant_info, keyword, hit_count, error, url):
    return {
        "tenant": tenant_info["tenant"],
        "prefecture": tenant_info["prefecture"],
        "municipality": tenant_info["municipality"],
        "keyword": keyword,
        "hit_count": hit_count,
        "error": error,
        "search_url": url,
        "search_date": str(date.today()),
    }


RESULT_FIELDS = [
    "tenant", "prefecture", "municipality", "keyword",
    "hit_count", "error", "search_url", "search_date"
]
DETAIL_FIELDS = [
    "tenant", "keyword", "meeting", "date",
    "hits_in_meeting", "minute_url"
]


def load_completed_tenants(output_csv):
    """既存の結果CSVから完了済みテナント名のセットを返す"""
    done = set()
    if not output_csv.exists():
        return done
    with open(output_csv, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            done.add(row["tenant"])
    return done


def append_results(output_csv, results, write_header=False):
    """結果をCSVに追記する"""
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if write_header else "a"
    with open(output_csv, mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerows(results)


def append_details(detail_csv, details, write_header=False):
    """詳細をCSVに追記する"""
    detail_csv.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if write_header else "a"
    with open(detail_csv, mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DETAIL_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerows(details)


def load_hit_tenants(output_csv):
    """既存results CSVからヒットがあったテナント名のセットを返す"""
    tenants = set()
    if not output_csv.exists():
        return tenants
    with open(output_csv, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if int(row["hit_count"]) > 0:
                tenants.add(row["tenant"])
    return tenants


def load_details_completed(detail_csv):
    """既存details CSVから取得済みテナント名のセットを返す"""
    done = set()
    if not detail_csv.exists():
        return done
    with open(detail_csv, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            done.add(row["tenant"])
    return done


def run_details_only(args):
    """既存results CSVのヒットありテナントだけdetailsを取得する"""
    hit_tenant_names = load_hit_tenants(OUTPUT_CSV)
    if not hit_tenant_names:
        print("No hit tenants found in results CSV.")
        return

    # Load all tenants and filter to those with hits
    filter_tenants = set(args.tenant.split(",")) if args.tenant else None
    all_tenants = load_tenants(TENANTS_CSV, filter_tenants)
    target = [t for t in all_tenants if t["tenant"] in hit_tenant_names]

    if args.test:
        target = target[:args.test]

    # Resume: skip tenants already in details CSV
    if args.fresh:
        completed = set()
        append_details(DETAIL_CSV, [], write_header=True)
    else:
        completed = load_details_completed(DETAIL_CSV)
        if not DETAIL_CSV.exists():
            append_details(DETAIL_CSV, [], write_header=True)

    remaining = [t for t in target if t["tenant"] not in completed]

    print(f"Mode: details-only")
    print(f"Hit tenants: {len(hit_tenant_names)}")
    print(f"Already have details: {len(completed)}")
    print(f"Remaining: {len(remaining)}")
    print(f"Output: {DETAIL_CSV}")
    print()

    if not remaining:
        print("All hit tenants already have details. Use --fresh to re-run.")
        return

    total_details = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                    args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        )
        context.add_init_script(
            'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
        )

        for i, tenant_info in enumerate(remaining):
            label = f"[{i+1}/{len(remaining)}]"
            print(f"{label} {tenant_info['municipality']} ({tenant_info['tenant']})...", end=" ", flush=True)

            _, details = search_tenant(context, tenant_info, collect_details=True)

            if details:
                append_details(DETAIL_CSV, details)
                total_details += len(details)
                print(f"details:{len(details)}", end="")
            else:
                print("no details", end="")
            print()

            time.sleep(args.delay)

        browser.close()

    print(f"\nDetails saved: {DETAIL_CSV}")
    print(f"This run: {len(remaining)} tenants, {total_details} detail records")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", type=int, help="テスト実行：最初のN件のみ")
    parser.add_argument("--tenant", type=str, help="カンマ区切りで指定tenant名のみ")
    parser.add_argument("--delay", type=float, default=2.0, help="tenant間の待ち時間（秒）")
    parser.add_argument("--no-details", action="store_true", help="詳細取得をスキップ（高速モード）")
    parser.add_argument("--fresh", action="store_true", help="既存結果を無視してゼロから実行")
    parser.add_argument("--details-only", action="store_true",
                        help="既存results CSVのヒットありテナントのみdetails取得（results CSVは更新しない）")
    args = parser.parse_args()

    # --details-only mode: only fetch details for tenants with hits
    if args.details_only:
        run_details_only(args)
        return

    filter_tenants = set(args.tenant.split(",")) if args.tenant else None
    tenants = load_tenants(TENANTS_CSV, filter_tenants)

    if args.test:
        tenants = tenants[:args.test]

    collect_details = not args.no_details

    # Resume: skip already-completed tenants
    if args.fresh:
        completed = set()
        # Overwrite with fresh headers
        append_results(OUTPUT_CSV, [], write_header=True)
        if collect_details:
            append_details(DETAIL_CSV, [], write_header=True)
    else:
        completed = load_completed_tenants(OUTPUT_CSV)
        # If no file yet, write headers
        if not OUTPUT_CSV.exists():
            append_results(OUTPUT_CSV, [], write_header=True)
        if collect_details and not DETAIL_CSV.exists():
            append_details(DETAIL_CSV, [], write_header=True)

    remaining = [t for t in tenants if t["tenant"] not in completed]

    print(f"Target tenants: {len(tenants)}")
    print(f"Already completed: {len(completed)}")
    print(f"Remaining: {len(remaining)}")
    print(f"Keywords: {KEYWORDS}")
    print(f"Collect details: {collect_details}")
    print(f"Output: {OUTPUT_CSV}")
    if collect_details:
        print(f"Details: {DETAIL_CSV}")
    print()

    if not remaining:
        print("All tenants already completed. Use --fresh to re-run from scratch.")
        return

    total_results = 0
    total_hits = 0
    total_errors = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                    args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        )
        context.add_init_script(
            'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
        )

        for i, tenant_info in enumerate(remaining):
            label = f"[{i+1}/{len(remaining)}]"
            print(f"{label} {tenant_info['municipality']} ({tenant_info['tenant']})...", end=" ", flush=True)

            results, details = search_tenant(context, tenant_info, collect_details)

            # Append incrementally after each tenant
            append_results(OUTPUT_CSV, results)
            if details:
                append_details(DETAIL_CSV, details)

            for r in results:
                status = f"{r['hit_count']}件" if r["hit_count"] >= 0 else f"ERR:{r['error'][:20]}"
                print(f"{r['keyword']}={status}", end=" ")
                total_results += 1
                if r["hit_count"] > 0:
                    total_hits += 1
                elif r["hit_count"] < 0:
                    total_errors += 1
            if details:
                print(f"(details:{len(details)})", end="")
            print()

            time.sleep(args.delay)

        browser.close()

    # Summary
    print(f"\nResults saved: {OUTPUT_CSV}")
    print(f"This run: {total_results} searches ({len(remaining)} tenants x {len(KEYWORDS)} keywords)")
    print(f"  Hits (>0): {total_hits}")
    print(f"  No hits (0): {total_results - total_hits - total_errors}")
    print(f"  Errors: {total_errors}")
    print(f"Total completed tenants: {len(completed) + len(remaining)}/{len(tenants)}")


if __name__ == "__main__":
    main()

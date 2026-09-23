"""
全国自治体の議事録検索システムURL一覧を取得するスクリプト
データソース: https://app-mints.com/kaigiroku/
出力: data/master/council_search_systems.csv

HTML構造:
  <li class="lg-list-item">
    <h2 class="lg-list-item-title">○○議会</h2>
    <ul class="lg-link-list">
      <li class="lg-link-list-item">
        <div class="link-url"><a href="URL">...</a></div>
      </li>
    </ul>
  </li>
"""

import csv
import re
import time
import urllib.request
from pathlib import Path

BASE_URL = "https://app-mints.com/kaigiroku/lg/"
OUTPUT_PATH = Path(__file__).parent.parent / "data" / "master" / "council_search_systems.csv"

PREFECTURES = [
    "hokkaido1", "hokkaido2", "hokkaido3", "hokkaido4",
    "aomori", "iwate", "miyagi", "akita", "yamagata", "fukushima",
    "ibaraki", "tochigi", "gunma", "saitama", "chiba", "tokyo", "kanagawa",
    "niigata", "toyama", "ishikawa", "fukui", "yamanashi", "nagano",
    "gifu", "shizuoka", "aichi", "mie",
    "shiga", "kyoto", "osaka", "hyogo", "nara", "wakayama",
    "tottori", "shimane", "okayama", "hiroshima", "yamaguchi",
    "tokushima", "kagawa", "ehime", "kochi",
    "fukuoka", "saga", "nagasaki", "kumamoto", "oita", "miyazaki", "kagoshima", "okinawa",
]

PREF_NAME_MAP = {
    "hokkaido1": "北海道", "hokkaido2": "北海道", "hokkaido3": "北海道", "hokkaido4": "北海道",
    "aomori": "青森県", "iwate": "岩手県", "miyagi": "宮城県", "akita": "秋田県",
    "yamagata": "山形県", "fukushima": "福島県", "ibaraki": "茨城県", "tochigi": "栃木県",
    "gunma": "群馬県", "saitama": "埼玉県", "chiba": "千葉県", "tokyo": "東京都",
    "kanagawa": "神奈川県", "niigata": "新潟県", "toyama": "富山県", "ishikawa": "石川県",
    "fukui": "福井県", "yamanashi": "山梨県", "nagano": "長野県", "gifu": "岐阜県",
    "shizuoka": "静岡県", "aichi": "愛知県", "mie": "三重県", "shiga": "滋賀県",
    "kyoto": "京都府", "osaka": "大阪府", "hyogo": "兵庫県", "nara": "奈良県",
    "wakayama": "和歌山県", "tottori": "鳥取県", "shimane": "島根県", "okayama": "岡山県",
    "hiroshima": "広島県", "yamaguchi": "山口県", "tokushima": "徳島県", "kagawa": "香川県",
    "ehime": "愛媛県", "kochi": "高知県", "fukuoka": "福岡県", "saga": "佐賀県",
    "nagasaki": "長崎県", "kumamoto": "熊本県", "oita": "大分県", "miyazaki": "宮崎県",
    "kagoshima": "鹿児島県", "okinawa": "沖縄県",
}


def classify_system(url):
    """URLからシステム種別を判定"""
    if "ssp.kaigiroku.net" in url:
        return "kaigiroku_ssp"
    elif "dbsr" in url or "db-search" in url:
        return "dbsr"
    elif "gijiroku.com" in url:
        return "gijiroku_com"
    elif "kensakusystem" in url:
        return "kensakusystem"
    elif "discusscabinet" in url:
        return "discusscabinet"
    else:
        return "other"


def extract_kaigiroku_tenant(url):
    """ssp.kaigiroku.net のURLからtenant名を抽出"""
    m = re.search(r"ssp\.kaigiroku\.net/tenant/([^/]+)/", url)
    return m.group(1) if m else ""


def fetch_page(url):
    """URLからHTMLを取得"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_page(html, prefecture):
    """
    HTMLからリスト構造をパースして自治体名とURLを抽出。
    構造: <li class="lg-list-item"> 内に <h2>自治体名</h2> と <a href="URL"> がある。
    """
    results = []

    # Split by lg-list-item
    items = re.split(r'<li\s+class="lg-list-item[^"]*"', html)

    for item in items[1:]:  # skip first (before first item)
        # Extract municipality name from <h2>
        h2_match = re.search(r'<h2[^>]*>(.*?)</h2>', item, re.DOTALL)
        if not h2_match:
            continue

        # Clean h2 content (remove HTML tags like <i>)
        muni_raw = re.sub(r'<[^>]+>', '', h2_match.group(1)).strip()
        if not muni_raw:
            continue

        # Extract all <a href="..."> URLs within this item
        urls = re.findall(r'<a\s+href="([^"]+)"', item)

        # Filter to external URLs (not internal navigation)
        for url in urls:
            url = url.strip()
            if not url or url.startswith("#") or url.startswith("/kaigiroku"):
                continue

            system = classify_system(url)
            tenant = extract_kaigiroku_tenant(url) if system == "kaigiroku_ssp" else ""

            results.append({
                "prefecture": prefecture,
                "municipality": muni_raw,
                "url": url,
                "system_type": system,
                "kaigiroku_tenant": tenant,
            })

    return results


def main():
    all_results = []

    for pref_key in PREFECTURES:
        pref_name = PREF_NAME_MAP[pref_key]
        url = BASE_URL + pref_key
        print(f"Fetching: {pref_name} ({pref_key})...", end=" ", flush=True)

        try:
            html = fetch_page(url)
            results = parse_page(html, pref_name)
            all_results.extend(results)
            print(f"{len(results)} entries")
        except Exception as e:
            print(f"ERROR: {e}")

        time.sleep(0.5)

    # Deduplicate: keep first URL per municipality (prefer PC version)
    seen = set()
    unique_results = []
    for r in all_results:
        key = (r["prefecture"], r["municipality"], r["url"])
        if key not in seen:
            seen.add(key)
            unique_results.append(r)

    # Write CSV
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "prefecture", "municipality", "url", "system_type", "kaigiroku_tenant"
        ])
        writer.writeheader()
        writer.writerows(unique_results)

    # Summary
    print(f"\nTotal URLs: {len(unique_results)}")

    # Count unique municipalities
    unique_munis = set((r["prefecture"], r["municipality"]) for r in unique_results)
    print(f"Unique municipalities: {len(unique_munis)}")

    from collections import Counter
    systems = Counter(r["system_type"] for r in unique_results)
    for sys, count in systems.most_common():
        print(f"  {sys}: {count}")

    kaigiroku_tenants = set(r["kaigiroku_tenant"] for r in unique_results if r["kaigiroku_tenant"])
    print(f"\nkaigiroku_ssp unique tenants: {len(kaigiroku_tenants)}")
    print(f"Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()

"""
各データソースを統合して stoma_benefits_db.csv を構築するスクリプト。

入力:
  data/master/municipalities.csv          - 全国市区町村マスター（1918件）
  data/master/benefit_by_prefecture.csv   - 都道府県別基準額（白書2023）
  data/master/council_search_systems.csv  - 議事録検索システムURL一覧
  data/raw/council_minutes/stoma_search_results.csv - 議事録キーワード検索結果
  data/raw/benefit_amounts/individual_benefits.csv  - 個別自治体の基準額

出力:
  data/database/stoma_benefits_db.csv     - 統合データベース
"""

import csv
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
MUNI_CSV = PROJECT_ROOT / "data" / "master" / "municipalities.csv"
BENEFIT_CSV = PROJECT_ROOT / "data" / "master" / "benefit_by_prefecture.csv"
COUNCIL_CSV = PROJECT_ROOT / "data" / "master" / "council_search_systems.csv"
SEARCH_CSV = PROJECT_ROOT / "data" / "raw" / "council_minutes" / "stoma_search_results.csv"
INDIV_CSV = PROJECT_ROOT / "data" / "raw" / "benefit_amounts" / "individual_benefits.csv"
OUTPUT_CSV = PROJECT_ROOT / "data" / "database" / "stoma_benefits_db.csv"

FIELDS = [
    "municipality_code",
    "prefecture",
    "municipality",
    # 都道府県別基準額（白書2023ベースライン）
    "pref_digestive_min",
    "pref_digestive_max",
    "pref_digestive_mean",
    "pref_digestive_median",
    "pref_urinary_min",
    "pref_urinary_max",
    "pref_urinary_mean",
    "pref_urinary_median",
    "pref_n",
    # 個別自治体の基準額（個別調査で判明したもの）
    "benefit_digestive",
    "benefit_urinary",
    "benefit_source",
    # 議事録検索
    "council_system_type",
    "council_search_url",
    "council_stoma_hits",
    "council_ostomy_hits",
    "council_chikuben_hits",
    "council_chikunyo_hits",
    "council_search_date",
    # 備考
    "notes",
]


def load_csv(path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_database():
    # 1. Load all data sources
    municipalities = load_csv(MUNI_CSV)
    benefits = load_csv(BENEFIT_CSV)
    councils = load_csv(COUNCIL_CSV)
    searches = load_csv(SEARCH_CSV)
    individuals = load_csv(INDIV_CSV)

    print(f"Municipalities: {len(municipalities)}")
    print(f"Prefecture benefits: {len(benefits)}")
    print(f"Council systems: {len(councils)}")
    print(f"Search results: {len(searches)}")
    print(f"Individual benefits: {len(individuals)}")

    # 2. Index benefit data by prefecture
    benefit_by_pref = {}
    for b in benefits:
        benefit_by_pref[b["prefecture"]] = b

    # 3. Index council systems by municipality name
    # Multiple entries per municipality possible (PC/mobile versions)
    # Keep first (usually PC version) per municipality
    council_by_muni = {}
    for c in councils:
        # Key: prefecture + municipality name (cleaned)
        muni_name = c["municipality"].replace("議会", "").strip()
        key = (c["prefecture"], muni_name)
        if key not in council_by_muni:
            council_by_muni[key] = c

    # 4. Index individual benefits by municipality name
    indiv_by_muni = {}
    for ind in individuals:
        indiv_by_muni[ind["municipality"]] = ind

    # 5. Index search results by tenant
    search_by_tenant = {}
    for s in searches:
        tenant = s["tenant"]
        keyword = s["keyword"]
        if tenant not in search_by_tenant:
            search_by_tenant[tenant] = {}
        search_by_tenant[tenant][keyword] = s

    # 6. Build unified database
    rows = []
    matched_council = 0
    matched_search = 0

    for m in municipalities:
        pref = m["prefecture"]
        muni = m["municipality"]
        code = m["municipality_code"]

        row = {
            "municipality_code": code,
            "prefecture": pref,
            "municipality": muni,
            "benefit_digestive": "",
            "benefit_urinary": "",
            "benefit_source": "",
            "council_system_type": "",
            "council_search_url": "",
            "council_stoma_hits": "",
            "council_ostomy_hits": "",
            "council_chikuben_hits": "",
            "council_chikunyo_hits": "",
            "council_search_date": "",
            "notes": "",
        }

        # Add prefecture-level benefit data
        b = benefit_by_pref.get(pref, {})
        if b:
            row["pref_digestive_min"] = b.get("digestive_min", "")
            row["pref_digestive_max"] = b.get("digestive_max", "")
            row["pref_digestive_mean"] = b.get("digestive_mean", "")
            row["pref_digestive_median"] = b.get("digestive_median", "")
            row["pref_urinary_min"] = b.get("urinary_min", "")
            row["pref_urinary_max"] = b.get("urinary_max", "")
            row["pref_urinary_mean"] = b.get("urinary_mean", "")
            row["pref_urinary_median"] = b.get("urinary_median", "")
            row["pref_n"] = b.get("digestive_n", "")
        else:
            for f in FIELDS:
                if f.startswith("pref_"):
                    row[f] = ""

        # Match individual benefit data
        ind = indiv_by_muni.get(muni)
        if ind:
            row["benefit_digestive"] = ind.get("benefit_digestive", "")
            row["benefit_urinary"] = ind.get("benefit_urinary", "")
            row["benefit_source"] = ind.get("benefit_source", "")

        # Match council system
        key = (pref, muni)
        c = council_by_muni.get(key)
        if c:
            row["council_system_type"] = c["system_type"]
            row["council_search_url"] = c["url"]
            matched_council += 1

            # Match search results by tenant
            tenant = c.get("kaigiroku_tenant", "")
            if tenant and tenant in search_by_tenant:
                sr = search_by_tenant[tenant]
                stoma = sr.get("ストーマ", {})
                ostomy = sr.get("オストメイト", {})
                chikuben = sr.get("蓄便袋", {})
                chikunyo = sr.get("蓄尿袋", {})
                row["council_stoma_hits"] = stoma.get("hit_count", "")
                row["council_ostomy_hits"] = ostomy.get("hit_count", "")
                row["council_chikuben_hits"] = chikuben.get("hit_count", "")
                row["council_chikunyo_hits"] = chikunyo.get("hit_count", "")
                row["council_search_date"] = stoma.get("search_date", "") or ostomy.get("search_date", "")
                matched_search += 1

        rows.append(row)

    # Sort by code
    rows.sort(key=lambda r: r["municipality_code"])

    # Write output
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    # Summary
    print(f"\nOutput: {OUTPUT_CSV}")
    print(f"Total rows: {len(rows)}")
    print(f"Matched council system: {matched_council}")
    print(f"Matched search results: {matched_search}")

    # Stats on council coverage
    with_council = sum(1 for r in rows if r["council_system_type"])
    with_search = sum(1 for r in rows if r["council_stoma_hits"])
    with_stoma_hit = sum(1 for r in rows
                         if r["council_stoma_hits"] and int(r["council_stoma_hits"]) > 0)
    print(f"\nCoverage:")
    print(f"  With council system URL: {with_council}/{len(rows)}")
    print(f"  With search results: {with_search}/{len(rows)}")
    print(f"  With stoma hits (>0): {with_stoma_hit}/{len(rows)}")


if __name__ == "__main__":
    build_database()

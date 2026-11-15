"""
要望書提出（依頼元DB）× 議会での基準額言及（04の判定結果）× 基準額改定（依頼元DB）の
年度別タイムラインを作る。

入力:
  references/2026／27 データベース（分部さんお渡し）.xlsx
  data/raw/council_minutes/stoma_mentions_classified.csv          (04の出力, ssp系)
  data/raw/council_minutes/stoma_mentions_classified_nonssp.csv   (東大阪市等, 任意)
出力:
  output/reports/council_request_revision_timeline_v2.xlsx
"""

import csv
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import openpyxl
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import column_index_from_string, get_column_letter

ROOT = Path(__file__).parent.parent
DB_XLSX = ROOT / "references" / "2026／27 データベース（分部さんお渡し）.xlsx"
CLASSIFIED = ROOT / "data" / "raw" / "council_minutes" / "stoma_mentions_classified.csv"
CLASSIFIED_NONSSP = ROOT / "data" / "raw" / "council_minutes" / "stoma_mentions_classified_nonssp.csv"
OUT = ROOT / "output" / "reports" / "council_request_revision_timeline_v2.xlsx"

FYS = list(range(2019, 2027))
SUBMIT_COLS = {2025: "AY", 2024: "BA", 2023: "BC", 2022: "BE", 2021: "BG", 2020: "BI", 2019: "BK"}

# ssp以外の検索システムで、会議録本文を人手で確認済みの自治体（基準額言及のあった会議）
# これらの検索システムは会議ごとの固定URLがないため、リンク先は検索システムのトップ
MANUAL_A = {
    ("福岡県", "福岡市"): {
        "url": "https://www.city.fukuoka.fukuoka.dbsr.jp/index.php/",
        "meetings": [
            ("2022-09-22", "令和4年決算特別委員会"),
            ("2022-10-11", "令和4年第4回定例会（第9日）"),
            ("2023-03-01", "令和5年第1回定例会（第4日）"),
            ("2025-10-01", "令和7年決算特別委員会福祉都市分科会"),
            ("2026-06-11", "令和8年第3回定例会（第2日）"),
        ],
    },
    ("栃木県", "宇都宮市"): {
        "url": "https://www.kensakusystem.jp/utsunomiya/index.html",
        "meetings": [
            ("2024-06-14", "令和6年第2回定例会"),
            ("2026-06-18", "令和8年第2回定例会"),
        ],
    },
    ("滋賀県", "大津市"): {"url": "https://www.kensakusystem.jp/otsu/index.html", "meetings": []},
}


def fy_label(y):
    return f"{y}/{str(y + 1)[-2:]}"


def to_fy(d):
    return d.year if d.month >= 4 else d.year - 1


def parse_dates(v):
    if v is None:
        return []
    if isinstance(v, datetime):
        return [v]
    out = []
    for m in re.finditer(r"(20\d{2})[/年.\-](\d{1,2})[/月.\-](\d{1,2})", str(v)):
        try:
            out.append(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    return out


def norm_name(s):
    return (s or "").strip().replace("ケ", "ヶ")


def norm_pref(p):
    return "東京都" if p and p.startswith("東京都") else p


def load_db():
    wb = openpyxl.load_workbook(DB_XLSX, data_only=True)
    ws = wb["2627データベース"]
    idx = {fy: column_index_from_string(c) - 1 for fy, c in SUBMIT_COLS.items()}
    L, M, N, O = (column_index_from_string(c) - 1 for c in "LMNO")
    db = {}
    for row in ws.iter_rows(min_row=3):
        pref, name = row[4].value, row[5].value
        if not name:
            continue
        submit = {fy: row[i].value not in (None, "") for fy, i in idx.items()}
        revisions = parse_dates(row[N].value) + parse_dates(row[O].value)
        db[(norm_pref(pref), norm_name(name))] = {
            "pref": pref, "name": name,
            "digestive": row[L].value, "urinary": row[M].value,
            "last_revised": row[N].value, "history": row[O].value,
            "submit": submit,
            "revised_fys": {to_fy(d) for d in revisions},
        }
    return db


def load_classified(path):
    rows = []
    if path.exists():
        with open(path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    return rows


def _parse_speech_date(s):
    for f in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(s, f)
        except (TypeError, ValueError):
            pass
    return None


def build_summary(wb, db):
    """ssp系で一括取得できた市区町村を分母に、改定と要望書・議会発言の前後関係を集計するシート"""
    progress = ROOT / "data" / "raw" / "council_minutes" / "stoma_mentions_progress.csv"
    with open(progress, encoding="utf-8") as f:
        ok = {r["tenant"] for r in csv.DictReader(f) if r["status"] == "ok"}
    keys = set()
    with open(ROOT / "data" / "master" / "council_search_systems.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["kaigiroku_tenant"] in ok:
                k = (norm_pref(r["prefecture"]), norm_name(re.sub(r"議会$", "", r["municipality"])))
                if k in db:
                    keys.add(k)
    speech_dates = defaultdict(list)
    for r in load_classified(CLASSIFIED):
        d = _parse_speech_date(r.get("date"))
        if r.get("rule_label") == "A" and d:
            speech_dates[(norm_pref(r["prefecture"]), norm_name(re.sub(r"議会$", "", r["municipality"])))].append(d)

    def latest_rev(k):
        ds = parse_dates(db[k]["last_revised"])
        return max(ds) if ds else None

    def flags(k, cutoff):
        """cutoff より前の 要望書あり / 議会発言あり(2019年度以降) / 要→議(同年度含む) / 要→議(後の年度)"""
        cfy = to_fy(cutoff) if cutoff else 9999
        subs = [y for y, v in db[k]["submit"].items() if v and y < cfy]
        gis = [d for d in speech_dates.get(k, []) if (cutoff is None or d < cutoff) and to_fy(d) >= 2019]
        seq = bool(subs and gis and any(to_fy(d) >= min(subs) for d in gis))
        seq_strict = bool(subs and gis and any(to_fy(d) > min(subs) for d in gis))
        return [bool(subs), bool(gis), seq, seq_strict]

    rev = sorted(k for k in keys if latest_rev(k))
    non = sorted(k for k in keys if not latest_rev(k))

    def pct(a, b):
        return f"{a / b:.1%}" if b else ""

    ws = wb.create_sheet("改定との対応（集計）", 1)
    bold = Font(bold=True)
    ws.append(["1. 分母と改定"]); ws[ws.max_row][0].font = bold
    ws.append(["項目", "数", "割合", "分母"])
    ws.append(["議事録検索を一括で取得できた市区町村", len(keys), "", ""])
    ws.append(["改定があった市区町村", len(rev), pct(len(rev), len(keys)), len(keys)])
    ws.append([])

    labels = ["要望書あり", "議会発言あり（2019年度以降）", "要望書→議会発言（同年度含む）", "要望書→議会発言（後の年度のみ）"]
    ws.append(["2. 改定あり（改定前）と改定なしの比較"]); ws[ws.max_row][0].font = bold
    for title, cut_rev, cut_non in [
        ("比較A：改定あり＝改定日より前／改定なし＝2019年度〜現在", latest_rev, lambda k: None),
        ("比較B：両群とも2025-04-01より前で揃える", lambda k: datetime(2025, 4, 1), lambda k: datetime(2025, 4, 1)),
    ]:
        ws.append([title]); ws[ws.max_row][0].font = Font(italic=True)
        ws.append(["項目", "改定あり 数", "改定あり 割合", "改定なし 数", "改定なし 割合"])
        fr = [flags(k, cut_rev(k)) for k in rev]
        fn = [flags(k, cut_non(k)) for k in non]
        for i, lab in enumerate(labels):
            a, b = sum(x[i] for x in fr), sum(x[i] for x in fn)
            ws.append([lab, a, pct(a, len(rev)), b, pct(b, len(non))])
        ws.append(["分母", len(rev), "", len(non), ""])
        ws.append([])

    ws.append(["3. 要望書・議会発言の有無別の改定割合（2019年度以降、時期の前後を問わない）"]); ws[ws.max_row][0].font = bold
    ws.append(["要望書", "議会発言", "改定あり", "市区町村数", "改定割合"])
    allf = {k: flags(k, None) for k in keys}
    for a in (True, False):
        for b in (True, False):
            g = [k for k in keys if allf[k][0] == a and allf[k][1] == b]
            r = sum(1 for k in g if latest_rev(k))
            ws.append(["あり" if a else "なし", "あり" if b else "なし", r, len(g), pct(r, len(g))])
    ws.append([])

    ws.append(["4. 改定前に要望書の記録がない改定自治体"]); ws[ws.max_row][0].font = bold
    ws.append(["都道府県", "市区町村", "消化器系", "尿路系", "改定日", "要望書提出年度（全期間）", "改定前の議会発言"])
    for k in rev:
        f = flags(k, latest_rev(k))
        if not f[0]:
            rec = db[k]
            subs = [y for y, v in rec["submit"].items() if v]
            ws.append([rec["pref"], rec["name"], rec["digestive"], rec["urinary"],
                       latest_rev(k).strftime("%Y-%m-%d"), ",".join(fy_label(y) for y in sorted(subs)) or "なし",
                       "あり" if f[1] else "なし"])
    ws.append([])

    notes = [
        "定義",
        "分母：ssp.kaigiroku.net系の検索システムで全会議を取得でき、依頼元DBと突き合わせられた市区町村（県議会、404で未取得の越前市・小郡市・合志市、人手確認の福岡市・宇都宮市・大津市・東大阪市は含まない）",
        "改定：依頼元DBのN列「最新の変更年月日」に日付がある市区町村。最新の1回のみで判定",
        "要望書あり：基準日の年度より前の年度に提出記録がある（同年度の提出は含めない）。要望書データは2019/20〜2025/26",
        "議会発言あり：基準額に触れた議会発言（自動判定のA）が基準日より前にある。2019年度以降の発言に限る",
        "要望書→議会発言：議会発言が最初の要望書と同じ年度かそれ以降（同年度含む）、または後の年度（後の年度のみ）にある",
        "比較Aは改定なし群の観察期間が長く（現在まで）、改定なし群の割合が高めに出る方向に偏る。比較Bは基準日を揃えたもので、改定あり群には2025-04-01以降に改定した自治体も含む",
        "いずれも記述的な集計であり、要望書や議会発言が改定を引き起こしたことを示すものではない。依頼元DBの要望書記録はJOAが働きかけた自治体の記録であり、要望書と改定の関連は記録の性質上強く出やすい",
    ]
    for n in notes:
        ws.append([n])
    ws.column_dimensions["A"].width = 44
    for col in "BCDEFG":
        ws.column_dimensions[col].width = 14


def main():
    db = load_db()
    speeches = load_classified(CLASSIFIED) + load_classified(CLASSIFIED_NONSSP)

    a_fys = defaultdict(set)
    a_meetings = defaultdict(dict)  # (key, fy) -> {url: (date, meeting)}
    other_fys = defaultdict(set)
    searched = {}
    a_rows = []
    for r in speeches:
        key = (norm_pref(r["prefecture"]), norm_name(re.sub(r"議会$", "", r["municipality"])))
        searched[key] = r["tenant"]
        try:
            fy = int(r["fiscal_year"])
        except (TypeError, ValueError):
            continue
        if r.get("rule_label") == "A":
            a_fys[key].add(fy)
            a_rows.append(r)
            if r.get("minute_url"):
                a_meetings[(key, fy)].setdefault(r["minute_url"], (r.get("date", ""), r.get("meeting", "")))
        else:
            other_fys[key].add(fy)

    # 進捗ファイルにあるが言及0件のtenantも「検索済み」に含める
    progress = ROOT / "data" / "raw" / "council_minutes" / "stoma_mentions_progress.csv"
    done_tenants = set()
    if progress.exists():
        with open(progress, encoding="utf-8") as f:
            done_tenants = {r["tenant"] for r in csv.DictReader(f)}
    systems = ROOT / "data" / "master" / "council_search_systems.csv"
    with open(systems, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t = r.get("kaigiroku_tenant")
            if t in done_tenants:
                key = (norm_pref(r["prefecture"]), norm_name(re.sub(r"議会$", "", r["municipality"])))
                searched.setdefault(key, t)

    manual_links = {}  # (key, fy) -> (url, [(date, meeting)])
    for key, info in MANUAL_A.items():
        searched.setdefault(key, "manual")
        for d, mt in info["meetings"]:
            fy = to_fy(datetime.strptime(d, "%Y-%m-%d"))
            a_fys[key].add(fy)
            manual_links.setdefault((key, fy), (info["url"], []))[1].append((d, mt))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "タイムライン"
    header = ["都道府県", "市区町村名", "現行_消化器系", "現行_尿路系", "最新改定年月日", "過去の変更履歴",
              "要望書提出回数(2019-25)", "基準額言及年度数(2019-26)"] + [fy_label(y) for y in FYS] + \
             ["2018以前の基準額言及年度", "議事録検索"]
    ws.append(header)

    fill_a = PatternFill("solid", fgColor="FFE0B3")
    fill_rev = PatternFill("solid", fgColor="C6EFCE")
    unmatched = []
    rows = []
    for key, tenant in searched.items():
        rec = db.get(key)
        if rec is None:
            unmatched.append((key, tenant))
            continue
        cells = []
        for y in FYS:
            marks = []
            if rec["submit"].get(y):
                marks.append("要望書")
            if y in a_fys[key]:
                marks.append("議会発言有")
            if y in rec["revised_fys"]:
                marks.append("改定")
            cells.append("\n".join(marks))
        old = sorted(y for y in a_fys[key] if y < 2019)
        lr = rec["last_revised"]
        rows.append([
            rec["pref"], rec["name"], rec["digestive"], rec["urinary"],
            lr.strftime("%Y-%m-%d") if isinstance(lr, datetime) else (lr or ""),
            rec["history"].strftime("%Y-%m-%d") if isinstance(rec["history"], datetime) else (rec["history"] or ""),
            sum(rec["submit"].values()),
            len([y for y in a_fys[key] if y >= 2019]),
            *cells,
            ",".join(str(y) for y in old),
            "人手確認" if tenant == "manual" else "自動判定",
            key,
        ])
    rows.sort(key=lambda r: (r[0] or "", r[1] or ""))
    row_keys = [r.pop() for r in rows]
    for r in rows:
        ws.append(r)

    first_fy_col = header.index(fy_label(FYS[0])) + 1
    link_font = Font(color="0563C1", underline="single")
    for key, row in zip(row_keys, ws.iter_rows(min_row=2, min_col=first_fy_col, max_col=first_fy_col + len(FYS) - 1)):
        for y, c in zip(FYS, row):
            c.alignment = Alignment(wrap_text=True, vertical="top")
            if c.value and "改" in c.value:
                c.fill = fill_rev
            elif c.value and "議" in c.value:
                c.fill = fill_a
            if not (c.value and "議" in c.value):
                continue
            if (key, y) in manual_links:
                url, mts = manual_links[(key, y)]
                lines = [f"{d} {mt}" for d, mt in sorted(mts)]
                lines.append("（会議ごとの固定URLがないため検索システムのトップにリンク）")
            else:
                mts = sorted(a_meetings.get((key, y), {}).items(), key=lambda kv: kv[1][0])
                if not mts:
                    continue
                url = mts[0][0]
                lines = [f"{d} {mt}\n{u}" for u, (d, mt) in mts]
                if len(mts) > 1:
                    lines.insert(0, f"基準額言及のあった会議 {len(mts)}件（リンクは最初の会議）")
            c.hyperlink = url
            c.font = link_font
            cm = Comment("\n".join(lines), "NPHL")
            cm.width, cm.height = 420, min(60 + 40 * len(lines), 400)
            c.comment = cm
    for c in ws[1]:
        c.font = Font(bold=True)
        c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "C2"
    widths = [8, 12, 9, 9, 11, 22, 9, 9] + [11] * len(FYS) + [14, 9]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.auto_filter.ref = ws.dimensions

    ws2 = wb.create_sheet("基準額言及の発言一覧")
    cols = ["prefecture", "municipality", "fiscal_year", "date", "meeting", "speaker", "keywords", "excerpt", "minute_url"]
    ws2.append(["都道府県", "市区町村", "年度", "日付", "会議", "発言者", "語", "抜粋", "URL"])
    for r in sorted(a_rows, key=lambda r: (r["prefecture"], r["municipality"], r["date"])):
        ws2.append([r.get(c, "") for c in cols])
        u = ws2.cell(row=ws2.max_row, column=9)
        if u.value:
            u.hyperlink = u.value
            u.font = link_font
    for c in ws2[1]:
        c.font = Font(bold=True)
    for col, w in zip("ABCDEFGHI", [8, 12, 6, 11, 28, 20, 14, 80, 40]):
        ws2.column_dimensions[col].width = w
    ws2.freeze_panes = "A2"
    ws2.auto_filter.ref = ws2.dimensions

    ws3 = wb.create_sheet("凡例・注意点")
    notes = [
        ["表記", "要望書＝JOA要望書提出（依頼元DBの年度別提出フラグ）／議会発言有＝議会で基準額（給付上限額・公費負担上限額等）への言及があった年度／改定＝基準額改定（依頼元DBの最新改定日・過去の変更履歴から）。同じ年度に複数ある場合はセル内で改行して併記"],
        ["色", "緑＝改定のあった年度、橙＝基準額言及のあった年度"],
        ["リンク", "「議会発言有」を含むセルは、その年度で最初に基準額言及のあった会議の会議録にリンク。同じ年度に複数の会議がある場合は、セルのコメントに全会議の日付・会議名・URLを記載。福岡市・宇都宮市は会議ごとの固定URLがないため検索システムのトップにリンクし、コメントに会議名を記載"],
        ["基準額言及の判定", "同一発言内で、ストーマ関連語（ストーマ/ストマ/オストメイト/蓄便袋/蓄尿袋/人工肛門/人工膀胱）の前後150字以内に上限額関連語（基準額・上限額・給付額・公費負担・自己負担・増額・引上げ・見直し・物価高・月額等）がある発言をA判定。間にトイレ/備蓄/避難/便座/洗浄を挟むペア、および「オストメイト対応トイレ」のように設備の修飾語として使われたストーマ関連語は除外。会議内にA発言が1件以上ある年度を「議」とした"],
        ["判定精度", "人手ラベル付き117会議・303発言で、会議単位 precision 0.74 / recall 1.00、発言単位 precision 0.79 / recall 0.89（ルール選択と同じサンプルでの評価のため楽観寄り）。主な誤検出は「ガイドラインの見直し」等の『見直し』、制度説明の中の『自己負担』"],
        ["対象", "ssp.kaigiroku.net系の議事録検索システムを持つ自治体（自動判定、503テナント中499件取得。越前市・小郡市・合志市・三重県は検索システム側の404で未取得）＋福岡市・宇都宮市・大津市（人手確認）＋東大阪市（別途判定）。それ以外の自治体は未検索。県議会は依頼元DBに行がないため表に含めていない"],
        ["読み方の注意", "「議」は議事録に残った発言のみ。要望書提出（要）は依頼元DBの記録で、DBの記録漏れがあれば空欄になる。改定年度は依頼元DBの最新改定日・変更履歴の記載に依存し、記載が年月日形式でないものは拾えていない"],
        ["2026/27年度の「要」", "依頼元DB側が未更新のため空欄。提出があっても反映されていない"],
        ["要望書データの出典", "依頼元DB（2026/27データベース）列AW〜BL"],
        ["詳細", "notes/council_mention_classifier_design.md"],
    ]
    for r in notes:
        ws3.append(r)
    ws3.column_dimensions["A"].width = 20
    ws3.column_dimensions["B"].width = 110
    for row in ws3.iter_rows():
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
        row[0].font = Font(bold=True)

    build_summary(wb, db)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"rows={len(rows)} A_speeches={len(a_rows)} unmatched={len(unmatched)}")
    for u in unmatched[:40]:
        print("  unmatched:", u)
    print(f"saved {OUT}")


if __name__ == "__main__":
    main()

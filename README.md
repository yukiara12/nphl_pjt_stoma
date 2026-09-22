# 全国自治体オストメイト情報データベース

全国の自治体が公開しているオストメイト関連情報を集め、共通の形式に整えて分析・資料化するための共同リポジトリです。データ収集、基盤構築、分析、資料作成、議事録、スケジュールをここで一括管理します。

## メンバー

- Arakawa
- Wakebe
- Niidome
- Suzuki

個人の作業メモや担当資料は `members/` 配下の各自のフォルダに置きます。

## フォルダ構成

```
.
├── README.md
├── data/
│   ├── raw/          # 収集した元データ（加工前）
│   └── master/       # 分析・公開に使う統合版
├── code/
│   ├── collection/   # データ収集・スクレイピング
│   ├── cleaning/     # データ整形
│   └── analysis/     # 解析コード
├── outputs/
│   ├── figures/      # 図
│   ├── tables/       # 表
│   └── reports/      # 報告書・発表資料
├── docs/
│   ├── meetings/     # 議事録
│   ├── protocol/     # データ収集ルール・定義書
│   └── references/   # 参考資料・URL一覧など
├── project/
│   ├── schedule.md   # スケジュール
│   └── tasks.md      # タスク
└── members/
    ├── Arakawa/
    ├── Wakebe/
    ├── Niidome/
    └── Suzuki/
```

## データの置き方

1. 収集したファイルは、手を加えずに `data/raw/` へ置く。
2. 列名・単位・欠損の扱いを揃えた統合版を `data/master/` に置く。
3. 図・表・報告書は `outputs/` に分けて保存する。
4. 元データを直接書き換えず、整形は `code/cleaning/` の処理として残す。

## 進め方

- 予定は [project/schedule.md](project/schedule.md)
- 作業の分担と進捗は [project/tasks.md](project/tasks.md)
- 議事録は `docs/meetings/`、収集ルールと定義は `docs/protocol/`、参考資料は `docs/references/`

# 第5回 Firewallは何を判断するか

出典：`docs/study/CONTENT-PLAN.md`。到達点：Network／Applicationルールの違いと評価順、内部中継・基盤通信の許可を説明できる。

対応資料：[26〜30節](01-network-control.html#fw-roles)、[34〜35節](01-network-control.html#fw-decisions)

資料リンクは`docs/study/`基準。台本のsourceReferences.fileはvideo-sample/基準。

共通仕様は../../PRODUCTION-GUIDE.md、配役は../../podcast-cast-gemini.jsonを読む。
script.jsonは80台詞の対話・字幕・図を執筆し、内容レビュー済み。
statusはready-for-generation。約15分を目安とし、通常速度で生成した音声の実長から尺を確定する。
共通ファイルは変更せず、このフォルダーと担当回のoutputだけを編集する。

## 構成

今回の問い → Firewallへ届く経路 → 2種類のルール → 評価順と優先度 → 内部中継 → APIM基盤通信 → 管理VM・ACA・DNS → 3問の確認 → 保守とまとめ。

主例はAppGWからAPIM、APIMからStorageタグ、管理VMのNTP。PE直接経路を対照例にする。第6回のFQDN生成・SNAT、第7回のログ調査、第10回のIDPS詳細は扱いを広げない。

図の400/350は評価原理を説明する仮の優先度。実際のグループ値の一覧ではない。実IPは作らず、APIM実内部VIPやACA実入口IPとして示す。

## 採用画面（2026-11-02更新）

teaching-settings-v2へ再制作。76本編台詞を25のviewIdにまとめ、同じ質問・回答でメニュー・構図・縮尺を保持する。設定表と補助図を併記し、赤枠は回答・説明開始から表示する。SVGの強調以外の構造を同じviewId内で一致させ、通常の設定ビューも行順・列幅を保持した。内部中継032〜035の47.79秒を連続確認用動画にする。音声本文・字幕・声・styleは80件すべて旧版と同一。

# 第6回 外向き通信と出口IP

出典：[コンテンツ計画](../../../CONTENT-PLAN.md)の第6回。到達点：Web依存先の許可、生成されるFQDN、SNAT、出口IPの期待値と実測の違いを説明できる。

## 対応資料

[01 ネットワークの制御](../../../01-network-control.html)の31〜33節（APIM・管理VM、Portal・ビルドのApplicationルール、生成される宛先）、36〜37節（明示的な外向き接続、SNATと見える送信元）。参照時点は2026-11-01。稼働Azureの取得値・実測結果ではない。

## 構成

今回の問い → 宛先・経路・送信元の見る順番 → Web依存先の許可 → 設定生成・API公開で増えるFQDN → 公開IPを持たない管理VMの明示出口 → SNATの違い → 期待値と実測 → 確認3問 → まとめ・第7回への問い。

87台詞、9章、21個の固定設定ビュー。最新のteaching-settings-v2へ更新。設定メニューと補助図を併記し、問答ごとに同じ構図を保持する。音声・字幕本文は旧版と同一で、通常速度の検証済み原音を再利用する。第5回のルール評価順、第7回の詳細なログ調査、応用編のハブ切替手順へは広げない。

## 共通仕様と成果物

[制作手順](../../PRODUCTION-GUIDE.md)・[現行キャスト](../../podcast-cast-gemini.json)を使用。MAKO=Aoede / MANABU=Charon / IKUMI=Orus、Gemini 3.8 Flash TTS、全員1.0倍。共有ファイルを変更しない。

[台本](script.json)と[技術レビュー](source-review.md)、[画面レビュー](visual-review.md)を用意。完成状態・実尺・検証・再生先は[制作記録](production.md)を正本とする。出力先は `output/lesson-06-gemini/`。

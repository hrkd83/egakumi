# 第10回 Firewallの許可とIDPSの検知

出典：`docs/study/CONTENT-PLAN.md`。到達点：通信規則のAllow／DenyとIDPS Alert／Deny、経路・TLS・方向による検査範囲を区別できる。

対応資料：[9〜11節](02-external-protection.html#idps-settings)

資料リンクは`docs/study/`基準。台本のsourceReferences.fileはvideo-sample/基準。

共通仕様は../../PRODUCTION-GUIDE.md、画面は../../SETTINGS-VIEW.md、配役は../../podcast-cast-gemini.jsonを読む。
本編はteaching-settings-v2。layout:settingsを基本にし、質問と回答で同じviewId・構図を保つ。
本編の自動ズーム・パン・pulse・模擬ポインターは使わず、赤枠は説明対象に合わせる。
設定と補助図を対応付け、40〜70秒程度の連続した会話を動画で確認する。
script.jsonは制作開始用の未完成原稿。TODOをすべて置き換え、約15分の対話・図・字幕を執筆する。
レビューとプレビュー後にstatusをready-for-generationへ変更する。
共通ファイルは変更せず、このフォルダーと担当回のoutputだけを編集する。

## 執筆済みの構成

- 対話90台詞。目的と本番/テスト → Alertの並列検知/Denyのinline遮断 → 署名条件 → 経路/TLS/方向 → 例外と関連設定 → 通常API一要求とログ → 3問 → まとめと第11回。
- 本文画面14 viewId。11個の設定表・欄と3個の設定shell付き補助図を使い、各問答内は構図とデータを保持する。
- 確認問題の質問turnでは強調なし。回答turnから候補Bを赤枠で示す。
- 第10回は定義・機能の説明。Azure稼働環境を取得した値やDenyへ変更して実験した結果として扱わない。
- 対応HTML 9〜11節が中心。経路の前提は2節、ログの最低限の照合は19節のIDPSログ補足。詳細な調査・変更運用は第13回へ残す。

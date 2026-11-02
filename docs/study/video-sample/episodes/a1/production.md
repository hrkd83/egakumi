# A1 ハブ＆スポーク接続と往復経路 — 設定メニュー版 制作記録

改訂日：2026-11-02（JST）。状態：改訂版完成。最新画面基準：`teaching-settings-v2`。

## 依頼と今回の差分

最新CONTENT-PLAN.mdに基づくA1の再制作。テーマ・到達点・参照範囲は初稿と同じで、本編を2026-11-02採用の教材用設定メニューへ更新する。前回は図・カード中心でviewId管理がなく、確認問題の質問中に回答文が見える箇所もあった。今回、固定カメラ、問答単位の同一構図・縮尺、説明対象への赤枠、設定代表行と必要な補助図の併記、連続した会話の確認を適用する。

[最新の画面仕様](../../SETTINGS-VIEW.md)と[制作手順](../../PRODUCTION-GUIDE.md)に従う。Azure Portalのブランドバー・検索欄・疑似操作を模倣せず、公開設定例/リポジトリ定義に基づく教材専用の画面とする。稼働Azureへの変更適用・実測は行わない。

## 到達点と参照

private接続・出口集約・Gateway共有の採用範囲を選び、双方向ピアリング、区画別UDR、復路、Gateway経路伝搬を説明する。主例はAPIM公開宛てをハブFWへ集約する往復。AppGW↔APIM/ACAのローカルFW経由、PE直接接続を維持する設計とする。

元資料は01ネットワークの制御47・48・49・53節。補助資料はdocs/setup/07-hub-spoke.md、infra/modules/network.bicep等。A2の中央DNS・許可・切替/再配備へつなぐ。[技術再監査](source-review.md)ではテーマ・到達点に変更なし、既存82台詞に再録が必要な技術指摘なし。

## 旧版の保存

- [初稿の台本・設定・検証・制作記録](revisions/initial-v1/)
- [初稿MP4](../../output/lesson-a1-gemini/revisions/initial-v1/egakumi-study-a1.mp4)
- [初稿の保存マニフェスト](revisions/initial-v1/archive-manifest.json)

初稿のMP4 SHA-256：`4e394027101b28efd05cc681615646498f455cf53a07f9749fe1e31c9c2bdf71`。生成成功済み原音・リクエスト・実行履歴はcanonical出力内で保持し、書き直した画面と再結合する。

## 準備と固定仕様

[事前検査](preproduction-verification.json)：passed。共通ファイル/素材ハッシュと必要ツールを確認。空き容量4.43 GiB。変更はepisodes/a1/およびoutput/lesson-a1-gemini/内のみ。共有ファイル、A2や他回、他のロック/サーバーは変更しない。

Gemini 3.8 Flash TTS、MAKO=Aoede / Moderator、MANABU=Charon / Product Manager、IKUMI=Orus / Solution Architect。全員通常速度1.0倍。冒頭のホログラムと滑らかな話者カメラ、ロゴ4秒、本編BGMの2秒クロスフェード/ダッキング、ブランド曲63.8〜71.8秒の8秒クロージングは維持する。本編カメラは固定し、自動zoom/pan、赤枠pulse、mock pointerを使わない。

本文・caption・delivery・ID・章・間を変えず、prepare --replace→検証済みキャッシュ結び直し→build --reuse-audio trueによる再合成する。全82候補が初稿と完全一致し、成功済みWAVと送信requestのSHA-256も照合済み。prepare --replace後のgenerateはcomplete、attemptedThisRun=0で終了し、追加API送信・認証取得なし。

## 改訂画面と保持する問答

[生成用スクリプト](render-settings-v2.mjs)と専用SVGで、18固定viewId（通常設定13＋図併記5系統、赤枠variant23）を作成。全78本編turnはsettings、または同じsettings shell内の図併記へ対応。トップvisualStyleはteaching-settings-v2。初稿との比較で82turnのvisual以外の全データ・章・出典・順序・間が完全一致。

| viewId | 台詞番号 | 設定/図と説明 |
| --- | --- | --- |
| a1-scope | 005〜007 | private接続・公開出口・中央DNS・Gateway共有を個別採用 |
| a1-ownership | 008〜010 | 接続先・責任と現行IaCが作らない共有設備 |
| a1-preserve | 011〜013 | 6区画、ローカルFW、PEを維持する範囲 |
| a1-address-plan | 014〜016 | 全VNet・ハブ・他スポーク・社内の重複照合 |
| a1-peering | 017〜019 | 両側peering、VNetアクセス、Connected |
| a1-forwarded | 020〜023 | 転送受入側の設定＋中継図を併記 |
| a1-public-egress | 024〜032 | default-egressと準備行＋APIM↔ハブFW↔公開先の往復図 |
| a1-specific-routes | 033〜034 | 具体的PE/VNet/peering経路と0/0の区別 |
| a1-local-return | 035〜041 | 入口の設定行＋AppGW↔ローカルFW↔APIM/ACA |
| a1-agw-default | 042〜043 | 通常AppGW v2の既定経路・制御系の制約 |
| a1-subnet-routes | 044〜051 | 管理VM/ACA/PE/FW区画の変更と維持 |
| a1-gateway-sharing | 052〜058 | 共有条件・両側設定と社内↔Gateway↔Coreの往復 |
| a1-propagation | 059〜065 | 利用者設定/ARM属性反転、ACA固定と明示経路 |
| a1-agw-propagation | 066〜069 | AppGWへの0/0、優先順位、伝搬停止の範囲 |
| a1-quiz-peering | 070〜072 | 接続と全通信の経路の違い |
| a1-quiz-return | 073〜075 | 公開出口と入口の復路の違い |
| a1-quiz-propagation | 076〜078 | 伝搬停止は既定経路だけを除く指定か |
| a1-summary | 079〜082 | 要点と次回A2 |

同じviewId内でメニュー・設定行・図内容・構図・縮尺を保持し、赤枠だけを説明対象に合わせて切り替える。SVGもhighlight矩形を除去したXMLが同viewId内で同一であることを照合した。quiz質問070/073/076は条件と選択肢のみ、赤枠なし。回答から正しい選択肢を強調する。図・矢印を指す台詞027等には該当する経路図を同じ画面へ併記した。

[設定画面の構造QA](structural-qa-v2.json)：issues/warnings 0、protectedDataUnchanged=true。[技術再監査](source-review.md)：改訂表・図も合格、未解決指摘なし。ハブの設備・経路値は接続案として表示し、実IPを推定しない。

## 再合成・全編検証

`build-lesson.mjs ... --reuse-audio true`は検証済みsaved audioを使用して完了。完成尺 **15分47.417秒**、22,738フレーム、1600×900/24fps、H.264/AAC。82台詞・字幕98区間を初稿と同じ時刻で採用。

[全編の自動検証](verification.json)：passed、warnings 0。全編A/V decode、生成証跡と原音/設定ハッシュ、字幕/台詞の完全対応、通常速度、冒頭カメラ、BGMと8秒closingを確認。音量 **−16.0 LUFS / −1.5 dBTP**。本編固定画面・viewId・赤枠は別途v2構造/映像QAで確認する。

初稿のaudio-cache.jsonと今回のaudio-cache.jsonは完全一致。元のPCM音源のSHA-256も維持：

- lesson-narration.wav：`baac097f273c78e45ab3ae367ab182859479e25a7b3a31005eb40dc567dda475`
- lesson-mix.wav：`d016c24ec2ee753eb8853ea74de5172446fa4c2a7bfe53cf9e95948f2888b43d`

今回の追加音声API送信は **0件**。既存原音82件・声・style・速度をそのまま使用。初稿の生成量（prompt 28,598 / candidates 22,574 / total 51,172 tokens）は既存使用量であり、今回新たに発生した使用量ではない。

## 連続した質問・回答の確認

[共通抽出ツール](../../make-conversation-preview.py)で、027 MAKOの図の確認から032 MANABUの補足までを切り出した。

- 元動画の範囲：276.416667〜345.750秒
- 抜粋：**69.333秒 / 1,664フレーム / 24fps**
- 対象turn：027〜032（質問・往路の回答、SNAT、復路の質問・回答・補足）
- 同一viewId：`a1-public-egress`
- 内部カットなし、通常速度1.0、速度/ピッチ変換なし、元の声/BGMを連続維持
- [連続プレビューの検証・時刻記録](../../output/lesson-a1-gemini/conversation-preview.json)：passed、warnings 0、full A/V decode合格
- [連続プレビュー再生](../../output/lesson-a1-gemini/conversation-preview.html)

個別の静止画だけで画面保持の自然さを確認済みとはせず、連続動画の時系列/差分とブラウザーでの連続再生も別途確認する。

## 完成ファイル

- [設定メニュー版MP4](../../output/lesson-a1-gemini/egakumi-study-a1.mp4)
- [全編の再生ページ](../../output/lesson-a1-gemini/index.html)
- [字幕](../../output/lesson-a1-gemini/lesson-captions.vtt)
- [台本](../../output/lesson-a1-gemini/lesson-transcript.md)
- [完成版から抽出した68代表画面](../../output/lesson-a1-gemini/final-review-v2/manifest.json)

完成MP4 SHA-256：`93e95ecb40512338633194d7ef884f8d0f2cbbf9adb8b1a1f70f4c16794a5522`。サイズ：49984319 bytes。

一時再生URL：<http://127.0.0.1:55856/>。連続確認用：<http://127.0.0.1:55856/conversation-preview.html>。

localhostは一時的な閲覧用で、MP4が成果物の正本。サーバー再開は `python3 -u docs/study/video-sample/episodes/a1/serve.py`（127.0.0.1の空きポートを自動選択）。再生ページの「動画を保存」から保存できる。

この環境では音声内容の聴取・認識ができないため、発音・用語・言葉の欠落・演技指示の発声・会話の自然さの聴感確認は未実施。本文/送信style/原音ハッシュの一致や波形/音量検査は聴感確認と区別する。

## 最終映像QAと連続再生結果

[映像QA](visual-review-v2.md)：合格。完成MP4から68代表画面・6コンタクトシートを確認し、最終previewと一致。全設定表/補助図/赤枠/字幕/3人表示、8秒closingを確認。

[連続性検査](continuity-qa-v2.json)：69.333秒の本文領域を全1,664フレーム差分で確認。主要変化5回はturn境界の赤枠切替に一致。途中のpan/zoomや字幕chunkごとのリセットなし。時系列8点の視認でも設定行・図・構図・縮尺が同一。圧縮由来の小差分は画面移動と区別した。

ブラウザーでも69.333秒を最初から最後まで途中seek/停止なし、playbackRate=1で連続再生し、currentTime=0.059→18.013→48.009→69.333秒を確認。終端でended=true、paused=true、errorなし。18秒/48秒時点の実表示でも同じ設定行と図が維持され、赤枠だけが説明対象へ移った。音声内容を聴取しての判定ではない。

連続再生後、全編の再生ページへ移動し、15:47、字幕/動画の保存リンクと全章を確認、成果物タブとして残した。未解決の技術・表示指摘なし。

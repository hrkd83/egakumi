# 第7回「つながらないときの調べ方」設定メニュー版

2026-11-02 JST。最新の `CONTENT-PLAN.md`・`AGENTS.md`・`PRODUCTION-GUIDE.md`・`SETTINGS-VIEW.md` に従う新版制作記録。**完成：設定メニュー版の全編検証・連続会話・最終画面確認に合格。**旧版の完成状態と検証記録は[履歴](history/diagram-v1/production.md)へ保存した。

## 最新計画との差分

第7回のテーマ・到達点・出典範囲は同じ。DNS・実効経路・NSG・Firewallログを同じ通信で照合し、ログ記録と通知を区別する。出典は `01-network-control.html` の38〜40節・46節。変更は本編の画面基準で、旧版の独立図・カード中心から **teaching-settings-v2** の教材用設定メニューと必要な補助図へ更新する。

本編は固定カメラ。質問から回答・確認まで `viewId`、メニュー、行順、構図、縮尺を保つ。赤枠は説明対象を示す静的な枠で、ズーム・パン・pulse・ポインターを使わない。字幕chunkや話者の切替では本編画面をリセットしない。冒頭のホログラムと話者へ移るカメラは従来どおり保持する。

## 内容・音声の扱い

主例U1は AppGW → Firewall → APIM / TCP 80。D2 APIM → Foundry PE / TCP 443 と比較する。設定値は現行定義・公開設定例と、調査で確認する項目として表示し、実IP・実ログを推測で埋めない。稼働Azure環境の取得・KQL実行・疎通や通知メール到着の試験は行わない。

[旧台本](history/diagram-v1/script.json)から、visual以外の全turn項目を変更せず更新する。全91台詞・採用89台詞・190字幕cue、本文・声・delivery・章・間・ID・省略集合を保持する。音声は Gemini 3.8 Flash TTS、MAKO=Aoede / MANABU=Charon / IKUMI=Orus、全員通常速度1.0倍、速度・ピッチ変更なし。`prepare --replace` と検証済みキャッシュの結び直し、`build --reuse-audio true` を使う。追加音声API送信を必要としない。

## 問答と画面

[画面原稿](author-settings-v2.py)、[補助SVG描画](render-settings-v2.mjs)、[補助図の設定データ](settings-diagrams.json)。19のviewIdを使い、本編はsettings、補助図併記は共通renderSettingsShellに基づくSVGとする。

| 採用turn | viewId | 同じ画面で保つ内容 |
| --- | --- | --- |
| 007〜017 | connection-identity | U1小図＋調査票、時刻・通信元・宛先・ポート |
| 018〜021 | source-dns | 送信元・DNS・APIM内部IPの図＋照合項目 |
| 022〜027 | u1-roundtrip | 往復小図＋代表UDR・実効情報 |
| 028〜031 | u1-nsg | U1小図＋送信/受信NSG・FW・送信元 |
| 032〜036 | fw-log-kind | Network/Applicationログと評価順 |
| 037〜041 | fw-log-evidence | 検索条件・Action・ActionReason・規則階層 |
| 042〜043 | fw-allow-scope | 許可の観測と、未確認の範囲 |
| 044〜047 | fw-log-absence | 収集・検索条件＋PE直接経路図 |
| 048〜055 | u1-triage-example | 演習上の調査票。観測を説明時点で表示 |
| 056〜059 | fw-allow-followup | 受信・復路・APIM・AppGW記録と引継ぎ |
| 060〜062 | log-destinations | 診断設定の保存先分岐と監査収集条件 |
| 063〜065 | signal-coverage | 通信ログ・数値・管理操作の収集範囲 |
| 066〜070 | notification-chain | 評価・通知図＋代表メトリック・Action Group |
| 071〜073 | activity-alert-scope | 対象ID・操作名・結果・範囲外 |
| 074〜076 | log-alert-flag | フラグの切替範囲と別設定 |
| 077〜078 | check-pe-no-log | 確認①の条件・問い・判断・次の確認 |
| 080r2〜081 | check-fw-allow | 確認②の条件・問い・判断・次の確認 |
| 083r2〜085 | check-no-notification | 確認③の条件・問い・判断・次の確認 |
| 086〜091 | troubleshooting-summary | 調べる順序・引継ぎ・第8回の問い |

質問077/080r2/083r2では、判断欄と次の確認欄は「回答で確認する」で、赤枠なし。回答078/081/084から同じ欄へ判断を表示し赤枠を出す。元の台詞を省略・加速せず、旧版の答えの先取りを解消する。

## 確認計画

[事前検査](preproduction-settings-v2.json)はpassed。最新共通ファイル・素材・ツール・teaching-settings-v2を確認。[設計レビュー](settings-plan-review.md)と[新版QA](visual-settings-v2-review.md)で静止画と会話の連続性を分けて確認する。

連続会話は022〜027、旧実長では205.750〜269.583秒・63.833秒。質問の頭から回答・反応の末尾まで内部カットなし・通常速度で抽出し、全編デコードとブラウザー連続再生、質問/回答/字幕切替時点の画面保持を確認する。完成後に共通make-conversation-preview.pyで確定する。

完成MP4・尺・音声再利用の証拠・追加API送信数・検証・再生先を下記に記録した。旧MP4は担当回出力の `history/diagram-v1/egakumi-study-07.mp4` に保存済み。

## 音声再利用と事前QAの結果

[audio-reuse-settings-v2.json](audio-reuse-settings-v2.json)：**passed**。採用89件すべての原音を検証済みキャッシュから再利用し、今回の追加合成API送信は**0件**、認証取得も行っていない。本文・声・style・間・採用集合は同じ。ナレーションWAVとBGM込みミックスWAVのSHA-256も旧版と完全一致し、通常速度・ピッチ・BGM・尺を保った。

[設定ビュー専用検証](settings-v2-verification.json)：**passed、errors/warningsとも0**。旧版91台詞のvisual以外の全項目一致、採用89件・19viewIdのメニュー/タイトル/列幅/行順/図の保持、固定カメラ、確認問題の回答時点、SVGの自己完結性と非アニメーションを検証した。[検証ツール](verify-settings-v2.py)は担当回専用で、共通プログラムを変更していない。

[事前の映像QA](visual-settings-v2-review.md)：19viewIdを含む22場面と、最終微調整の往復経路024・通知068を原寸1600×900で目視し合格。質問と回答の同じ行位置、正解先取り防止、文字と補助図の収まりを確認。音声内容の聴感・完成MP4の連続視聴・全編デコードとは分けている。

## 完成MP4と全編検証

**16分48秒（1,008秒）・1600×900・24fps・24,192フレーム・H.264 / ステレオAAC。** MAKO / MANABU / IKUMI全員通常速度1.0倍。

- [新版MP4](../../output/lesson-07-gemini/egakumi-study-07.mp4)
- [保存済み再生ページ](../../output/lesson-07-gemini/index.html)
- [字幕 WebVTT](../../output/lesson-07-gemini/lesson-captions.vtt) / [採用台本](../../output/lesson-07-gemini/lesson-transcript.md)
- [タイムライン](../../output/lesson-07-gemini/lesson-timeline.json) / [書出し記録](../../output/lesson-07-gemini/lesson-export.json)
- [連続会話プレビュー](../../output/lesson-07-gemini/conversation-preview.html)
- [旧図中心版MP4](../../output/lesson-07-gemini/history/diagram-v1/egakumi-study-07.mp4)

新版MP4 SHA-256：`2c1f13666db51047c6c4cea9a388106a3e25936c1b67b8ddc3cd577e61a9bce8`。旧版のSHA-256は `da3f1eeedb85c65dfe461c4cf928c29f421a8444e0c0fb9bf2ac01d456007426`。

[標準検証](verification.json)：**passed、警告0件**。生成証跡・原音・通常速度・190字幕cue・採用89台詞・全編24,192フレームの映像/音声デコード・冒頭カメラ・BGM・共通8秒クロージングを検証。音量は **−16.02 LUFS / True Peak −1.5 dBTP**。[納品前共通仕様検査](delivery-profile-check.json)もpassed。

## 連続した問答のプレビュー

共通 `make-conversation-preview.py` で022〜027を抽出した。[プレビュー記録](../../output/lesson-07-gemini/conversation-preview.json)：**passed、警告0件**。

- 元動画205.750〜269.583333秒、63.833333秒、1,532フレーム、24fps。
- 質問022 → 回答023/024 → 確認025 → 実効情報026 → 役割の確認027。すべて `u1-roundtrip`。
- 往復小図と代表UDR/実効情報の表を同じ画面に保持。内部カット・速度/ピッチ変更なし、音声とBGMも連続。
- プレビュー全編の映像/音声デコード・フレーム数・タイムスタンプ・音声長を検証した。
- プレビューSHA-256：`04495f530a826d5e64b8afd35ab280d0eeac4caa564a8bf469127aebf334705f`。

## 再生先と確認範囲

旧一時サーバーは終了していたため、空きポートへ担当回専用サーバーを起動した。現行の全編再生先は [http://127.0.0.1:54933/](http://127.0.0.1:54933/)、連続会話は [conversation-preview.html](http://127.0.0.1:54933/conversation-preview.html)。127.0.0.1限定で、外部公開していない。サーバー停止後も保存済みMP4/HTMLが成果物の正本。再起動はリポジトリルートで `python3 -u docs/study/video-sample/episodes/07/serve.py` を実行し、表示された空きポートを使う。

全編の正常再生とチャプター移動をブラウザーで確認。duration 1,008秒、1600×900、playbackRate 1、readyState 4、エラーなし。完成MP4抽出25場面、連続プレビューの再生観測、全編デコードはそれぞれ別の確認として記録する。

音声内容の聴取・認識機能が利用できないため、発音・自然さ・読み上げ欠落の聴感確認とASRは未実施。原音とミックスの完全一致・送信本文/styleの整合・音量の機械的検証を、聴感確認と混同しない。実環境の設定取得・通信・通知メール到着も制作の検証範囲に含めていない。

## 最終の映像・連続再生確認

**完了。** [新版映像QA](visual-settings-v2-review.md)で、完成MP4から抽出した[25場面](../../output/lesson-07-gemini/final-qa/manifest.json)をすべて原寸確認し合格。顔・英語役割・冒頭話者中央寄せ・19設定ビューの代表場面・確認問題の先取り防止・終了ロゴに文字切れや重なりなし。

[ブラウザー確認記録](browser-verification.json)：63.833秒の連続プレビューをシークせずplaybackRate 1で最後まで再生し、ended=true、エラーなしを確認した。0.016秒（質問）、11.756秒（実効情報の回答）、34.505秒（復路の確認）、61.427秒（役割の反応）で画面を目視し、同じ往復図・設定表・行位置を保持し、静的な赤枠だけが対象に合わせて変わることを確認。63.833秒で正常終了。動画内部の構図を確認したもので、全フレームの連続目視や音声の聴感確認とは分けている。

全編タブは42.847秒で一時停止し、更新した設定ビューが見える成果物として残した。旧一時URLから新しい [全編ページ](http://127.0.0.1:54933/) を使用する。確認用プレビュータブは閉じ、プレビューファイルと専用HTML/記録は保存した。

標準検証、設定ビューの検証、音声再利用検証、納品前共通仕様検査はいずれもpassed。標準検証・連続プレビュー・設定ビュー検証は警告0件。今回の追加音声生成API送信は0件、最終MP4の尺は16分48秒。

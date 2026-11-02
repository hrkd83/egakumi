# 第16回 仕様を調べ、APIを試す — 完成記録

2026-11-02 JSTに完成。**17分11.042秒、1600×900、24fps、H.264 / AAC、24,745フレーム**。全員通常速度1.0、生成後の速度・ピッチ変換なし。85台詞・読み上げ5,994文字・字幕191区間・7章・確認問題3問。

## 再生と保存

- [完成MP4](../../output/lesson-16-gemini/egakumi-study-16.mp4)（51,011,121 bytes）
- [再生ページ](../../output/lesson-16-gemini/index.html) / [このMacの一時再生URL](http://127.0.0.1:63496/)
- [字幕WebVTT](../../output/lesson-16-gemini/lesson-captions.vtt) / [完成台本](../../output/lesson-16-gemini/lesson-transcript.md)
- [45.75秒の連続問答](../../output/lesson-16-gemini/conversation-preview.html) / [MP4](../../output/lesson-16-gemini/conversation-preview.mp4)

再生ページの「動画を保存」「字幕を保存」から保存できる。MP4が成果物の正本。localhostはこのMacのサーバー稼働中だけ有効。再開はリポジトリルートで `python3 -u docs/study/video-sample/episodes/16/serve.py`。127.0.0.1と空きポートを使い、HTTP byte rangeに対応する。

MP4 SHA-256：`f92c65eeb964cea2650733022e5563c990195d8eceaf7f9a716bb47137406518`。

## 到達点と出典

カタログと試用の経路、台帳ID・APIMサブスクリプションID・秘密キーの違いを説明できることを到達点とした。

元資料は[仕様確認](../../../03-subscription-management.html#catalog)・[API試用](../../../03-subscription-management.html#execution)。API定義→カタログ生成→Portal同梱→表示、Graph/SQL/製品内APIによる候補の照合、台帳ロックと再認可、ARMのGET・製品API確認・listSecrets、固定Gateway送信、結果表示と保存範囲を追う。Realtime・文書解析・PII MCPの違いは概要に絞り、第17回の発行後管理へつなぐ。

章別・実装ファイル別の出典は[script.json](script.json)のsourceReferences/sourceRefsへ記載。[内容監査](content-review.md)で対応実装と照合済み。実装にない会社有効状態の再確認を除き、候補表示でAPIM実体のactiveを個別GETしていない点を明確にした。稼働環境の取得値・実行結果の実演として説明していない。

## チャプター

| 開始 | 内容 |
| --- | --- |
| 00:05 | 今回の問い |
| 00:38 | カタログはどこから来るか |
| 03:51 | 台帳ID・実ID・秘密キー |
| 05:08 | 試用要求を一つ追う |
| 09:51 | 結果と保存範囲を確認する |
| 13:38 | 三つの確認問題 |
| 16:12 | まとめと次の問い |
| 17:03 | 共通クロージング |

## 画面と会話

画面基準は`teaching-settings-v2`。本編は教材用の設定メニューを主役にし、カタログ生成と通常REST試用の2系統の補助図を共通renderSettingsShell内へ配置した。Azure Portalのブランドバーや検索欄を模倣していない。本編に自動ズーム・パン・赤枠pulse・模擬ポインターはない。

18のviewIdで一つの問答を保持。同じviewIdの構造は、赤枠の違いを除いてハッシュが一致。質問時に確認問題の正解を赤枠で先取りせず、回答から強調する。字幕の切替でも構図・表順・縮尺を維持する。

| viewId | 台詞番号 |
| --- | --- |
| `catalog-purpose` | 005–008 |
| `catalog-build-flow` | 009–013 |
| `catalog-display` | 014–017 |
| `catalog-change` | 018–021 |
| `identity-three` | 022–027 |
| `execution-choices` | 028–032 |
| `execution-flow` | 033–038 |
| `execution-lock` | 039–042 |
| `execution-arm` | 043–046 |
| `execution-input` | 047–051 |
| `execution-response` | 052–056 |
| `execution-storage` | 057–060 |
| `execution-services` | 061–065 |
| `execution-trouble` | 066–069 |
| `quiz-catalog` | 070–073 |
| `quiz-id` | 074–077 |
| `quiz-recheck` | 078–081 |
| `lesson-summary` | 082–085 |

[連続プレビュー記録](../../output/lesson-16-gemini/conversation-preview.json)：元動画の **485.541667〜531.291667秒**、turn043 MAKOの質問からturn046 MANABUの補足まで45.75秒・1,098フレーム。`execution-arm`の同じ表を保持し、質問時は赤枠なし、GET/製品API→listSecrets→サーバー内の利用範囲へ赤枠が移る。途中カット・速度変更・音声再生成なし。

ブラウザーで0秒から45.75秒まで途中シーク・一時停止なし、playbackRate=1、played=[0,45.75]、ended=true、video.errorなしを確認。[ブラウザー確認](browser-review.json)。本文の同じ表と縮尺が保持され、話者・字幕・説明対象の枠だけが変化することを視覚確認した。全編ページの章移動、1600×900、全範囲のseekableも確認。

## 音声・音楽と使用量

Gemini 3.8 Flash TTS（gemini-3.8-flash-tts）。MAKO=Aoede、MANABU=Charon、IKUMI=Orus、全員通常速度1.0。自然言語styleと読み上げ本文を分け、画面・字幕には正式表記を使用。原音は24kHz mono PCM。共通ロゴ4秒、既存肖像のホログラム、main-bgmの2秒クロスフェードと会話中のダッキング、brand-theme 63.8〜71.8秒の共通クロージング8秒を使用。

台本85台詞を指定のGoogle Cloudプロジェクトへ送信することについてユーザーの明示許可を受け、ユーザーが既存アカウントの再ログインを完了してから生成した。全85件HTTP 200、workers=1、再送0。トークンはメモリーのみで扱い、成果物へ保存していない。

API返却使用量：promptTokenCount=28,399 / candidatesTokenCount=24,783 / totalTokenCount=53,182。原音合計991.32秒。音量は最終mix **−16.03 LUFS / −1.5 dBTP**。

[原音監査](audio-audit.json)では破損・ゼロ・長い無音・長さ異常のエラーなし。6台詞で+32767の短い平坦ピークを確認（8〜10サンプル、0.33〜0.42ms、該当率0.012〜0.025%）。[追加調査](audio-review.json)に記録した。低頻度の短いピークだけで再録必須とする根拠はないため原音を保持し、共通のloudnormで制作した。正規化が元の平坦化を復元したとは扱わない。

**発音・演技・会話の自然さ・原音ピークの聴感上の影響は未確認**。利用ツールで音声内容を聴取できないため、機械検証やブラウザーの再生進行と区別する。完成動画から試聴できる。

## 検証結果と尺

[標準検証](verification.json)はpassed。本文と生成要求・style・音声の証跡、ハッシュ、通常速度、全編の映像・音声デコード、フレーム数、字幕時刻と本文、冒頭カメラ、BGM・クロージング、音量を確認した。[制作基盤と共有素材](final-production-check.json)もpassed。[画面構造の検証](preproduction-verification.json)はエラー0。

尺について「14〜17分の目標を外れる」という警告1件あり。完成尺は上限より11.042秒長い。カタログ生成、3値、候補と実行の時点差、保存範囲、3問の確認を通常速度で保持する編集判断とし、速度変更や不自然な無音で調整していない。

生成前に全86枚の静止画を作成し、18ビュー・冒頭全員表示・図の赤枠・3問の質問と回答を確認した。[確認コンタクトシート](qa-previews/manifest.json)を保持。ディスク節約のため、確認済みの出力側一時PNGのみ解放済み。完成MP4から[22枚の代表フレーム](final-frames/manifest.json)を抽出し、3人の名前と役割、話者中央寄せ、代表図、固定表、字幕、正解の回答時点、ロゴ終了を確認した。

## 書き出しと再現

マシン全体の空き容量が開始時約1.82GiBから合成中に約116MiBまで変動し、途中出力が2度停止した。共有ファイルや他回の一時領域は変更・削除していない。原音と完成済み音声ミックスを保持し、第16回の確認済み一時PNGだけを解放、`--reuse-audio true`で完成した。

[回専用ラッパー](build-low-disk.mjs)は、プロファイルの共有build-lesson.mjs SHA-256一致と一意な変換箇所を検査し、共有描画・音声処理・字幕分配・タイムライン・カメラ・符号化条件を保持する。PNGの一括ディスク保持をlossless image2pipeへ変更し、ナレーション確定後に回専用の再生成可能なscene WAVを解放する。共有ファイルは変更しない。最終の[書き出しログ](../../output/lesson-16-gemini/build.log)は正常完了。

再作成の順はauthor-script.py→finish-script.py→render-diagrams.mjs→静止画レビュー→status=ready-for-generation→共通prepare/generate→回専用build→共通verify。生成後のvisual/字幕のみの変更では、制作手順どおり設定を更新し検証済み原音を結び直す。本文・声・styleを変える場合は既存IDの原音を消さず、新IDで再録する。

整理後の映像再書き出し（保持原音からWAVを再合成。音声API送信なし）：

```sh
node docs/study/video-sample/episodes/16/build-low-disk.mjs --script episodes/16/script.json --generation-config episodes/16/generation.json --cast podcast-cast-gemini.json --output lesson-16-gemini
```

共通計画、README、キャスト、素材、レンダラー、他の回に変更なし。生成・ビルド・プレビューのロックは正常終了後に解放済み。


## 完成後のデータ整理

完成状態：完成。データ整理状態：**整理済み**（2026-11-02T17:00:59.733436+09:00）。[CONTENT-PLAN.mdの追加手順](../../../CONTENT-PLAN.md#完成後のデータ整理)を適用した。

完成記録・採用MP4・書き出し記録・整理前の検証合格・連続プレビューの再生完了・生成完了・最終ビルド正常終了を照合し、制作・修正・再書き出し・検証が進行中または再開待ちでないことを確認した。ロックはなく、削除直前にも再確認した。

対象は第16回の出力直下の `lesson-narration.wav`（98,980,078 bytes）と `lesson-mix.wav`（197,960,078 bytes）の2ファイルだけ。合計 **296,940,156 bytes（約283.18 MiB）** を解放した。観測した空き容量の増加は293,195,776 bytes。共有ディスクの空き容量には他処理の変動も含み得るため、削除したファイルの論理容量と分けて記録する。

削除前に[整理計画・削除一覧と保持対象ハッシュ](cleanup-plan.json)を保存し、実行直前に対象のサイズ・mtime・SHA-256が未変更であることを確認した。別版・別回・再制作比較の元データとして必要とする参照は見つからず、第16回内にversions/revisions/historyもない。出力画像はポスターのみだったため残した。episodes/16内のQA画像・SVG・ツールも制作ソースとして保持した。

削除後に**保持対象382ファイルの存在・SHA-256一致**を確認。完成MP4、クロージング、ポスター、ページ、字幕、台本、タイムライン、書き出し・検証記録、85本の生成原音と対応メタデータ、要求・生成・試行記録、audio-cache.json、設定・図・共通素材は保持した。制作記録はこの追記のため意図的に更新し、更新後ハッシュを[整理結果](cleanup-result.json)に記録した。MP4のSHA-256は整理前後で同じ。

**完成動画の再生・保存は利用可能。再生ページの音声単独プレーヤーは、合成済みWAVの整理に伴い利用できない。** `--reuse-audio true` とWAVを必要とする完全検証も、WAVの復元までは実行できない。整理前のverification.jsonはそのまま保持し、整理後に完全検証を再実行したとは扱わない。

復元は、上記の回専用ビルダーを **`--reuse-audio true`を付けず** に実行する。保持した原音・設定・共通素材からWAVと映像を再合成でき、新しい音声API送信は不要。その後、必要に応じて標準verify-lesson.pyで再検証する。生成原音を消したり、音声APIへ再送して作り直したりしない。

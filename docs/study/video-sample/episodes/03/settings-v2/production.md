# 第3回 teaching-settings-v2 再制作

2026-11-02（JST）。完成。全編の標準検証・独立検証・目視QA・3本の連続再生確認に合格。最新 CONTENT-PLAN.md の第3回「NSGによる通信制御」、11〜19節と対応する公開IaCを参照する。

## 最新基準との差と対応

旧図中心版は設定メニューを入口にしておらず、質問と回答で図の構図が変わる場面があった。前回のAzure Portal模倣案も、ブランドバー・自動ズーム・パン・疑似ポインターを含み、最新版の採用基準と異なるため今回の完成版には用いない。

共有settings-visual.mjs と renderSettingsShell を使った教材専用設定ビューへ全面更新する。本編は固定カメラ。同じ問いから回答・補足まで共通viewIdと構図を保ち、赤枠だけ説明対象へ切り替える。確認問題は回答前に正答を強調しない。設定と必要な経路図は同じ画面に併記する。冒頭のホログラムと話者カメラは共通仕様を維持する。

独立レビューで現行119台詞の本文は最新到達点に適合すると確認した。実際の設定値を詳しく解説し、尺延長を許容するユーザー指定を引き継ぐ。speech・caption・delivery・台詞順・章・話速を保ち、検証済み原音と既存ミックスを再利用する。追加TTS送信は行わない。

旧版は episodes/03/versions/diagram-v1 と output/lesson-03-gemini/versions/diagram-v1 に保存した。共有ファイル・素材・別の回は編集しない。shared-before.jsonに今回の開始時ハッシュを記録した。

## 確認予定

全編の標準自動検証に加え、元音声・字幕・尺の一致、全会話のviewIdと構図保持、設定値・図・赤枠を確認する。完成動画から40〜70秒の連続した質問・回答を抽出し、通常速度でプレビューする。音声の聴感確認は利用可能ツールの制約により未実施として分離する。

## 画面の実装と保持単位

本編115台詞を31個の連続したviewIdへ整理した。69台詞は共有のnative settings、46台詞はrenderSettingsShellで設定と補助図を併記する固定SVG。全36 SVGは静的・自己完結で、文字・列見出し・図の包含領域を確認した。全グループの台詞対応は[view-map.json](view-map.json)、再生成元は[render-diagrams.mjs](render-diagrams.mjs)。

| viewId | 保持する会話 | 設定と補助図の対応 |
| --- | --- | --- |
| six-subnets | 008〜014 | 6区画のCIDRと4つのNSG関連付けを同じ表で確認 |
| two-directions | 015〜018 | 同じ接続を送信側と受信側の両端で図示 |
| priority-order | 027〜034 | 小さい優先度から最初の一致、独自4096と既定65000以降、区画別の送受信を併記 |
| agw-apim-http | 035〜039 | AppGW送信100とAPIM受信100を左右に並べ、新規TCP80の矢印を保持 |
| pe-service-rules | 059〜063・117 | サービス別PE実IP群、送信元、ポートとVM送信を対応付ける |
| stateful-connection | 064〜071 | 同じ接続への応答と逆向きの新規接続、変更後の新規接続での検証を同じ図で説明 |
| probe-priority / probe-empty-client | 099〜102 | Web100 Allowと監視194 Deny、監視194 AllowとWeb4080 Denyを区別 |
| quiz-vm-apim | 106〜108 | 設問中は両端の実規則一覧のみ。108から両4096を赤枠 |
| quiz-stateful | 109〜110 | 設問中は許可済み接続と応答の前提だけ。110から応答と変更後検証を赤枠 |
| quiz-overlap | 111〜112 | 設問中は100/194の条件を無枠で表示し、112から最初に一致する100を赤枠 |

設定値は資料11〜19節と公開params/config・関連IaCの定義を再照合した。管理VM送信120の送信元は区画全体ではなくmanagementPrivateIp（管理VM実IP）として表示する。PEの実IP、管理VM単体IP、APIM実VIP、Firewall公開IP、ACA実内部IPは実出力で確認する役割名で表示し、推測の数値を補っていない。稼働Azureの画面・実測値として扱わない。

## 音声の再利用と実行

独立レビューで元119台詞の技術内容と最新版の到達点の一致を確認した。旧版とのspeech・caption・delivery・話者・章・台詞順などの非visualデータは完全一致。prepare --replace後、検証済みキャッシュを再紐付けし、119件すべてreusedRaw=true、attemptedThisRun=0で完了。認証取得や追加のTTS送信は発生していない。既存audio-cacheの署名・WAVハッシュを検証して--reuse-audio trueでビルドする。

```sh
node docs/study/video-sample/episodes/03/settings-v2/render-diagrams.mjs
node docs/study/video-sample/build-lesson.mjs --script episodes/03/script.json --output lesson-03-gemini --preview true
python3 docs/study/video-sample/prepare-gemini-lesson.py --script episodes/03/script.json --config episodes/03/generation.json --output lesson-03-gemini --replace
python3 docs/study/video-sample/generate-gemini-lesson.py --config episodes/03/generation.json --workers 1
node docs/study/video-sample/build-lesson.mjs --script episodes/03/script.json --generation-config episodes/03/generation.json --cast podcast-cast-gemini.json --output lesson-03-gemini --reuse-audio true
python3 docs/study/video-sample/verify-lesson.py --script episodes/03/script.json --generation-config episodes/03/generation.json --cast podcast-cast-gemini.json --output lesson-03-gemini
python3 docs/study/video-sample/episodes/03/settings-v2/verify-settings.py
```

最終標準プレビュー120枚を生成した。最終レビューで確認1のVM送信120の送信元表記だけを修正したため、106〜108の3枚を同じ共有renderLessonRasterから再描画して最新値へ更新した。全編は修正後の凍結ソースから合成する。

## 完成ファイルと検証

完成尺は24分39.542秒（1479.541666667秒）、35,509フレーム、1600×900・24fps、H.264/yuv420p・48kHzステレオAAC。全119台詞、字幕129cue、最大2行。MAKO=Aoede、MANABU=Charon、IKUMI=Orus、Gemini 3.8 Flash TTS、全員1.0倍、速度・ピッチ変換なし。共通ロゴ4秒、冒頭ホログラムの話者カメラ、BGM、終了8秒を維持した。

- [完成MP4](../../../output/lesson-03-gemini/egakumi-study-03.mp4) / 71,697,361 bytes
- [再生ページ](../../../output/lesson-03-gemini/index.html) / 一時ローカルURL：[http://127.0.0.1:53719/](http://127.0.0.1:53719/)
- [書き出し記録](../../../output/lesson-03-gemini/lesson-export.json)、[タイムライン](../../../output/lesson-03-gemini/lesson-timeline.json)、[字幕](../../../output/lesson-03-gemini/lesson-captions.vtt)
- MP4 SHA-256：`d2b5416a56f50672635c15625388661e1449e634638f909fa74c3be3ee42c45e`

[標準検証](../verification.json)は合格。全編と共通closingの映像・音声デコード、35,509フレーム、全原音/リクエストの証跡、通常速度、全字幕、冒頭カメラ5区間、音楽・クロージングを検査した。音量は−16.01 LUFS / −1.5 dBTP。唯一の警告は14〜17分の標準目安より長い点であり、具体設定値を詳しく説明し尺延長を許容するユーザー指定に基づき維持した。

[改修専用検証](../../../output/lesson-03-gemini/settings-v2-qa/revision-verification.json)も合格。全115本編scene.visualが最新原稿と完全一致。native69場面は全visualデータ一致、SVG46場面は設定と実SVGのSHA・データURI復号バイトも一致した。同じviewIdで設定内容・行順・図・縮尺を固定し、赤枠だけを変更している。共有67ファイル・素材は今回の開始時から変更なし。

[圧縮音声の比較](../../../output/lesson-03-gemini/settings-v2-qa/encoded-audio-comparison.json)では、元版と新版のAACパケット本体のSHA-256が一致し、音声のstart_pts=0、duration_ts=71018000、time_base=1/48000も一致した。元のWAVミックス、ナレーション、119原音も完全一致。音声パケットSHA-256：`7fa2fd7d92d96c7fe4ed20acbb6264ee7c191915fd445048a8d8a3de8a12e776`。[字幕/台本の比較](../../../output/lesson-03-gemini/settings-v2-qa/caption-comparison.json)でもVTTと公開transcriptは元版から変更なし。

[目視QA](../../../output/lesson-03-gemini/settings-v2-qa/visual-review.json)は合格。完成MP4から35枚と3contactsheet、原寸7枚を確認した。3人の顔・名前・役割、CIDR、PE宛先、077の全ポート、gRPCの公開8443/内部443、VM22、Broker176/175/177、確認問題、まとめ、closingに重大な切れ・重なりはない。聴感による発音・会話の自然さの確認は未実施であり、自動検証や画面確認とは区別する。

## 連続した質問・回答の動画

3本とも元MP4の連続フレーム範囲から、途中のカット・速度変更なしで抽出した。各clipの全編デコード・フレーム数・音声時刻は共通ツールで検証済み。静止画の寄せ集めは用いていない。

| 内容 | 台詞 | 元動画の時刻範囲（秒） | 尺 | 再生 |
| --- | --- | --- | --- | --- |
| 通常APIの両端 | 035〜039 | 364.750〜419.291667 | 54.541667秒 | [HTML](../../../output/lesson-03-gemini/conversation-preview.html) / [MP4](../../../output/lesson-03-gemini/conversation-preview.mp4) |
| 応答と逆向き新規接続 | 064〜068 | 749.291667〜799.250 | 49.958333秒 | [HTML](../../../output/lesson-03-gemini/settings-v2-qa/conversation-state/conversation-preview.html) / [MP4](../../../output/lesson-03-gemini/settings-v2-qa/conversation-state/conversation-preview.mp4) |
| 確認1の質問から回答 | 106〜108 | 1324.916667〜1365.250 | 40.333333秒 | [HTML](../../../output/lesson-03-gemini/settings-v2-qa/conversation-quiz/conversation-preview.html) / [MP4](../../../output/lesson-03-gemini/settings-v2-qa/conversation-quiz/conversation-preview.mp4) |

[連続安定性の検証](../../../output/lesson-03-gemini/settings-v2-qa/continuous-stability.json)では1秒間隔145サンプルで本文位置の維持を照合し、4秒間隔の順序画像でも全区間を確認した。話者・字幕chunkで構図をリセットせず、確認1は108の回答からだけ正答の赤枠を表示する。ブラウザーによる実時間の連続再生は別記録へ保存する。

```sh
python3 docs/study/video-sample/make-conversation-preview.py --episode 3 --from-turn lesson03-turn-035-mako --through-turn lesson03-turn-039-manabu --replace
python3 -u docs/study/video-sample/episodes/03/serve-preview.py
```

出力ファイルが成果物の正本。localhostは一時閲覧用であり、空きポート・127.0.0.1限定で公開する。最終再生ページには55秒の問答プレビューと、制作条件内に残り2本の確認動画へのリンクを追加した。

[ブラウザー確認](../../../output/lesson-03-gemini/settings-v2-qa/browser-verification.json)も合格。全編は1600×900・通常速度1.0・エラーなし、全尺でseek可能。18分13秒のチャプターから18分14秒台へ実際に移動し、通常再生が進むことを確認した。3本の連続clipも途中のシークや速度変更なしで先頭から終端まで再生し、ended=trueを確認した。最後に全編ページへ戻し、先頭・一時停止・55秒プレビューへのリンクを表示して、ユーザー向けタブとして残した。聴感検査はこのブラウザー確認には含めない。

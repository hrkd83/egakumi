# 勉強会動画の制作ツール

[コンテンツ計画](../CONTENT-PLAN.md)と回番号を指定して、15分前後の勉強会動画を制作します。実行手順の正本は[PRODUCTION-GUIDE.md](PRODUCTION-GUIDE.md)、共通設定は[production-profile.json](production-profile.json)です。

## 採用仕様

| 項目 | 指定 |
| --- | --- |
| 音声モデル | Google Cloud `gemini-3.8-flash-tts`、全員通常速度（1.0倍） |
| MAKO | Moderator / Aoede / 読みはマコ |
| MANABU | Product Manager / Charon / 読みはマナブ |
| IKUMI | Solution Architect / Orus / 読みはイクミ |
| 映像 | 1600×900、24fps、H.264 / AAC |
| 冒頭 | 共通ロゴ4秒、3人のホログラム、その回の話題に沿った掛け合い |
| 本編 | 教材用設定ビュー、固定カメラ、説明対象の赤枠と補助図 |
| 音楽・終了 | 共通BGM、最後はegakumiロゴ8秒のクロージング |

声・役割・話し方は[現行キャスト](podcast-cast-gemini.json)、会話・カメラ・音楽は[Podcast形式](PODCAST-FORMAT.md)、本編の構図は[設定ビュー仕様](SETTINGS-VIEW.md)と[書式見本](settings-view-examples.json)を使います。共通素材は`assets/`に置いています。

## 制作の流れ

以下はリポジトリルートで実行する第2回の例です。担当回の番号・パスへ置き換えます。既存の回を再開するときは、担当回の`production.md`と生成記録を読み、既存ファイルを上書きして初期化しません。

### 1. 準備とひな型作成

Node.js、Python 3、FFmpeg、Google Cloud CLIを使います。新規worktreeでは[初回準備](PRODUCTION-GUIDE.md#worktreeの初回準備)に従って依存関係を導入します。同じworktreeで制作が動いている間は`npm ci`を実行しません。

```sh
npm ci --prefix docs/study/video-sample --include=optional --no-audit --no-fund
python3 docs/study/video-sample/check-production.py --episode 2
python3 docs/study/video-sample/new-episode.py --episode 2
```

入力は`episodes/02/`、出力は`output/lesson-02-gemini/`へ分けます。並列タスクでは担当回だけを変更します。

### 2. 台本・字幕・設定ビューを執筆する

コンテンツ計画の到達点と対応資料から、担当回の台本・図を執筆します。MAKOが質問し、MANABUが目的と判断、IKUMIが技術設計と実装を答えます。質問から回答まで同じ`viewId`・構図・縮尺を保ちます。

```sh
node docs/study/video-sample/build-lesson.mjs --script episodes/02/script.json --output lesson-02-gemini --preview true
```

ひな型は未完成です。TODOを埋め、字幕・図・赤枠・出典を確認してから、台本の`status`を`ready-for-generation`にします。

### 3. 音声を生成する

```sh
python3 docs/study/video-sample/prepare-gemini-lesson.py --script episodes/02/script.json --config episodes/02/generation.json --output lesson-02-gemini
python3 docs/study/video-sample/generate-gemini-lesson.py --config episodes/02/generation.json --workers 1 --dry-run
python3 docs/study/video-sample/generate-gemini-lesson.py --config episodes/02/generation.json --workers 1
```

最後のコマンドだけが音声APIへ送信します。既存のgcloudユーザー認証を使い、APIキーや認証情報をファイルへ保存しません。成功済み原音の再利用・再録・中断からの再開は[音声生成手順](PRODUCTION-GUIDE.md#3-音声を生成する)に従います。

### 4. 映像を合成・検証・確認する

```sh
node docs/study/video-sample/build-lesson.mjs --script episodes/02/script.json --generation-config episodes/02/generation.json --cast podcast-cast-gemini.json --output lesson-02-gemini
python3 docs/study/video-sample/verify-lesson.py --script episodes/02/script.json --generation-config episodes/02/generation.json --cast podcast-cast-gemini.json --output lesson-02-gemini
python3 -u docs/study/video-sample/serve-episode.py --episode 2
```

検証結果は担当回の`verification.json`へ保存し、完成尺・出典・音声・検証結果・完成MP4のSHA-256・再生先を`production.md`に記録します。再生サーバーは空きポートを選ぶため、表示されたURLを使います。完成MP4が成果物の正本です。

[連続プレビュー作成ツール](make-conversation-preview.py)で、質問の先頭から回答・反応の末尾まで40〜70秒程度を切り出して確認します。指定方法は[設定ビューの確認と引き継ぎ](SETTINGS-VIEW.md#確認と引き継ぎ)を参照します。通常速度の連続した会話で、画面保持・台詞と赤枠の対応・字幕を確認します。

### 5. 完成後に整理する

書き出し・検証と連続会話の確認を終えたら、[完成後のデータ整理](../CONTENT-PLAN.md#完成後のデータ整理)に従います。完成動画、生成原音・証跡、台本・図・共通素材、再制作が参照する元データを残し、不要な確認用画像と再合成できるWAVだけを整理します。制作途中・中断中・状態不明の回には触れません。

WAV整理後の音声単独再生・再編集・完全検証には、保持した原音からの再合成が必要です。整理内容と復元方法も担当回の制作記録に残します。

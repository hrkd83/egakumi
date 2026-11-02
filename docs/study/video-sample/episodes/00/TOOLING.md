# 導入回の制作コマンド

共有ツールは第1〜23回・A1〜A4を制作IDとして検査するため、導入回だけ回専用の入口を使用する。共有プログラム、制作プロファイル、キャストは変更しない。

`intro_runtime.py` は、正規の `episodes/00/script.json`、整数の `episodeNumber: 0`、`episodeKey: "00"`、`lesson-00-gemini` の組合せだけを許可する。本文・完成状態・声・通常速度・ハッシュ・認証・生成記録・ロック・既知失敗だけの再送などの検査は共有実装をそのまま使用する。連続プレビューも同じ検査・書出し処理を使い、回番号0の解決だけを追加する。

以下は egakumi-core のルートから実行する。最初に共有準備チェックを行い、原稿と静止画を確認後に `ready-for-generation` とする。

```sh
python3 docs/study/video-sample/check-production.py
node docs/study/video-sample/build-lesson.mjs --script episodes/00/script.json --output lesson-00-gemini --preview true
python3 docs/study/video-sample/episodes/00/prepare-intro.py --script episodes/00/script.json --config episodes/00/generation.json --output lesson-00-gemini
python3 docs/study/video-sample/episodes/00/generate-intro.py --config episodes/00/generation.json --workers 1 --dry-run
python3 docs/study/video-sample/episodes/00/generate-intro.py --config episodes/00/generation.json --workers 1
node docs/study/video-sample/build-lesson.mjs --script episodes/00/script.json --generation-config episodes/00/generation.json --cast podcast-cast-gemini.json --output lesson-00-gemini
python3 docs/study/video-sample/verify-lesson.py --script episodes/00/script.json --generation-config episodes/00/generation.json --cast podcast-cast-gemini.json --output lesson-00-gemini
python3 docs/study/video-sample/episodes/00/preview-intro.py --episode 0 --from-turn lesson00-turn-NNN-mako --through-turn lesson00-turn-NNN-ikumi
python3 -u docs/study/video-sample/episodes/00/serve-intro.py
```

最後から2番目の台詞IDは、完成した質問と回答の実際のIDへ置き換える。本文・声・styleが同じ音声を再利用する場合は、共有制作手順と同じく prepare に `--replace` を付けて設定を更新し、generate で原音との結び直しを行う。共有ビルダーと全編検証は回番号0に対応済みのためラッパーを使用しない。

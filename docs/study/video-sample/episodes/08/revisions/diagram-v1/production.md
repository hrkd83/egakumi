# 第8回 基盤管理者の接続と作業実行 — 制作記録

制作日：2026-11-02（JST）。**完成MP4の制作・機械検証・画面確認を完了。音声内容の聴感確認は未実施。**

## 完成動画と再生

- [完成MP4](../../output/lesson-08-gemini/egakumi-study-08.mp4)：16分39.708秒（約16分40秒）、47,916,376 bytes（約45.7 MiB）。
- [チャプター付き再生ページ](http://127.0.0.1:52168/)：ローカルサーバー稼働中のみ利用可能。動画・字幕・台本の保存リンクを用意。
- [再生ページのファイル](../../output/lesson-08-gemini/index.html)、[WebVTT字幕](../../output/lesson-08-gemini/lesson-captions.vtt)、[全台本](../../output/lesson-08-gemini/lesson-transcript.md)、[タイムライン](../../output/lesson-08-gemini/lesson-timeline.json)、[書き出し情報](../../output/lesson-08-gemini/lesson-export.json)。

成果物の正本はMP4ファイル。再生サーバーは127.0.0.1の空きポートを選び、byte rangeに対応する。再起動時はポートが変わる。

```sh
python3 -u docs/study/video-sample/episodes/08/serve.py
```

## 到達点と参照範囲

管理VMへのprivate接続、NSG、SSH認証、Managed Run Commandの分担を説明できること。主出典は `01-network-control.html` の第41〜43節（`maintenance-network` / `maintenance-auth` / `maintenance-command`）。第45節の管理・外向き経路を補助参照とする。教材・リポジトリ定義・公開設定例に基づく説明で、稼働Azureの取得値・ログインや配備の実測結果ではない。

9章86台詞、読み上げ本文5,872字・表示本文5,626字、字幕167cue。到達経路→受信NSG→OS待受とSSH認証を段階ごとに追い、初期ユーザーの公開鍵とEntra SSH、配備主体の管理操作、VMの下流利用権限を区別する。API受付とVM内の実行成功・成果物も分ける。確認問題3問で回収し、推奨受講順に従って必修A1「ハブ＆スポーク接続と往復経路」へつなぐ。

章・台詞・図・出典の対応は [brief.md](brief.md)、全参照は [script.json](script.json) の `sourceReferences` と各章の `sourceRefs` に記録。図は第8回専用のdiagram/cards/quiz/summaryを明示し、第1回の既定図を使わない。

## 音声・映像仕様

Gemini 3.8 Flash TTS / MAKO=Aoede（Moderator）、MANABU=Charon（Product Manager）、IKUMI=Orus（Solution Architect）。**全員通常速度1.0倍、生成後の速度・ピッチ変更なし。** 読み用カタカナと表示名・技術用語を分離し、演技と文脈の指示はstyleへ渡した。

1600×900、24fps、H.264 / AACステレオ48kHz、23,993フレーム。冒頭ロゴ4秒→3人の引きと約34秒の掛け合い→図を主役とする本編→まとめ・A1予告→共通ロゴ8秒。既存の肖像・ロゴ・BGMを再利用した。本編main-bgmは2秒クロスフェードと会話中のダッキング、終了はbrand-theme 63.8〜71.8秒を先頭1秒・末尾2秒フェードで使用。

最終ミックスは **−15.96 LUFS / True Peak −1.5 dBTP**。

## 生成と使用量

既存Google Cloudプロジェクト `egakumi-example` と指定gcloudユーザー認証を使用。新規プロジェクト・APIキー・IAM設定は作成せず、認証トークンは成果物に保存しない。

初回の音声生成実行は、自動承認レビューが台本外部送信の明示許可不足を理由にコマンド実行前に拒否した。その後ユーザーが、台本86台詞を指定Google Cloud TTSへ送信し、完成動画まで制作を続けることを明示的に許可した。

実送信86件はすべてHTTP 200で成功。原音合計955.000秒、冒頭4台詞34.200秒。API返却使用量はprompt **30,534** / audio candidates **23,875** / total **54,409 tokens**。自動再試行0、失敗・結果不明0。実請求額は取得していない。[使用量記録](../../output/lesson-08-gemini/speech-usage-summary.json)、[実送信時の生成記録](../../output/lesson-08-gemini/generation-before-metadata.json)、[現在の生成記録](../../output/lesson-08-gemini/generation.json)を保存した。

## 検証結果

[完成動画の検証](verification.json)：**passed、warnings=[]**。送信本文とstyle、原音/設定ハッシュ、通常速度、167字幕cue、話者と役割、5カメラ区間、BGM、共通8秒クロージング、全編映像・音声の完全デコード、尺・フレーム数・音量が合格。

- [準備検査](preproduction-verification.json)：共通ファイル・素材のハッシュ、必要ツール、sharpを確認。
- [技術レビュー](source-review.md)：教材41〜43節・実装・Microsoft公式資料との整合、到達点とA1への案内を確認。
- [プレビューレビュー](visual-review.md)：図33種類と3人の引きを目視。カード幅、字幕の孤立した句点・行頭読点・語中分断を修正し、全87枚更新後に修正字幕7枚と関連カード4枚を原寸再確認。
- [完成フレームのレビュー](final-visual-review.md)：完成MP4から抽出した14フレームで顔・名前・英語役割、話者の中央強調、代表図・字幕・ロゴ終了を確認。
- [原音の機械監査](audio-review.md)：86件の証跡・ハッシュ・24kHz mono PCM16を照合。空/全無音/重複波形0件。内部低振幅2件と話者内の文字数対尺3件を注意候補として記録した。[監査JSON](../../output/lesson-08-gemini/audio-audit.json)。
- [HTTP納品検査](delivery-check.json)：MP4 HEAD、先頭/末尾Rangeの206と実バイト一致、範囲外416、VTT/台本と保存リンク、サイズとSHAが合格。[納品メモ](delivery-notes.md)。
- [ブラウザー再生検査](browser-check.json)：1600×900・999.708333秒・再生速度1・readyState=4・media errorなし。冒頭、中盤9分、終盤16分31秒のチャプター移動を実操作で確認。冒頭で停止し、再生タブを納品用に保持した。

**人名・用語の発音、読み上げの欠落、演技指示の発声、会話の自然さの聴感確認は未実施。** この環境には利用可能と確認できる音声認識モデルがなく、機械検査・波形検査・画面確認と聴感を区別する。完成動画で試聴可能。

## メタデータ調整と合成時の記録

最初の完成検証で、scopeが「取得・実測した結果ではない」と記述され、検証器が要求する「取得値」の文字列を含まないため停止した。scopeだけを「稼働環境の取得値・実測結果ではない」へ明示し、prepare --replace後に86件の検証済み原音を再利用して生成記録を結び直した。追加API送信0件、認証取得なし。全turn・音声候補・style・字幕・図・タイミングが完全一致することを比較し、レンダリングに使わないscopeだけの変更なので既存MP4を保持した。MP4のハッシュも不変。[変更差分と結び直しの証跡](metadata-rebind.json)。その後、標準検証を全編実行し合格した。

合成中は別回の書き出しで共有ディスクの空きが減り、第8回自身のレンダラーをSIGSTOP/SIGCONTで待機・再開した。第7回の完了後に同じプロセスで書き出しを完了し、標準ビルダーが第8回の一時フレームを解放した。他回のプロセス・ロック・一時ファイルは変更・削除していない。

編集範囲は `episodes/08/` と `output/lesson-08-gemini/` のみ。共有計画、README、キャスト、プロファイル、素材、レンダラーは変更していない。

## ハッシュ

- MP4 SHA-256：`6cbfb3874a7e4028a4df73c83dc848ef83c3ea18beeda5b670bdab5dba9c3c3e`
- 最終script.json SHA-256：`f0d67bc7d330648a466604aeefaab850b19411bc4dc3dcbc5b3c0c11f6dc4540`
- 最終generation.json（入力設定）SHA-256：`9aea6e190b8944982a6ff8e3c48f9c552425c423f2e8e72e55cf3e3c1ea376cd`

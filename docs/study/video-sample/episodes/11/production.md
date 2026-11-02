# 第11回 APIMの認証と入力検査

制作日：2026-11-02（JST）。状態：完成MP4書き出し・標準検証合格。完成尺 **15分49.458秒**。

- [完成MP4](../../output/lesson-11-gemini/egakumi-study-11.mp4)
- [保存済み再生ページ](../../output/lesson-11-gemini/index.html) / [起動中のローカル再生ページ](http://127.0.0.1:58645/)
- [47.125秒の連続プレビュー](../../output/lesson-11-gemini/conversation-preview.mp4) / [プレビュー再生ページ](http://127.0.0.1:58645/conversation-preview.html)
- [字幕WebVTT](../../output/lesson-11-gemini/lesson-captions.vtt) / [公開台本](../../output/lesson-11-gemini/lesson-transcript.md)
- [検証結果](verification.json) / [API返却使用量](../../output/lesson-11-gemini/speech-usage-summary.json)

ローカルURLは一時的な閲覧用。出力ファイルを成果物の正本とする。

到達点は、APIMサブスクリプションキー、Operation・model・入力profileの契約、ポリシー実行順を、一つの要求について説明できること。

## 根拠と範囲

- [教材12節：継承と実行順](../../../02-external-protection.html#apim-scope)
- [教材13節：入口認証](../../../02-external-protection.html#apim-authentication)
- [教材14節：入力契約](../../../02-external-protection.html#apim-contract)
- [教材15節：入力検査](../../../02-external-protection.html#apim-input)
- リポジトリの `apps/gateway/apim/apis/openai-inference-v1.json`、`policies/service.xml`、`gateway.xml`、`pii.xml`、`speech-ssml-guard.xml`、`document-analysis.xml`、`infra`配備定義と照合した。

公開設定例とリポジトリ定義に基づく教材。稼働Azureから設定を取得・検証した結果ではない。キーはダミー表記であり、実値を画面や成果物へ保存しない。

通常RESTのChat Completionsを主例にする。APIの利用範囲と入力契約、既定chatとvision、schema登録と実行時検査、共通入力検査と専用ガードを区別する。PIIの固有検査前に計数・認証準備・acceptedが済む場合と、SSMLガードがbase前にある例外を扱う。利用枠の数値、SQLプロファイル取得、トークン制限、MI／CMの詳細は第12回へ残す。

初回94台詞の発話は1008.68秒。約15分へ整理するため、入口経路の詳細（021〜024）、固定転送先（041〜043）、ストリーム検査境界（060〜062）の補足を問答単位で省略し、本編を84台詞にした。省略した発話は102.44秒。これらの内容は参照資料と元の台本・音源に保持する。つなぎの025／044／063は直前文脈styleが変わるためr2の新IDで再録し、他81台詞は本文・声・styleの一致を確認して再利用した。最初の台本・生成設定・実行結果は出力先の`initial-recording/`に保存した。

## 台本・画面

[台本](script.json)は第11回専用に執筆。3人の新しい掛け合いから始め、キー・入力契約・入力ガード・実行順・3問の確認・まとめへ進む。

本編は `teaching-settings-v2`。設定表と補助図を用い、固定カメラ。同じviewIdでは表・構図・縮尺を保ち、話者・字幕chunkで本文画面をリセットしない。赤枠は説明対象へ合わせ、確認問題の質問時には出さない。確認問題のnoticeも中立な文面にする。

- 入口認証：gates / standard / key-format / principal
- 入力契約：definition / chat / vision / match
- 専用入力検査：schema / pii / other-guards / waf
- 実行順：`lesson11-policy-order`はturn063〜066と071〜074をそれぞれ連続保持。赤枠のみ説明段階へ移す。
- 継承：`lesson11-inheritance`はturn067〜070と075〜077をそれぞれ連続保持。
- 確認問題：q1 / q2 / q3の質問から回答まで同じ表を保持し、回答時に候補Bを強調する。

補助図は[再生成スクリプト](render-diagrams.mjs)と`diagrams/`へ保存。通常RESTの6段階を示す固定図と、Operation→API→Serviceのbase継承・復帰図を同じ教材用設定ビューへ組み込む。

技術レビューで全94台詞と出典を照合済み。画面レビューでは22viewの代表・赤枠付き全体画面・3人のホログラムを確認。英語の識別名の改行、確認問題の注記、比較説明の赤枠を修正する。

## 音声・共通演出

Google Cloud `gemini-3.8-flash-tts`、MAKO=Aoede / MANABU=Charon / IKUMI=Orus。全員通常速度1.0、生成後の速度・ピッチ変換なし。既存のユーザー認証を使用し、トークンを成果物へ保存しない。workers=1。

共通ロゴ4秒、冒頭ホログラムの滑らかな話者カメラ、既存肖像、本編BGMの2秒クロスフェード・ダッキング、共通8秒クロージングを共有ビルダーで合成する。終曲はbrand-themeの63.8〜71.8秒。

## 検証と再生

標準 `verify-lesson.py` は **passed / warningsなし**。成功した出力を `verification.json` へ保存した。

| 項目 | 検証結果 |
| --- | --- |
| 映像 | 1600×900 / 24fps / H.264 yuv420p |
| 全編 | 949.458333秒 / 22,787フレーム。音声・映像全編デコード合格 |
| 台詞・字幕 | 本編84台詞 / 字幕188cue。原音・要求・本文・設定・字幕の整合合格 |
| 音声 | AACステレオ48kHz / 通常速度1.0 / 速度・ピッチ変更なし |
| 最終ミックス | −15.99 LUFS / True Peak −1.5 dBTP |
| 冒頭 | 共通ロゴ4秒、3人の顔・名前・役割、話者中央へ滑らかに移動。5カメラ区間の検証合格 |
| 本編 | 固定カメラ、同一問答で同一viewId・構図を保持。質問中の正解赤枠なし |
| BGM・終了 | 2秒クロスフェード・ダッキング、ブランド曲63.8〜71.8秒、共通8秒終了 |

完成MP4から冒頭、話者中央寄せ、入力契約、実行順、確認問題、終了ロゴの6フレームを抽出し、目視確認した（出力先`final-qa/`）。

連続プレビューは **10:53.583〜11:40.708（47.125秒 / 1,131フレーム）**。turn071の問いからturn074の確認までを途中カットなし・速度変更なしで抽出した。原音とBGMは連続。`lesson11-policy-order`を保持し、赤枠は「先行する利用枠・認証準備」→「業務呼出し」→「固有検査」の説明対象に合わせる。プレビューの全編デコード・フレーム数・通常速度検査も合格した。ブラウザーでは完成MP4を通常速度で再生し、duration=949.458333 / playbackRate=1 / 1600×900 / media errorなしを確認した。

連続プレビューはブラウザーで0秒から31.955秒の回答中、最後の47.125秒まで再生進行を確認。最後はended=true / playbackRate=1 / media errorなし。途中でシークや速度変更を行わず、質問から回答・補足まで同じ本文図を保持した。完成動画の再生ページを開いた状態で引き渡す。

MP4 SHA-256：`5a52779efb3dfdbe6c940fbb24631a7076ce2c18a7c1b2b2623f4342b9274faa`

生成は初回94台詞と文脈変更3台詞の合計97件、すべてHTTP 200。全生成のAPI返却使用量は入力32,410・音声出力25,843・合計58,253トークン。本編採用84台詞は入力28,127・音声出力22,711・合計50,838トークン。選択音声の実長908.44秒。非採用音源・要求・履歴は保持する。実請求額は未確認。

利用可能なツールでは音声内容の聴取・認識ができないため、発音、言葉の欠落、演技指示の不発声、会話の自然さの聴感確認は未実施として扱う。機械検証やブラウザー再生の成功を、聴感検査の代わりとは記録しない。

## 再生成

リポジトリルートで実行する。

```sh
node docs/study/video-sample/episodes/11/render-diagrams.mjs
python3 docs/study/video-sample/prepare-gemini-lesson.py --script episodes/11/script.json --config episodes/11/generation.json --output lesson-11-gemini
python3 docs/study/video-sample/generate-gemini-lesson.py --config episodes/11/generation.json --workers 1
node docs/study/video-sample/build-lesson.mjs --script episodes/11/script.json --generation-config episodes/11/generation.json --cast podcast-cast-gemini.json --output lesson-11-gemini
python3 docs/study/video-sample/verify-lesson.py --script episodes/11/script.json --generation-config episodes/11/generation.json --cast podcast-cast-gemini.json --output lesson-11-gemini
python3 -u docs/study/video-sample/serve-episode.py --episode 11
```

表示だけを変更した場合は、生成中・合成中でないことを確認し、`prepare ... --replace`後に同じgenerateコマンドで検証済み原音を再利用する。本文・声・styleを変えて再録する場合は、新しい台詞IDと履歴を使う。

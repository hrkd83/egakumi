# 第14回 誰が何を管理できるのか

完成：2026-11-02T15:46:42+09:00（JST）。**完成動画・字幕・再生ページを制作済み。**

- 完成尺：16分03.7秒（963.708333333秒）
- 映像：1600×900、24fps、H.264 / AAC、23,129フレーム
- 80台詞、字幕155区間、全員通常速度1.0。音声の速度・ピッチ変更なし。
- [完成MP4](../../output/lesson-14-gemini/egakumi-study-14.mp4)
- [再生ページ](../../output/lesson-14-gemini/index.html) / [一時再生URL](http://127.0.0.1:60327/)
- [字幕WebVTT](../../output/lesson-14-gemini/lesson-captions.vtt) / [対話原稿](../../output/lesson-14-gemini/lesson-transcript.md)
- [連続した問答55.625秒](../../output/lesson-14-gemini/conversation-preview.html)

localhostのURLは実行中サーバーの一時的な閲覧先。成果物の正本は上記MP4と回別の出力ファイル。

## 到達点と出典

CONTENT-PLAN.md第14回の到達点：Portal・SQL・APIMの分担、本人認証、所有グループ、会社と費用帰属を区別できる。

元資料は `../../03-subscription-management.html` のpurpose・signin・company。関連Portal実装はscript.jsonのsourceReferencesと[内容レビュー](content-review.md)に記録した。稼働Azureから取得した画面・実測結果ではなく、リポジトリ定義に基づく教材。A社と分析チームは架空例。

8章・80台詞、表示原稿5,526字。本人→現在所属→所有と管理対象→会社への費用帰属を、一つのキーに結び付ける。API発行の詳細、キー管理、組織観測・同期、Credential Brokerの本人接続は後続回に残した。独立レビューで重大な事実相違・範囲逸脱なし。

## 映像と画面保持

本編はteaching-settings-v2、17種類の固定ビュー。PortalからSQL/APIMへの役割分担図と、本人→Graph現在所属→SQLの所有・承認・状態の関連図を回専用に制作した。

質問から回答までviewId・構図・縮尺を保持し、話者交代や字幕chunkで本文をリセットしない。自動ズーム・パン・pulse・模擬ポインターはない。冒頭のホログラムでは3人の位置を保ち、約0.85秒で話者へカメラを移す。

画面保持の代表単位はrolesの006〜012、loginの018〜024、identity-flowの034〜039、caseの062〜068。確認問題3問は質問069/072/075に赤枠を出さず、回答070/073/076から対象項目を強調する。

共通の4秒ロゴ、既存肖像、ブランド音楽、本編BGMと2秒クロスフェード・ダッキング、8秒クロージングを使用。クロージングのブランド曲は63.8〜71.8秒。

## 音声生成と承認

Gemini 3.8 Flash TTS（gemini-3.8-flash-tts）、Google Cloudの既存プロジェクトegakumi-example、global、指定済みgcloudアカウントを使用。MAKO=Aoede / MANABU=Charon / IKUMI=Orus、全員通常速度。

最初の拡張権限実行は、台本の外部送信と従量課金に関する明示承認が必要として自動承認レビューに拒否された。ユーザーへ送信先・本文80台詞・従量課金を示し、「承認して完成動画まで進める」との明示承認を取得。同じ指定コマンドを承認付きで実行し、80件すべてHTTP 200で完了した。再送・追加生成は0件。認証情報はメモリーで扱い、本文・設定・ログ・成果物へ保存していない。

原音合計924.04秒。API報告使用量：promptTokenCount=26,691、candidatesTokenCount=23,101、totalTokenCount=49,792。API応答に金額がないため金額は未算定。

最終画面確認でturn016のHTTP、turn026のMicrosoftの語内改行を整えた。本文・声・style・原音ハッシュを保ち、prepare --replace → generateによる原音の検証済み再利用（attemptedThisRun=0）→ build --reuse-audio trueで最終版を再合成した。最終台本のstatusは生成証跡のハッシュを維持するためready-for-generationのまま、完成状態は本記録で管理する。

## 検証結果

[最終verification.json](verification.json)はpassed、warningsなし。本文・設定・送信証跡・原音ハッシュ、選定声、通常速度、字幕、冒頭カメラ、BGM、共通クロージング、全編の映像・音声デコード、フレーム数・長さを確認した。

- 最終ミックス：-15.99 LUFS、True Peak -1.5 dBTP。
- [原音の機械QA](audio-audit.json)：80件の形式・生成証跡は構造合格。無音・長さ外れフラグ0。PCMの微小ピーク到達の要聴取フラグ35件、連続near-full-scale区間の最長0.375ms。原音ピークの品質を後段の音量調整で修復済みとは扱わない。
- [事前検査](preproduction-verification.json)：出典・字幕内容・最大2行・固定カメラ・同一view構造保持・質問時の赤枠なしに合格。
- [静止画レビュー](visual-review.md)：全17本文viewと冒頭・質問回答を確認。turn044の台詞と赤枠のずれを発見し、不要な枠を外して解消。
- [最終映像レビュー](final-visual-review.md)：完成MP4から33枚を抽出し、3人の顔・名前・役割、話者中央寄せ、代表表・2図、確認問題、ロゴ終了を目視。英語の語内改行2か所も最終版で解消。

発音、内容の欠落、会話の自然さ、可聴歪みの聴感確認は未実施。利用可能なローカルASRには実用モデルがなく、新たな外部音声送信やモデル取得はしていない。機械検査と画面の目視確認を、聴感評価と同一視しない。

## 連続した質問・回答の確認

最終MP4の386.375〜442.000秒（6:26.375〜7:22.000）、turn034 MAKOからturn038 IKUMIまで、55.625秒・1,335フレームを切り出した。元時刻範囲を飛ばさず、内部カット・速度変更・ピッチ変更なし。追加音声生成なし。プレビューの全編デコードもpassed。

同じlesson14-identity-flow画面で、本人からGraph現在所属を経由してSQLの所有・承認・状態へたどる問答を保持する。画面・図は固定で、字幕と話者表示が切り替わる。ブラウザー上でもplaybackRate=1で開始から終了55.625秒までの連続再生を確認した。画面保持の確認と、未実施の音声聴感を分けて記録する。

[conversation-preview.json](../../output/lesson-14-gemini/conversation-preview.json)に元MP4ハッシュ・範囲・抽出条件、[playback-review.json](playback-review.json)に再生確認結果を保存した。

## 再生成と再生

リポジトリルートで実行する。台本・声・styleを変えない映像修正では検証済み音源の再利用を維持する。

```sh
node docs/study/video-sample/build-lesson.mjs --script episodes/14/script.json --generation-config episodes/14/generation.json --cast podcast-cast-gemini.json --output lesson-14-gemini --reuse-audio true
python3 docs/study/video-sample/verify-lesson.py --script episodes/14/script.json --generation-config episodes/14/generation.json --cast podcast-cast-gemini.json --output lesson-14-gemini
python3 -u docs/study/video-sample/serve-episode.py --episode 14
```

再生サーバーは127.0.0.1の空きポートを選ぶ。既存サーバーを止めず、他回・共有計画・キャスト・素材・レンダラーは変更していない。

完成MP4 SHA-256：`453c85f91462895b6aa73061eab977d01789d429b2254015420195d45b983da9`

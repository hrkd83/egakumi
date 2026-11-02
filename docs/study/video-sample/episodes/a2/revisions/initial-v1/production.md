# A2 ハブ接続後のDNS・通信制御と切替 — 制作記録

制作日：2026-11-02（JST）。状態：完成。

## 到達点と範囲

中央DNS・Private DNS・NSG／Firewallの許可・出口IPを整合させ、段階切替、新規接続での検証、切戻し、IaCへの反映と再配備の管理を説明できること。元資料は01ネットワークの制御50・51・52・54節。主例は中央DNSへの切替から、公開宛て通信の検証、次回配備の管理まで。

A1の往復経路を土台に、名前解決、許可、送信元、運用・宣言の管理元を結び付ける。教材・現行リポジトリ定義に基づく接続案として説明し、Azure稼働環境への変更適用・実測は行わない。

## 共通仕様・準備

[事前検査](preproduction-verification.json)：passed。必要ツール・sharp・共通ファイルと素材のハッシュを確認。空き容量1.82 GiB。担当回episodes/a2/およびoutput/lesson-a2-gemini/のみ変更し、他回・共有資源を変更しない。

Gemini 3.8 Flash TTS、MAKO=Aoede / Moderator、MANABU=Charon / Product Manager、IKUMI=Orus / Solution Architect。全員通常速度1.0倍、生成後の速度・ピッチ変換なし。既存Google Cloudプロジェクトegakumi-exampleと指定gcloud認証を使い、トークンを成果物へ保存しない。

1600×900、24fps、H.264 / AAC。既存肖像、冒頭ロゴ4秒、本編BGMの2秒クロスフェードとダッキング、ブランド曲63.8〜71.8秒による8秒クロージングを使用。

## 再生サーバー

A1で動作確認済みのbyte range対応サーバーをA2専用[serve.py](serve.py)へ適用。共通プログラムとA1のサーバーは変更しない。127.0.0.1の空きポートを自動選択する。

再起動：リポジトリルートから `python3 -u docs/study/video-sample/episodes/a2/serve.py`。

## 台本・図・レビュー

82台詞・11章、表示本文5,290字、読み上げ5,757字、冒頭4台詞225字。37種類のvisual指定を今回の内容に合わせて執筆した。全台詞を採用し、次回は計画どおり第9回WAFへつなぐ。

| 章 | 元資料 | 代表図・説明 |
| --- | --- | --- |
| 今回の問い・完了条件 | 50〜52・54節 | 名前・経路・許可・管理元をそろえる |
| 二つのDNS設定 | 50節 | VNetとFW自身、未指定と[]、Proxy、TCP/UDP53、反映 |
| 8ゾーンと管理元 | 51節 | PE6＋APIM/ACA2、Resolver転送とCNAME連鎖、参照・所有 |
| 許可と実送信元 | 52節 | 実効NSG、VirtualNetworkタグ、内部入口、Network先行評価 |
| 出口IPの照合 | 52節 | Network/ApplicationのSNAT、observer/期待値、Portalの公開呼出し |
| 保存・準備・段階切替 | 54節＋50節 | 現状保存・責任、切替前に中央DNSへ直接照会、対象範囲ごとの反映 |
| 新しい接続で検証 | 54節 | DNS・経路・NSG/FW・SNAT、管理系、中央のログ/通知担当 |
| 切戻しと再配備 | 54節 | Coreの変更範囲、既存経路保持と同名ルート再適用、中央ゾーンと撤去 |
| 確認3問・まとめ | 50〜52・54節 | DNS空配列、実送信元、手動変更と次回up、次回第9回 |

[技術レビュー](source-review.md)：教材・IaCとMicrosoft公式5ページを照合、合格。DNS問い合わせとAPI要求を別通信として明記し、Resolverへの転送とCNAME連鎖も別画面へ分けた。DNSはVNet/FW設定として扱い、切戻しカードの対象を「区画」から「範囲」へ修正。切替前の直接DNS照会を追加した。

字幕QAは全文連結一致、最大2行、全角換算最大38.72字、issues/warningsとも0。83枚のpreviewを生成し、最終修正した4枚（050/062/063/065）は同じ共有renderLessonRasterで差分更新した。共有レンダラーや設定は変更していない。

[映像プレビューQA](visual-review.md)：37種類のvisualとcastを全種類確認、合格。DNS転送、6ゾーン、長い設定名、直接照会追加、確認問題を1600×900でも確認。はみ出し・欠け・衝突なし。

レビュー後はstatusのみready-for-generationへ変更。prepare/dry-run合格、82リクエスト、Aoede/Charon/Orus、speed=1。指定の既存Google認証による実行は自動承認レビューを通過した。新しい認証・プロジェクト・IAM設定は作成していない。

## 音声生成結果

82/82件がHTTP 200で成功、送信82回、自動再試行0。原音合計930.800秒。モデル報告使用量はprompt 28,710 / audio candidates 23,270 / total 51,980 tokens。請求額の取得は行っていない。通常速度・生成後速度/ピッチ変換なし・認証情報未保存を生成証跡に記録した。

[原音の機械検査](../../output/lesson-a2-gemini/audio-audit.json)：82件すべてを検査、破損・空/無音・長すぎる頭尾/内部無音・極端な文字数対尺の候補なし。25原音に短い振幅飽和候補がある。これは聴感で歪みを判断したものではなく、元波形は変更・再生成していない。最終合成で指定の音量・True Peakを検証する。

利用可能なツールで音声内容を聴取・認識できないため、人名・用語の発音、本文欠落、演技指示の発声、会話の自然さについて聴感確認は未実施。送信本文・style・音声/設定ハッシュの照合と波形検査を聴感確認と区別する。

全内容を通常速度で収録した実長から、ロゴ・区切り・自然な短い間を含む完成尺は16分16.375秒となる。15分前後という目安に対し、DNS/ゾーン管理/通信許可/切替と再配備の到達点を保ち、尺合わせの加速や長い無音の挿入は行っていない。

ディスク余裕確保のため、音声ミックス完了後にA2自身のrender-work内の使用済み一時WAV 95件（89.4 MiB）を削除した。原音、完成narration/mix、生成証跡は保持。合成の後段は完成mixとPNGを参照し、他回の出力は変更していない。

## 完成成果物・検証

完成尺 **16分16.375秒**（976.375秒、23,433フレーム）。11章、82台詞すべて採用、字幕118区間。1600×900 / 24fps / H.264 yuv420p / AAC 48kHz stereo。

[自動検証](verification.json)：**passed、warnings 0**。全編の映像・音声デコード、音源・送信本文・style・設定ハッシュ、採用声と通常速度、字幕連結と時刻、3人へのカメラ移動、BGM・共通クロージングを確認。音量 **−16.0 LUFS、True Peak −1.5 dBTP**。原音サンプル数を保持し、台詞末尾を切り落としていない。

合成終了後、共有ビルダーがA2自身のrender-workを削除。原音・生成証跡・完成WAV・MP4・字幕・台本・timeline・export・indexは保持した。他回の出力・ロック・サーバーは変更していない。

- [完成MP4](../../output/lesson-a2-gemini/egakumi-study-a2.mp4)
- [再生ページ](../../output/lesson-a2-gemini/index.html)
- [WebVTT字幕](../../output/lesson-a2-gemini/lesson-captions.vtt)
- [表示台本](../../output/lesson-a2-gemini/lesson-transcript.md)
- [実長から確定したtimeline](../../output/lesson-a2-gemini/lesson-timeline.json)
- [export情報](../../output/lesson-a2-gemini/lesson-export.json)
- [完成MP4から抽出した42代表フレーム](../../output/lesson-a2-gemini/final-review/manifest.json)

MP4 SHA-256：`58795f0b4254eae84cb8384ff9f18cf2399ab4dde6d77d0bc18c0b772cd2a12d`。

一時再生URL：<http://127.0.0.1:55155/>。ブラウザーでduration=976.375秒、readyState=4、errorなし、seekable全区間0〜976.375秒を確認。09:06「保存・準備・段階切替」へ移動し、再生が進み、停止できることを確認。A2タブを成果物として残した。localhostサーバーは一時的な閲覧用で、MP4が成果物の正本。

再生ページの「動画を保存」からMP4を保存できる。サーバー終了後もMP4を直接再生可能。再開は `python3 -u docs/study/video-sample/episodes/a2/serve.py`（空きポートを自動選択）。

MP4ファイルサイズ：48,081,640 bytes（45.85 MiB）。

完成MP4の[視認QA](visual-review.md)も合格。42代表フレームと4コンタクトシートで、3人の顔・英字名・役割、話者の中央寄せ、全論点図と字幕、直接DNS照会カードを確認し、最終プレビューとの一致を確認した。クロージング968.375〜976.375秒の8秒は、直前・開始1.125秒後・最終フレームも抽出し、区切りと終端フェードを確認。未解決の技術・表示指摘なし。聴感確認は上記のとおり未実施。

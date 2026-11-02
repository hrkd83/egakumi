# A1 ハブ＆スポーク接続と往復経路 — 制作記録

制作日：2026-11-02（JST）。状態：完成。

## 到達点と範囲

private接続・出口集約・Gateway共有の採用範囲を選び、双方向ピアリング、区画別UDR、復路、Gateway経路伝搬を説明できること。対応資料は01ネットワークの制御47・48・49・53節、補助資料はdocs/setup/07-hub-spoke.mdとinfra/modules/network.bicep。

主例はAPIMの公開宛て通信をハブFWへ集約する際の往復。独立VNetの6区画、AppGWとAPIM・ACAのローカルFW経由、PE直接接続を維持する設計を扱う。中央DNS切替・通信制御・検証・切戻し・再配備の詳細は次回A2へつなぐ。Azure稼働環境への変更適用や実測は実施しない。

## 共通仕様・準備

[事前検査](preproduction-verification.json)：passed。必要ツール・sharp・共通ファイルと素材のハッシュを確認。準備時の空き容量3.69 GiB。担当回のソースと出力のみ変更し、他回のロック・出力・共通プログラムを変更しない。

Gemini 3.8 Flash TTS、MAKO=Aoede / Moderator、MANABU=Charon / Product Manager、IKUMI=Orus / Solution Architect。全員通常速度1.0倍、生成後の速度・ピッチ変換なし。既存のGoogle Cloudプロジェクトegakumi-exampleと指定gcloud認証を使い、トークンを成果物へ保存しない。

1600×900、24fps、H.264 / AAC。既存の肖像・4秒ロゴ・本編BGMの2秒クロスフェードとダッキング・ブランド曲63.8〜71.8秒の8秒クロージングを使用する。

## 再生方法

成果物はoutput/lesson-a1-gemini/へ書き出す。共有serve-episode.pyのbyte range未対応によるチャプター移動への影響を避け、実績のある第6回のサーバーをA1専用[serve.py](serve.py)へ適用。127.0.0.1の空きポートを自動選択する。

再起動：リポジトリルートから `python3 -u docs/study/video-sample/episodes/a1/serve.py`。

## 台本・図・レビュー

82台詞・10章。表示本文5,266字、読み上げ5,782字、冒頭4台詞229字（読み上げ）。今回専用の図・カード・確認問題で構成し、第1回の図や台詞は複製しない。

| 章 | 元資料 | 主に示す内容 |
| --- | --- | --- |
| 今回の問い・共有範囲 | 47節 | private接続・出口集約・中央DNS・Gateway共有を個別に採用 |
| 双方向ピアリングと転送 | 48節 | CIDR重複、両側Connected、受け入れる方向の転送許可、非推移性 |
| APIM公開宛て | 49節 | 0/0をハブFWへ、SNATと復路、具体的なPE経路との違い |
| 入口の往復 | 49節 | AppGW↔ローカルFW↔APIM/ACA、/32、AppGW区画への復路、管理直接経路 |
| 区画別の変更 | 49節 | 管理VM・ACAの依存先、保守元への復路、PEとFW区画 |
| Gateway共有 | 48節 | hub allowGatewayTransit / spoke useRemoteGateways、共有制約と社内復路 |
| Gateway経路伝搬 | 53節 | 利用者設定とARM属性の反転、ACA固定、AppGW既定経路、経路優先順位 |
| 確認3問・まとめ | 47〜49・53節 | 接続と経路、二つの往復、伝搬停止の範囲、次回A2へ |

[技術レビュー](source-review.md)：教材・IaCとMicrosoft公式を照合、合格。Gateway図の復路をCore→Gateway→社内に分け、ApiManagementサービスタグ表記を明記。公開backendとの接続とAppGWとの接続を本文でも区別した。

[映像プレビュー](visual-review.md)：83枚を生成、29種類のvisual指定（冒頭1＋本編28）を確認。転送矢印ラベルの隠れと字幕の助詞切れを修正。字幕は全文連結一致、最大2行、全角換算最大39字、QA issues/warningsとも0。3人の顔・名前・役割、往復矢印、設定真偽のカードを確認済み。

レビュー後は台本statusのみready-for-generationへ更新し、本文・図は固定した。prepare/dry-run合格、82リクエスト、Aoede/Charon/Orus、speed=1。指定の既存Google認証を利用する音声生成を開始。認証・ネットワーク実行は自動承認レビューを通過した。

## 音声生成結果

82/82件がHTTP 200で成功、送信82回、自動再試行0。原音合計902.960秒、モデル報告使用量はprompt 28,598 / audio candidates 22,574 / total 51,172 tokens。請求額の取得は行っていない。通常速度・生成後速度/ピッチ変換なし・認証情報未保存を生成証跡に記録した。

[原音の機械検査](../../output/lesson-a1-gemini/audio-audit.json)：82件すべてを検査、破損・空/無音・長すぎる頭尾/内部無音・極端な文字数対尺の候補なし。32原音に短い振幅飽和候補がある。これは発音/歪みの聴感判定ではなく、元波形は変更・再生成していない。最終合成で指定の音量・True Peakを検証する。

この環境では音声内容を聴取・認識するツールを利用できないため、人名・用語の発音、本文の欠落、演技指示の発声、会話の自然さについての聴感確認は未実施。送信本文・style・音声/設定ハッシュの照合と波形検査は聴感確認と分けて扱う。

## 完成成果物・検証

完成尺 **15分47.417秒**（947.416667秒、22,738フレーム）。10章、82台詞すべて採用、字幕98区間。1600×900 / 24fps / H.264 yuv420p / AAC 48kHz stereo。

[自動検証](verification.json)：**passed、warnings 0**。全編の映像・音声デコード、音源・送信本文・style・設定ハッシュ、採用声と通常速度、字幕連結・時刻、3人へのカメラ移動、BGM・共通クロージングを確認。音量 **−16.0 LUFS、True Peak −1.5 dBTP**。原音サンプル数を保持し、台詞末尾を切り落としていない。

合成完了後、共有ビルダーがA1自身のrender-workを削除。原音と生成証跡、完成WAV、MP4、字幕、台本、timeline、export、indexを保持した。他回の一時ファイルやロックには触れていない。

- [完成MP4](../../output/lesson-a1-gemini/egakumi-study-a1.mp4)
- [再生ページ](../../output/lesson-a1-gemini/index.html)
- [WebVTT字幕](../../output/lesson-a1-gemini/lesson-captions.vtt)
- [表示台本](../../output/lesson-a1-gemini/lesson-transcript.md)
- [実長から確定したtimeline](../../output/lesson-a1-gemini/lesson-timeline.json)
- [export情報](../../output/lesson-a1-gemini/lesson-export.json)
- [完成MP4から抽出した34代表フレーム](../../output/lesson-a1-gemini/final-review/manifest.json)

MP4 SHA-256：`4e394027101b28efd05cc681615646498f455cf53a07f9749fe1e31c9c2bdf71`。

一時再生URL：<http://127.0.0.1:51707/>。ブラウザーで動画長947.416667秒、readyState=4、エラーなし、seekable全区間0〜947.416667秒を確認。06:07「入口の往復を維持する」へ移動して再生が進み、停止できることも確認。A1再生タブを成果物として残した。localhostサーバーは一時的な閲覧用で、MP4が成果物の正本。

再生ページの「動画を保存」からMP4を保存できる。サーバー終了後もMP4を直接再生可能。再開は `python3 -u docs/study/video-sample/episodes/a1/serve.py`（空きポートを自動選択）。

MP4ファイルサイズ：46,930,980 bytes。

完成MP4の[視認QA](visual-review.md)も合格。34代表フレームと3コンタクトシートで、3人の引きと顔・英字名・肩書き、各話者の中央寄せ、全論点図と字幕、Gateway復路、ロゴ8秒を確認した。クロージング直前と最終フレームも抽出し、区切りと終端フェードを確認。最終プレビューとの不一致・修正前図への戻りなし。未解決の技術・表示指摘なし。聴感確認は上記のとおり未実施。

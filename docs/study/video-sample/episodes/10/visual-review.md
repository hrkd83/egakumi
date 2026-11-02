# 第10回 静止画の表示レビュー

2026-11-02（JST）。対象は `script.json` と共有ビルダーの `output/lesson-10-gemini/previews/`。

初回静止画レビュー時の台本SHA-256：`7cabe95d59e0ad8fe9fa13d570e806b83d0296fd05b2f7673f9fd6bd6d502740`。最終版は末尾の「最終改行修正の再確認」を参照。

## 確認対象と方法

- 3人の引き1枚、90台詞の先頭字幕フレーム90枚、計91枚を確認した。
- 全91枚を2列×3行のcontact sheet16枚へまとめ、`output/lesson-10-gemini/qa/all-frames-01.png`〜`all-frames-16.png`へ保存した。
- 14個の本編viewIdすべて、冒頭の3話者、確認問題3問の質問・回答・補足を確認した。
- 字幕203chunkのうち、表示幅の大きい12chunkを追加レンダリングし、`qa/long-caption-*.png`と`qa/long-sheets-01.png`・`long-sheets-02.png`を確認した。最大幅の例はturn048の第3chunk「企業で独自範囲を変える場合も、経路と署名の適用を合わせて確認します。」。
- 語中改行の指摘後、最終版のturn057・058、次回案内の表示変更を再確認した。全contact sheetを最終プレビューから更新した。
- `qa/static-qa-manifest.json`に対象台本のハッシュと確認用ファイル数を記録した。

## 結果

静止画の表示は合格。教材の文字・図・字幕に、領域外への切れ、要素同士の重なり、判読を妨げる縮小を認めなかった。長い字幕も所定の下部領域内に収まり、最大2行を維持する。

冒頭の引きではMAKO / Moderator、MANABU / Product Manager、IKUMI / Solution Architectが肖像と対応して表示される。各話者のフレームでも話者本人が中央へ移った状態となり、字幕欄の名前・役割も一致する。本編は右側の話者強調だけが交代し、設定・図の構図は保たれる。

本編は淡色の教材専用設定ビューと、同じ共有shell内の補助図を使用する。Azureのブランドバーや検索欄を追加していない。定義・公開例である旨の注記が見え、稼働環境の取得画面として表示していない。

## 設定・図と発話の対応

| viewId | 台詞 | 確認した対応 |
| --- | --- | --- |
| decision-roles | 005〜010 | 接続条件、署名照合、下流結果の3項目。説明する値へ赤枠が切り替わる。 |
| environment-modes | 011〜016 | 本番Premium/Alert、テストStandard/Off、署名上書き、Threat Intelligenceの比較。 |
| idps-modes | 017〜028 | 上段Alertの並列2枝と、下段Denyのinlineを同じ図で保持。下段は現行の配備状態ではないと明示。 |
| signature-actions | 029〜034 | 署名の特徴、既定Alert Only、上書きなし、適用条件。 |
| coverage-paths | 035〜042 | 「上段」HTTP80、「中段」Portal HTTPS443、「下段」PE直接経路が3行の図と一致。 |
| direction | 043〜048 | 上2段のInbound/Outbound、下段private同士Internal、空配列と既定RFC1918の注記が一致。 |
| idps-exceptions | 049〜054 | 署名動作の変更と通信bypassの違いを同じ項目構成で説明。 |
| related-settings | 055〜060 | IDPS private範囲、SNAT private範囲、DNS proxy、Threat Intelligenceの役割。 |
| request-walkthrough | 061〜066 | 同一通常API要求の経路、通信規則、現行IDPS Alert、APIM以降の確認先を保持。 |
| log-evidence | 067〜072 | 通信規則とIDPSログ、照合情報、Alertと通知の違いを対象項目へ対応。 |
| quiz-alert-deny | 073〜076 | 質問073は赤枠なし。回答074から候補Bを強調し、補足でも同じ表。 |
| quiz-tls-path | 077〜080 | 質問077は赤枠なし。回答078から候補Bを強調し、同じ構図を保持。 |
| quiz-direction | 081〜084 | 質問081は赤枠なし。回答082から候補B、補足083はprivate範囲の条件も強調。 |
| lesson10-summary | 085〜090 | 判断、検査範囲、証拠、第11回の利用契約への問いを同じ設定欄で保持。 |

確認問題では正解候補の色・枠を質問時点に先取りしていない。解答文は他の候補と同じ書式で提示し、回答の発話から赤枠を出す。

補助図では「二つの枝」「上段」「中段」「下段」「private同士」といった発話に対応する対象が画面に存在する。図と設定表を問答の途中で往復させていない。図は固定で赤枠pulseや模擬ポインターを含まない。

## 修正と残る確認

初回確認時にturn057のNetwork、turn058のThreat Intelligenceで語中改行を検出した。rootが字幕の改行位置を変更し、最終プレビューで語を分断しない表示へ改善されたことを再確認した。音声本文の変更を要しない表示修正である。

この記録は静止画と台本による表示・意味の確認であり、完成動画の40〜70秒連続再生、音声の発音・聴感、全編デコード、音量測定を済ませた記録ではない。それらは完成後の検証記録と制作記録へ別途残す。

## 最終改行修正の再確認

音声生成後にrootが行った字幕表示のみの修正を確認した。最終台本SHA-256は `abdfe1ad917e58cac48f6a9691c27a7611445a3d10f44400cb3176ba5c3cc6c0`。台詞90、字幕204chunk、最大2行・各行最大34文字。

全204chunkの行境界を再走査し、Latin英数字の語中改行は0件だった。音声本文・style・原音を変更する修正ではない。

次の4フレームを最終台本から共通`renderLessonRaster()`で直接レンダリングし、1600×900の画像で再確認した。文字切れ・重なりはなく、Bicep、snatPrivateRanges、Network、AppGWの語を分断しない表示で合格。

| 確認画像（`output/lesson-10-gemini/qa/`） | 確認内容 |
| --- | --- |
| final-caption-lesson10-turn-014-ikumi-chunk3.png | Bicepを語中で折らず、29文字ずつ2行。 |
| final-caption-lesson10-turn-056-ikumi-chunk2.png | IDPSの方向分類を20文字1行で表示。 |
| final-caption-lesson10-turn-056-ikumi-chunk3.png | snatPrivateRangesとNetworkを保持し、28文字・12文字の2行。 |
| final-caption-lesson10-turn-061-mako-chunk2.png | AppGWを保持し、15文字・23文字の2行。 |

`qa/final-caption-qa-manifest.json`が最終台本ハッシュと該当画像・字幕の対応記録である。`previews/`の91枚と`qa/all-frames-*`は初回静止画レビューの証跡であり、上記3台詞の最新字幕については`qa/final-caption-*`の4画像を正本とする。フルビルド実行中のscript、共有ファイル、render-workは変更していない。

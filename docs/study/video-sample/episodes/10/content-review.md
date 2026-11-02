# 第10回 台本・画面の独立内容QA

確認日: 2026-11-02 JST

## 第一次レビュー

対象: `script.json`、90台詞、読み上げ 5999 字。
対象SHA-256: `5b5fe5689921f30f74dbb7b211646f21113903322e5506cdd6835161fb1aa9dc`。
この時点ではauthorが重複削減中であり、最終確定稿は再確認する。

### 結果

重大な技術的誤りは検出しなかった。`source-review.md` に記したHTML9〜11節、関連IaC、Microsoft一次資料の条件と一致する。

- 本番Premium/Alert、テストStandard/OffとIDPS property省略を正しく区別。
- Alertの並列処理、Denyのinline処理、規則Allow後のIDPS drop、RSTなしの説明は妥当。
- 署名ごとの有効モード、一部Alert Only、signatureOverrides空配列の意味を保持。
- HTTP80の通常API、TLS非復号のPortal/gRPC/HTTPS外向き、PE直接経路を区別。
- RFC1918既定とInternal分類、利用者のInternet上の居場所とFWの観測区間の違いを説明。
- private範囲/SNAT、署名override/通信bypass、Threat Intelligence/IDPS、ログ/通知を区別。
- 代表要求の結果は仮定として説明し、Denyを稼働環境へ適用した結果とはしていない。

### 字幕・図・問答の整合

- 全90台詞のsubtitleChunksを連結したcaptionは、元captionと一致。各chunkは最大2行、lines連結もcaptionと一致。
- 発話と表示の差は製品名・略語の読み替え等で、意味の欠落は検出しなかった。TCP RSTと読み上げのTCPリセットも同義。
- coverage-pathsの上段/中段/下段、directionの下段/上二段、idps-modesの上段/下段の言及は図の実際の位置と対応。
- 本編の問答は同じviewIdと構成を保持。確認問題73/77/81は赤枠なし、回答74/78/82から正解を強調しており、質問中の赤枠による先取りはない。
- 設定表の公開値は現行config/params例と一致。

### 改善提案

1. turn089とsummary.nextの「誰の要求」は個人本人識別の誤解を招き得る。第11回はAPIMサブスクリプションキーの認証が中心なので、「どのサブスクリプションの要求」「どの利用契約で受ける」等への変更をrootとauthorへ推奨した。
2. 既存図の「規則Denyは開かない」→「規則Denyは覆さない」は表現上の改善として先に提案済み。

機械的な整合と技術的内容の確認であり、合成後の発音・自然さ・可読性・連続映像の確認を代替しない。


## 最終稿の再確認

最終SHA-256: `5b5fe5689921f30f74dbb7b211646f21113903322e5506cdd6835161fb1aa9dc`。
`ready-for-generation`、90台詞、読み上げ5999字。短縮後の全台詞を再読し、重要な技術条件が残っていることを確認した。

turn089とsummary.nextの個人識別を連想させる表現は、サブスクリプション/利用契約へ修正済み。最終稿でも全字幕chunk・linesの連結一致、最大2行、確認問題の質問中の赤枠なしを再検査し、問題はなかった。

**内容QA合格。** 台本・字幕・図に、出典との重大な矛盾や意味の欠落は検出しなかった。音声生成と映像合成へ進める。発音・聴感・完成映像の確認は後工程として別途実施する。


## 字幕改行のみの修正

最終script SHA-256: `7cabe95d59e0ad8fe9fa13d570e806b83d0296fd05b2f7673f9fd6bd6d502740`。057と058の字幕改行を英単語の境界へ移動。読み上げ本文・表示本文・話し方は変更なし。構造QAで全chunkの本文一致と最大2行・34字を再確認した。


## 字幕の最終改行調整後

対象SHA-256: `abdfe1ad917e58cac48f6a9691c27a7611445a3d10f44400cb3176ba5c3cc6c0`。

全90台詞を生成記録と照合し、speech/sourceSpeech・captionが全件一致することを確認した。字幕は204chunkで、各chunkのcaption連結とlines連結が元captionと一致する。行末と次行頭の両方が英数字となる英単語分断候補は0件。字幕改行・turn056のchunk分割による意味の欠落は検出しなかった。本文の内容QA結論は維持する。

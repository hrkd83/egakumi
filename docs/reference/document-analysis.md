# Document Intelligence — OCR・レイアウト解析

Tool Gatewayは、既存のFoundryリソース（`AIServices`）をAPIMのマネージドIDで呼びます。専用のSQLテーブル、ストレージ、追加のEntraアプリ、カスタムロールは作成しません。Microsoftの[マネージドID手順](https://learn.microsoft.com/azure/ai-services/document-intelligence/authentication/managed-identities?view=doc-intel-4.0.0)は、Document IntelligenceリソースまたはFoundryリソースを前提にしています。

## 公開API

`POST https://<gateway-host>/tools/documents/v1/analyze?model=prebuilt-layout`

- `api-key`: このAPIを含む製品の有効な購読キー。
- `Content-Type`: `application/pdf`、`image/jpeg`、`image/png`、`image/tiff`、`image/bmp`。
- 本文: ファイルのバイナリ。JSON・ファイルURL・multipartは受け付けません。
- `Content-Length`: ファイルサイズ。本文とともに上限を検査します。
- `model`: `prebuilt-read`（文字認識）または`prebuilt-layout`（文字とレイアウト・表）。その他のクエリは受け付けません。

Gatewayのファイル上限は**1 MiB（1,048,576 bytes）**、待機時間は**最大約60秒**、上流結果の受信上限と公開応答の上限はそれぞれ**4 MiB**です。公開応答は`requestId`を追加した後のUTF-8容量も検証します。これらは対話的なGateway利用のための境界であり、Document Intelligence自体の最大処理容量を示していません。ページを暗黙に一部だけ選択することはありません。

```bash
curl 'https://<gateway-host>/tools/documents/v1/analyze?model=prebuilt-layout' \
  -H 'api-key: <YOUR_SUBSCRIPTION_KEY>' \
  -H 'Content-Type: application/pdf' \
  --data-binary '@document.pdf'
```

## 応答と処理

正常応答は `{"analyzeResult": {...}, "requestId": "..."}` です。`analyzeResult`は[Document Intelligence 2024-11-30のネイティブ解析結果](https://learn.microsoft.com/rest/api/aiservices/document-models/get-analyze-result?view=rest-aiservices-v4.0%20(2024-11-30))で、`apiVersion`・`modelId`・`content`・`pages`を検査したうえで返します。表や段落などの任意フィールドは、文書と選択モデルにより存在しない場合があります。抽出内容はHTMLとして解釈せず、文字列・構造化データとして表示してください。

上流の[非同期解析API](https://learn.microsoft.com/rest/api/aiservices/document-models/analyze-document?view=rest-aiservices-v4.0%20(2024-11-30))へ開始POSTを1回だけ送信し、APIM内でGETの完了確認を繰り返します。呼び出し元には内部のOperation-Location・ジョブID・URLを渡しません。完了確認先は、同じFoundryホスト・選択済みモデル・正しいUUIDの結果パスだけに制限します。

時間内に完了しなかった場合は`504 DocumentAnalysisTimeout`です。POSTを自動的に再送すると二重処理・追加料金が発生し得るため、GatewayもPortalも自動再送しません。新しい実行が必要かを利用者が判断してください。タイムアウトは上流処理の取消完了を意味しません。

その他の失敗は、`DocumentInvalidInput`・`DocumentInputTooLarge`・`DocumentUnsupportedMediaType`・`DocumentRejected`・`DocumentAnalysisThrottled`・`DocumentAnalysisUnavailable`などの固定コードと追跡IDで返します。共通認可・流量制限の拒否は、Gatewayの共通エラー契約に従います。入力原本・抽出本文・上流の診断本文は監査ログへ含めません。

## 配備と保護

`apps/gateway/apim/apis/document-analysis-v1.json`を標準APIとして自動読込し、`released-apis`製品へ関連付けます。既存の検証製品は`pii-users`を使います。`scripts/deploy_gateway.py`がAPI公開に続いて、文書受付の完全パスだけへ専用WAFポリシーを関連付けます。

専用WAFは本文サイズの強制・全文検査を有効に保ちます。WAFが設定上限ちょうどの本文も拒否するため、この完全パスの最大値・検査枠には1 KiBの余裕を設けて1,025 KiBとし、公開契約の1 MiB上限はAPIMで厳密に検証します。文書のバイナリ受付を許可するため、この完全パスに限りOWASP 3.2の`920420`（Content-Typeの許可リスト）を無効にします。Content-Typeの構文検査`920470`、SQLインジェクション規則、その他の共通WAF規則は維持します。受付可能なMIME型とファイル署名・サイズはAPIMの操作ポリシーで別途検証します。他のパス、Gateway全体のWAF、既存の認証・音声API用ポリシーは変更しません。

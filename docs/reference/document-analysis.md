# Document Intelligence — OCR・レイアウト解析

Tool Gatewayは、既存のFoundryリソース（`AIServices`）をAPIMのマネージドIDで呼びます。専用のSQLテーブル、ストレージ、追加のEntraアプリ、カスタムロールは作成しません。Microsoftの[マネージドID手順](https://learn.microsoft.com/azure/ai-services/document-intelligence/authentication/managed-identities?view=doc-intel-4.0.0)は、Document IntelligenceリソースまたはFoundryリソースを前提にしています。

## 公開API

`POST https://gateway.example.com/tools/documents/v1/analyze?model=prebuilt-layout`（ホストは合成例）

- `api-key`: このAPIを含む製品の有効な購読キー。
- `Content-Type`: PDF、JPEG、PNG、TIFF、BMP、HEIF／HEIC、Office Open XML（Word／Excel／PowerPoint）、HTMLの対応MIME。正確な一覧は[API定義のcontentTypes](../../apps/gateway/apim/apis/document-analysis-v1.json)を参照。
- 本文: ファイルのraw binary。Base64へ変換せず転送します。JSON・ファイルURL・multipartのGateway直送は受け付けません。
- `Content-Length`は必須にせず、実際に受信した本文のサイズを検査します。
- `model`: `prebuilt-read`（文字認識）または`prebuilt-layout`（文字とレイアウト・表）。その他のクエリは受け付けません。

Gatewayの受付は**500,000,000 bytes**、上流結果と公開応答はそれぞれ**500 MiB（524,288,000 bytes）**までです。前者はDocument Intelligence S0の500 MB、後者はAPIM Classicのバッファー上限に合わせています。提供元がF0の場合は4 MBなど、その料金プラン・モデル・形式の制約が先に適用されます。ページを暗黙に一部だけ選択しません。[Document Intelligenceの制約](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/service-limits?view=doc-intel-4.0.0)、[APIMの技術上限](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/azure-subscription-service-limits#api-management-gateway-runtime-limits)

```bash
curl 'https://gateway.example.com/tools/documents/v1/analyze?model=prebuilt-layout' \
  -H "api-key: ${GATEWAY_API_KEY}" \
  -H 'Content-Type: application/pdf' \
  --data-binary '@document.pdf'
```

## 応答と処理

開始POSTは上流の[非同期解析API](https://learn.microsoft.com/rest/api/aiservices/document-models/analyze-document?view=rest-aiservices-v4.0%20(2024-11-30))を1回だけ呼び、受付後に**202**と`operationId`・`model`・`statusPath`・`ticket`・`expiresAt`を返します。内部のFoundry URL・`Operation-Location`は公開しません。

後続は同じ購読キーと`X-Core-Document-Ticket: <受付時のticket>`で`GET /tools/documents/v1/operations/{operationId}`を呼びます。UUID形式のIDと署名、購読・API・接続先・モデル・期限を照合してから、固定の提供元へGETします。処理中は202と`Retry-After`、完了時は200と`{"analyzeResult": {...}, "requestId": "..."}`です。チケット期限は開始から24時間で、期限切れは410、結果が取得できない場合は404です。購読キーとチケットはサーバー側で保管し、ブラウザー・URL・ログへ出しません。

`analyzeResult`は[Document Intelligence 2024-11-30のネイティブ解析結果](https://learn.microsoft.com/rest/api/aiservices/document-models/get-analyze-result?view=rest-aiservices-v4.0%20(2024-11-30))で、API版・モデル・本文・ページ構造を検査して返します。表や段落などは文書と選択モデルに依存します。抽出内容をHTMLとして実行しないでください。

Portalの画面はFileをraw本文で送り、BFFは形式判別用の先頭と受信byte数だけを検査してGatewayへストリーミングします。ファイル全体をBFFメモリーへ集約せず、元ファイルを一度だけ送信し、以後は状態確認を自動実行します。旧multipart入口も互換用に残します。ブラウザーへ渡すのは本人と選択キーの台帳IDに束縛した暗号化handleです。中止・一時的な通信障害・同じタブの再読込後も状態確認を再開でき、原本や平文チケットは保存しません。画面の中止は上流ジョブの取消を意味しません。最初のPOSTの結果が不明な場合は、二重処理・追加料金を避けるため自動再送しません。

その他の失敗は、`DocumentInvalidInput`・`DocumentInputTooLarge`・`DocumentUnsupportedMediaType`・`DocumentRejected`・`DocumentAnalysisThrottled`・`DocumentAnalysisUnavailable`などの固定コードと追跡IDで返します。共通認可・流量制限の拒否は、Gatewayの共通エラー契約に従います。入力原本・抽出本文・上流の診断本文は監査ログへ含めません。

## 配備と保護

`apps/gateway/apim/apis/document-analysis-v1.json`を標準APIとして自動読込し、`released-apis`製品へ関連付けます。既存の検証製品は`pii-users`を使います。`scripts/deploy_gateway.py`がAPI公開に続いて、文書受付の完全パスだけへ専用WAFポリシーを関連付けます。

Gatewayの`/tools/documents/v1/analyze`とPortalの`/api/tools/documents`の専用WAFはPreventionと本文検査を維持し、先頭**2000 KiB**まで検査します。本文・ファイルのサイズ強制は解除し、検査枠を超える部分は検査せず受け付けます。受付MIMEの`920420`とraw本文の`930110`だけを調整し、URI・query・header・cookieのtraversalは4本のBlock規則で補完します。`920470`・`930100`などの他規則、認証・管理経路の厳格なポリシーは維持します。MIME・ファイル形式・容量はBFF・APIM・提供元でも検証します。[WAFの適用範囲](../testing/waf-compatibility.md)

通常のChat／Responsesの同期・SSE契約は変更しません。AppGWのバックエンド待機時間は既存の既定600秒・設定可能なままで、FWも変更しません。この改修のAzure反映と実経路での受入確認は未実施です。

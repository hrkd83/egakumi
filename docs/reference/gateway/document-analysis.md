# Document Intelligenceの文書解析

Tool Gatewayは、CoreのFoundryリソース（`AIServices`）にAPIMのマネージドIDで接続します。

## 公開API

`POST /tools/documents/v1/analyze?model=prebuilt-layout` には、Gateway向けのBearerまたは有効なAPIM購読キーを送ります。

- `model` は空でない1値で指定します。例は `prebuilt-read`（文字認識）と `prebuilt-layout`（文字・レイアウト・表）。モデルIDの利用可否は提供元が判定します。
- 本文は文書のraw binary、または提供元が扱う `urlSource`／`base64Source` のJSONです。入力方式に対応するContent-Typeを付けます。URLで指定した文書はDocument Intelligenceが取得します。 [API定義](../../../apps/api-gateway/apis/document-analysis-v1.json) · [提供元の解析開始API](https://learn.microsoft.com/rest/api/aiservices/document-models/analyze-document?view=rest-aiservices-v4.0%20(2024-11-30))
- APIMは `pages`、`features` などの追加クエリを開始POSTから提供元へ渡します。`model` は提供元URLのパスへ組み込みます。`api-version` は対応版に固定し、`stringIndexType` がなければ `unicodeCodePoint` を補います。
- 申告Content-Lengthが **500,000,000 bytes** を超える要求はGatewayが413を返します。提供元の応答と公開応答は、それぞれ **500 MiB（524,288,000 bytes）** が上限です。実ファイルの形式、サイズ、ページ数は提供元の契約を確認します。 [Document Intelligenceの制約](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/service-limits?view=doc-intel-4.0.0)

```bash
curl 'https://gateway.example.com/tools/documents/v1/analyze?model=prebuilt-layout' \
  -H "Authorization: Bearer ${GATEWAY_ACCESS_TOKEN}" \
  -H 'Content-Type: application/pdf' \
  --data-binary '@document.pdf'
```

JSON入力の合成例です。

```json
{"urlSource":"https://documents.example.com/document.pdf"}
```

## 応答と処理

開始POSTは提供元の受付後に202と `operationId`、`model`、`statusPath`、`ticket`、`expiresAt` を返します。Gatewayは受け取った受付URLを登録済みバックエンドと照合し、公開応答にはGatewayの照会パスを返します。

同じ認証主体で `X-Core-Document-Ticket: <TICKET>` を付けて `GET /tools/documents/v1/operations/{operationId}` を呼びます。Gatewayは署名、本人、API、バックエンド、モデル、ジョブ、期限を照合します。チケットは発行から24時間有効です。本文なしで照会し、処理中は202と `Retry-After`、完了時は200と `analyzeResult`、`requestId` を受け取ります。追加クエリは照会先へ転送しません。

対応するAPI版は `2024-11-30` です。配備時に公開Schemaの値を開始・照会の両操作へ適用します。クライアントが結果の文字位置を使う際は、選択した `stringIndexType` に沿ってspanを解釈します。 [公開Schema](../../../apps/api-gateway/schemas/document-analysis.json) · [提供元の結果取得API](https://learn.microsoft.com/rest/api/aiservices/document-models/get-analyze-result?view=rest-aiservices-v4.0%20(2024-11-30))

| 状況 | 応答 |
| --- | --- |
| modelの欠落・複数値・空値、`.`、`..` | 400 `DocumentInvalidInput` |
| チケットの欠落・不正・主体やジョブの不一致 | 403 `DocumentOperationDenied` |
| チケット期限切れ | 410 `DocumentOperationExpired` |
| 申告サイズ超過・提供元によるサイズ超過 | 413 `REQUEST_TOO_LARGE`／`DocumentInputTooLarge` |
| 提供元が返した400、415、422 | 422 `DocumentRejected` |
| Gateway内部の認証情報や操作設定の引渡し不備 | 500 `DocumentAnalysisUnavailable` |

PortalはBearerで文書原本を送る試用画面です。Read／Layoutから選択し、受付後の照会を自動で行います。ブラウザーには利用者に束縛した暗号化handleを返します。開始要求の結果が不明な場合、二重処理を避けるため、Portalは自動で開始要求を送り直しません。画面の中止後も、提供元で受付済みの解析が継続する場合があります。

## 配備と保護

Gateway個別配備が標準APIとWAFを登録します。文書受付のGateway `/tools/documents/v1/analyze` とPortal `/api/tools/documents` は、それぞれのパス専用のWAFをPreventionで使います。先頭2,000 KiBの本文を検査し、原本文書を通すためのMIMEとraw本文、URL入力に必要な指定項目だけに管理ルールの除外を適用します。URI・query・header・cookieのtraversalはカスタム規則で検査します。 [WAFの受入](../../testing/waf-compatibility.md)

業務要求の照会や拒否はGateway要求IDで照合します。監査はAPIMのメタデータと提供元の診断を使用します。 [監査の対象](../audit/request-body-audit.md) · [Documentの受入](../../testing/integration-tests.md)

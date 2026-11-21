# Document Intelligence — OCR・レイアウト解析

Tool Gatewayは、既存のFoundryリソース（`AIServices`）をAPIMのマネージドIDで呼びます。専用のSQLテーブル、ストレージ、追加のEntraアプリ、カスタムロールは作成しません。Microsoftの[マネージドID手順](https://learn.microsoft.com/azure/ai-services/document-intelligence/authentication/managed-identities?view=doc-intel-4.0.0)は、Document IntelligenceリソースまたはFoundryリソースを前提にしています。

## 公開API

`POST https://gateway.example.com/tools/documents/v1/analyze?model=prebuilt-layout`（ホストは合成例）

- 認証: `api-key`にこのAPIを含む製品の有効な購読キーを指定するか、`Authorization: Bearer <ENTRA_ACCESS_TOKEN>`で社員認証を行います。
- `Content-Type`: 提供元の入力方式に合う値を送ります。原本の代表的なMIMEは[API定義のcontentTypes](../../apps/gateway/apim/apis/document-analysis-v1.json)を参照。JSON入力には`application/json`を使います。APIMはDocumentのMIME一覧比較や、ヘッダーの欠落・複数値・空欄を確認する独自の検査を行いません。
- 本文: 原本ファイルのraw binary、または提供元のJSON入力`urlSource`／`base64Source`。APIMは本文を解析・変換せず転送します。`urlSource`の文書はDocument Intelligenceが取得します。Portalのアップロード画面は、引き続き原本ファイルを送る方式です。
- `Content-Length`は必須にしません。読み取れた申告サイズが**500,000,000 bytes**を超える場合は、共有処理が早期413で止めます。サイズ確認のためだけに本文全体を読み出す検査は行いません。
- `model`: 提供元のモデルIDを、空でない1値で指定します。URL階層を変える`.`／`..`は400にします。Gateway独自の2モデル一覧による拒否は撤去し、モデルの利用可否は提供元が判定します。`prebuilt-read`（文字認識）や`prebuilt-layout`（文字とレイアウト・表）は利用例です。
- 追加クエリ: 解析開始POSTは`pages`・`features`などを、APIM標準のURI書換えで提供元へ転送します。`model`は下流URIのモデル要素として使い、下流のクエリからは除きます。追加クエリの転送後、APIM標準のクエリ設定ポリシーでapi-versionは公開契約の対応版（現在`2024-11-30`）へ上書きします。文字位置の単位を選ぶ`stringIndexType`は、`utf16CodeUnit`などの指定値を保持し、未指定時だけ従来の`unicodeCodePoint`を補完します。`exists-action=skip`を使い、不正値・複数値の適否は提供元へ任せます。㉑では状態取得GETの追加クエリを無視して同じジョブの状態・結果を取得し、クエリの有無だけでは403にしません。チケット・UUID・本人・期限の照合と固定URI・対応版は維持します。㉒では未使用クエリを転送しない`copy-unmatched-params=false`を維持し、目的コメントを整えます。[公式Get Analyze Result（2024-11-30）](https://learn.microsoft.com/rest/api/aiservices/document-models/get-analyze-result?view=rest-aiservices-v4.0%20(2024-11-30))が使うクエリはapi-versionのみで、チケット・操作IDで確定したジョブを照会します。将来正式なGET追加クエリが必要になれば、公開結果契約と合わせて見直します。URLへのAPIキー指定の共通拒否は維持します。

Gatewayの申告サイズ上限は送信する本文全体に適用します。Base64では元ファイルより送信量が増え、復号後やURL先の文書サイズは提供元が判定します。申告サイズがない・数値化できない場合、APIM独自の実サイズ413判定はありません。上流結果と公開応答は、それぞれ**500 MiB（524,288,000 bytes）**までです。提供元の料金プラン・モデル・形式の制約は別に適用され、S0の文書上限は500 MB、F0は4 MBなどです。APIM／AppGW自身の受信・保持処理やAzureの技術制約がなくなるわけではありません。ページを暗黙に一部だけ選択しません。[Document Intelligenceの制約](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/service-limits?view=doc-intel-4.0.0)、[APIMの技術上限](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/azure-subscription-service-limits#api-management-gateway-runtime-limits)

```bash
curl 'https://gateway.example.com/tools/documents/v1/analyze?model=prebuilt-layout' \
  -H "api-key: ${GATEWAY_API_KEY}" \
  -H 'Content-Type: application/pdf' \
  --data-binary '@document.pdf'
```

提供元JSON入力の独立した合成例です。`model`は引き続きURLクエリで指定します。

```json
{"urlSource":"https://documents.example.com/document.pdf"}
```

`base64Source`では同じJSONの項目へBase64化した文書を渡します。方式ごとの入力条件は[提供元の解析開始API](https://learn.microsoft.com/rest/api/aiservices/document-models/analyze-document?view=rest-aiservices-v4.0%20(2024-11-30))に従います。APIM独自の空本文400、先頭バイトによる簡易形式判定、`Content-Encoding`による415は設けません。HTTPの受信処理と提供元が入力の適否を判定します。APIMに解凍処理は追加せず、提供元の圧縮対応は未確認です。

## 応答と処理

対応API版の正本は[公開Schema](../../apps/gateway/apim/schemas/document-analysis.json)の`DocumentAnalysisResult.analyzeResult.apiVersion.enum`の単一値で、現在は`2024-11-30`です。標準API定義の開始・状態取得に版を重複記載せず、配備前のPython正規化で両操作へ補完します。明示された版が正本と異なる場合は配備前に拒否します。同じ値をBicepから開始URI、受付URLの照合、状態GETへ渡します。⑱では結果照合が、共通APIの準備済み設定`gatewayOperationConstraint`から`apiVersion`と`maxResponseBytes`を直接読みます。Status専用の設定変数・埋込みBase64の復元を撤去し、上限・版・チケット・結果の条件は維持します。⑲ではStatusの操作設定`gatewayOperationConstraint`の取得・JSON解析失敗だけを500・`DocumentAnalysisUnavailable`へ分類し、`policy-error`・`OPERATION_SETTINGS_INVALID`・500の監査を1回記録します。提供元本文・版・容量などの不備は既存502を維持し、要求IDと`Cache-Control: no-store`も維持します。網羅的な再検査は追加しません。Portalの公開表示・結果検査も既存生成物を通じて同じ版を使います。実行時の追加読取りや再検査は行わず、公開仕様・対応版・チケット形式は維持します。

開始POSTは上流の[非同期解析API](https://learn.microsoft.com/rest/api/aiservices/document-models/analyze-document?view=rest-aiservices-v4.0%20(2024-11-30))を1回だけ呼び、受付後に**202**と`operationId`・`model`・`statusPath`・`ticket`・`expiresAt`を返します。内部のFoundry URL・`Operation-Location`は公開しません。

開始POSTは既存の`X-Core-Document-Ticket`を使わず削除して続行し、受付後に新しいチケットを発行します。状態取得GETは照合後に削除するため、削除処理を無条件に共通化しません。棚卸⑮は目的コメントの整理で、現挙動を維持します。

`documentBaseUrl`は登録済みの同じAPIバックエンドから生成し、受付URLの照合と、接続先・API・本人にチケットを束縛する処理に使います。棚卸⑯では開始・状態取得の各操作に標準`set-variable`1行ずつを保持し、現挙動を変えず用途コメントだけを整えます。

棚卸⑰では、開始時に保持していた時刻を撤去し、提供元の202受付・受付URL・本人情報を確認した後、Gatewayがチケット発行時のUTC epoch秒を一度だけ取得します。発行時刻を`iat`、期限を`exp = iat + 86400`とします。提供元の正確な受付時刻や結果保持期限を取得する処理ではありません。

後続は開始時と同じ認証主体（キー認証は同じ購読、Bearer認証は同じ社員）の認証情報と`X-Core-Document-Ticket: <受付時のticket>`で`GET /tools/documents/v1/operations/{operationId}`を呼びます。UUID形式のIDと署名、購読・API・接続先・モデル・期限を照合してから、固定の提供元へGETします。処理中は202と`Retry-After`、完了時は200と`{"analyzeResult": {...}, "requestId": "..."}`です。チケット期限はGatewayでのチケット発行から24時間です。㉛ではUTC epoch秒での時刻の目的コメントを整え、発行・検証の時計差として未来発行30秒までの許容を維持します。期限後の猶予ではありません。iat・expの不備や24時間の期間不一致は403、取得許可時点で`exp <= now`なら410です。Statusの現在時刻取得2回は、未来発行の許容確認と取得許可時点の期限確認で役割が異なります。提供元に結果がない場合の404・結果保持期限とは別で、追加検査や変数は設けません。購読キーとチケットはサーバー側で保管し、ブラウザー・URL・ログへ出しません。

棚卸⑳では、状態取得GETの`X-Core-Document-Ticket`を`TryGetValue`で一度だけ読み取り、存在とAPIMが認識した値が一つであることを確認します。既存の4096文字上限・形式・署名・本人・期限の検査を維持し、欠落・複数値・不正は403で返します。㉓では状態取得GETの操作ID（D形式GUID）とチケットの長さ・形式を別々の条件に分け、日本語コメントを整えます。条件・403・後段のジョブ照合は維持します。㉔は条件を維持するコメント整理です。4096文字は未検証チケットの処理量を抑える上限、43文字はHMAC-SHA256署名のpaddingなしBase64url固定長を示します。㉕では署名の用途を区別する専用接頭辞と、長さ・全文字で一致を確認する処理の目的コメントを整えます。処理・鍵・形式・条件・403は維持します。㉖・㉘では状態取得GETと開始outboundの`gatewayDecision.allowed`の再確認を削除します。共通認証からAPIへの引渡し不備は上位APIで500にして止め、後続で上書きしません。㉗では開始・状態取得とも`gatewayDecision.callerId`を使い、購読・Bearer成功・社員JSON・GUID整形・ID再照合の重複を削除します。キーのownerは接頭辞なしの購読ID、Bearerは`user:<tenant>:<principal>`の既存表記を維持します。認証方式の分岐、署名後のチケット復元・JSON解析と、チケットの本人・API・接続先・ジョブ照合は維持します。㉙では開始の`documentAccepted`とStatusの`documentOwner`で、内部認証結果`gatewayDecision`の取得・JSON解析、認証方式・owner引渡しの不備だけを500・`policy-error`／`AUTHENTICATION_PROCESSING_ERROR`へ分離します。署名・claims・owner不一致403、期限切れ410、受付URL・Operation ID空値502は維持します。500の公開コードは`DocumentAnalysisUnavailable`とし、要求ID・no-storeを維持して内部JSONや例外は出力しません。本人再検査・新共通部品・新結果JSONは追加せず、⑲の操作設定失敗とは分けて扱います。㉚ではStatusの`v`／`aud`／`job`／`scope`による本人・API・接続先・解析ジョブの照合と、modelの文字列・空欄・`.`／`..`のURL用確認を別のifに分け、目的コメントを整えます。判定条件・403・既発行チケット互換と、提供元の任意モデルIDを扱う方針は維持します。㉝ではStatus最後のチケット削除を維持し、chooseの`document-status-body-clear`で`context.Request.Body != null`の場合だけ`set-body`を空にします。本文は読み出さず、検証済みJSONからjob/modelを後続の結果取得へ渡す用途コメントも整えます。拒否条件・版固定・クエリ非転送は維持します。

`analyzeResult`は[Document Intelligence 2024-11-30のネイティブ解析結果](https://learn.microsoft.com/rest/api/aiservices/document-models/get-analyze-result?view=rest-aiservices-v4.0%20(2024-11-30))で、API版・モデル・本文・ページ構造を検査して返します。表や段落などは文書と選択モデルに依存します。位置・長さを使うクライアントは、指定した文字位置の単位に沿ってspanを解釈します。Portal画面はspanの位置情報を利用していません。抽出内容をHTMLとして実行しないでください。

Portal画面のモデル選択は`prebuilt-read`／`prebuilt-layout`の2種類を維持し、BFFの制約も今回変更しません。画面はFileをraw本文で送り、BFFは形式判別用の先頭と受信byte数だけを検査してGatewayへストリーミングします。ファイル全体をBFFメモリーへ集約せず、元ファイルを一度だけ送信し、以後は状態確認を自動実行します。旧multipart入口も互換用に残します。ブラウザーへ渡すのは本人と選択キーの台帳IDに束縛した暗号化handleです。中止・一時的な通信障害・同じタブの再読込後も状態確認を再開でき、原本や平文チケットは保存しません。画面の中止は上流ジョブの取消を意味しません。最初のPOSTの結果が不明な場合は、二重処理・追加料金を避けるため自動再送しません。

残すDocumentの入力検査は、利用回数制限・下流接続の準備・`accepted`監査より前で実行します。正本は[core-document-input-validation](../../apps/gateway/apim/policies/fragments/request/core-document-input-validation/README.md)にまとめ、解析開始の操作ポリシーは`policies/operation-policies/document-analysis/document-analysis.xml`に置きます。`model`の存在・単一値・非空と`.`／`..`の拒否は維持し、2モデル一覧による拒否と、そのためだけの操作設定JSON再読取りは撤去します。先行する設定・共有サイズの検査、チケット・返却URIとのモデル照合、固定接続先は維持します。開始POSTのstringIndexTypeと状態取得GETのクエリ非転送は⑭・㉒で整理済みです。Document固有inboundの棚卸はここで区切り、対象APIM資材のまとめ反映・読戻し照合を完了しました。実経路の受入は小文書の入力方式を一部確認し、容量境界・内部障害・期限切れなどの残件を維持します。開始側の受付・チケット発行、応答変換、`on-error`の残る条件は続く棚卸の対象です。開始POSTの追加クエリ転送に合わせたWAF変更や、クエリ名・値の監査への追加記録は行いません。

提供元の400／415／422は、現行の応答変換によりGatewayの422・`DocumentRejected`になります。独立した小さい試験文書による空本文・短い本文・別MIME・gzip宣言の要求では、Gatewayの422・`DocumentRejected`を確認しました。Content-Type欠落の403は原因を未切り分けで、空値など未実施の条件へ結果を広げません。APIMの申告サイズ超過は共有の413・`REQUEST_TOO_LARGE`、提供元の413は`DocumentInputTooLarge`、modelの欠落・複数値・空値と`.`／`..`は400・`DocumentInvalidInput`で返します。内部変数のモデル情報を引き継げない失敗は停止を維持して500・`DocumentAnalysisUnavailable`へ分け、監査は`OPERATION_SETTINGS_INVALID`とします。Portal BFFの原本ファイル検査と、その`DocumentUnsupportedMediaType`などの固定失敗コードは維持します。㉜ではGETのチケット・認証結果確認の拒否を応答前に1回監査し、403は`rejected`／`DOCUMENT_OPERATION_DENIED`（文書チケット段階の拒否）、410は`rejected`／`DOCUMENT_OPERATION_EXPIRED`（期限切れ）、500は`policy-error`／`AUTHENTICATION_PROCESSING_ERROR`を記録します。既存の拒否条件・公開body・status・要求ID・no-storeは維持し、生チケット・JSON・例外は出力しません。共通認可・流量制限の拒否は、Gatewayの共通エラー契約に従います。入力原本・抽出本文・上流の診断本文は監査ログへ含めません。

## 配備と保護

`apps/gateway/apim/apis/document-analysis-v1.json`を標準APIとして自動読込し、`released-apis`製品へ関連付けます。既存の検証製品は`pii-users`を使います。`scripts/deploy_gateway.py`がAPI公開に続いて、文書受付の完全パスだけへ専用WAFポリシーを関連付けます。

Gatewayの`/tools/documents/v1/analyze`とPortalの`/api/tools/documents`の専用WAFはPreventionと本文検査を維持し、先頭**2000 KiB**まで検査します。本文・ファイルのサイズ強制は解除し、検査枠を超える部分は検査せず受け付けます。受付MIMEの`920420`とraw本文の`930110`だけを調整し、URI・query・header・cookieのtraversalは4本のBlock規則で補完します。`920470`・`930100`などの他規則、認証・管理経路の厳格なポリシーは維持します。Portal BFFは原本のMIME・先頭形式・受信サイズを確認し、APIMは申告サイズの早期413と残るmodelの条件を確認します。文書の形式・内容・実サイズの受付可否は提供元が判定します。[WAFの適用範囲](../testing/waf-compatibility.md)

通常のChat／Responsesの同期・SSE契約は変更しません。AppGWのバックエンド待機時間は既存の既定600秒・設定可能なままで、FWも変更しません。Document入力の棚卸①〜㉝は、対象APIM資材の一括反映・読戻し照合を完了しました。APIM入力棚卸の反映時点ではWAF設定を変更していません。その後、入力文書URLの2専用ポリシー・3項目だけへ931130の除外を追加し、Azure反映・全ポリシーの読戻し照合を完了しました。対象と残る検証は[WAFの追加除外](../testing/waf-compatibility.md#入力文書urlの個別rfi除外)に記載します。PortalのJSON入力画面・BFFの拡張は別工程です。

実環境から独立して作成した小さい試験文書では、キー／Bearerの両方で原本と`base64Source`の202受付から、本文なしの状態取得GETによる200を確認しました。`pages=2`、指定した`utf16CodeUnit`、未指定時の`unicodeCodePoint`、公開API版への固定と、従来の2モデル一覧外の`prebuilt-invoice`の成功も確認しました。model不備の5条件は400、チケット拒否の5条件は403となり、それぞれ最終拒否監査が1回であることを照合しました。

本文なしGETに未使用クエリを付けても200でした。APIM入力棚卸の受入時点では、本文付きGETはWAFの920170、キー／Bearerの`urlSource`はWAFの931130などによる403でした。その後の入力文書URL用931130除外は反映・読戻し済みで、独立した小文書URLを使うキー／Bearerの要求は202受付から本文なしGETの200まで確認しました。model・API版・未指定時の`unicodeCodePoint`と、チケットの`exp - iat = 86400`も照合しました。本文付きGETの920170は維持しています。申告サイズ境界、内部引渡し失敗の500、期限切れ410、チケット発行時刻との直接比較、spanの直接比較と不正・複数値のstringIndexTypeなどは未実施です。全条件の受入完了とは扱わず、[追加受入表](../testing/integration-tests.md#document入力棚卸の追加受入)で確認範囲と残件を管理します。

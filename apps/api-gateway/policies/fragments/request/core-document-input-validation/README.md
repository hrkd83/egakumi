# Document解析の入力設定を確認する

`core-document-input-validation`は、Document解析の開始操作について、`model`クエリの必須・単一値・非空とURL階層を変える値でないことを確認する部品です。利用回数制限・下流認証・正常受付の監査より前に判定し、モデルの利用可否は提供元へ任せます。

## 資材と実行位置

| 資材 | 役割 |
| --- | --- |
| `core-document-input-validation.xml` | 呼出しモデルと結果変数を用意するフラグメント |
| `core-document-input-validation.cs` | XMLへ展開する同名C#の判定式と説明コメント |
| Azureの`core-document-input-validation` | 同じXMLとC#から生成するフラグメント |

APIテンプレートは`gatewayTargetAllowed`の拒否処理を通過した後、操作設定の`kind`が`documentAnalysis`の場合だけ部品を呼びます。操作ポリシーの`<base />`を通じてここへ到達し、成功した要求だけが後続の利用回数制限・下流認証・正常受付の記録へ進みます。

## 読取り元と結果

| 情報 | 用途 |
| --- | --- |
| 要求の`model`クエリ | 解析モデルの指定 |
| `documentModel` | 既存のモデル取得値を、転送URIと受付結果の処理へ渡す |
| `documentInputStatus` | 条件を通過した場合は200、model入力の不備は400、内部変数のモデル情報を引き継げない失敗は500 |

`model`が存在し、その値が一つで空でないことを確認します。欠落・複数値・空値と、URL階層を変える`.`／`..`は400にします。棚卸⑨で、`model`以外のクエリがあるだけで400にする条件を撤去し、⑪で`prebuilt-read`／`prebuilt-layout`の2モデル一覧による拒否も撤去しました。

一覧照合だけに使っていた操作設定JSONの再読取りも撤去しました。先行する操作設定・共有サイズ設定の検査は維持します。モデルの利用可否は提供元が判定し、チケット・返却URIとのモデル照合と固定接続先は維持します。ほかのモデルIDはURIの単一要素としてエスケープし、提供元の受付条件に従います。

棚卸⑫で、呼び出し元の解析開始ポリシーが`model`をURIのモデル要素へ使った後、元のクエリから削除し、pathだけを書き換える`rewrite-uri`の`copy-unmatched-params=true`で`pages`・`features`などの追加クエリを転送します。⑭では、その後のAPIM標準`set-query-parameter`を、api-versionは対応版へ上書きし、stringIndexTypeは指定値を保持して未指定時だけ`unicodeCodePoint`を補完する形へ整理します。後者には`exists-action=skip`を使い、不正値・複数値の適否は提供元へ任せます。この部品に転送処理や追加のクエリ名・値の監査出力は持たせません。対応版は公開Schemaの単一値（現在`2024-11-30`）へ一元化します。

⑬では公開Schemaを版の正本とし、API定義の重複値を配備前の正規化で補完します。同じ値をBicepのmarkerから開始URI・受付URL照合・状態GETへ展開し、⑱では状態取得の結果照合も、共通APIが準備した`gatewayOperationConstraint`の`apiVersion`・`maxResponseBytes`を使います。Status専用の`documentSettings`と埋込みBase64の復元を撤去し、outboundの既存try内で準備済みJSONを直接読みます。上限・版・チケット・結果の条件は維持します。⑲ではStatusの操作設定`gatewayOperationConstraint`の取得・JSON解析失敗だけを500へ分類し、応答前に`core-gateway-telemetry`で`policy-error`・`OPERATION_SETTINGS_INVALID`・500を1回記録します。提供元の本文・版・容量などの不備は既存の502を維持し、網羅的な再検査は追加しません。この部品には版の再読取りや追加の実行時検査を入れません。

⑮は開始POSTのチケット削除について目的コメントを整える棚卸で、現挙動を維持します。開始POSTは既存の`X-Core-Document-Ticket`を使わず削除して続行し、受付後に新しいチケットを発行します。状態取得GETは照合後に削除するため、両操作の削除を無条件に共通化しません。

⑯では、登録済みの同じAPIバックエンドから生成する`documentBaseUrl`を、開始・状態取得の各操作に標準`set-variable`1行ずつで保持します。受付URLの照合と、接続先・API・本人にチケットを束縛する処理に使います。現挙動を維持し、用途コメントだけを整えます。

⑰では開始inboundの`documentStartedAt`を撤去し、提供元の202受付・受付URL・本人情報を確認した後、`documentAccepted`でチケット発行時のUTC epoch秒を一度だけ取得します。`iat`をその発行時刻、`exp`を`iat + 86400`とし、Gatewayでのチケット発行から24時間を有効期間とします。㉛では時刻の目的コメントを整えます。発行・検証の時計差として未来発行は30秒まで許容します。期限後の猶予ではありません。iat・expの不備や24時間の期間不一致は403、取得許可時点で`exp <= now`なら410を維持します。Statusの現在時刻取得2回は、未来発行の許容確認と取得許可時点の期限確認で役割が異なります。提供元に結果がない場合の404・結果保持期限とは別で、追加検査や変数は設けません。提供元の正確な受付時刻や結果保持期限を取得する処理ではありません。

⑳では、状態取得GETの`X-Core-Document-Ticket`を標準`TryGetValue`で一度だけ取得し、存在とAPIMが認識した値が一つであることを確認します。先頭値への既存の4096文字上限・形式・署名・本人・期限の検査を維持し、欠落・複数値・不正は403にします。㉓では状態取得GETの操作ID（D形式GUID）とチケットの長さ・形式を別々の条件に分け、日本語コメントを整えます。条件・403・後段のジョブ照合は維持します。㉔は条件を維持するコメント整理です。4096文字は未検証チケットの処理量を抑える上限、43文字はHMAC-SHA256署名のpaddingなしBase64url固定長を示します。㉕では署名の用途を区別する専用接頭辞と、長さ・全文字で一致を確認する処理の目的コメントを整えます。処理・鍵・形式・条件・403は維持します。㉖・㉘では状態取得GETと開始outboundの`gatewayDecision.allowed`の再確認を削除します。共通認証からAPIへの引渡し不備は上位APIで500にして止め、後続で上書きしません。㉗では開始・状態取得とも`gatewayDecision.callerId`を使い、購読・Bearer成功・社員JSON・GUID整形・ID再照合の重複を削除します。キーのownerは接頭辞なしの購読ID、Bearerは`user:<tenant>:<principal>`の既存表記を維持します。認証方式の分岐、署名後のチケット復元・JSON解析と、チケットの本人・API・接続先・ジョブ照合は維持します。㉙では開始の`documentAccepted`とStatusの`documentOwner`で、内部認証結果`gatewayDecision`の取得・JSON解析、認証方式・owner引渡しの不備だけを500・`policy-error`／`AUTHENTICATION_PROCESSING_ERROR`へ分離します。署名・claims・owner不一致403、期限切れ410、受付URL・Operation ID空値502は維持します。500の公開コードは`DocumentAnalysisUnavailable`とし、要求ID・no-storeを維持して内部JSONや例外は出力しません。本人再検査・新共通部品・新結果JSONは追加せず、⑲の操作設定失敗とは分けて扱います。㉚ではStatusの`v`／`aud`／`job`／`scope`による本人・API・接続先・解析ジョブの照合と、modelの文字列・空欄・`.`／`..`のURL用確認を別のifに分け、目的コメントを整えます。判定条件・403・既発行チケット互換と、提供元の任意モデルIDを扱う方針は維持します。㉜ではGETのチケット・認証結果確認の403・410・500を単一chooseの`document-operation-rejection`へまとめ、応答前に既存`core-gateway-telemetry`を1回呼びます。監査は403が`rejected`／`DOCUMENT_OPERATION_DENIED`（文書チケット段階の拒否）、410が`rejected`／`DOCUMENT_OPERATION_EXPIRED`（期限切れ）、500が`policy-error`／`AUTHENTICATION_PROCESSING_ERROR`です。旧GET500専用chooseは統合し、POST500専用は維持します。拒否条件・公開body・status・要求ID・no-storeは変えず、生チケット・JSON・例外は出力しません。㉝ではStatus最後のチケット削除を維持し、chooseの`document-status-body-clear`で`context.Request.Body != null`の場合だけ`set-body`を空にします。本文は読み出さず、検証済みJSONからjob/modelを後続の結果取得へ渡す用途コメントも整えます。拒否条件・版固定・クエリ非転送は維持します。

㉑では状態取得GETの追加クエリを無視して同じジョブを取得し、クエリの有無だけで403にする処理を撤去します。チケット・UUID・本人・期限の照合、固定URI・対応版とURLへのAPIキー指定の共通拒否は維持します。㉒では未使用クエリを転送しない`copy-unmatched-params=false`を維持し、目的コメントを整えます。[公式Get Analyze Result（2024-11-30）](https://learn.microsoft.com/rest/api/aiservices/document-models/get-analyze-result?view=rest-aiservices-v4.0%20(2024-11-30))が使うクエリはapi-versionのみで、チケット・操作IDで確定したジョブを照会します。将来正式なGET追加クエリが必要になれば、公開結果契約と合わせて見直します。

model入力の拒否時はAPI側で`rejected`・`INVALID_REQUEST`・400を監査へ渡し、`DocumentInvalidInput`のJSON、APIM要求ID、`Cache-Control: no-store`を返します。フラグメントは監査を呼び出さず、本文や例外の詳細を記録しません。

内部変数のモデル情報を引き継げない失敗は、API側が`OPERATION_SETTINGS_INVALID`・500を監査へ渡し、公開エラーは既存の`DocumentAnalysisUnavailable`へ揃えます。利用回数制限・下流接続・正常受付の記録へ進めず、内部値や例外の詳細を公開しません。

## 本文の扱い

原本の本文と、提供元のNative Analyzeに渡すJSONをそのまま転送します。この部品はMIME一覧、先頭バイト、固定の8バイト下限、空本文、実サイズ、Content-Encodingを検査せず、圧縮・解凍・本文変換も行いません。`urlSource`の文書をGatewayが取得したり、`base64Source`をGatewayが復号したりする処理はありません。

共有のサイズ検査は操作設定の`requestBodySizeCheck: declared`を使い、Content-Lengthの申告値から上限超過が分かる場合だけ早期413にします。JSONのBase64復号後のサイズやURL先文書のサイズはAPIMで計測しません。提供元が実際に受け付ける形式・文書サイズは、提供元の条件に従います。

Document経路の本文全量読取りを削除しても、共有テンプレートの他形式向けの式や、APIM・AppGWの受信保持処理が残ります。完全な無バッファ転送や、Azureの技術制約の撤去を示すものではありません。

## 続けて棚卸する範囲

開始POSTのstringIndexType・チケット発行時刻と状態取得GETのクエリ非転送は⑭・⑰・㉒で整理済みです。Document固有inboundの棚卸はここで区切り、対象APIM資材のまとめ反映・読戻し照合を完了しました。受付結果・開始側のチケット発行と、outbound・on-errorの残る条件は後続の棚卸対象です。開始と状態取得の操作ポリシーは`operation-policies/document-analysis/`にまとめています。Portalの原本アップロード方式・2モデルの選択肢とBFFの制約は今回変更しません。Portal画面は結果のspanの位置情報を利用していません。

配備後は原本・Base64・文書URLと結果取得を[文書解析の受入](../../../../../../docs/testing/tool-gateway-tests.md#文書解析の受入)で確認します。

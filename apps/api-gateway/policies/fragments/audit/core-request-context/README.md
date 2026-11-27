# 認証前の要求情報を準備する

`core-request-context`は、APIMが受けた要求の時刻・API・操作・接続元と相関候補を、監査で追跡できる形にまとめる部品です。同名XML／C#をこのフォルダーで管理し、AzureのPolicy fragment名も`core-request-context`を使います。

この部品は本人を認証せず、ログもまだ出力しません。要求ごとのAPIM変数`gatewayRequestContext`へJSON文字列を保存し、後続の監査処理に渡します。要求ごとに変わる情報なので、配備設定を保持する「名前付きの値」には保存しません。

## 実行する位置

各APIのInboundにある`<base />`の位置で、[共通ポリシー](../../../service.xml)のInboundが実行されます。その先頭がこの部品です。

```text
各APIのInbound → <base /> → All APIsの共通Inbound
  STEP1 core-request-context              要求情報を準備する
  STEP2 core-trusted-ingress              HTTP入口を確認する
  STEP3 core-credential-browser-exception ブラウザー受付を判定する
  STEP4 core-common-authentication        キー／Bearerを認証する
  STEP5 core-audit-authenticated-subject  確認できた利用者情報を準備する
→ 各APIの後続処理へ戻る → 必要な時点で監査ログを出力する
```

STEP2やSTEP4で拒否する場合も、先に準備した要求情報を監査へ渡せます。認証前に作るため、社員IDや購読の確認結果はここへ混ぜず、[STEP5の利用者情報](../core-audit-authenticated-subject/README.md)と分けます。

## 読み取る情報と出力

入力はAPIMの要求・API・操作の情報と、下表の2つのヘッダーです。外部サービスへ問い合わせず、この要求内で取得・整形します。

| JSONの項目 | 情報源と保持条件 |
| --- | --- |
| `requestId` | `context.RequestId`。APIMの要求IDを文字列で保存します。利用者IDではありません |
| `apiId`・`operationId` | APIMのAPI・操作ID。対応する情報がない場合は`null`です |
| `requestReceivedAt` | `context.Timestamp`。APIMの要求受信時刻を`ToString("O")`で保存します |
| `httpMethod` | 1～20文字のASCII大文字だけなら保存し、それ以外は`null`です |
| `operationTemplate` | 登録済み操作のURLひな形から`?`以降を除き、最初の最大512文字から制御文字を除きます。ひな形がない場合は空文字です |
| `apimPeerIp` | APIMと直接通信した相手のIP。64文字以内の入力をIPとして解析できた場合だけ整形して保存し、それ以外は`null`です |
| `reportedUserAgent` | `User-Agent`が1値の場合だけ、最初の512文字から制御文字とサロゲート文字を除きます。欠落・複数値なら`null`です |
| `appGatewayTraceId` | `x-appgw-trace-id`が1値・64文字以内・非ゼロGUIDの場合だけ、ハイフン付きのD形式で保存します。それ以外は`null`です |

C#の末尾はこれらを`Formatting.None`のJSON文字列として返し、XMLの`set-variable`が`gatewayRequestContext`へ保存します。補助項目を採用できなくても、この部品にはその理由で要求を拒否する分岐がありません。

`apimPeerIp`はAppGWなどのプロキシのIPになり得るため、社員PCのIPとは断定しません。`reportedUserAgent`はクライアントの申告であり、認証されたアプリの証明には使いません。STEP2より前に読む`appGatewayTraceId`もAppGWログとの相関候補であり、入口通過や本人の証明ではありません。

## 読み取らない情報

要求本文、APIキーの値、Bearerトークン、Cookie、実要求のURL・クエリ名・値、任意の追加ヘッダー、`X-Forwarded-For`は読み取りません。トークンの再解析、本文のバッファリング、外部通信も追加しません。

監査のためにAPIMが観測した要求IDやIP等を取得する処理と、公開ソースへ実環境のID・IP・トークン等を書き込む行為は区別します。この正本・説明書には環境の実値を埋め込みません。

## 生成と後続の監査

[同名XML](core-request-context.xml)は処理位置と変数への保存を所有し、[同名C#](core-request-context.cs)は1つの要求情報生成式を所有します。[Pythonの生成処理](../../../../../../scripts/deployment/helpers/apim_audit.py)が`__CORE_REQUEST_CONTEXT_CSHARP__`へC#をXML属性用に展開します。通常の説明付き生成では、C#の`//`説明を閉じた`/* ... */`コメントへ変換し、Azureポータルでも読めるようにします。

C#は外部モジュールとして別に動くものではなく、生成したXMLのポリシー式としてAPIM内で実行します。生成後のXMLを確認すると、マーカーの位置に実装と説明が入っています。

後続の`core-gateway-telemetry`等は、保存した要求情報から出力対象の項目を取り出し、要求ID・認証済み利用者情報・処理段階と関連付けてログへ記録します。独立した要求情報イベントは追加しません。最終的な監査JSONとAppGWログとの照合規則は[監査識別情報の契約](../../../../../../docs/reference/audit-identification.md)を参照してください。

## 現在の確認範囲

このフォルダーのXML／C#と生成処理は実装済みです。[部品の生成・展開の試験資材](../../../../../../tests/audit/test_audit_fragments.py)と[識別情報の合成試験資材](../../../../../../tests/audit/test_audit_identification.py)があり、要求情報・利用者情報・監査出力を確認する対象を定義しています。このREADMEの追加では、それらの試験を再実行していません。

既存の限定受入と全体の残件は[管理構成](../../../README.md)で確認します。この文書の整合確認は、全API・全経路の受入完了、Azure上の性能検証、J-SOXへの適合を確認した結果ではありません。保存先・保持期間・閲覧権限は既存の監査基盤の設計に従い、この部品の文書追加で変更しません。

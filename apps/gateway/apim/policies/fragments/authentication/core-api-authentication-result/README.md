# 認証済み利用者情報の引き渡し

HTTP APIが、共通認証済みの購読または社員情報を後続の利用枠・接続処理と監査へ渡す部品です。Azureのフラグメント名、フォルダ名、XML・C#の名前はすべて`core-api-authentication-result`に揃えます。

## ソースとAzureでの表示

同名XMLがポリシーの外枠、同名C#が`gatewayDecision`を組み立てる本体です。配備時にC#をXMLへ差し込み、コメントを含む完成XMLをAzureへ渡します。Azureポータルでは、その完成XMLを参照できます。

既存のC#展開処理を使います。他のフラグメントのソースは展開せず、この部品から別フラグメントを呼びません。

## 呼び出す順序

HTTP APIテンプレートは、API設定の準備、`<base />`による共通認証、要求準備、入力検査、操作・モデル設定の準備が成功した後に、この部品を呼びます。`gatewayOperationSelection`はこの位置ですでに準備済みです。

| 入力 | 内容 |
| --- | --- |
| `context.Subscription` | APIMが検証した購読。存在すればキー側の結果を使う |
| `gatewayEntraAuthenticationValidated` | 購読がない場合に確認するBearer認証の成功記録 |
| `gatewayUserAuthentication` | 共通認証が確認・整形した社員情報JSON |
| `gatewayOperationSelection` | 準備済みの操作・モデル設定。任意の監査用ラベル`settingId`を読む |

## 後続へ渡す結果

`gatewayDecision`をJSON文字列として保存します。`allowed`はこの部品の引き渡し成功を表し、利用枠や接続先の確認は後続処理が行います。

| 出力 | 内容 |
| --- | --- |
| `allowed` | 引き渡し成功なら`true`、内部情報を引き継げなければ`false` |
| `method` | キー側は`subscriptionKey`、社員Bearer側は`user` |
| `callerId` | キー側は購読IDに`subscription:`を付ける。社員側は共通認証済みの呼出者ID |
| `allocationId` | 社員側の利用枠の帰属先。共通認証済みの値を引き継ぐ |
| `verifiedTenantId`／`verifiedPrincipalId` | 社員側の確認済みテナントIDと社員Object ID |
| `verifiedClientId` | 社員側の呼び出し元アプリID。任意のため、欠落時は空文字 |
| `profileId` | 管理用ラベル`settingId`が空でない場合だけ転記する既存の監査項目 |

社員情報の形式・バージョン、IDの形式、呼出者と利用枠の帰属先の一致は共通認証で確認済みです。この部品ではその値を引き継ぎます。クライアントの本文やトークンは読みません。

Bearer成功記録の欠落・false、社員情報JSONの読取り例外では`allowed=false`を返します。HTTP APIテンプレートが`policy-error`・`AUTHENTICATION_PROCESSING_ERROR`を監査へ記録し、要求ID付きの500で終了します。例外の詳細や生JSONは出力しません。操作・モデル設定の準備結果を読む処理の例外は、既存の`on-error`へ進みます。

Protocol／MCPの本人情報の引き渡しは、それぞれの棚卸が済むまで既存テンプレートに保持します。

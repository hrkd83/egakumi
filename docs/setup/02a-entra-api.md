# 02a. egakumi-apiを登録し、Gateway.Invokeを公開する

[← Entraの5アプリ](02-entra.md) · [Codexクライアントの準備 →](02b-entra-codex.md) · [paramsの設定](04-params.md) · [アプリ分離の作業計画](../reference/entra-app-rollout-plan.md)

APIMで公開するAPIをEntra ID上で表すリソースアプリ`egakumi-api`を準備します。このページは一般化した登録手順であり、特定環境での作業完了や接続成功を示す記録ではありません。

**APIM入口は購読キーの提示を優先し、キーなしのBearer要求をEntraで認可します。** HTTP・WebSocket・MCP・Realtime・CMを含む業務APIの対象は正本から自動生成します。APIMポリシー、本人別利用上限・監査、専用Productの生成と公開controllerは資材の実装範囲です。この全API対応の変更はAzureへ再配備しておらず、登録だけではBearer認可を利用できません。対象環境への反映、呼出元クライアントの設定と接続受入を別途確認します。BearerによるGraph呼び出しでは、このAPIアプリの証明書と委任権限を使ってBrokerがOBO交換します。

## 1. 認証テナントでAPIアプリを登録する

Entraのアプリ登録を担当できるアカウントで、社員の認証先テナントを選びます。Azure基盤のテナントと同じとは限りません。「Microsoft Entra ID」→「アプリの登録」→「新規登録」を開き、次を入力します。

| 入力欄 | 設定 |
| --- | --- |
| 名前 | `egakumi-api` |
| サポートされているアカウントの種類 | この組織ディレクトリのみに含まれるアカウント（単一テナント） |
| リダイレクトURI | 未設定。プラットフォームも追加しない |

[![APIアプリの名前・単一テナント・リダイレクト未設定を確認する登録画面](../images/entra-api-setup/03-register-form.png)](../images/entra-api-setup/03-register-form.png)

内容を確認して「登録」を実行します。ポータルに同意や確認が表示される場合は、内容を確認し、組織の担当者が判断します。

登録後の「概要」で、次の値を保護した設定表へ控えます。実値はチェックアウト直下の`.local/<environment>/`で管理し、公開文書・Gitへ転記しません。

| 画面の項目 | 用途 |
| --- | --- |
| アプリケーション（クライアント）ID | `<API_APP_CLIENT_ID>`。APIの識別子と、後続のAPIM audience検証に使う |
| ディレクトリ（テナント）ID | `<AUTH_TENANT_ID>`。APIアプリを登録した認証テナント |
| オブジェクトID | アプリ登録自体の管理用ID。Client IDとは別に扱う |

[![登録後のAPIアプリの概要でClient IDと認証テナントを確認する画面](../images/entra-api-setup/04-overview.png)](../images/entra-api-setup/04-overview.png)

## 2. Application ID URIを設定する

「APIの公開」→「Application ID URI」の編集を開きます。

| 項目 | 設定 |
| --- | --- |
| Application ID URI | `api://<API_APP_CLIENT_ID>` |

`<API_APP_CLIENT_ID>`には、このAPIアプリのアプリケーション（クライアント）IDを使います。Object ID、APIMのManaged Identity、PortalやCodexのClient IDは使いません。保存後にURIを再表示して確認します。

[![APIアプリのClient IDを使ったApplication ID URIを設定する画面](../images/entra-api-setup/05-api-uri.png)](../images/entra-api-setup/05-api-uri.png)

## 3. 委任スコープGateway.Invokeを公開する

「APIの公開」→「スコープの追加」を開き、次を設定します。

| 入力欄 | 設定 |
| --- | --- |
| スコープ名 | `Gateway.Invoke` |
| 同意できるユーザー | 管理者のみ（Admins only）。manifestでは`type: "Admin"` |
| 管理者の同意の表示名 | `Invoke egakumi APIs` |
| 管理者の同意の説明 | `Allows the signed-in user to invoke egakumi APIs through APIM.` |
| 状態 | 有効（Enabled） |

[![Gateway.Invokeの管理者同意設定・表示名・説明・有効状態を入力する画面](../images/entra-api-setup/06-scope-form.png)](../images/entra-api-setup/06-scope-form.png)

「スコープの追加」を実行し、「APIの公開」で次を確認します。

| 確認項目 | 期待する値 |
| --- | --- |
| 完全なスコープ名 | `api://<API_APP_CLIENT_ID>/Gateway.Invoke` |
| 同意の種類 | Admin |
| 状態 | Enabled |

[![APIの公開で追加したGateway.Invokeを確認する画面](../images/entra-api-setup/07-expose-api.png)](../images/entra-api-setup/07-expose-api.png)

**「管理者のみ」は、同意できる人を定める設定です。** PortalやCodexへ実際の管理者同意を付与したことにはなりません。後続工程で各クライアントアプリにこの委任アクセス許可を追加し、管理者が対象クライアントへ同意を付与します。この段階ではクライアントの事前承認も追加しません。

## 4. v2アクセストークンを要求する

「マニフェスト」でMicrosoft Graph形式の`api.requestedAccessTokenVersion`を`2`にして保存します。変更対象を示す断片は次のとおりです。

```json
{
  "api": {
    "requestedAccessTokenVersion": 2
  }
}
```

公開したスコープを含む`api.oauth2PermissionScopes`などの既存項目は保持します。この断片でマニフェスト全体や`api`全体を置き換えず、保存後に値とスコープの保持を確認してください。

[![Microsoft Graph形式のmanifestでrequestedAccessTokenVersionを2に設定する画面](../images/entra-api-setup/08-manifest-v2.png)](../images/entra-api-setup/08-manifest-v2.png)

v2のAPI向けアクセストークンでは、APIMが検証する`aud`は`<API_APP_CLIENT_ID>`のGUIDです。要求するスコープの`api://.../Gateway.Invoke`文字列とは区別します。実際のトークン取得・検証は後続の接続試験で確認します。

## 5. Graphの委任許可を設定する

「APIのアクセス許可」→「アクセス許可の追加」→「Microsoft Graph」→「委任されたアクセス許可」で `User.Read` を選び、管理者同意を付与します。このアプリの用途はAPIMのaudienceとOBOの中間APIです。「認証」のプラットフォームと戻り先は空にし、「証明書とシークレット」では後の手順で専用の公開証明書を登録します。PortalのGraph管理に使うアプリケーション権限はPortalアプリへ設定します。モデル推論のアクセスはAPIMのマネージドIDとAzure RBACで設定します。 [Microsoft EntraのOBO](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow)

## 6. Core配備後にOBO用の公開証明書を登録する

1. [初回配備手順](06-deploy.md)に従い `azd provision` を実行します。専用ACA Jobが業務用Key Vault内に、エクスポートできないOBO署名鍵と証明書 `gateway-obo` を生成します。
2. 対象環境の保護出力 `.local/<environment>/generated/gateway-obo-public.cer` を選びます。Portal用の証明書は別の登録で使用します。
3. Entraの認証テナントでegakumi-apiを開き、「証明書とシークレット」→「証明書」から公開CERをアップロードします。登録した証明書の指紋と有効期限、Graph委任 `User.Read` の管理者同意を照合します。
4. Gatewayを配備します。BrokerのランタイムIDにOBO鍵の署名権限を割り当て、保護stateの鍵バージョンと公開証明書のSHA-256指紋を使用します。実受入ではBearerで本人のGraph `/me`、購読キーで本人のCM接続を確認します。

OBO証明書を手動更新する際は、同じ証明書名で新しい非エクスポート鍵版を作成し、公開CERを先にegakumi-apiへ追加します。保護paramsに版固定の `gateway.authentication.entra.obo.keyId` と、同じ公開証明書のSHA-256指紋 `certificateThumbprint` を設定できます。指紋はコロンを除いた64桁の16進数で、ポータルに表示されるSHA-1とは用途が異なります。Gateway配備と実OBOを確認してから旧証明書を回収します。切り戻す場合は、まだ有効な旧鍵と旧公開証明書を対で戻します。 [Entraの証明書資格情報](https://learn.microsoft.com/en-us/entra/identity-platform/certificate-credentials)

## 後続工程へ引き継ぐもの

| 引継ぎ項目 | 後続の用途 |
| --- | --- |
| 認証テナントとAPIのClient ID | APIMのtenant・issuer・audience検証 |
| Application ID URIとGateway.Invoke | Portal・Codex等の委任アクセス許可、管理者同意、要求scope |
| 単一テナント・scope・token versionの保存後確認 | アプリ登録の受入。接続試験の結果とは別に記録 |

**APIのClient IDを`credentialProviders.graphDelegated.clientId`や追加providerの`clientId`へ設定しません。** そこはCMの下流OAuthクライアント用です。[paramsの認可設定](04-params.md)の`gateway.authentication.entra.audience`へ引き継ぐ値です。公開paramsには`audience`を置き、共通`userLimits`はconfigへ移設しました。委任scopeは資材固定の`Gateway.Invoke`で、公開`requiredScope`入力は廃止しています。有効化・対象API・クライアント許可リストは入力しません。専用Productの所属を閉じた配備段階で資材を照合し、対象環境での実APIMの検証・公開・接続受入を別に確認します。既存の共用アプリを回収する条件は[移行手順](../reference/entra-app-rollout-plan.md#工程8旧共用アプリと移行設定を撤去する)で確認します。

掲載画像はフルHD（1920×1080ピクセル）の画面を原寸で撮影し、拡大縮小せずPNGで保存しています。文書上で縮小表示される場合は、画像をクリックして原寸画像を開き、確認してください。

画像は操作項目の説明のために設定画面を再表示・再撮影したものです。登録済みのアプリに`Gateway.Invoke`を重複して追加しないでください。

画面は操作箇所の例として扱い、公開する画像から実環境のID・組織・ドメイン・アカウント等を除去します。原本と作業結果は`.local/<environment>/`で保護します。登録確認、クライアントへの同意、APIM認可、実接続の結果をそれぞれ分けて記録してください。

公式仕様：[Web APIとアクセス許可](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-configure-app-access-web-apis) · [Microsoft Graph形式のアプリマニフェスト](https://learn.microsoft.com/en-us/entra/identity-platform/reference-microsoft-graph-app-manifest) · [アクセストークンのクレーム](https://learn.microsoft.com/en-us/entra/identity-platform/access-token-claims-reference)

[Codexクライアントの準備へ進む](02b-entra-codex.md) · [5アプリの準備へ戻る](02-entra.md) · [後続のAPIM認可・アプリ分離計画](../reference/entra-app-rollout-plan.md)

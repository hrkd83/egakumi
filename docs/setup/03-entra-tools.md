# 03. egakumi-toolsを登録する

前：[02. egakumi-apiの登録](02-entra-api.md) · [README](../../README.md#新規クローンから構築する) · 次：[04. egakumi-credentialの登録](04-entra-credential.md)

`egakumi-tools` は、購読キーで呼ばれたGraph APIが利用するAPIM Credential ManagerのOAuthクライアントです。利用者が自分のMicrosoft Graph接続を作成するときに使います。GatewayのAPI認可は[egakumi-api](02-entra-api.md)、接続を始める利用者の本人確認は[egakumi-credential](04-entra-credential.md)が担当します。

## CMのWeb戻り先を確認する

[第01章](01-prerequisites.md)で環境別paramsのひな型とAPIM識別子を準備してから、リポジトリのルートで実行します。このコマンドはローカルのBicep CLIで登録用の名前とURIを計算します。

```sh
python3 -I -B scripts/setup/prepare_registration.py --config "$DEPLOY_PARAMS_FILE"
```

出力の `credentialManagerRedirectUri` が登録値です。形式は `https://authorization-manager.consent.azure-apim.net/redirect/apim/<APIM_NAME>` で、導入先のAPIM名まで含みます。`credentialIdentityRedirectUri` も控え、第04章で使います。環境固有の出力を保存する場合は、同じ環境の `.local/<environment>/evidence/` を使います。 [MicrosoftのCredential Manager構成](https://learn.microsoft.com/en-us/azure/api-management/credentials-how-to-azure-ad)

## AzureポータルでWebアプリを作成する

1. Azureポータルで `egakumi-api` と同じ認証用Entraテナントに切り替え、「Microsoft Entra ID」→「アプリの登録」→「新規登録」を開きます。
2. 名前に `egakumi-tools` を入力し、サポートされるアカウントの種類で「この組織ディレクトリ内のアカウントのみ（シングルテナント）」を選びます。
3. リダイレクトURIに「Web」を選び、`credentialManagerRedirectUri` の値を貼り付けて「登録」を選びます。登録後に入力する場合は「認証」→「プラットフォームの追加」→「Web」を開きます。
4. 「認証」でWebの戻り先が保存されていることを確認します。「暗黙的な許可およびハイブリッド フロー」のアクセストークンとIDトークンの発行、「パブリック クライアント フローを許可する」は無効にします。「概要」で認証用テナントIDとアプリケーション（クライアント）IDを控えます。 [アプリ登録とWebのリダイレクトURI](https://learn.microsoft.com/en-us/graph/auth-register-app-v2)

## Graphの委任アクセス許可を設定する

1. 「APIのアクセス許可」を開き、「Microsoft Graph → 委任されたアクセス許可 → `User.Read`」があることを確認します。なければ「アクセス許可の追加」で追加します。
2. 標準のGraphデータアクセス許可は委任 `User.Read` です。アプリケーションのアクセス許可と`Gateway.Invoke` は追加しません。
3. 実行時はCredential Managerの標準設定が `openid profile email offline_access https://graph.microsoft.com/User.Read` を要求します。認証テナントで事前同意が必要な場合は、Microsoft Graphの「委任されたアクセス許可」でこの4つのOIDCスコープも追加し、対象を確認して「管理者の同意を与えます」を選びます。付与後は状態欄を確認します。 [MicrosoftのOIDCスコープ](https://learn.microsoft.com/en-us/entra/identity-platform/scopes-oidc)
4. 利用者ごとのCredential Manager接続は、Gateway配備後に本人のサインインで作成します。

## シークレットを発行してparamsへ引き継ぐ

1. 「証明書とシークレット」→「クライアント シークレット」→「新しいクライアント シークレット」を開きます。用途の分かる説明と組織の方針に合う有効期限を設定して追加します。
2. 発行直後に表示される「値（Value）」を組織の保護された秘密保管先に保存します。「シークレットID」は認証に使いません。有効期限と更新担当を記録します。 [クライアントシークレットの発行](https://learn.microsoft.com/en-us/entra/identity-platform/how-to-add-credentials)
3. [第07章](07-params.md)で同じ環境のparamsに下表を設定します。[第08章の `azd up`](08-provision-core.md) で「CM接続: graph-delegated」の欄に値を非表示入力すると、共通configで定義した `.local/<environment>/secrets/entra-client-secret.txt` に保存されます。

| paramsの項目 | 設定する値 |
| --- | --- |
| `entraTenantId` | API・Tools・Credentialで共通の認証用テナントID |
| `credentialProviders.graphDelegated.clientId` | `egakumi-tools` のアプリケーション（クライアント）ID |

Coreの構築前に、準備したAPIM名とWeb戻り先、委任 `User.Read`、クライアントID、シークレットの有効期限を照合します。接続の動作は[Gateway配備後](11-deploy-gateway.md#5-本人接続を試験する)に確認します。

次：[04. egakumi-credentialの登録](04-entra-credential.md)

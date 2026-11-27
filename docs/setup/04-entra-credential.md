# 04. egakumi-credentialを登録する

前：[03. egakumi-toolsの登録](03-entra-tools.md) · [README](../../README.md#新規クローンから構築する) · 次：[05. 公開DNSの準備](05-public-dns.md)

`egakumi-credential` は、Gatewayの接続画面から操作する利用者が誰かをEntraで確認するためのWebアプリです。本人確認の後、Graph接続の作成には別の[egakumi-tools](03-entra-tools.md)を使います。

## 本人確認のWeb戻り先を確認する

[第03章で計算した登録用の出力](03-entra-tools.md#cmのweb戻り先を確認する)から `credentialIdentityRedirectUri` を使います。形式は `https://<GATEWAY_HOST>/auth/connections/browser/identity-callback` です。公開するGatewayのホスト名と完全パスを照合します。

## AzureポータルでWebアプリを作成する

1. Azureポータルで `egakumi-api`・`egakumi-tools` と同じ認証用Entraテナントを選び、「Microsoft Entra ID」→「アプリの登録」→「新規登録」を開きます。
2. 名前に `egakumi-credential` を入力し、サポートされるアカウントの種類で「この組織ディレクトリ内のアカウントのみ（シングルテナント）」を選びます。
3. リダイレクトURIに「Web」を選び、`credentialIdentityRedirectUri` の値を貼り付けて「登録」を選びます。登録後に入力する場合は「認証」→「プラットフォームの追加」→「Web」を開きます。
4. 「認証」でGatewayのWeb戻り先が保存されていることを確認します。「暗黙的な許可およびハイブリッド フロー」のアクセストークンとIDトークンの発行、「パブリック クライアント フローを許可する」は無効にします。 [アプリ登録とWebのリダイレクトURI](https://learn.microsoft.com/en-us/graph/auth-register-app-v2)
5. 「概要」で認証用テナントIDとアプリケーション（クライアント）IDを控え、API用・Tools用のClient IDと異なることを確認します。

## 本人確認のアクセス許可を確認する

1. 「APIのアクセス許可」に既定のMicrosoft Graph `User.Read` が追加されていれば削除します。Graphのデータアクセス許可と、Gateway・Portal用のAPI権限は付与しません。
2. BrokerはAuthorization CodeとPKCEで `openid profile` を要求します。テナントで事前同意が必要な場合は、「アクセス許可の追加」→「Microsoft Graph」→「委任されたアクセス許可」で `openid` と `profile` を追加します。管理者が対象を確認して「管理者の同意を与えます」を選びます。 [MicrosoftのOIDC設定手順](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/add-application-portal-setup-oidc-sso#step-4-configure-api-permissions)
3. エンタープライズアプリケーションで割り当て必須の運用をしている場合は、対象利用者を割り当てます。

## シークレットを発行してparamsへ引き継ぐ

1. 「証明書とシークレット」→「クライアント シークレット」→「新しいクライアント シークレット」を開きます。用途の分かる説明と組織の方針に合う有効期限を設定して追加します。
2. 発行直後の「値（Value）」を、Tools用と分けて組織の保護された秘密保管先に保存します。有効期限と更新担当を記録します。 [クライアントシークレットの発行](https://learn.microsoft.com/en-us/entra/identity-platform/how-to-add-credentials)
3. [第07章](07-params.md)で同じ環境のparamsに下表を設定します。[第08章の `azd up`](08-provision-core.md) で「CM本人確認（OIDC）」の欄に値を非表示入力すると、環境別の秘密ファイルに保存されます。paramsにはファイル参照だけを置きます。

| paramsの項目 | 設定する値 |
| --- | --- |
| `entraTenantId` | API・Tools・Credentialで共通の認証用テナントID |
| `gateway.authentication.credentialLogin.identity.clientId` | `egakumi-credential` のアプリケーション（クライアント）ID |
| `gateway.authentication.credentialLogin.identity.clientSecretFile` | 標準は `./secrets/entra-credential-secret.txt` |

Coreの構築前に、Web戻り先、Graphデータアクセス許可の不在、クライアントID、Tools用と異なる秘密ファイル参照を確認します。本人確認からGraph接続までの動作は[Gateway配備後](11-deploy-gateway.md#5-本人接続を試験する)に確認します。

次：[05. 公開DNSの準備](05-public-dns.md)

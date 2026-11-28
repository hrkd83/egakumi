# Entraアプリと認可

共用基盤の構築では、認証テナントに4つのシングルテナントアプリを登録します。Azure配備と利用者認証のテナントは、それぞれのparamsで指定します。 [テナントの設定](../../setup/07-params.md#azureとentraのテナント指定)

| アプリ | 使う場所 | Entraの設定と配備入力 |
| --- | --- | --- |
| `egakumi-api` | Gatewayへの委任アクセスと、Graphに対するOn-Behalf-Of（OBO） | `api://<API_APP_CLIENT_ID>/Gateway.Invoke` を公開します。アクセストークンをv2にし、Graphの委任 `User.Read` を設定します。Client IDは `gateway.authentication.entra.audience` に指定し、基盤が生成したOBO用の公開証明書を登録します。 |
| `egakumi-tools` | APIM Credential ManagerのGraph用OAuthプロバイダー | Graphの委任 `User.Read` と、APIMのOAuth同意画面からのWeb戻り先を設定します。 `credentialProviders.graphDelegated` にこのアプリのClient IDと保護されたsecretファイルの参照を指定します。 |
| `egakumi-credential` | Credential Brokerがブラウザーで利用者本人を確認する | Gateway上の `/auth/connections/browser/identity-callback` をWeb戻り先に設定します。本人確認に `openid profile` を使い、Graphのデータ権限は付けません。 `gateway.authentication.credentialLogin.identity` にClient IDと保護されたsecretファイルの参照を指定します。 |
| `egakumi-portal` | Portalへのログインと、画面からのBearer API試用 | Web戻り先は `/api/auth/callback/microsoft` と `/api/api-auth/callback` です。OIDCと `Gateway.Invoke` の委任許可を設定し、基盤が生成したPortal署名用の公開証明書を登録します。 `portal.authentication` にTenant IDとClient IDを指定します。 |

アプリごとの詳細は、第 [02章](../../setup/02-entra-api.md)、[03章](../../setup/03-entra-tools.md)、[04章](../../setup/04-entra-credential.md)、[09章](../../setup/09-egakumi-portal.md)に従います。ToolsとCredentialのsecretは用途ごとに別々の保護ファイルへ置きます。APIのOBOとPortalの署名はそれぞれのKey Vault鍵を使います。

## Gatewayの利用者認可

利用アプリは、認証テナントから `api://<API_APP_CLIENT_ID>/Gateway.Invoke` を要求します。APIMはv2トークンの署名、issuer、テナント、期限、`aud`、委任scopeを検証します。トークンの `aud` にはスコープURIに含まれるClient IDのGUIDが入ります。Portalへのサインイン用IDトークンと、Gateway向けアクセストークンは別です。ユーザー単位の上限は共通configの `gateway.authentication.entra.userLimits` を参照します。 [Gatewayの認証契約](gateway-authentication.md)

| 呼び出し | GatewayからGraphへの接続 |
| --- | --- |
| `Gateway.Invoke` 付きBearerで `GET /tools/graph/v1/me` | OBOで本人のGraphトークンを取得します。APIアプリの委任 `User.Read` が必要です。 |
| APIM購読キーで同じAPIを呼ぶ | Credential Managerで本人のGraph接続を選びます。本人接続セッションが必要です。 |

Portalは前者を含むBearer試用を提供します。購読キーの例外発行と管理は基盤管理者がAPIMで行います。 [Credential Managerの本人接続](credential-manager-login.md)

## 構築と受入

1. 基盤構築前にAPI、Tools、Credentialの3アプリを登録し、公開DNS・ID・paramsを準備します。基盤の `azd up` がOBO・Portal用の公開証明書を生成します。 [基盤の構築](../../setup/08-build-infra.md)
2. APIアプリにOBOの公開証明書を登録し、別の公開証明書でPortalアプリを登録します。Portalの委任許可に管理者同意を行い、paramsを完成させます。 [証明書登録とPortalアプリの作成](../../setup/09-egakumi-portal.md)
3. Gateway、Portalの順に個別配備します。GatewayではBrokerと本人接続、APIとポリシーを確認します。[Gatewayの配備](../../setup/11-deploy-gateway.md) · [Portalの配備](../../setup/12-deploy-portal.md)
4. PortalへのログインとAPI試用、Graph OBO、購読キーとCredential Manager本人接続を確認します。テナント・audience・委任scopeが正しくないトークンや、他の本人接続セッションを拒否することも確認します。 [配備後の受入](../../setup/13-verify-deployment.md) · [実経路試験](../../testing/integration-tests.md)

CodexをOAuthクライアントとして使う場合は、共用基盤の配備後に利用者側でアプリを別途登録します。ChatGPT Enterpriseとの使い分けも同じ手順で確認できます。 [Codexの任意構成](../../tips/codex-gateway-oauth.md)

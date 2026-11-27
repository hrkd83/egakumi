# 02. Entraの5アプリを登録する

[← 01. 前提を確認する](01-prerequisites.md) · [次へ · 03. 公開DNS](03-public-dns.md)

新規構築は次の5アプリを認証テナントへ登録します。登録ごとに固有のApplication（Client）IDを使います。認証テナントは`entraTenantId`、Azure基盤のテナントは`azureTenantId`で指定します。`entraTenantId`を省略した場合はAzure側と同じテナントを使います。Entraアプリの登録・同意とAzureのロール割当は、各テナントの担当者が実施します。

| アプリ | 権限・資格情報 | 環境別params |
| --- | --- | --- |
| [egakumi-api](02a-entra-api.md) | `Gateway.Invoke`を公開。Graph OBO用の委任`User.Read`と専用の公開証明書 | `gateway.authentication.entra.audience` |
| [egakumi-codex](02b-entra-codex.md) | Public clientとAuthorization Code＋PKCE。`Gateway.Invoke`の委任許可と同意 | `codex.clientId` |
| [egakumi-portal](entra-portal.md) | Web、Portal専用の公開証明書、Graphの所属管理用アプリケーション権限、`Gateway.Invoke`の委任許可と同意 | `portal.authentication.tenantId`・`clientId` |
| egakumi-tools | Web、CM標準の戻り先、Graph委任`User.Read`、CM専用のclient secret | `credentialProviders.graphDelegated.clientId`・`clientSecretFile` |
| egakumi-credential | Web、本人確認専用の戻り先とclient secret、OIDCによる本人確認 | `gateway.authentication.credentialLogin.identity.clientId`・`clientSecretFile` |

## 初回の作成と登録順

1. 認証テナントでAPIアプリを登録し、`Gateway.Invoke`を公開します。Graphの委任`User.Read`を設定し、管理者同意を確認します。
2. Codex、Tools、Credentialを登録します。各Client ID、認証テナント、ToolsとCredentialそれぞれの保護された秘密ファイル参照を環境別paramsへ記入します。`codex.clientId`を指定すると、初期構築からアプリ分離の構成を選びます。
3. [初回配備手順](06-deploy.md)に従い`azd provision`で共通基盤を配備します。CoreがAPI OBO用とPortal署名用の公開証明書を用意します。
4. APIアプリへOBO用公開証明書を登録します。Portalアプリを作成し、Portal署名用公開証明書の登録、戻り先と権限の登録・同意を行い、PortalのClient IDを環境別paramsへ記入します。Portalアプリを先に作成していた場合は同じ登録に公開証明書を追加します。
5. GatewayとPortalを順に配備し、各アプリの認可と接続を確認します。[Codexの先行端末](../reference/codex-endpoint-rollout.md)でGateway接続を確認してから配布します。

アプリの表示名は用途を識別できる名前にします。構築担当者へ引き継ぐIDは「アプリケーション（クライアント）ID」です。認証用テナント、各ID、秘密値と実環境の確認結果は環境別の保護領域へ保存します。

## ToolsとCredentialのWebアプリを登録する

第1章で準備したparamsから、CMと本人確認の戻り先を確認します。

```sh
python3 -I -B scripts/prepare_registration.py --config "$DEPLOY_PARAMS_FILE"
```

`credentialManagerRedirectUri`はその環境のAPIM名を含みます。本人確認の戻り先には`gatewayOrigin`と表の固定パスを使います。APIMインスタンス識別子を変更した環境を再構築するときは、CMの戻り先を再計算してからEntraへ登録します。実値を含む出力を保存する場合は、同じ環境の`.local/<environment>/evidence/`を使います。

Azureポータルで認証テナントを選び、「Microsoft Entra ID」→「アプリの登録」→「新規登録」を開きます。次の2アプリをSingle tenantで作成します。「認証」ではWebを選び、Implicit grantとPublic client flowsはOFFにします。

| 設定 | egakumi-tools | egakumi-credential |
| --- | --- | --- |
| Web戻り先 | `https://authorization-manager.consent.azure-apim.net/redirect/apim/<APIM_NAME>` | `https://<GATEWAY_HOST>/auth/connections/browser/identity-callback` |
| 委任スコープ | `openid`、`profile`、`email`、`offline_access`、Graphの`User.Read` | OIDCの`openid`、`profile` |
| Graphのアプリケーション権限 | なし | なし |
| 「証明書とシークレット」 | CM専用のclient secretを発行 | 本人確認専用のclient secretを発行 |

Credentialの新規登録時に既定のGraph `User.Read`が追加されていれば取り除きます。購読キーのGraph要求は、Credentialで本人を確認し、本人がToolsで再同意して作成したCM接続を使います。BearerのGraph要求ではAPIアプリの委任許可を使ってBrokerがOBO交換します。

管理者が権限の種類と対象を確認して同意を付与します。secretの有効期限と更新担当を記録し、発行直後の「値（Value）」をそれぞれ別の保護ファイルへ保存します。配備ではToolsのsecretをCMへ、CredentialのsecretをBroker専用のKey Vault参照へ渡します。 [MicrosoftのCM設定](https://learn.microsoft.com/en-us/azure/api-management/credentials-how-to-azure-ad) · [本人接続の受入](credential-manager-login.md)

## 既存環境の変更

既存環境のCM接続・更新トークンは発行元Client IDにひも付きます。切替を行う場合は新しいアプリと接続を別に受け入れ、利用者の再同意と旧接続の停止を確認してから旧アプリの権限や資格情報を回収します。 [移行と回収の順序](../reference/entra-app-rollout-plan.md#工程8旧共用アプリと移行設定を撤去する)

[次へ · 03. 公開DNS](03-public-dns.md)

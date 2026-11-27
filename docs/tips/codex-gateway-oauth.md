# TIPS: CodexをAIゲートウェイにOAuthで接続する

基盤の初回構築と受入が済んだ環境で、Codexを採用するときの手動手順です。AzureポータルとMicrosoft Entraの画面で登録・確認し、利用端末でCodexの `config.toml` を編集します。リポジトリのクローン、構築資材、設定生成スクリプトは不要です。 [初回構築の受入](../setup/13-verify-deployment.md)

## 1. EntraでCodex専用アプリを登録する

`egakumi-api` と同じEntraテナントに `egakumi-codex` をPublic clientとして登録します。固定のループバックURIを設定し、既存APIの委任権限 `Gateway.Invoke` を追加して管理者同意を確認します。Client secretは作成しません。 [Azureポータルでの登録手順](codex-entra-registration.md)

## 2. Azureポータルで端末設定に使う値を確認する

| 確認する値 | 確認する画面と項目 |
| --- | --- |
| 認証用テナントID、Codex用Client ID | Microsoft Entra ID → アプリの登録 → `egakumi-codex` → 概要 → 「ディレクトリ（テナント）ID」「アプリケーション（クライアント）ID」 |
| OAuthスコープ | Microsoft Entra ID → アプリの登録 → `egakumi-api` → APIの公開 → `Gateway.Invoke` の完全なスコープ名。標準構成は `api://<API_APP_CLIENT_ID>/Gateway.Invoke` |
| Gatewayの公開ホスト名 | Application Gateway → リスナー → `listener-https` → ホスト名 |
| APIのパス | API Management → API → `Openai Inference V1` →「設定」でAPI URLサフィックス `models/openai/v1`、「デザイン」で `POST /responses` を確認 |
| モデル名 | Gatewayの受入試験で `POST /responses` が受理したリクエストの `model`。別名がある場合も、実際に送信した値を使う |

公開ホストとAPI URLサフィックスから `base_url` を組み立てます。標準例は `https://gateway.example.com/models/openai/v1` です。Codexが末尾に `/responses` を付けます。 [Application Gatewayのリスナー](https://learn.microsoft.com/en-us/azure/application-gateway/configuration-listeners) · [APIMのAPI URLサフィックス](https://learn.microsoft.com/en-us/azure/api-management/import-and-publish)

対象モデルが公開入口からEntra Bearerで認可され、Responses APIがSSEで応答することを前提にします。この構成が受入済みであれば、Codex接続のためのAPIM・App Gateway・WAFの設定変更や再配備は不要です。 [GatewayのBearer受入試験](../testing/integration-tests.md#entra-gateway-oauthと専用open-productの追加受入)

## 3. 利用端末でCodexの設定を編集する

CLIではGateway OAuthのトークンを既定のAuthorization Bearerで送る0.166.0以降を使用します。Desktopは対象版でGateway OAuthの動作を確認します。 [CodexのGateway OAuth](https://developers.openai.com/codex/enterprise/connect-to-a-gateway#use-native-gateway-oauth)

現在の設定を控え、macOS／Linuxでは `~/.codex/config.toml`、Windowsでは `%USERPROFILE%\.codex\config.toml` をテキストエディターで開きます。`CODEX_HOME` を変更している場合は、そのディレクトリのファイルを使います。既に同じキーやテーブルがある場合は、該当箇所を編集します。

端末でEgakumiを既定にする場合の例です。先頭の3項目は最初のテーブルより前に置きます。公開例のホスト名とプレースホルダーを手順2で確認した値へ置き換えます。

```toml
model = "<RESPONSES_MODEL>"
model_provider = "egakumi"
web_search = "disabled"

[model_providers.egakumi]
name = "Egakumi"
base_url = "https://gateway.example.com/models/openai/v1"
wire_api = "responses"
requires_openai_auth = false

[model_providers.egakumi.gateway_oauth]
authorization_url = "https://login.microsoftonline.com/<AUTH_TENANT_ID>/oauth2/v2.0/authorize"
token_url = "https://login.microsoftonline.com/<AUTH_TENANT_ID>/oauth2/v2.0/token"
client_id = "<CODEX_APP_CLIENT_ID>"
scopes = ["api://<API_APP_CLIENT_ID>/Gateway.Invoke", "offline_access"]
redirect_port = 43127
```

認可・トークンURLのテナントIDには認証用の値を使います。`client_id` にはCodex専用アプリのClient ID、`scopes` の先頭にはAPI側で公開した完全なスコープ名を入れます。`offline_access` はトークン更新用です。`redirect_port` は手順1で登録したURIに合わせます。BearerはCodexがサインイン後に付与します。現行のResponses APIはfunctionツールを受け付けるため、この設定例ではモデルの組み込みWeb検索を無効にします。

対象版の組み込みカタログに必要なモデルのメタデータがあることを確認します。独自のモデル別名を使う場合にカタログが必要なら、Gateway管理者が用意したCodex形式のカタログを端末に置き、`model_catalog_json` に絶対パスを設定します。`model` とカタログの `slug` はGatewayが受理する名前に揃えます。 [Codexのモデルカタログ](https://developers.openai.com/codex/enterprise/roll-out-a-gateway#start-with-a-local-modelsjson-file)

### 同じ端末でChatGPT Enterpriseも使う場合

CLIの既定設定を維持する場合は、`config.toml` に上の2つの `[model_providers.egakumi...]` テーブルを追加します。例の先頭3項目は同じディレクトリの `egakumi.config.toml` に置いてください。会社Gatewayは `codex --profile egakumi` で起動し、既存のEnterprise接続は従来の起動方法を使います。独自カタログが必要な場合はプロファイル側に設定します。 [CLIのプロファイル](https://developers.openai.com/codex/config-advanced#profiles)

Desktopは、対象版の「設定」→「Model Providers」で切替が利用できる場合に、Egakumiを選んで新規チャットを開きます。既存のEnterprise接続に戻したときも新規チャットを開き、接続先を確認します。CLIのプロファイルとは別に確認します。 [Desktopでのプロバイダー設定](https://developers.openai.com/codex/enterprise/connect-to-a-gateway)

## 4. サインインして接続を確認する

1. CLIを起動するかDesktopを再起動し、Egakumiのプロバイダーと対象モデルを選びます。
2. Gatewayのサインイン案内からブラウザーで会社のEntraアカウントにサインインします。MFAや条件付きアクセスは会社の運用に従います。
3. 新規セッションで機密情報を含まない短い質問を送り、応答の逐次表示と完了を確認します。同じセッションで追加の質問を送り、利用予定のローカルツールも試します。CLIでは `/status` でプロバイダーとモデルを照合します。Gateway管理者は監査ログで利用者・モデル・接続を確認します。
4. トークン更新または再サインインが成立することを確認します。併用端末ではEnterprise側でも新規セッションを開き、接続先を照合します。

端末設定を戻す場合は、今回追加した項目を取り除き、編集した項目を作業前の値へ戻します。組織として利用を停止する場合は、管理者がEntraで専用アプリの割当や同意を見直し、発行済みトークンの有効期限と処理中の接続を別に確認します。

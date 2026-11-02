# Portalの旧リソースグループ移行資料

> 履歴資料：2026-11-01の整理開始前の文書を保存しています。旧リソース配置・CI変数・確認APIに基づく手順であり、現行Coreの構築・更新には使用しません。現行の配置と配備手順は [Portalの配備](../setup/portal.md)、更新・撤去は [運用手順](../operations.md)を参照してください。

以下は旧文書の本文です。相対リンクは保存先に合わせて調整しています。

# PortalのRG配置を変更する

旧版でネットワークRGに作成したPortal資源を、役割別の配置へ整理するための手順です。対象環境の名前と実リソースIDを照合してから実施します。作業中はPortalの停止時間を確保し、Coreの構築とGitHub Actionsのアプリ配備を直列に実行します。

## 配置先

| 資源 | 旧配置 | 新配置 |
|---|---|---|
| Portal用ACR | `-nw` | `-svc` |
| Portal用CAE | `-nw` | `-svc` |
| PortalのCA | `-nw` | `-svc` |
| `id-<環境名>-<製品名>-portal-pull` | `-nw` | `-id`に新しいIDを作成 |
| ACRのPE・Private DNS・通信規則・入口経路 | `-nw` | `-nw` |

ACR・CAEは[Azureの移動対応表](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/move-support-resources)でRG移動に対応しています。CAを含む依存資源・ネットワーク設定・サービス固有の制約は、対象環境に対するAzureの移動検証で確認します。

ユーザー割り当てマネージドIDは同表でRG移動に非対応です。新しいIDの作成によってprincipal IDも変わるため、新IDへのAcrPull付与とCAの割り当て切り替えを行います。

現在のCAE名は`cae-<環境名>-<製品名>`、PortalのCA名は`ca-<環境名>-<製品名>-portal`です。この手順のRG移動はリソース名を変更しません。旧名の`cae-<環境名>-<製品名>-portal`・`portal`が残る環境は、先に保存対象のイメージや設定を記録し、旧CA・旧CAEを削除して現在の名前で再作成します。CAEを再作成すると内部VIP・defaultDomainも変わるため、続く`azd up`でDNS・経路・App Gatewayの参照を更新します。

## 1. 現在の状態を記録する

Azure Portalで対象のSubscription・製品名・環境名と5RGの所有タグを照合します。進行中の配備がある場合は、結果を確認してから移行を始めます。

次の情報を、導入先のアクセス管理された保存先に記録します。

- ACRの名前・リソースID・login server。Portalイメージがある場合はタグとdigestも記録します。イメージの確認はACRのPEへ到達できる管理サブネットの端末やrunnerで行います。
- CAEの名前・リソースID・リージョン・ACAサブネットID・defaultDomain・内部VIP。
- CAのイメージdigest・環境変数・secret参照・Ingress・プローブ・レプリカ設定・割り当てID・ACR認証設定。
- 現在のpull IDのリソースID・principal ID・割り当て先・ロール。
- ACRのPE接続先ID、Private DNS、App GatewayのPortal用バックエンドとヘルスプローブ。
- `.state/latest.json`とGitHub Environment `portal`の変数・Actions用IDの権限スコープ。

CAの設定には秘密値を含む環境変数等が入り得るため、取得したJSONはGit管理外の保護された場所へ保存します。

## 2. ACR・CAE・CAの移動を検証する

1. Azure Portalで旧ネットワークRGを開き、Portal用ACR・CAE・CAを選択します。
2. 「移動」→「別のリソースグループに移動する」で、同じSubscriptionのサービスRGを指定します。
3. Azureの移動検証で、選択した資源と依存資源が移動可能か確認します。Portal以外のサービスやVNet・PE・Private DNSを移動する必要があると表示された場合は、対象を広げる前に依存関係を確認します。
4. 検証に失敗した場合は既存資源を保持し、表示されたサービス固有の制約に応じた再配置計画へ切り替えます。検証結果が確定した構成で、保持したいイメージ・アプリ設定を引き継ぐ方法を決めます。
5. 検証成功後に移動を実行します。移動後のACR・CAE・CAがサービスRGにあり、CAの所属CAEがサービスRG内の実リソースIDを参照することを確認します。

移動後は名前・イメージdigest・設定・内部VIP・defaultDomainを移動前の記録と比較します。ACRのPEの接続先もサービスRG内のACRの実リソースIDと照合します。PE接続先の修正に再作成が必要な場合は、ネットワークRG内でPEとDNS zone groupを再準備し、private IP・DNS・接続承認を確認します。CAは同じリージョンの対象CAEに所属する状態へ揃えます。

PEの再作成でprivate IPが変わった場合は、PE側NSGの`allow-aca-acr`と管理側NSGの`allow-runner-acr`の宛先を、ACRのlogin/data endpointに割り当てられた新しいprivate IP群へ更新します。既存の送信元範囲とTCP 443の条件を維持し、管理サブネットからの名前解決・ACR到達と、ACAからのイメージ取得経路を確認してからID切り替えへ進みます。CAEの再配置でdefaultDomainが変わった場合は、Core再構築後に使う新しいDNS・経路を確認し、参照がなくなった旧設定を最後に整理します。

## 3. 新しいpull IDとACR権限を準備する

この段階は、ACR・CAE・CAの移動または再配置が完了し、サービスRGに実資源があることを確認してから実施します。例はリポジトリのルートからAzure CLIを実行する構成です。変数は導入先の値へ置き換えます。

```sh
SUBSCRIPTION_ID='<subscription-id>'
RESOURCE_STEM='<environmentName>-<productName>'
LOCATION='<location>'
ID_RG="rg-${RESOURCE_STEM}-id"
SVC_RG="rg-${RESOURCE_STEM}-svc"
PULL_ID_NAME="id-${RESOURCE_STEM}-portal-pull"
ACR_NAME="acr$(printf '%s' "$RESOURCE_STEM" | tr -d '-')"
APPLICATION_NAME="ca-${RESOURCE_STEM}-portal"
```

ID用RGへ、新しいユーザー割り当てIDを作成します。移行後の通常の`azd up`も同じ名前・IDを利用します。

```sh
az deployment group create \
  --subscription "$SUBSCRIPTION_ID" --resource-group "$ID_RG" \
  --name "${RESOURCE_STEM}-portal-identity-migration" \
  --template-file infra/modules/portal-identity.bicep \
  --parameters stem="$RESOURCE_STEM" location="$LOCATION" \
  --output none

PULL_ID=$(az identity show --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$ID_RG" --name "$PULL_ID_NAME" --query id --output tsv)
PULL_PRINCIPAL_ID=$(az identity show --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$ID_RG" --name "$PULL_ID_NAME" --query principalId --output tsv)
```

移動済みACRへ、新IDのAcrPullを設定します。このモジュールは通常構築と同じロール割り当て名を使うため、以後の`azd up`でも同じ割り当てを管理できます。ACRの組み込みネットワーク設定は、CoreのPremium・PE利用の設定へ揃えます。

```sh
az deployment group create \
  --subscription "$SUBSCRIPTION_ID" --resource-group "$SVC_RG" \
  --name "${RESOURCE_STEM}-portal-registry-migration" \
  --template-file infra/modules/portal-registry.bicep \
  --parameters stem="$RESOURCE_STEM" location="$LOCATION" \
    pullIdentityId="$PULL_ID" pullIdentityPrincipalId="$PULL_PRINCIPAL_ID" \
  --output none
```

## 4. CAのIDとACR認証を切り替える

対象CAがサービスRG内のCAEに所属し、イメージ・アプリ設定が保存済みの内容と一致することを先に確認します。新IDを追加し、ACR認証をそのIDへ切り替えます。

```sh
az containerapp identity assign \
  --subscription "$SUBSCRIPTION_ID" --resource-group "$SVC_RG" \
  --name "$APPLICATION_NAME" --user-assigned "$PULL_ID" --output none

ACR_SERVER=$(az acr show --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$SVC_RG" --name "$ACR_NAME" --query loginServer --output tsv)

az containerapp registry set \
  --subscription "$SUBSCRIPTION_ID" --resource-group "$SVC_RG" \
  --name "$APPLICATION_NAME" --server "$ACR_SERVER" --identity "$PULL_ID" --output none
```

CAの割り当てIDと、ACR認証設定の`identity`が新しいIDを参照することを読み戻します。既存CAがprivate ACRのイメージを使用している場合は、新しいリビジョンで取得に成功しReadyになることを確認します。初期MCRイメージを使用している段階では、private ACRからの取得を第6段階のActionsによる本体配備で確認します。新IDはACRスコープのAcrPull、移行を実行する構築者はID割り当てとロール付与を実行できる権限を使用します。

旧IDは切り替えが確認できた後、CAの割り当てから外します。他の利用先とロールも確認し、不要になった旧ID・旧ロール割り当てを整理します。

## 5. Coreの構築を再実行する

サービス本体がサービスRG、新しいpull IDがID用RG、PE・DNS・経路がネットワークRGにある状態で、従来のparams・azd環境を使って`azd up`を実行します。

構築hookは次を確認します。

- 旧ネットワークRGに、今回の名前のACR・CAE・CAが残っていないこと。
- 既存CAのID・リージョン・所属CAEがサービスRGの配置と一致すること。
- 既存CAがID用RGのpull IDを割り当て、ACR認証にも同じIDを使用していること。
- CAの配備状態が`Succeeded`であること。

確認できた既存CAは初期イメージで更新せず、配置済みのイメージ・環境変数・Ingressを保持します。実際のCAE内部VIP・defaultDomainに合わせ、ネットワークRG内のPrivate DNS・App Gateway側経路を構築します。

完了後は新しい`.state/latest.json`と`portal` outputを確認します。旧出力のRGやIDは、保存した移行前記録として扱います。

## 6. GitHub Actionsと疎通を確認する

GitHub Environment `portal`の次の設定と、Actions用IDのAzure RBACを更新します。

| 設定 | 移行後の値 |
|---|---|
| `PORTAL_RESOURCE_GROUP` | `portal.resourceGroup`のサービスRG |
| `PORTAL_APPLICATION_NAME` | `portal.applicationName`のCA名 |
| `PORTAL_PULL_IDENTITY_ID` | `portal.pullIdentityId`のID用RGのID |
| `PORTAL_ACA_ENVIRONMENT` | 移行後のCAE名 |
| `PORTAL_ACR_NAME` | 移行後のACR名 |
| `PORTAL_EXPECTED_EGRESS_IP` | 最新outputのFirewall公開IP |
| Actions用IDの配備権限 | サービスRGの読取・配備、CA更新、CAE/ACR読取 |
| Actions用IDのイメージ配布権限 | 移行後のACRスコープのAcrPush |
| Actions用IDのID割り当て権限 | ID用RGのpull IDスコープのManaged Identity Operator |

更新後にActionsを実行し、イメージ取得とアプリの更新を確認します。初期画面またはPortal本体の公開URL、App Gatewayのbackend health、CAのReady、ACRのprivate DNS解決、Firewall経由の送信を確認します。Portal本体配備後は`/api/health`・`/api/egress`も[通常の受入手順](../setup/portal.md#受入確認)で確認します。

## 参考

- [Azureリソースの移動対応表](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/move-support-resources)
- [リソースを別RGまたはSubscriptionへ移動する](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/move-resource-group-and-subscription)
- [CAのID割り当てコマンド](https://learn.microsoft.com/en-us/cli/azure/containerapp/identity?view=azure-cli-latest)
- [CAのレジストリ認証設定コマンド](https://learn.microsoft.com/en-us/cli/azure/containerapp/registry?view=azure-cli-latest)

[履歴一覧](README.md)

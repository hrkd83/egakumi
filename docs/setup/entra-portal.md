# Portal専用Entraアプリの手動登録

状態：構築・手動登録手順のWIPです。工程3の資材・運用手順はローカル検証済みです。対象環境の適用・受入状況は保護記録で確認します。[工程4の詳細設計案](../reference/portal-stage4-design.md)は判断前で、以下の工程4用設定はその設計に含む未確定案です。

初期構築の後に、この手順でアプリ作成と公開証明書の登録を一度の依頼にまとめます。Entraを管理する担当がAzureポータルで操作します。構築資材からEntraアプリを作成したり、管理者同意を自動付与したりしません。

## この手順が対象にする工程

「専用Portalアプリ」は、Microsoft Entra IDの「アプリの登録」で新しく作る、Portal用の認証アプリを指します。Portalを動かすACAの枠は共有資材で準備し、その枠へ後からPortalのコンテナイメージを配備します。既存のAPI用アプリやCM用アプリと、今回作るPortal用の認証アプリの役割を区別します。

以下は現在の開発・段階受入の順序です。**工程3・4の実装が完成した資材を使う通常の新規構築では、共有資材・鍵準備 → Entraアプリの一度の作成・設定 → 完成済みPortalイメージのビルド・配備・受入**になります。通常の構築担当が工程4のソースを実装したり、そのための別のEntra編集依頼を出したりする手順にはしません。API／CM用アプリの従来の事前準備は維持します。

| 順序 | 実施すること | 担当 |
| --- | --- | --- |
| 1. 共有資材の構築 | KV・管理VM・ACAなどの共有資源とPortalの初期サンプルを用意。KV内に秘密鍵・自己署名証明書を発行し、公開`.cer`を取得 | 構築資材 |
| 2. 新しいPortal用Entraアプリの作成 | 新規アプリ作成、公開証明書、Portalログインの戻り先、Graph管理権限、判断済みの工程4用設定を一度の依頼で登録 | Entra管理担当が手動操作。本書の対象 |
| 3. 工程3のPortal配備・受入 | 登録結果を使ってPortal本体をビルド・配備し、証明書認証・Graph管理・定期ジョブ等を受入 | Portalの配備資材と受入担当 |
| 4. 工程4の実装・追加配備・受入 | 詳細設計の判断後、API用認証・トークン管理・各Tryへの接続を正式実装し、追加配備・受入 | Portalの実装・配備・受入担当 |

本書の「初回登録」は、順序2の**新しいEntraアプリを作成し、必要な証明書と設定をその作成時にまとめて登録すること**です。証明書更新時は、このアプリへ新しい公開証明書を追加します。

順序2で申請を一度にまとめるため、Entraの設定に影響する工程4の設計を先に確定します。工程4用設定を登録しただけでは、API用OAuthや各Tryが実装・受入済みになるわけではありません。[詳細設計](../reference/portal-stage4-design.md)では、API認証の流れ、ライブラリ、保存・更新・失効、全Tryと受入をまとめて判断します。URIやscope名だけの承認依頼には分割せず、判断前の内容を確定済みの登録指示として扱いません。

## 初期構築で用意するもの

`azd up`は共有資源、Gateway、Portal ACAの初期サンプルまでを用意します。管理VMへ`portal-auth-certificate.ps1`を配置・実行し、KVが認証専用の3072bit RSA鍵と自己署名証明書を生成します。秘密鍵は非exportableです。HTTPS用のACME証明書とは別です。

`postprovision`はVM Run Commandの正常終了と出力を照合し、公開証明書を`.local/<environment>/generated/portal-auth-public.cer`へ、固定参照を同じ環境の`state/portal-auth-certificate.json`へ保存します。単にファイルが存在するだけで初期構築の成功とは判断せず、今回のコマンドの終了結果を確認してください。

この初期発行は全体構築のCoreとhookに組み込まれており、構築後に別の発行コマンドを追加で実行する手順にはしません。すでに構築済みの環境へ証明書準備だけを追加する場合は、[初期発行の限定追加手順](../operations/portal-authentication.md#初期構築と既存環境への限定追加)を使います。どちらも公開`.cer`を取得してから、以下の一度のEntra登録依頼へ進みます。

```mermaid
flowchart LR
  Build[初期構築資材] --> VM[管理VMの発行スクリプト]
  VM -->|生成要求| KV[KV内で秘密鍵・自己署名証明書を生成]
  KV -->|公開証明書だけ| CER[公開 .cer]
  CER -->|手動アップロード| Entra[専用Portalアプリ]
  Entra -->|Tenant ID / Client ID| Deploy[Portal本体をビルド・配備]
  Deploy --> ACA[ACA / Graphジョブ]
  ACA -->|専用UAMIで署名要求| KV
  ACA -->|署名したclient assertion| Entra
```

KVの秘密鍵、PFX、backing secretは取り出しません。Entra担当へ渡す成果物は公開`.cer`です。申請票、メール、manifestを生成する処理はありません。公開証明書も実資源に対応するため、OSSのソース・Git・配布物には追加しません。

## 一度の登録依頼に含める設定

以下のドメインとIDは独立した例示・プレースホルダーです。実際の値は対象環境の保護入力・保護stateから担当者間で確認します。

| 項目 | 設定 |
| --- | --- |
| 登録先tenant | Portalの利用者を認証するEntra tenant。KV/ACAのAzure tenantとは区別する |
| アプリ | 専用Portalアプリ。名前は組織の命名規則で決める |
| サポートされるアカウント | この組織ディレクトリ内のアカウントのみ（Single tenant） |
| プラットフォーム | Web |
| PortalログインのRedirect URI | `https://portal.example.com/api/auth/callback/microsoft` |
| 工程4のAPI用Redirect URI | `https://portal.example.com/api/api-auth/callback`。API試行用の追加認証からPortalへ戻る固定経路の未確定案。初回登録前に工程4設計を説明して判断を受ける |
| 証明書 | 初期構築で取得した`portal-auth-public.cer` |
| Graph Application権限 | `GroupMember.Read.All`、`User.ReadBasic.All`。Portal管理と定期照合用 |
| GraphのOIDC Delegated権限 | `openid`、`profile`、`email`、`offline_access`。ID tokenとAPI用更新トークン要求に対応する同意を同じ作業で確認 |
| API Delegated権限 | 既存のAPI用アプリが公開する`Gateway.Invoke`を、新しいPortal用アプリに追加する工程4用の先行案。ユーザー本人のアクセストークンでGateway APIを呼ぶための許可。API用アプリの構築・scope公開は[Entra準備](02-entra.md)の前提 |
| 管理者同意 | 上記権限を組織の手続きに従って同意 |
| Implicit grant / Public client flows | 無効 |
| 新Portalのclient secret | 作成しない |

Portalログインで要求する`openid profile email`はOIDCのscopeです。API用OAuthの`offline_access`は更新トークンを要求するscopeです。どちらもKV鍵やGraph Application権限を表すものではありません。Portalログイン用ID tokenをAPI試行へ送信しません。Graph下流OBOは工程5の別実装です。

## Azureポータルで操作する

1. 認証対象tenantへ切り替え、「Microsoft Entra ID」→「アプリの登録」→「新規登録」を開きます。専用PortalアプリをSingle tenantで作成し、WebのPortalログイン戻り先を指定します。
2. 「認証」でWebのRedirect URIを確認し、判断済みのAPI用戻り先も同時に追加します。Implicit grantのアクセストークン・IDトークンのチェック、Public client flowsはOFFにします。ローカル開発を行う場合だけ、専用の開発用アプリとlocalhost戻り先を別途設計します。
3. 「証明書とシークレット」→「証明書」→「証明書のアップロード」から公開`.cer`を登録します。「クライアントシークレット」で新しいsecretを作成しません。
4. 登録されたthumbprintと有効期限が、保護stateの`current.certificateThumbprint`・`current.expires`と一致することを確認します。別用途のTLS証明書を選んでいないことも確認します。
5. 「APIのアクセス許可」→「アクセス許可の追加」でMicrosoft Graphの「アプリケーションの許可」を選び、`GroupMember.Read.All`と`User.ReadBasic.All`を追加します。Graphの「委任されたアクセス許可」ではOIDC用の`openid`・`profile`・`email`・`offline_access`を確認します。次に「所属する組織で使用しているAPI」からAPI用アプリを選び、「委任されたアクセス許可」の`Gateway.Invoke`を追加します。設計どおりID tokenで本人を確認する構成では、未使用の既定`User.Read`を新Portalアプリに残さないよう確認します。
6. 必要な管理者同意を実施し、許可の種類・対象API・同意状態を確認します。Entra担当が必要とする申請・承認は組織の手続きに従います。
7. 「概要」のDirectory tenant IDとApplication client IDを構築担当へ安全に渡します。Object IDをClient IDの代わりに入力しません。利用者割当必須や条件付きアクセスがある場合は、受入用の利用者も許可されていることを確認します。

## 登録後のPortal配備

環境別paramsの`portal.authentication`へ次の2項目を設定します。実値は`.local/<environment>/params.local.json`だけで管理します。

```json
{
  "portal": {
    "publicHostName": "portal.example.com",
    "authentication": {
      "tenantId": "<PORTAL_ENTRA_TENANT_ID>",
      "clientId": "<PORTAL_ENTRA_CLIENT_ID>"
    }
  }
}
```

これは入力項目を示す例であり、プレースホルダーのまま構築できるJSONではありません。鍵の版とthumbprintは保護stateから読みます。明示入力した場合はstateと一致しなければ停止します。登録前は`authentication`全体を省略して初期構築できます。Portal本体配備では登録結果がなければ停止し、CMのsecretへ自動fallbackしません。

```sh
python3 -B scripts/deploy_portal.py --config "$DEPLOY_PARAMS_FILE"
python3 -B scripts/deploy_portal.py --config "$DEPLOY_PARAMS_FILE" --execute
```

計画を確認してから実行します。共通基盤を作り直す必要はありません。ログイン、Graph管理、定期ジョブの実受入は[認証試験](../testing/portal-authentication-tests.md)に従います。HTTPヘルスの成功だけで認証完了とは判断しません。

更新時も新しい公開証明書の登録が必要です。期限は既定で12か月であり、永続証明書ではありません。更新手順は[Portal認証の運用](../operations/portal-authentication.md)を参照してください。

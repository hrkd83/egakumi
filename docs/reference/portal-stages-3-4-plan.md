# Portal 工程3 工程4 実施計画

この文書は工程3・4の設計当時の記録です。現行のPortalは本人のBearer試用を提供し、購読キーは基盤管理者がAPIMで管理します。最新の作業は[Entra登録](../setup/09-egakumi-portal.md)と[Portal配備](../setup/12-deploy-portal.md)を参照してください。

状態はWIPです。工程3の証明書認証と工程4のAPI試用を、新規構築の資材・手順に組み込んでいます。ローカル検証を揃えてから、空のAzure環境でAPIM、Portalログイン、API試用の順に受け入れます。Azure／Entraの実反映と実受入は別に記録します。

[統合・Azure適用計画](portal-stage3-azure-acceptance-plan.md)に、共有差分の照合結果、最初のAzure適用の変更範囲と、適用前の判断・受入順序を記載する。ローカル実装の完了を、稼働中Portalの認証切替完了として扱わない。

SQLはProxy維持と決定し、Redirect変更、PortalのTedious/Kysely接続層の変更、Native試作は取りやめた。Networkの取消コミット`5f72d83`をPortalタスクが取り込み、SQL policyのProxy・SQL向けNSG4件のTCP1433と、工程3の証明書実装の保持を確認した。取消後の実装資材は`511575f`と一致する。AzureへRedirectは未適用のためAzure切戻し操作は不要。証明書認証の工程3と工程4の設計は独立して継続し、driver選定・Redirect検証を待機条件にしない。

共有基盤の初期構築では、`azd up`でPortalのACA初期サンプルを起動し、証明書専用のACA環境でPortal署名Jobを実行する。JobがKVへ要求し、KV内にPortal認証専用の非exportable秘密鍵と自己署名証明書を生成する。公開証明書を取得した後、Entra担当が専用アプリを手動作成・設定する。登録結果を反映してからGatewayとPortalを順に個別配備し、Portalの初期サンプルを本体へ更新する。 [初回構築手順](../../README.md#新規クローンから構築する)

全工程の位置付けは[Entraアプリ分離の作業計画](entra-app-rollout-plan.md)を参照する。工程3の証明書認証と工程4のAPI用OAuthは実装済みで、現行の配備候補を検証する。

## 構築と担当の分担

| 工程 | 担当と処理 | 完了条件 |
| --- | --- | --- |
| 初期構築 | `azd up`で共有ネットワーク、KV、管理VM、非公開ACR、ACA環境、共有SQL、Portalの初期サンプルを用意。証明書専用JobがKV内に鍵・自己署名証明書を生成する | Coreの構築記録とJobの正常終了、Portal署名用とGateway OBO用の公開証明書を確認する |
| Entra登録 | Entra担当がAzureポータルで専用Portalアプリを作成。公開証明書、Webの戻り先、工程3・4で必要なAPI権限と管理者同意をまとめて設定する | 構築担当がClient IDなどの登録結果を受け取り、公開証明書と設定内容を照合できる |
| Gateway配備 | APIアプリにOBO用公開証明書を登録した後、Gatewayを個別配備する | Gateway配備の正常終了と接続先を確認する |
| Portal配備 | 登録結果を保護入力へ設定し、Portalイメージをビルド・配備。DB初期化、認証設定、権限、定期ジョブ、既存の入口・APIM連携も同じ工程で扱う | Portalログインと管理機能を受入。その後、工程4の全対象を受入する |

外部へ渡す資材の成果物は公開証明書とする。申請票、依頼メール、申請用manifest等は生成せず、Entra担当が使う手動構築手順を別途整備する。Entraアプリの作成・証明書登録・権限付与を資材から自動実行しない。

この計画の「専用Portalアプリ」は、新しく作成するPortal用のEntraアプリ登録を指す。「初回登録」は、共有資材と公開証明書の準備後に、その新規作成・公開証明書・戻り先・権限を一度の依頼で登録する工程である。ACAの枠は初期構築で準備し、Portalのコンテナ本体は登録結果を受け取ってから配備する。[手動登録手順の全体位置付け](../setup/09-egakumi-portal.md#初期構築で用意するもの)で担当と順序を確認する。

Portal用EntraアプリのClient IDが未確定でも、初期構築は実行できるようにする。CM/API用アプリなど、Gatewayが従来必要としているEntra事前準備は別の前提として維持する。

## 実装前に確認する事項

| 判断事項 | 現在の案と確認内容 |
| --- | --- |
| 証明書の発行元 | 初回発行・更新ともにKVの自己署名を前提とする。外部CAやLet's Encryptによる認証用証明書の発行を、この計画の前提に含めない |
| 有効期間と更新 | 12か月有効、60日前から準備、更新時に新しい秘密鍵を生成する案。Microsoft共通の必須期間として扱わない |
| 構築入口 | `azd up`はCore、Portal初期サンプルと公開証明書の準備まで。Entraの手動登録後、GatewayとPortalを個別配備する |
| 工程4のEntra設定 | 合意済み。`Gateway.Invoke`委任権限、API用OAuthの固定callback、Graph権限・OIDC scope・同意を[手動登録手順](../setup/09-egakumi-portal.md)へ確定。初回の作成・設定にまとめる |
| 工程4の認証処理 | 合意済み。PortalログインとAPI用追加認証を分け、Better Auth公開OAuth関数とJOSE、専用の暗号化SQL領域・KV保存暗号鍵、全Tryの本人・方式と終了の契約を採用する |

初回のアプリ作成と公開証明書登録は一度の依頼にまとめる。工程3・4の資材とローカル検証を揃えてから新規Azure環境を構築する。標準の初回配備ではAPI試用も準備し、実環境ではPortal認証・Graph管理を先に確認してからAPI試用を受け入れる。

工程1・2の受入範囲と残る制御を元の作業計画で照合する。API用アプリの公開scope、キー／Bearerの入口認可、GraphのCM本人接続、非同期結果や接続の所有者に関する契約を、工程3・4の前提として確認する。前提が不足している場合は不足箇所を示し、今回の改修へ未判断のGateway制御変更を混ぜない。

## 現在の着手状況

| 領域 | 現在位置 | 次に確認すること |
| --- | --- | --- |
| Portal認証とGraph | `client-assertion.ts`、認証設定、Graphクライアント、照合ジョブを実装・ローカル検証済み | 専用Entraアプリの登録後、実ログイン・Graph管理・定期ジョブを受入する |
| schemaと配備 | 認証用設定、専用ID、鍵scopeの権限、アプリ・ジョブの設定を実装・ローカル検証済み | 統合した資材の配備差分を確認し、対象環境へ適用する範囲の判断を受ける |
| 初回証明書生成と構築入口 | 固定イメージの証明書専用ACA Job、公開証明書の収集、CoreとGateway・Portalの個別配備を実装済み | 現行差分の合成回帰を確認し、Azureで発行・照合・再実行を受け入れる |
| 証明書更新の運用 | 更新準備・登録確認・切替・確認・復旧・旧版廃止の運用スクリプトを実装・ローカル検証済み | 手動Entra登録を挟む更新・復旧・廃止を実受入する |
| API用OAuth | `lib/api-auth/`、追加route、SQL migration、保存鍵と専用IDを実装 | 初回Entra設定を照合し、工程3の認証を受け入れてからAPI試用の実コード交換と更新・失効を確認する |
| Tryへの接続 | 購読キー／Bearerの選択を各Tryの画面・実行routeへ接続 | 両方式で、全対象の正常・拒否・本人／所有者と長時間操作の終了を実環境で受け入れる |
| 公開情報の混入検査 | 工程3差分の検出を解消し、通常フックを通過して独立コミット済み | 統合差分・追加文書も確認する。実情報を許可リストへ追加したり、検査を無効化したりして通過させない |
| 正式手順と受入 | Core・公開証明書取得、Entraの手動登録、Gateway・Portal個別配備と更新運用の手順を整備済み | 統合した参照先を照合し、正式手順に従った実環境の受入を記録する |

変更途中のコードが存在することを、工程3・4の完了として扱わない。過去の部分的なローカル検証は、改修後の構築・認証・全Tryの受入に置き換えない。

## 工程3の作業

### 入力と資源の責任範囲

共有KV・ACR・ACA環境・SQL・管理VMの所有を初期構築側に維持する。既にPortal本体が配備された環境では、再実行で初期サンプルへ戻さない。

初期構築用の証明書生成設定と、Portal配備時に必要な認証Entraのtenant/client設定を区別する。初期構築では未確定のClient IDを許容し、Portal配備では必要な登録結果が揃っていなければ明確な理由で停止する。設定を未入力のまま共用secretへ自動fallbackしない。

変更対象は`schemas/params.schema.json`、`schemas/configuration.schema.json`、`scripts/deployment/helpers/configuration.py`と、初期構築・Portal配備の入力処理。公開例には独立した架空値だけを使う。

### 初回の鍵と証明書の準備

初回発行と更新では、証明書専用ACA環境のPortal署名Jobが運用IDでKVに発行を要求する。発行者は`Self`とし、非exportableの鍵をKV内に保持する。Portal本体とGraph定期ジョブには対象鍵の読取り・署名に限定した別の署名IDを関連付ける。Azure側のこれらのIDは、認証EntraのClient IDが未確定でも準備できる。 [証明書Jobの構成](../operations/certificate-jobs.md)

| 作成・改修する資材 | 役割 |
| --- | --- |
| `infra/main.bicep` | 専用ACA環境、Private Endpoint、ACR、KV、実行用と署名用のID、Jobの結果保存先を準備する |
| `scripts/deployment/helpers/certificate_jobs.py` と `scripts/deployment/lifecycle.py` | 非公開ACRへ登録したイメージのdigestでJobを配備・実行し、今回の実行結果を照合して公開証明書を収集する |
| `scripts/operations/portal_auth_certificate.py` | Portal署名Jobを使う更新用の運用入口。更新準備、手動登録確認、配備切替、確認、復旧、旧版廃止を段階管理する |

初回構築は次の順序で行う。

1. CoreでKV、Private Endpoint、DNS、管理VM、ACR、専用ACA環境、ストレージ、用途別RBACを準備する。
2. 管理VMのビルド経路でイメージを非公開ACRへ登録し、digestを確定する。
3. 構築の`postprovision`でPortal署名Jobの`Initialize`を開始し、KVに初回発行を要求する。
4. 対象Job実行の正常終了を待ち、非exportable・自己署名の方針と対応する鍵・証明書の組を照合する。
5. 公開証明書を保護した出力先へ取得し、参照情報をstateへ記録して初期証明書準備を完了する。

構築工程でJobを実行し、結果の収集と照合まで確認する。失敗や結果不明のときは実行記録を保持して原因を確認する。

公開証明書はチェックアウト直下の`.local/<environment>/generated/portal-auth-public.cer`へ保存する。鍵のversion付き参照、有効期限、対応する公開証明書のthumbprintは同じ環境の`state/portal-auth-certificate.json`へ記録する。秘密鍵はKVに保持する。

初回生成と更新を分ける。同じ入力の再実行では既存の組を照合して使い、毎回生成・更新しない。作成結果が不明な場合は要求中の状態を照合し、別の鍵を重ねて作らない。既存鍵のexportable設定・発行方針・参照が期待と異なる場合は上書きせず停止する。

必要に応じて、保存済みの初期証明書stateを`initialize`で確認する。 [初期証明書の運用](../operations/portal-authentication.md#初期証明書の発行と確認)

### 初期構築とPortal配備の入口

`azd up`は`azure.yaml`の構築hookでCoreと公開証明書の準備まで行う。手動Entra登録を終えた後、`deploy_gateway.py`でGateway、`deploy_portal.py`でPortalを順に個別配備する。

基盤stateとPortal配備stateの対応を照合し、Client IDを設定した後は基盤を作り直さずPortal配備へ進める。構築済み環境の再配備・失敗後の再試行・初期サンプルの確認・本体配備済み環境の扱いを手順で分ける。

### Entraアプリの手動構築手順

新しい`docs/setup/09-egakumi-portal.md`に、Azureポータルの画面名、設定項目、入力例、確認方法を記載する。手順は公開証明書を取得した後から始める。

| 設定 | 手順に含める内容 |
| --- | --- |
| アプリ登録 | 認証Entraの専用Portalアプリ。Single tenant、Web。認証EntraとAzure側KVのtenantを混同しない |
| 証明書 | 公開証明書のアップロード、thumbprint・期限の照合。新Portal用client secretは作成しない |
| 認証 | Portalログイン用の固定callbackと、先行設計で確定したAPI用callback。Implicit grantとPublic client flowsはOFF |
| Graph | 管理機能に必要な`GroupMember.Read.All`・`User.ReadBasic.All`のアプリケーション権限と管理者同意を確認する |
| API試行と購読管理 | APIが公開する`Gateway.Invoke`と`Subscription.Manage`を委任権限として登録する。OAuth要求の`openid`・`offline_access`等と、アプリに登録するAPI権限を区別する |
| 登録後 | Client IDなどを構築担当へ渡し、選択した認証tenant・アプリ・公開証明書の一致を確認する |

利用者割当や条件付きアクセスがある場合の確認点を記載する。コードからEntra管理権限や同意を自動追加しない。

### Portalの認証と配備

PortalログインとGraph管理・定期ジョブを専用アプリのclient assertionへ切り替える。Portal本体と署名を使うジョブに専用の署名IDを関連付け、対象鍵scopeの必要な権限を設定する。既存のSQL・APIM・ACR用IDの役割は維持する。

鍵のversionと公開証明書のthumbprintを組で固定する。認証コード交換とGraph用トークン取得に同じ署名処理を使うが、ID token・Graph token・API tokenの用途は分ける。本人の同一性は認証済みの`tid`と`oid`で確認し、Client ID変更に伴う`sub`を移行キーにしない。

`apps/portal/lib/auth-*`、`lib/client-assertion.ts`、`lib/api-keys/graph.ts`、`scripts/reconcile-api-key-groups.ts`、`apps/portal/deploy/`、`scripts/deployment/deploy_portal.py`とローカル認証準備を整備する。認証コードはイメージに含め、Client ID・鍵参照等は配備設定として渡す。Cookie署名鍵と既存の保存暗号化資材を保持する。

移行中だけ旧Portal構成を復旧用に保持する。新構成の受入後に、Portalの共用secret参照・旧読取権限・旧Portal専用設定を撤去する。恒久互換にはしない。CMが使うsecretや戻り先は、利用先を照合して維持する。

### 更新と復旧

新しい鍵・証明書をKVで用意し、公開証明書を手動登録してからPortal本体とジョブを切り替える。受入完了後にEntraの旧公開証明書を削除し、KVの旧鍵versionを無効化する。KVの同名鍵をまとめて削除して現行versionまで失わないよう、操作対象を照合する。

運用スクリプトは既定で計画を表示する。明示した実行操作で専用ACA Jobを呼び、KV内に新しい秘密鍵と自己署名証明書を用意する。初回発行と更新は同じJobの対応する段階を使う。 [更新の運用手順](../operations/portal-authentication.md#更新の段階)

更新準備、手動登録確認、切替、動作確認、旧版廃止、復旧を段階管理する。Entra担当による新公開証明書の登録完了を確認してから、Portal本体とジョブの固定参照を切り替える。確認前に旧版を無効化せず、途中再開で不要な再発行を重ねない。イメージと認証構成を組で復旧できるようにする。KV自動更新だけでEntra登録・配備切替まで完了するとは案内しない。

## 工程4の作業

[工程4の詳細設計](portal-stage4-design.md)に、新しいPortal用Entraアプリと既存APIアプリの役割、通常構築と段階受入、公開OAuth APIの再利用、Cookie・SQL session、独立暗号鍵、競合・失効、全Try、配備・復旧をまとめた。ローカルで確認した範囲と、実Azureの受入範囲は詳細設計と検証記録へ記載する。

### 全Try対象と認証契約の確認

API正本と実行可能なPortal画面・routeを照合し、Text、Embedding、Rerank、Image、Speech、Document、PII、Graph、MCP、Realtime等の対象一覧を作る。未提供機能を受入対象の成功に含めず、廃止したtransportを復活させない。

APIキーとBearerで必要な入力・認可・所有者・終了処理を比較する。詳細設計では、Bearerを擬似的な`apiKeyId`でキー処理へ渡す試作を採用せず、認証方式を判別可能なunionとして扱う。画面だけを切り替えてサーバー処理を未対応のままにしない。

### API用OAuthと保存の設計

工程3のPortalログインを維持し、API用の追加認証に失敗してもPortal管理機能を利用できる設計を提示する。Authorization Code＋PKCE、固定callback、一回限りstate、nonce、署名・issuer・audienceを検証したID tokenによる本人照合を扱う。

認証取引とトークンをPortalのSQL session、`tid/oid`、client、resource/scopeへ束縛する。CM保存とは別の暗号化SQL領域・暗号鍵用途・versionを使い、保存期限、logout、disconnect、削除を設計する。API tokenの単純decodeで本人を確定しない。

複数instanceの更新競合はDB時刻のleaseとrevision・失効世代で扱う。遅れて戻った更新応答がdisconnect後に保存されないこと、外部token交換中にSQL transactionを保持しないこと、`invalid_grant`と通信障害の扱いを確認する。

既存の`lib/api-auth/`と追加routeはこの設計判断に合わせて見直す。SQL migration番号を現行の並びと照合し、暗号鍵のKV保存・配備設定・runtime grants・readinessも同じ変更に含める。

### Try実行と画面への接続

各Tryのサーバー処理から、同じ登録済みAPIのURLへAPIキーまたはBearerを送る。認証失敗時に別方式へ自動fallbackしない。ID token・Graph管理tokenをAPI試行へ流用しない。

HTTP、SSE、WebSocket、WebRTC、multipart、非同期結果取得、MCP sessionの開始から終了まで、本人・認証方式・接続の所有者を維持する。GraphのCM本人接続を確認し、OBOは工程5の別判断として扱う。API tokenを下流Graphへそのまま転送する設計にしない。

WebSocketのBearerはサーバーrelayから送る。tokenをブラウザーのURL queryへ埋め込まない。ストリーム途中の認証失敗でモデル課金・副作用を伴う要求を自動再送しない。接続の有効期間と、複数instanceでlogout・disconnectを反映する方法・最大遅延を定義する。

shadcn/uiの認証方式選択と日本語・英語の案内を同時に追加する。標準導線に秘密値の入力・表示を設けず、保存されるAPI token等をブラウザー・ログ・例外本文へ出さない。

## 正式手順の整備

| 文書 | 更新または新設する内容 |
| --- | --- |
| `README.md`、`docs/setup/08-provision-core.md`、`docs/setup/12-deploy-portal.md` | 初期構築、公開証明書取得、Entra手動登録、Portal本体配備の順序と各段階の完了条件 |
| `docs/setup/02-entra-api.md`、`docs/setup/03-entra-tools.md`、`docs/setup/04-entra-credential.md` | Coreに先立つAPI・Tools・Credentialの登録と、各アプリの権限を確認する |
| `docs/setup/07-params.md`、`docs/reference/config-options.md`、公開params例 | 初期構築時の未確定値とPortal配備前の必須値、証明書参照、保護保存先 |
| `docs/setup/09-egakumi-portal.md` 新設 | 工程3・4で使う専用アプリの手動作成・証明書・認証・API権限・同意・登録後確認 |
| `docs/testing/portal-local-auth.md` | 開発用アプリ・鍵、KV PEへの到達、Azure CLI credentialの用途、ローカル／Azure検証の分担 |
| `docs/operations/portal-authentication.md` 新設 | 証明書更新、手動登録確認、切替・復旧、旧版廃止、工程4の保存・切断・暗号鍵運用 |
| `docs/operations/secrets-and-certificates.md`、`docs/operations/portal.md`、関連目次 | CM secret、HTTPS証明書、Portal認証証明書の区別と正式な更新・配備導線 |
| `docs/testing/construction-tests.md`、`docs/testing/portal-authentication-tests.md` 新設 | 構築順序・再実行・認証・全Try・更新・復旧の試験と、予定／ローカル検証／実受入の区別 |
| `docs/reference/network-communication-requirements.md` 等の通信文書 | 既存KV PEへの署名・証明書生成、Entraへのtoken交換の用途を追加。必要な場合だけ通信規則を変更する |

既存の`docs/reference/entra-app-rollout-plan.md`と`docs/testing/integration-tests.md`は参照にとどめ、この計画の整備では変更しない。工程3・4の詳細と追加の試験は上記の補足文書へまとめる。

## 検証と完了条件

| 区切り | 必須の確認 |
| --- | --- |
| 工程3のローカル確認 | Coreからの専用ACA Job配備・実行順序、発行者`Self`、非exportable、発行完了待機、失敗時の停止、更新の段階管理を合成データで確認する。Client ID未確定の入力、Portal配備前の不足検知、再実行・作成応答不明、鍵versionとthumbprintの組、署名権限、秘密出力の抑止も確認する。関連Python試験・Bicep生成・Portal認証／Graph試験・型検査・lint・buildを実施し、依存変更時にnpm auditを確認する |
| 工程3の実受入 | 新規構築で専用ACA Jobの実行、KV内の非exportable鍵と自己署名証明書の生成、公開証明書の取得を確認する。手動Entra登録、KV PEの名前解決・署名、認証コード交換、ログイン、本人・所有権維持、Graph管理、定期ジョブを確認する。更新用の運用入口で新証明書の準備、切替・復旧・旧版廃止を受け入れる |
| 工程4のローカル確認 | state再利用、nonce・本人不一致、期限切れ、scope/resource違い、他sessionの保存行、更新競合、lease喪失、disconnect後の遅延応答、鍵version、秘密非露出を確認。全Try対象の認証方式別契約と接続終了処理を検証する |
| 工程4の実受入 | 対象一覧の全機能でキー／Bearerを確認し、認証拒否・再認証・logout・disconnect・複数instance・ストリーム／非同期の所有者と終了を確認。Portal管理機能とキー経路の継続、復旧手順も確認する |
| 正式手順の受入 | 新規構築を文書の順序だけで進められ、初期構築とPortal配備の間で公開証明書を取得・手動登録できる。再配備・更新・途中再開で基盤や現行鍵を不要に作り直さない |

専用ACA JobからのKV PEへのHTTPSと、Portal本体・Graph定期ジョブからのKV PE・EntraへのHTTPSを確認する。認証用証明書はKVで自己署名し、公開TLS証明書のACME通信は別のJobで使う。実環境ではDNS、実効経路、NSG／FWとRBACを照合する。

未実施・失敗・条件不足の確認を成功件数に含めない。実情報・出力・証跡はルートの`.local/`と`.azure/`で扱い、公開文書には一般化した条件と合成データの結果だけを記載する。公開前に差分・追加文書全文・参照先と混入検査を確認する。

## 実施順序と判断の区切り

1. この計画、構築入口、証明書の発行・更新方針を確認する。工程3・4で必要なEntra設定を先行設計し、初回登録の手順に含める設定を確定する。
2. 工程3の実装差分をレビューし、Coreからの専用ACA Job実行、更新用CLI、Portal認証・配備と正式手順をまとめてローカル検証する。
3. 工程4の保存・更新・認証契約とTry接続を配備候補でローカル検証する。
4. マージ後にCoreを構築し、手動Entra登録を済ませてGatewayとPortalを個別配備する。実環境で工程3の認証・Graph管理・定期照合を受け入れる。
5. 工程4を実環境で受け入れ、方式別の試用結果を記録する。既存環境を移行する場合は、復旧条件と旧参照の利用先も確認してから撤去する。

各区切りで変更範囲、残る判断、検証結果、次の適用内容を説明する。実装の判断と実環境適用の判断を分け、構築手順が未整備のままコードだけを完了扱いにしない。

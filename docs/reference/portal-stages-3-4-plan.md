# Portal 工程3 工程4 実施計画

状態：工程3の実装・ローカル検証中。実Azure受入は未実施。工程4は詳細設計の判断前。工程3の認証分離と、工程4のキー／アクセストークンによるAPI試行を、資材・正式手順・運用・検証まで含めて整備する。

共有基盤の初期構築でPortalのACA初期サンプルを起動し、構築資材から管理VM上の専用発行スクリプトを実行する。スクリプトがKVへ要求し、KV内にPortal認証専用の非exportable秘密鍵と自己署名証明書を生成する。公開証明書を取り出した後、Entra担当が専用アプリを手動作成・設定する。Portal本体は独立した配備工程でビルドし、初期サンプルから更新する。

全工程の位置付けは[Entraアプリ分離の作業計画](entra-app-rollout-plan.md)を参照する。本計画の承認に基づいて工程3を実装・ローカル検証し、工程4の本体実装には別途設計確認を設ける。

## 構築と担当の分担

| 工程 | 担当と処理 | 完了条件 |
| --- | --- | --- |
| 初期構築 | 構築資材で共有ネットワーク、KV、管理VM、ACR、ACA環境、共有SQL、Portalの初期サンプルを用意。専用発行スクリプトを管理VMへ配置し、構築工程から実行してKV内に鍵・自己署名証明書を生成する。Gateway配備は従来の工程で進める | 発行スクリプトの実行が成功し、証明書の生成完了・公開証明書の取得を確認できる。共有基盤とGatewayを確認する。Portal本体の完成とは区別する |
| Entra登録 | Entra担当がAzureポータルで専用Portalアプリを作成。公開証明書、Webの戻り先、工程3・4で必要なAPI権限と管理者同意をまとめて設定する | 構築担当がClient IDなどの登録結果を受け取り、公開証明書と設定内容を照合できる |
| Portal配備 | 登録結果を保護入力へ設定し、Portalイメージをビルド・配備。DB初期化、認証設定、権限、定期ジョブ、既存の入口・APIM連携も同じ工程で扱う | Portalログインと管理機能を受入。その後、工程4の全対象を受入する |

外部へ渡す資材の成果物は公開証明書とする。申請票、依頼メール、申請用manifest等は生成せず、Entra担当が使う手動構築手順を別途整備する。Entraアプリの作成・証明書登録・権限付与を資材から自動実行しない。

Portal用EntraアプリのClient IDが未確定でも、初期構築は実行できるようにする。CM/API用アプリなど、Gatewayが従来必要としているEntra事前準備は別の前提として維持する。

## 実装前に確認する事項

| 判断事項 | 現在の案と確認内容 |
| --- | --- |
| 証明書の発行元 | 初回発行・更新ともにKVの自己署名を前提とする。外部CAやLet's Encryptによる認証用証明書の発行を、この計画の前提に含めない |
| 有効期間と更新 | 12か月有効、60日前から準備、更新時に新しい秘密鍵を生成する案。Microsoft共通の必須期間として扱わない |
| 構築入口 | 初期構築は共有基盤・Portal初期サンプル・証明書準備・Gatewayまで。Portal本体は既存の個別配備入口を整備して実行する |
| 工程4のEntra設定 | `Gateway.Invoke`委任権限とAPI用OAuthの固定callbackを、初回アプリ登録前に確定する。追加の申請が必要になる設定を後から発見しないよう、工程4のこの部分を先行設計する |
| 工程4の認証処理 | PortalログインとAPI用追加認証を分ける案。Better Authの再利用範囲、独立したOAuth処理の要否、保存・更新・終了の契約を説明して判断を受ける |

初回のアプリ作成と公開証明書登録は一度の依頼にまとめる。工程4の詳細実装は工程3の受入後に進めるが、Entra設定に影響する設計は初回登録前に確定する。

工程1・2の受入範囲と残る制御を元の作業計画で照合する。API用アプリの公開scope、キー／Bearerの入口認可、GraphのCM本人接続、非同期結果や接続の所有者に関する契約を、工程3・4の前提として確認する。前提が不足している場合は不足箇所を示し、今回の改修へ未判断のGateway制御変更を混ぜない。

## 現在の着手状況

| 領域 | 現在位置 | 次に確認すること |
| --- | --- | --- |
| Portal認証とGraph | `client-assertion.ts`、認証設定、Graphクライアント、照合ジョブに変更途中のコードがある | 今回の構築順序と最終設定に合わせて差分をレビューし、必要な部分を採用する |
| schemaと配備 | 認証用設定、専用ID、鍵scopeの権限、アプリ・ジョブの設定に変更途中の資材がある | 初期構築でClient IDが未確定でも成立する入力契約と、初回証明書生成を整備する |
| 初回証明書生成と構築入口 | 専用証明書の初回生成と初期構築hookを追加。標準初期構築はGatewayまでとし、Portal ACA初期サンプルを維持する構成へ改修中 | 発行スクリプトの作成・管理VMへの配置・初期構築からの実行を追加し、Portal本体配備を独立させる |
| 証明書更新の運用 | Portal認証専用の更新用運用スクリプトを実装し、段階管理をローカル検証中 | 管理VM上の発行処理を利用する更新用運用スクリプトを作成し、手動Entra登録を挟む更新・切替・確認・復旧・旧版廃止を整備する |
| API用OAuth | `lib/api-auth/`、追加route、SQL migration、UI部品に試作は工程3のビルド対象から保護退避した | 設計確認前に正式実装として採用しない。保存・所有者・競合制御と配備設定を再確認する |
| Tryへの接続 | 認証切替用の試作は、各Try画面・実行routeへ未接続 | 全対象の一覧と契約を作り、設計判断後に接続する |
| 公開情報の混入検査 | 初回検出5件を含め、変更差分の検出を解消しローカル混入検査を再確認中 | 値を再掲せず原因を確認して解消する。実情報を許可リストへ追加したり、検査を無効化したりして通過させない |
| 正式手順と受入 | 既存の構築手順は共用アプリ・secretと一括配備を前提にしている | 新しい順序へ整備し、資材のローカル確認と実環境の受入を別に記録する |

変更途中のコードが存在することを、工程3・4の完了として扱わない。過去の部分的なローカル検証は、改修後の構築・認証・全Tryの受入に置き換えない。

## 工程3の作業

### 入力と資源の責任範囲

共有KV・ACR・ACA環境・SQL・管理VMの所有を初期構築側に維持する。既にPortal本体が配備された環境では、再実行で初期サンプルへ戻さない。

初期構築用の証明書生成設定と、Portal配備時に必要な認証Entraのtenant/client設定を区別する。初期構築では未確定のClient IDを許容し、Portal配備では必要な登録結果が揃っていなければ明確な理由で停止する。設定を未入力のまま共用secretへ自動fallbackしない。

変更対象は`schemas/params.schema.json`、`schemas/configuration.schema.json`、`scripts/deployment/helpers/configuration.py`と、初期構築・Portal配備の入力処理。公開例には独立した架空値だけを使う。

### 初回の鍵と証明書の準備

初回発行と更新で使う専用スクリプトを作成する。管理VMの実行IDでKVの証明書生成処理を呼び、Portal認証専用の非exportable鍵と自己署名証明書を生成する。KVの発行者は`Self`とし、秘密鍵をVM内で生成してアップロードする方式にはしない。専用の署名IDはAzure側のIDとして準備でき、認証EntraのClient IDが未確定でも作成できる。証明書生成と署名に必要なRBACの対象・操作を確定する。

| 作成・改修する資材 | 役割 |
| --- | --- |
| `infra/guest/portal-auth-certificate.ps1` | 管理VMで動く専用発行スクリプト。初回は既存の組を照合し、存在しない場合にKVへ生成を要求する。更新準備では新しい鍵・自己署名証明書を用意し、公開証明書と必要なmetadataだけを取得する |
| 管理VMの配置処理と`scripts/deployment/lifecycle.py` | 発行スクリプトを管理VMへ配置し、KV・PE・DNS・VM・RBACの準備後、初期構築工程からVM Run Command等で呼び出す。スクリプトの終了と証明書の生成完了を確認する |
| `scripts/operations/portal_auth_certificate.py` | 更新用の運用入口。管理VM上の発行スクリプトを利用し、更新準備、手動登録確認、配備切替、確認、復旧、旧版廃止を段階管理する |

初期構築への組込みは、次の順序で実装する。

1. KV、Private Endpoint、DNS、管理VM、用途別RBACとスクリプト実行環境を準備する。
2. 専用発行スクリプトを管理VMへ配置する。
3. 初期構築のhookから管理VM上のスクリプトを実行し、KVへ初回生成を要求する。
4. スクリプトの正常終了とKVの証明書生成完了を待ち、非exportable・自己署名・対応する鍵と証明書の組を照合する。
5. 公開証明書を保護した出力先へ取得し、参照情報をstateへ記録して初期証明書準備を完了する。

発行スクリプトを配置するだけでは完了としない。初期構築後に利用者が別途スクリプトを手動実行する前提にせず、構築工程が実行と結果確認まで担当する。失敗や結果不明のまま初期証明書準備を成功扱いにしない。

公開証明書の出力先は、チェックアウト直下の`.local/<environment>/generated/portal-auth-public.cer`を候補とする。鍵のversion付き参照、有効期限、対応する公開証明書のthumbprint等は同じ環境の保護stateへ記録する。秘密鍵・PFXをローカル、イメージ、配布物へ出力しない。

初回生成と更新を分ける。同じ入力の再実行では既存の組を照合して使い、毎回生成・更新しない。作成結果が不明な場合は要求中の状態を照合し、別の鍵を重ねて作らない。既存鍵のexportable設定・発行方針・参照が期待と異なる場合は上書きせず停止する。

`infra/main.bicep`、管理VMの用途別権限、`scripts/deployment/lifecycle.py`と専用の生成・公開証明書取得処理を整備する。既存のAppGW向けTLS証明書・ACME処理とは用途を分ける。

### 初期構築とPortal配備の入口

`azure.yaml`のhookと`deploy_applications.py`の役割を見直し、標準の初期構築がPortal本体のEntra登録を待たずに完了するようにする。Portal本体は`deploy_portal.py`を正式な個別配備入口として整備する。

基盤stateとPortal配備stateの対応を照合し、Client IDを設定した後は基盤を作り直さずPortal配備へ進める。構築済み環境の再配備・失敗後の再試行・初期サンプルの確認・本体配備済み環境の扱いを手順で分ける。

### Entraアプリの手動構築手順

新しい`docs/setup/entra-portal.md`に、Azureポータルの画面名、設定項目、入力例、確認方法を記載する。手順は公開証明書を取得した後から始める。

| 設定 | 手順に含める内容 |
| --- | --- |
| アプリ登録 | 認証Entraの専用Portalアプリ。Single tenant、Web。認証EntraとAzure側KVのtenantを混同しない |
| 証明書 | 公開証明書のアップロード、thumbprint・期限の照合。新Portal用client secretは作成しない |
| 認証 | Portalログイン用の固定callbackと、先行設計で確定したAPI用callback。Implicit grantとPublic client flowsはOFF |
| Graph | 管理機能に必要な`GroupMember.Read.All`・`User.ReadBasic.All`のアプリケーション権限と管理者同意を確認する |
| API試行 | 工程4で使うAPIの`Gateway.Invoke`委任権限を含める。OAuth要求の`openid`・`offline_access`等と、アプリに登録するAPI権限を区別して説明する |
| 登録後 | Client IDなどを構築担当へ渡し、選択した認証tenant・アプリ・公開証明書の一致を確認する |

利用者割当や条件付きアクセスがある場合の確認点を記載する。コードからEntra管理権限や同意を自動追加しない。

### Portalの認証と配備

PortalログインとGraph管理・定期ジョブを専用アプリのclient assertionへ切り替える。Portal本体と署名を使うジョブに専用の署名IDを関連付け、対象鍵scopeの必要な権限を設定する。既存のSQL・APIM・ACR用IDの役割は維持する。

鍵のversionと公開証明書のthumbprintを組で固定する。認証コード交換とGraph用トークン取得に同じ署名処理を使うが、ID token・Graph token・API tokenの用途は分ける。本人の同一性は認証済みの`tid`と`oid`で確認し、Client ID変更に伴う`sub`を移行キーにしない。

`apps/portal/lib/auth-*`、`lib/client-assertion.ts`、`lib/api-keys/graph.ts`、`scripts/reconcile-api-key-groups.ts`、`apps/portal/deploy/`、`scripts/deploy_portal.py`とローカル認証準備を整備する。認証コードはイメージに含め、Client ID・鍵参照等は配備設定として渡す。Cookie署名鍵と既存の保存暗号化資材を保持する。

移行中だけ旧Portal構成を復旧用に保持する。新構成の受入後に、Portalの共用secret参照・旧読取権限・旧Portal専用設定を撤去する。恒久互換にはしない。CMが使うsecretや戻り先は、利用先を照合して維持する。

### 更新と復旧

新しい鍵・証明書をKVで用意し、公開証明書を手動登録してからPortal本体とジョブを切り替える。受入完了後にEntraの旧公開証明書を削除し、KVの旧鍵versionを無効化する。KVの同名鍵をまとめて削除して現行versionまで失わないよう、操作対象を照合する。

更新用運用スクリプトを工程3の成果物として作成する。既定では計画を示し、明示した実行操作で管理VM上の専用発行スクリプトを呼び、KV内に新しい秘密鍵と自己署名証明書を用意する。初回発行と更新で共通の発行処理を使い、更新時は同じ秘密鍵を再利用しない。

更新準備、手動登録確認、切替、動作確認、旧版廃止、復旧を段階管理する。Entra担当による新公開証明書の登録完了を確認してから、Portal本体とジョブの固定参照を切り替える。確認前に旧版を無効化せず、途中再開で不要な再発行を重ねない。イメージと認証構成を組で復旧できるようにする。KV自動更新だけでEntra登録・配備切替まで完了するとは案内しない。

## 工程4の作業

### 全Try対象と認証契約の確認

API正本と実行可能なPortal画面・routeを照合し、Text、Embedding、Rerank、Image、Speech、Document、PII、Graph、MCP、Realtime等の対象一覧を作る。未提供機能を受入対象の成功に含めず、廃止したtransportを復活させない。

APIキーとBearerで必要な入力・認可・所有者・終了処理を比較する。Bearerを擬似的な`apiKeyId`でキー処理へ渡す試作を、正式な認証方式別契約として採用できるか再検討する。画面だけを切り替えてサーバー処理を未対応のままにしない。

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
| `README.md`、`docs/setup/06-deploy.md`、`docs/setup/portal.md` | 初期構築、公開証明書取得、Entra手動登録、Portal本体配備の順序と各段階の完了条件 |
| `docs/setup/02-entra.md` | CM/API用の既存準備と、新Portalの登録時期・権限を区別する |
| `docs/setup/04-params.md`、`docs/setup/05-config.md`、公開params例 | 初期構築時の未確定値とPortal配備前の必須値、証明書参照、保護保存先 |
| `docs/setup/entra-portal.md` 新設 | 工程3・4で使う専用アプリの手動作成・証明書・認証・API権限・同意・登録後確認 |
| `docs/testing/portal-local-auth.md` | 開発用アプリ・鍵、KV PEへの到達、Azure CLI credentialの用途、ローカル／Azure検証の分担 |
| `docs/operations/portal-authentication.md` 新設 | 証明書更新、手動登録確認、切替・復旧、旧版廃止、工程4の保存・切断・暗号鍵運用 |
| `docs/operations/secrets-and-certificates.md`、`docs/operations/portal.md`、関連目次 | CM secret、HTTPS証明書、Portal認証証明書の区別と正式な更新・配備導線 |
| `docs/testing/construction-tests.md`、`docs/testing/portal-authentication-tests.md` 新設 | 構築順序・再実行・認証・全Try・更新・復旧の試験と、予定／ローカル検証／実受入の区別 |
| `docs/reference/network-communication-requirements.md` 等の通信文書 | 既存KV PEへの署名・証明書生成、Entraへのtoken交換の用途を追加。必要な場合だけ通信規則を変更する |

既存の`docs/reference/entra-app-rollout-plan.md`と`docs/testing/integration-tests.md`は参照にとどめ、この計画の整備では変更しない。工程3・4の詳細と追加の試験は上記の補足文書へまとめる。

## 検証と完了条件

| 区切り | 必須の確認 |
| --- | --- |
| 工程3のローカル確認 | 初期構築が管理VMへ専用発行スクリプトを配置・実行すること、KV準備後の実行順序、発行者`Self`、非exportable、発行完了待機、失敗時の停止、更新用運用スクリプトの段階管理を合成データで確認。Client ID未確定の入力、Portal配備前の不足検知、再実行・作成応答不明、鍵versionとthumbprintの組、署名権限、秘密出力の抑止も確認する。関連Python試験・Bicep生成・Portal認証／Graph試験・型検査・lint・buildを実施し、依存変更時にnpm auditを確認する |
| 工程3の実受入 | 新規構築で管理VM上の専用発行スクリプトが実行され、KVに非exportable鍵と自己署名証明書が生成され、公開証明書を取得できることを確認する。手動Entra登録、同一／別tenantの対象構成、KV PEへの名前解決・署名、認証コード交換、ログイン、本人・所有権維持、Graph管理、定期ジョブを確認。作成した更新用運用スクリプトで新しい鍵・自己署名証明書の準備、切替・復旧・旧版廃止を確認し、旧Portal参照の撤去条件を満たす |
| 工程4のローカル確認 | state再利用、nonce・本人不一致、期限切れ、scope/resource違い、他sessionの保存行、更新競合、lease喪失、disconnect後の遅延応答、鍵version、秘密非露出を確認。全Try対象の認証方式別契約と接続終了処理を検証する |
| 工程4の実受入 | 対象一覧の全機能でキー／Bearerを確認し、認証拒否・再認証・logout・disconnect・複数instance・ストリーム／非同期の所有者と終了を確認。Portal管理機能とキー経路の継続、復旧手順も確認する |
| 正式手順の受入 | 新規構築を文書の順序だけで進められ、初期構築とPortal配備の間で公開証明書を取得・手動登録できる。再配備・更新・途中再開で基盤や現行鍵を不要に作り直さない |

現在の資材では、ACA・管理VMから既存KV PEへのHTTPS443と、EntraへのHTTPS443を許可する定義がある。今回の認証用証明書は既存KVの自己署名で発行するため、外部CAへの発行・更新通信は追加しない。既存HTTPS用証明書の通信は別用途として維持する。実環境ではDNS・実効経路・NSG／FW・RBACを確認し、定義上の許可を通信成功へ読み替えない。

未実施・失敗・条件不足の確認を成功件数に含めない。実情報・出力・証跡はルートの`.local/`と`.azure/`で扱い、公開文書には一般化した条件と合成データの結果だけを記載する。公開前に差分・追加文書全文・参照先と混入検査を確認する。

## 実施順序と判断の区切り

1. この計画、構築入口、証明書の発行・更新方針を確認する。工程3・4で必要なEntra設定を先行設計し、初回登録の手順に含める設定を確定する。
2. 工程3の既存差分をレビューし、入力、専用発行スクリプト、初期構築から管理VM上のスクリプトを実行する組込み、更新用運用スクリプト、構築入口、認証、配備、正式手順を一式で整備する。ローカル検証が揃ってから、実環境の適用差分と受入手順を提示する。
3. 工程3を手動Entra登録と実環境で受入する。復旧条件を確認し、受入後に旧Portal参照を撤去する。
4. 工程4の保存・更新・認証契約・全Try接続の詳細設計を提示し、判断後に実装する。先行試作は自動的に採用しない。
5. 工程4をローカル検証後に適用・受入し、全対象と正式手順の対応を確認する。

各区切りで変更範囲、残る判断、検証結果、次の適用内容を説明する。実装の判断と実環境適用の判断を分け、構築手順が未整備のままコードだけを完了扱いにしない。

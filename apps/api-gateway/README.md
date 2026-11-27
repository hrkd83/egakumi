# API Gatewayの配備資材

業務APIの公開に使うAPI定義、APIMポリシー、Application GatewayのWAFポリシー設定を管理します。

共通基盤と同じparamsを、ルートの`python3 scripts/deployment/deploy_gateway.py --config <params> --execute`へ渡します。GatewayのDB・CM・認証窓口が必要な場合は先に配備し、準備完了後にこのフォルダーのAPIを登録します。初回構築では、`azd up`による共通基盤の作成と、公開証明書のEntra登録を済ませてから[Gatewayを個別配備](../../docs/setup/11-deploy-gateway.md)します。

Credential BrokerのBackendと名前付きの値は、専用内部CAEの既定ドメインからHTTPS URLを生成します。本人接続・Graph OBOの公開前にはBrokerの所属CAEとHTTPS ingressを照合します。 [Credential Brokerの配備境界](../credential-broker/README.md)

標準API正本を常用で解決します。公開params/configの`apiDefinitions`と`publishApis`は廃止し、旧入力は空配列やfalseでも拒否します。標準定義の接続先は構築結果から解決し、API配列や環境固有URLをparamsへ書き戻しません。既存の非標準入力は無断変換せず、[移行条件](../../docs/setup/07-params.md#既存入力の移行を確認する)を確認します。今回の入力整理のAzure反映・全環境再構築E2Eは未実施です。

配備CLIは、このBicepを一度コンパイルし、[要求の分割処理](deploy/publication_templates.py)で二つのARM要求へ分けます。最初の要求は既存APIMを参照するだけで、照合用の完成XMLと設定を生成します。次の要求はAPI等の資源を配備し、大きな照合用データの代わりに、その生成を特定する小さなIDを出力します。資源定義と生成式は同じ正本を使い、コメントや監査処理を削ってサイズを抑える方式にはしません。

CMのブラウザー受付を判定するC#に埋め込む真偽値は、ARMの文字列変換に任せず`true`／`false`を明示します。正式BicepとPythonによる生成で表記を揃え、C#で使えない`True`／`False`の混入を防ぎます。

URLクエリは、API定義の`queryObservation`で指定した入口だけを観測します。HTTPの操作ID辞書`queryObservations`と、MCPのメソッド・公開相対path辞書`mcpQueryObservations`を生成し、サービス名やモデル形式から対象を推測しません。標準3ラベルと対象入口を維持し、任意の観測ラベルを複数APIで共有できます。観測・拒否を切り替えるmodeや、そのための名前付きの値は使いません。[運用時の確認](../../docs/operations/apim.md)を参照してください。

queryの[観測部品](policies/fragments/request/core-query-observation/README.md)と[監査部品](policies/fragments/audit/core-query-telemetry/README.md)は、それぞれ同名XML／C#を持つ専用フォルダで管理します。C#と日本語説明を生成時にXMLへ組み込み、AzureのPolicy fragmentsでも読めるようにします。外部MCPのquery監査は`policies/api-templates/mcp/external.xml`で適用区画を指定します。棚卸⑦では観測専用化を承認し、要求ごとの`gatewayQueryObservation`は`route`・`httpMethod`・`presence`・`duplicates`だけを持ちます。この部品による400／500の拒否分岐を撤去し、観測できない値は`unknown`として扱います。

⑥で承認した有無・複数値の取得を維持し、件数区分`countBucket`の計算・出力を削除します。監査イベント名`query-guard`と`schemaVersion: 2`、`auditSchemaVersion: 2`を維持し、modeや拒否専用phaseは新規出力しません。他イベントのschema 1と旧query/schema 1のKQL読取り互換は別に保持する方針です。観測専用化のローカル改修・検証は完了し、Azureには未反映で、Azureへのまとめ反映と実接続の受入は未実施です。旧API参照・旧fragment・旧Named Valueの撤去は、新参照の反映後に行います。その後の[入力検査](policies/fragments/request/core-api-input-validation/README.md)ではquery制限・メソッド再確認・入口MIME一覧比較を撤去し、対象表の空の値`{}`で選んだ操作へ、Content-Type宣言の存在・1値・非空MIME部分という共通条件だけを適用します。操作登録とAPIMの照合は維持します。後段のMIME制御、URI変換・Speech転送・WAF・モデル本文検査は別の未棚卸項目であり、任意形式の受付やサービス互換性の確認完了とは扱いません。

生成専用の配備記録は、コード・入力・APIMのManaged Identityから決めた別IDを使います。再利用時は保存テンプレートを`exportTemplate`で照合し、別コードによる同名記録の上書きを検出します。生成成功だけで適用済みとは扱わず、資源配備・全ポリシーの読戻し確認後に保護checkpointを保存します。復旧はcheckpointを優先し、checkpointがない場合も、成功した実配備が指す生成記録だけを採用します。配備権限には既存のARM配備・読取りに加えて`Microsoft.Resources/deployments/exportTemplate/action`が必要です。

Gateway配備は通常キー用の`released-apis`製品（`Released APIs`）を作成し、`released: true`の業務APIとその生成MCPを関連付けます。未指定・`false`のAPIは通常キーで利用できません。既存の`productId`は検証用製品として維持します。`released-apis`を直接`productId`に書く指定は拒否し、リリース状態は`released`だけで管理します。`true`から`false`へ戻す場合は、増分配備後に該当する関連を解除し、結果を`releasedProduct`へ記録します。削除するのは通常製品との関連だけです。

本人接続フローに必要な`credential-manager-login`は通常製品にも補助関連を付けます。`credential-manager-browser`は匿名で取引を照合するAPIのため、製品には所属させません。製品への所属によって、Graph等の本人接続や各操作の入力・モデル制約が省略されることはありません。

| 配置 | 役割 |
| --- | --- |
| [api-publication.bicep](api-publication.bicep) | API・バックエンド・製品・購読を登録・更新する |
| [policies/](policies/) | 認証・認可、下流認証、モデル制限、監査、プロトコル中継等のXMLテンプレート |
| [WAF設定](../../docs/testing/waf-compatibility.md) | APIの公開経路に適用する除外・本文検査等を`waf-*.json`で管理 |
| [schemas/](schemas/) | APIMへ登録するAPI入出力のスキーマ。PIIのJSON契約は[pii.json](schemas/pii.json) |
| [apis/](apis/) | `<API名>.json`で管理する標準API。1ファイルに1つのAPIオブジェクトを配置 |
| [public-bindings.json](public-bindings.json) | API定義で共用する公開モデル名とAPI版。Gateway配備とPortalのカタログ生成が同じ値を参照 |
| [model-capabilities.json](model-capabilities.json) | API・操作・モデル別の公表出力上限、確認元URL、確認日。不定・未対応・未確認を数値と区別する |
| [deploy/](deploy/README.md) | Gatewayの統合配備とAPI登録 |

登録方法は[Gatewayの更新手順](../../docs/operations.md#同じ環境を更新する)を参照してください。指定APIと関連資源の配備成功後、キーによる共通認可を全体ポリシーへ統合します。同じIDをGUIでも編集する場合は設定の競合を確認します。

WebSocket APIを登録すると、既存の監査用診断設定`core-audit`の接続ログも有効にします。送信先と他の収集設定を保持し、結果は`webSocketDiagnostics`に記録します。

登録後は、初期サンプルの`echo-api`、`starter`／`unlimited`製品と、対象の製品・Echo APIだけに紐づく購読を整理します。既にない場合は何もしません。設定で明示した名前、接続先等を変更したEcho、業務APIやMCPが所属する製品は保持し、理由を`defaultSampleCleanup`へ記録します。通常の購読・全API用購読・マスター購読は削除しません。

共通ポリシーは[policies/service.xml](policies/service.xml)です。All APIsへ登録する全体ポリシーと、各APIへ登録する[共有テンプレート3正本](policies/api-templates/README.md)を区別します。入口の保護設定は共通基盤を使い、CM関連の名前付きの値はGateway配備が用意します。各APIは`<base />`で共通ポリシーを継承します。本人接続が必要なAPIは[CM共通窓口](../../docs/reference/credential-manager-login.md)を利用します。

標準APIの追加・変更は[定義ファイルの管理方法](apis/README.md)を参照してください。APIファイルにモデルの配備やCMプロバイダーの設定を混在させず、モデルとCM共通定義はconfig、環境固有IDとアプリ登録・秘密参照はparamsで管理します。標準Graphは双方の`credentialProviders.graphDelegated`、追加providerは定義／登録の`additional[]`を論理名で照合します。

Voice Liveのモデル・API版の既定値は`public-bindings.json`にまとめています。ここには公開可能な識別子だけを置き、エンドポイント・テナント・秘密情報を追加しません。Portalはこの4項目だけを解決したスナップショットをイメージへ同梱します。Gateway側のバックエンドや認証のバインドは従来どおり配備処理が解決します。

生成上限は`model-capabilities.json`を正本とし、既存のprofile IDを保って`maxOutputTokensByModel`へ展開します。HTTPの生成操作は要求したモデルの値で検査し、Portalも同じデータを表示・検証します。Model Routerのように上限が変わる場合は`dynamic`、設定自体がない場合は`unsupported`、数値を確認できない場合は`unverified`とし、512等の推測値を補いません。要求の初期値とモデル上限は分けて扱います。

WebSocket確立後のフレームやWebRTCメディアはAPIMのHTTPポリシーでは検査しません。Realtimeの`initialOutputTokens`は開始時の初期値です。Voice Liveはモデル最大32,768と、数値指定のAPI上限4,096を区別し、モデル最大を明示選択した場合だけ公式の`inf`を使用します。

標準APIの適用順とモデル・音声・CMの接合は[APIMポリシー一覧](../../docs/reference/apim-policy-catalog.md)、公開APIと採用モデルの対応は[API・Operation定義](../../docs/reference/api-design-proposal.md)を参照してください。配備対象は実施時のAPI定義とconfigを正とします。

文書の文字認識・表や段落の解析は[Document Intelligence契約](../../docs/reference/document-analysis.md)を参照してください。既存Foundryを再利用し、Gatewayの配備で公開APIと完全パス用WAFを設定します。

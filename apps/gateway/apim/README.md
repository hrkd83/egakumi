# APIMのAPI登録用資材

共通基盤と同じparamsを、ルートの`python3 scripts/deploy_gateway.py --config <params> --execute`へ渡します。GatewayのDB・CM・認証窓口が必要な場合は先に配備し、準備完了後にこのフォルダーのAPIを登録します。`azd up`も`postup`からGateway配備コマンドを呼び、このBicepによるAPI登録まで実行します。`azd provision`は共通基盤までを対象とします。

`apiDefinitions: []`は標準APIの一括登録、非空の配列は明示したAPIの登録を表します。標準定義の接続先は構築結果から解決し、params・CM設定・モデル定義を変更しません。

Gateway配備は通常キー用の`released-apis`製品（`Released APIs`）を作成し、`released: true`の業務APIとその生成MCPを関連付けます。未指定・`false`のAPIは通常キーで利用できません。既存の`productId`は検証用製品として維持します。`released-apis`を直接`productId`に書く指定は拒否し、リリース状態は`released`だけで管理します。`true`から`false`へ戻す場合は、増分配備後に該当する関連を解除し、結果を`releasedProduct`へ記録します。削除するのは通常製品との関連だけです。

本人接続フローに必要な`credential-manager-login`は通常製品にも補助関連を付けます。`credential-manager-browser`は匿名で取引を照合するAPIのため、製品には所属させません。製品への所属によって、Graph等の本人接続や各操作の入力・モデル制約が省略されることはありません。

| 配置 | 役割 |
| --- | --- |
| [api-publication.bicep](api-publication.bicep) | API・バックエンド・製品・購読を登録・更新する |
| [policies/](policies/) | 認証・認可、下流認証、モデル制限、監査、プロトコル中継等のXMLテンプレート |
| [schemas/](schemas/) | APIMへ登録するAPI入出力のスキーマ。PIIのJSON契約は[pii.json](schemas/pii.json) |
| [apis/](apis/) | `<API名>.json`で管理する標準API。1ファイルに1つのAPIオブジェクトを配置 |
| [public-bindings.json](public-bindings.json) | API定義で共用する公開モデル名とAPI版。Gateway配備とPortalのカタログ生成が同じ値を参照 |
| [model-capabilities.json](model-capabilities.json) | API・操作・モデル別の公表出力上限、確認元URL、確認日。不定・未対応・未確認を数値と区別する |
| [../deploy/api_publication.py](../deploy/api_publication.py) | 統合配備コマンドから呼ぶAPI登録処理 |

登録方法は[Gatewayの更新手順](../../../docs/operations.md#同じ環境を更新する)を参照してください。指定APIと関連資源の配備成功後、キーによる共通認可を全体ポリシーへ統合します。同じIDをGUIでも編集する場合は設定の競合を確認します。

WebSocket APIを登録すると、既存の監査用診断設定`core-audit`の接続ログも有効にします。送信先と他の収集設定を保持し、結果は`webSocketDiagnostics`に記録します。

登録後は、初期サンプルの`echo-api`、`starter`／`unlimited`製品と、対象の製品・Echo APIだけに紐づく購読を整理します。既にない場合は何もしません。設定で明示した名前、接続先等を変更したEcho、業務APIやMCPが所属する製品は保持し、理由を`defaultSampleCleanup`へ記録します。通常の購読・全API用購読・マスター購読は削除しません。

共通ポリシーは[policies/service.xml](policies/service.xml)です。入口の保護設定は共通基盤を使い、CM関連の名前付きの値はGateway配備が用意します。各APIは`<base />`で共通ポリシーを継承します。本人接続が必要なAPIは[CM共通窓口](../../../docs/setup/credential-manager-login.md)を利用します。

標準APIの追加・変更は[定義ファイルの管理方法](apis/README.md)を参照してください。APIファイルにモデルの配備やCMプロバイダーの設定を混在させず、モデルはconfig、CMと環境固有値はparamsで管理します。

Voice Liveのモデル・API版の既定値は`public-bindings.json`にまとめています。ここには公開可能な識別子だけを置き、エンドポイント・テナント・秘密情報を追加しません。Portalはこの4項目だけを解決したスナップショットをイメージへ同梱します。Gateway側のバックエンドや認証のバインドは従来どおり配備処理が解決します。

生成上限は`model-capabilities.json`を正本とし、既存のprofile IDを保って`maxOutputTokensByModel`へ展開します。HTTPの生成操作は要求したモデルの値で検査し、Portalも同じデータを表示・検証します。Model Routerのように上限が変わる場合は`dynamic`、設定自体がない場合は`unsupported`、数値を確認できない場合は`unverified`とし、512等の推測値を補いません。要求の初期値とモデル上限は分けて扱います。

WebSocket確立後のフレームやWebRTCメディアはAPIMのHTTPポリシーでは検査しません。Realtimeの`initialOutputTokens`は開始時の初期値です。Voice Liveはモデル最大32,768と、数値指定のAPI上限4,096を区別し、モデル最大を明示選択した場合だけ公式の`inf`を使用します。

標準APIの適用順とモデル・音声・CMの接合は[APIMポリシー一覧](../../../docs/reference/apim-policy-catalog.md)、公開APIと採用モデルの対応は[API・Operation定義](../../../docs/reference/api-design-proposal.md)を参照してください。配備対象は実施時のAPI定義とconfigを正とします。

文書の文字認識・表や段落の解析は[Document Intelligence契約](../../../docs/reference/document-analysis.md)を参照してください。既存Foundryを再利用し、Gatewayの配備で公開APIと完全パス用WAFを設定します。

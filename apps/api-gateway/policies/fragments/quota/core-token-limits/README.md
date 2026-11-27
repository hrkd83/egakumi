# トークン利用枠の計数

認証済み主体のトークン利用枠をAPIMの標準ポリシーへ渡す部品です。同名XML・C#をこのフォルダーで管理し、AzureのフラグメントIDは`core-token-limits`を維持します。同名C#は`enabled`と`counter`の2区画を所有します。単純な枠値・期間の取得とAPIM標準ポリシーの3分岐はXMLに保持します。

MOD-135で現XMLの有効化判定だけをmarkerへ置き換え、既存counterの全文読取りと同時に固定BEGIN/ENDの2区画へ切り替えました。準備XML全体はコピーせず、Python/Bicepの属性用encoderと2区画の所有を揃えています。actual-beforeのfull／compact／omit生成物と、実行構造・全属性・C#tokens・string/char値が一致しました。関連72件は58.095秒で全件成功し、社員／購読のキー、免除・対象操作・明示nullと内部一覧不備の停止、数値型・5期間、配備／保護source一致・容量・構文・所有を確認しました。ローカル整理済みでAzure未反映です。

## 入出力

| 入力・出力 | 用途 |
| --- | --- |
| `gatewayDecision` | 認証方式と確認済み`verifiedTenantId`／`verifiedPrincipalId` |
| APIMの購読・`gatewaySubscriptionProfile`・生成済み設定 | 同名C#の有効化と購読キーの計数、XML内の利用枠取得 |
| 生成時の`tokenLimitOperations` | トークン枠を適用するHTTP操作の選択 |
| 生成時の`nativeLlmOperations` | Chat利用量監査の対象操作とAPI LLM診断の有効化 |
| `gatewayLlmTokenLimitEnabled` | 同名C#のenabled区画による判定結果 |
| `gatewayTokenCounterKey` | TPM・quotaで従来と同じ主体を計数 |
| `gatewayTokensPerMinute`・`gatewayTokenQuota`・`gatewayTokenQuotaPeriod` | XML内で準備する利用枠の値 |

## MOD-56の変更

ローカル実装・関連検証済みです。社員カウンターの社員情報JSON再読取り・ID再照合と、allowed・認証成功印の再確認を省きます。準備済みの確認済み社員IDから、従来と同じ`core-tokens-v1:user:tid:oid`形式のキーを生成します。利用者の利用枠と既存カウンターの帰属先は変えません。

## MOD-57の変更

ローカル実装・関連検証済みです。社員側のトークン有効化式からallowedと認証成功印の存在・trueの再確認だけを撤去します。直前の社員profileによる先行停止を維持し、認証成功の確認をそこへ任せます。MOD-56の78件（21.162s）は変更前の別実行で、今回の結果には転用しません。

## MOD-58の変更

ローカル実装・関連検証済みです。有効化式の社員元設定の存在・復元・`DeepEquals`と、TPM／quota必須項目の再確認を直前の社員profileへ集約します。準備済みprofileの読取りとnullによる有効化の判断、先行profileの欠落・型・範囲・一致検査は維持します。MOD-57の79件（24.220s）と維持条件は変更前の別記録で、今回の結果には転用しません。

## MOD-59の変更

ローカル実装・関連検証済みです。`tokenLimitOperations`の対象選択をAPIの既定接続先から、`backendOverride`を反映した操作ごとの実効接続先へ揃えます。Foundry以外が既定でもFoundryへのChat／Responses overrideは対象となり、Foundryが既定でもexternalへのChat overrideは対象外となります。標準openai-inference-v1のChat／Responsesは同じ対象です。

現在のHTTP・Chatのパスとkind・Responsesのkind条件、モデル一覧に依存しない選択を維持し、WebSocket・MCP・他形式へは広げません。枠値・null・免除・counter・APIM標準ポリシーの分岐は維持します。MOD-58の79件（26.658s）は変更前の別実行です。

## MOD-60の変更

ローカル実装・関連検証済みです。Chat利用量監査の`nativeLlmOperations`も、`backendOverride`を反映した操作の実効接続先へ揃えます。非Foundryが既定でもFoundry Chatへのoverrideは監査対象となり、APIのLLM診断も有効になります。Foundryが既定でもexternal Chatへのoverrideだけなら監査対象外・診断無効となり、別にFoundry Chatがある混在APIでは診断有効を維持します。Chat限定のためResponsesは対象外です。

新しい`emit-token-metric`は追加せず、既存の監査照合案内とnative LLMログの対象だけを変えます。本文記録の`body.bytes = 0`、global・生成MCPの診断無効、store・認証・利用枠・counter等は維持します。関連85件が全件成功しました（26.796s、失敗・スキップなし）。実Bicepのoverride監査ID・API診断・store対象、混在API・Responses等の境界、本文非記録と既存枠条件を確認し、コンパイル・Python／Bicep生成一致・同名所有・Razor・説明保持も成功しています。MOD-59の85件（21.181s）は変更前の別実行です。

## MOD-61の変更

ローカル実装・関連検証済みです。有効化式で操作ID一覧をJArrayとして直接読み、`operations != null`による内部不備時の無計数続行を撤去します。欠落・null・別型は既存on-errorへ渡し、免除時も一覧を確認してから免除を判断します。正常な空配列は対象操作なしとして合法で、一覧はモデル名ではなく操作IDです。対象外・免除時の枠値とcounterは維持します。

必要な値取得、0・Monthlyの内部値、APIM標準ポリシーの3分岐とInt32→Int64の受渡しの目的コメントを整え、条件と値は維持します。C#所有は引き続き計数キー式だけで、他の式はXML内に残します。新しい準備JSON・設定・応答・ログは追加しません。関連88件が全件成功しました（31.368s、失敗・スキップなし）。実C#の34対照で一覧不備20例の例外伝播、正常12例、先行停止2例を確認し、不備ではcounter・通常監査へ進みません。既存枠条件・override・診断・転送先選択と、実Bicepコンパイル・Python／Bicep生成一致・同名所有・Razor・説明保持も成功しています。MOD-60の85件（26.796s）は変更前の別実行です。

## 維持する処理と残件

- 先行共通認証による社員枠の設定検査と、準備済みprofile・nullの読取り。社員取得部品の再検査はMOD-62～65で集約し、現在は[取得・返却の3処理](../core-entra-user-profile/README.md)を維持します。
- TPM・quotaの上限・nullの扱い、免除・購読キー側のcounterと対象操作。
- `tokenLimitOperations`の実効接続先による対象選択はMOD-59でローカル検証済み。
- Chat利用量監査の`nativeLlmOperations`の対象選択はMOD-60でローカル検証済み。
- 操作ID一覧の直接読取りと、必要なparse・値抽出・Int32→Int64・APIM標準ポリシー3分岐は維持し、説明整備・ローカル検証済み。
- この部品のローカルInbound棚卸はここで一区切りです。独自拒否の未判断の残件はなく、社員取得部品もMOD-65でローカル棚卸を一区切りにしています。SQL利用枠の供給元と工程1全体は未完了です。
- Azure反映・実native計数・429・on-error分類（工程2）は未確認。

MOD-56～59の計数キー・有効化式・対象選択を棚卸対象とし、トークン制御全体と工程1全体の棚卸完了とは扱いません。MOD-56の関連78件が全件成功しました（21.162s、失敗・スキップなし）。実C#のID引継ぎと先行停止、数値型・5種類の期間を確認し、実Bicepコンパイル・Python／Bicep生成一致・同名所有・Razor・説明保持も成功しています。MOD-57も別実行の関連79件が全件成功しました（24.220s、失敗・スキップなし）。有効化式の認証成功印非アクセス、孤立したallowed／成功印の対照、正式な先行停止と残る設定不備の有効化停止、counter・null・免除・数値型・5種類の期間を確認し、実Bicepコンパイル・生成一致等も成功しています。MOD-58も別実行の関連79件が全件成功しました（26.658s、失敗・スキップなし）。有効化式の元社員設定・成功印非アクセス、先行UserProfileの欠落・不一致・必須項目・型・範囲不備の停止、有限／nullの組合せと既存counter・免除を確認し、実Bicepコンパイル・生成一致等も成功しています。MOD-59も別実行の関連85件が全件成功しました（21.181s、失敗・スキップなし）。正規化したoverrideの3対照、標準対象とWebSocket／MCP等の境界、profile→rate→tokenの順序、既存の転送先選択を確認し、実Bicepコンパイル・生成一致等も成功しています。Azure未反映・実カウンター未確認です。回数枠のMOD-55の74件（20.304s）は別部品・別時点の結果です。

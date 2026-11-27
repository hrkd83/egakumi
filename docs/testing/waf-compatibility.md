# App Gateway WAFの互換性・遮断確認

GatewayとPortalは、OWASP 3.2のPreventionと本文検査を有効にしたまま使用します。SQL、HTML、シェルコード、不透明な機械生成文字列をモデル・PII等へ渡すことは正当な利用です。そのデータを実行命令として扱うURL、ヘッダー、APIの制御情報とは分け、承認した操作経路・データ欄に限り、個別ルールまたは承認したアプリケーション攻撃ルールグループを調整します。

コマンドはリポジトリルートで実行し、[第01章](../setup/01-prerequisites.md#1-5-環境別のローカル領域を準備する)で設定した`DEPLOY_PARAMS_FILE`と絶対パスの`DEPLOY_LOCAL_DIRECTORY`を使います。計画・実測記録は同じ環境の領域へ保存します。

入力型と用途分類の正本は `apps/api-gateway/waf-input-contracts.json` です。`scripts/validation/waf_contracts.py` が再利用する内容型を各出現位置へ展開し、Azureへ渡す `apps/api-gateway/waf-data-exclusions.json` を生成・照合します。入力型、経路、通信方式で除外を決め、モデル名・世代・許可モデル数では分岐しません。

各profileはGatewayまたはPortalの完全パスと、下記の承認済み末尾パターンを指定し、`RequestArgValues`と、動的JSONキーで必要な場合の`RequestArgKeys`を使います。署名付き文書状態参照・通話終了は既存operationの可変ID位置を限定patternへ写し、その専用profileで正確なticket名の`RequestHeaderValues / Equals`を使います。画像編集・文字起こしの2操作は配備名の親パスを使います。任意のAPI templateやheaderを対象にしません。`StartsWith`は`state.`、`questions.`、JSON Schemaやobject形式の引数配下など、区切りを含むデータ構造に限定します。集計規則949110は除外しません。

Responsesの`input.call_id`は、ツール呼び出しと結果を結ぶ相関IDとして`control`分類を維持します。`g-responses`の完全パスだけで、`RequestArgValues / Equals / input.call_id`からSQL Hex検知の`942450`だけを除外します。7グループのデータ欄除外、他のSQL規則、Keys・Header・兄弟欄へは広げません。WAFはitemのtypeやIDの形式を検証する条件ではないため、この除外で特定のID形式だけが受理されるとは扱いません。同名queryにも引数除外が作用し得るため、適用範囲とAPIMの処理をそれぞれ読戻しと実経路で確認します。

## 受付容量と検査範囲

| 対象 | 現行資材の制御 |
| --- | --- |
| manifestのGateway／Portal AI操作経路（承認2末尾パターンを含む）、正本Speech・文書受付（次行の制御用途を除く） | Prevention・本文検査を維持。先頭2000 KiBまで検査し、本文・ファイルのサイズ強制は解除。超過部分は検査せず受け付ける |
| `g-document-status`・`g-realtime-ticket`・`p-document-status` | Prevention・本文検査とbaseの容量・サイズ強制を維持。Portal文書状態参照のアプリ側32 KiB上限も維持 |
| base・認証callback・管理系など上記以外 | 既定Prevention。configの本文検査枠・本文／ファイルサイズ強制を維持 |
| 外部管理WAF | 自動変更しない |

2000 KiBはAI入力の受付上限ではありません。通常AIのAPIM受付はClassicのバッファー上限500 MiB、文書は500,000,000 bytesで、提供元の制約が小さい場合はそちらが適用されます。WAFの検査枠を超えた本文まで検査したとは扱いません。

## 用途別のポリシー構成

名前はGatewayが`wafpol-g-<用途>-<ID>`、Portalが`wafpol-p-<用途>-<ID>`です。`<ID>`にはparamsの`services.apimInstanceId`に保存した6文字の構築識別子を共通利用します。Bicepと個別配備は同じ名前を計算し、更新や途中再開で識別子を生成し直しません。APIのURLは変更しません。

| 用途 | Gatewayの用途名 | Portalの用途名 |
| --- | --- | --- |
| 既定のHTTP経路 | `base` | `base` |
| Microsoft認証callback | `auth` | `auth` |
| Chat Completions | `chat-completions` | `generate`（Responsesも同じ入口） |
| Responses | `responses` | 上記`generate` |
| 埋め込み | `embeddings` | `embeddings` |
| リランク | `rerank` | `rerank` |
| 意思決定 | `decision` | `decision` |
| 画像生成・編集 | `images` | `images` |
| 音声 | `speech`（Azure Speech） | `speech`（Portalの音声入口） |
| 音声文字起こし | `transcribe` | 上記`speech` |
| Realtime | `realtime`（HTTP calls） | `realtime`（停止用handle） |
| Realtimeの署名付き制御 | `realtime-ticket`（hangup・observer） | 上記`realtime` |
| Live | `live`（HTTP sessions） | 個別のデータ欄除外なし |
| PII REST | `pii-rest` | `pii-redact` |
| PII MCP | `pii-mcp` | 上記`pii-redact` |
| 文書アップロード | `document-upload` | `document-upload` |
| 文書の状態参照 | `document-status` | `document-status` |

Gatewayの画像と埋め込みは複数の操作経路を1つのポリシーへまとめ、各パスに必要な除外の集合和を適用します。画像の`input_image`、埋め込みの`input`と`texts`の除外は、同じポリシーの全経路に作用します。GatewayのPIIは、RESTとMCPを分けます。PortalのPIIは両入口とも`text`と同じCookie除外なので、`p-pii-redact`を共有します。

### 配備名付き2操作のWAF経路

画像編集と文字起こしは、次の末尾パターンを使います。正本は`waf-input-contracts.json`の`modelOperationWafRoutes`で、PythonとBicepが同じ操作署名を読みます。API名・公開prefix・操作名・HTTPメソッド・URL template・API種類・kind・bodyFormat・parameterと既存のMIME契約が一致した場合だけ生成します。`allowedModels`の増減や新しい配備名はWAF経路の生成条件に含めません。

| 用途 | APIMに登録する操作 | WAFの適用パターン |
| --- | --- | --- |
| `g-images` | `POST /models/openai-images/v1/deployments/{deployment}/edits` | `/models/openai-images/v1/deployments/*` |
| `g-transcribe` | `POST /models/openai-audio/v1/deployments/{deployment}/transcriptions` | `/models/openai-audio/v1/deployments/*` |

[MicrosoftのPathPattern仕様](https://learn.microsoft.com/en-us/azure/application-gateway/url-route-overview#pathpattern)は中間wildcardを非対応とし、末尾wildcardを対応形として示しています。このため`*/edits`や`*/transcriptions`で操作suffixだけをWAFから選ぶ構成にはしません。末尾パターンは配下の未知suffix・深い子パス・別HTTPメソッドにも、既存の専用WAF規則とデータ欄除外を適用します。要求を業務操作として受け付けるかはAPIMの登録済みメソッド・URL照合で決まり、WAFの経路一致だけでは受け付けません。匿名401だけを、この操作境界の確認成功として扱いません。

### PII入口の除外をRESTとMCPへ分ける

App GatewayはPIIのREST、MCP、Portalそれぞれの公開パスに、用途別のWAFポリシーを関連付けます。

| WAFプロファイル | 公開パス | データ欄の除外 |
| --- | --- | --- |
| `g-pii-rest` | `/tools/pii/v1/redact` | `RequestArgValues / Equals / text`だけ |
| `g-pii-mcp` | `/tools/pii-v1-mcp/mcp` | `RequestArgValues / Equals / params.arguments.text`だけ |
| `p-pii-redact` | `/api/tools/pii`・`/api/tools/pii-mcp` | `text`とPortalセッションCookieの除外 |

Gateway側では表に示す各データ欄に限り、承認済みの管理規則の除外を適用します。Prevention、本文検査、容量制限、機密値のマスクは各入口で有効です。

新規構築後は、WAF設定の読戻しと、RESTのPII実行、MCPの初期化・ツール照会・PII実行をそれぞれ確認します。WAF通過、APIM認証、PIIマスキングの成功を区別して記録します。`RequestArgValues`は同名のquery引数にも作用し得るため、異常系と同名queryは別途確認します。ローカルのmanifest生成と構成照合で確認できるのは、設定と適用先までです。

用途名は検査するHTTP入口を表します。署名ticketを使うRealtime observer以外のRealtime／Live WebSocket入口など、個別のデータ欄除外がない経路は`base`を使います。データ欄の除外はmanifestに明記した条件から生成し、APIのMIMEや機能名によって別の除外を自動追加しません。外部管理ポリシーの指定時は、そのポリシーを再利用します。

### 入力文書URLの個別RFI除外

[入力文書URLの除外定義](../../apps/api-gateway/waf-source-url-exclusions.json)をBicepとPythonが参照します。適用先は次の組み合わせです。

| ポリシー | 完全パス | 正確な項目名 | 除外する規則 |
| --- | --- | --- | --- |
| `g-document-upload` | `/tools/documents/v1/analyze` | `urlSource` | OWASP 3.2の931130のみ |
| `g-responses` | `/models/openai/v1/responses` | `input.content.file_url`、`input.output.file_url` | OWASP 3.2の931130のみ |

`RequestArgValues / Equals`で項目の値を選び、規則グループは`REQUEST-931-APPLICATION-ATTACK-RFI`、個別IDは`931130`です。本文全体・Keys・Header・Cookie・他の項目へ広げず、931グループ全体や949110、GET本文の920170も無効化しません。App Gatewayの引数除外は同名query／formにも作用し、JSON本文だけには限定できません。除外はパス単位で、HTTPメソッドの操作照合はAPIMが行います。

Responsesの`file_url`は入力文書と関数結果の文書に使います。原本受付と入力文書URLの個別除外は、[文書解析の受入](tool-gateway-tests.md#文書解析の受入)で確認します。

## 配備後の受入

1. 対象のlistener／path ruleと関連ポリシーを読み戻します。Prevention、本文検査、マスク、個別除外と正本の経路を照合します。
2. `waf_contracts.py --check` で除外定義を照合し、`waf_probe.py --dry-run` で既存の合成入力の計画を作ります。
3. 隔離した検証環境で合意した範囲の合成入力を確認します。正常な入力は承認済みの用途・データ欄で通過し、制御欄と隣接経路では保護が維持されることを確認します。認証callback、JSON／multipart、WebSocket Upgrade、容量境界は採用する入口で確認します。
4. App GatewayのアクセスログとWAFログを同じTransactionIdで照合します。拒否の原因規則、APIM／Portal／提供元の応答、通信失敗を区別します。匿名の401はWAF通過までの判定に使います。機能の確認には認証付きの[Model Gateway](model-gateway-tests.md)・[Tool Gateway](tool-gateway-tests.md)の試験を行います。

```sh
python3 -B scripts/validation/waf_contracts.py --check
python3 -B scripts/validation/waf_probe.py --suite smoke --dry-run \
  --output "$DEPLOY_LOCAL_DIRECTORY/evidence/waf-required-plan.json"
```

計画と実施記録は同じ環境の保護領域へ保存します。WAFの調整・切戻しは[運用手順](../operations/platform-settings.md)を参照してください。

[Azure WAFの除外](https://learn.microsoft.com/azure/web-application-firewall/ag/application-gateway-waf-configuration) · [URI別ポリシー](https://learn.microsoft.com/azure/web-application-firewall/ag/per-site-policies)

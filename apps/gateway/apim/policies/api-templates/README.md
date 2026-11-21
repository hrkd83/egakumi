# APIポリシーテンプレート

API公開時に設定値を埋めて登録するポリシーテンプレートを管理する。
複数APIで使うテンプレートは正本を共有し、実API名ごとのコピーを作らない。
`service.xml`のようなAll APIsの全体ポリシーとは適用先が異なる。

| 正本 | Azureの適用先 | 役割 |
| --- | --- | --- |
| `gateway.xml` | 各HTTP APIのAll operations | HTTP APIごとの設定を受け取り、共通ポリシーを継承した後に操作・モデル・認可・転送を処理する |
| `mcp.xml` | 各生成MCP APIのAll operations | MCP APIごとの設定を受け取り、共通ポリシーを継承した後にMCP入力とREST操作への接続を処理する |
| `protocol.xml` | 各WebSocket APIの`onHandshake`操作／外部MCP APIのAll operations | 共通ポリシーを継承し、認可・転送先の接続を準備する。外部MCPだけにqueryルールの判定・監査処理を生成時に差し込む |

HTTPの`gateway.xml`はAPI・操作と本文のモデル名から内部設定を自動選択します。旧`X-Egakumi-Profile`の受付選択・値数/ID検査は行わず、下流転送前の削除だけを清掃として維持します。操作の基本設定＋モデル別差分を`core-api-operation-settings`で取得します。モデルalias、固定転送先と選択後の既存検査を維持します。[設定部品](../fragments/settings/core-api-settings/README.md)で実行時変数との関係を確認できます。

続く`core-api-authentication-result`が共通認証済みの本人・購読情報と設定ラベルを`gatewayDecision`へまとめます。内部情報を引き継げない場合の監査と500応答は、テンプレートの直後の分岐で処理します。[認証結果の部品](../fragments/authentication/core-api-authentication-result/README.md)に本体と入出力をまとめています。

Document解析の入力条件は、`gateway.xml`が[core-document-input-validation](../fragments/request/core-document-input-validation/README.md)を利用回数制限・下流接続準備・`accepted`監査より前で呼んで確認します。特定の開始操作へ適用するDocumentポリシーは、[操作別ポリシー](../operation-policies/README.md)の`document-analysis/document-analysis.xml`に置き、`<base />`でこのAPIテンプレートを継承します。同じ入力検査は開始操作で繰り返しません。

3正本の配置変更では内容とAzureの適用先を保持した。その後の汎用query構成では、HTTP・生成MCPの呼出位置を維持し、外部MCPには要求準備後の判定と応答・エラー監査を差し込む。WebSocketではquery差し込みを空にする。ローカル実装とAzure反映・受入を区別し、各テンプレート内の全制御を棚卸済みとは扱わない。
未着手の他の資材は現在の位置を保ち、初めて棚卸または改修する作業時に配置・名前・説明を揃える。
構成全体と移設の確認手順は [管理構成](../README.md) を参照する。

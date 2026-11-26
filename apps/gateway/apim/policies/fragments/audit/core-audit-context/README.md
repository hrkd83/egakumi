# 親要求IDと監査用モデル名

AzureのPolicy fragment名`core-audit-context`を維持し、同名XML／C#を正本として管理します。XMLは2つの観測式の順序とヘッダー削除、C#は固定の`BEGIN trace`／`END trace`と`BEGIN model`／`END model`で区画を選ぶ式を担当します。ファイル全体を一つのC#式として使いません。

| 区画 | XMLの変数 | marker |
|---|---|---|
| `trace` | `gatewayAgentTrace` | `__CORE_AUDIT_CONTEXT_TRACE_CSHARP__` |
| `model` | `gatewayAuditModel` | `__CORE_AUDIT_CONTEXT_MODEL_CSHARP__` |

HTTP・WebSocket・生成／外部MCPの入口で、共通認証の結果を引き継いだ後に呼び出します。`X-Core-Agent-Parent-Id`は単一値・空でないGUIDの場合だけ、確認済み呼出者IDを付けて追跡候補として保持します。値の欠落・不正形式・読取り失敗時の既存結果を維持し、処理後にこのヘッダーを削除します。親要求IDは認証や資源の所有者確認には使いません。

モデル名は、設定の`websocketModel`、HTTP専用部品から受け取った`gatewayAuditBodyModel`、pathモデル、操作の固定モデルの順に上書きして選びます。登録一覧とは照合せず、既存の文字種・長さを満たす未登録モデルも記録できます。本文を読む式を含めず、ストリーミング中継と共用します。読取り失敗時の空文字を含め、今回の配置整理では実行条件と結果を変えません。

モデル選択の設定取得は現行のままです。HTTPは準備済みの`gatewayOperationConstraint`を持ちますが、WebSocket・外部MCPと生成MCPはこの変数を作りません。生成MCPは対応するREST操作のIDを制御設定へ明示するため、全入口をHTTPの変数へ一律に置き換える変更は別の棚卸として評価します。

MOD-110で固定2区画をBicep・Pythonへ結線し、fixture・実式試験・Razor・同名所有検査を正規XML／C#へ揃え、直下の旧XMLを撤去しました。親要求ID・モデル選択・catchの結果、ヘッダー削除は維持します。Azureの日本語説明を揃え、full／compact／omitで移設前との構造・属性・実式トークン不変を確認しました。

関連92件が51.003秒で全件成功しました。失敗・エラー・スキップはありません。配備／保護source・容量と全入口の実式をローカルで確認済みです。Azure反映と実監査の受入は未実施です。

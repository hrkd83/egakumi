# CM本人セッションの使用確認

接続所有者・Graph本人IDを照合した後、Broker経由でSQLに使用直前の期限・失効を確認します。許可された個人接続だけがこの処理へ進み、世代・期限・セッション更新候補を検査して、再認証案内と下流へ送る資格情報を準備します。AzureのフラグメントIDは`core-credential-session-use`です。

同名C#は`use-result`と`expires-on`の2区画を所有し、各固定BEGIN/ENDを個別markerへ展開します。短い固定要求の組立てと選択結果の更新はXMLに保持すると判断しました。SQL呼出し、更新・再認証の応答と入口資格情報の除去もXMLへ保持します。Pythonの`render_connection("credential-session-use.xml", ...)`は旧名の論理aliasとして、この正本を読みます。配備XMLと保護された読戻しsourceは、同じXML／C#から説明付きで生成します。

MOD-133で既存use-resultの全文読取りを2区画読取りへ切り替え、expires-onの取得式だけを結線しました。短いuse-request／selection-updateを機械的にC#へ移さず、XMLのフロー内で管理します。full／compact／omitのactual-before生成物と実行構造・属性・C#tokens・string/char値が一致しました。関連35件は48.983秒、別の実C#対照23件は12.414秒でそれぞれ全件成功しました。SQL応答・本人／世代／期限／更新条件、配備／保護source一致・容量・構文・区画所有を確認しました。ローカル整理済みでAzure未反映です。

## MOD-78の変更

承認済みの3点をローカル実装しました。

- 接続部品にあった、後続で使わない`gatewayVerifiedConnectionSubject`のJSON生成を削除。
- この部品の内側にあったGraph本人照合成功の再確認だけを削除。接続部品は本人不一致・照合障害を`allowed:false`にし、この部品の外側`allowed && personal`がSQL呼出しを止めます。
- Broker authorize本文のセッションを、要求ヘッダーから読み直さず、保持済みの`gatewayConnectionRequestSession`から取得。SQL使用確認・再認証も同じ値を使います。

接続所有者・Graph本人IDの照合そのもの、SQLの使用直前確認、応答の型・世代・期限・rotation・セッション一致、更新と再認証、期限の取得と資格情報除去は維持します。未使用JSON以外の一般IDは加工しません。フラグメントの入れ子を追加せず、Azure名を保って旧physical XMLを撤去しました。

検証は実行ごとに記録します。CM実式・契約の2モジュール41件は全件成功（4.733s、失敗・スキップなし）。publication・容量の初回32件（27.947s）は追加assertの変数未代入で1件エラーとなり、publication18件は成功しました。試験の代入1行を修正し、容量モジュール14件を再実行して全件成功（15.916s、失敗・スキップなし）。正本所有・Razor構文・説明の別実行17件も全件成功（2.038s、失敗・スキップなし）。異なる実行を一回の成功へまとめません。

実caller・Broker選択・Graph照合からSQL使用確認の外側条件へ接続し、本人不一致・再認証・障害・非personalではSQLへ進まないことを確認しました。保持後に要求ヘッダーを変更／削除しても、Broker authorizeとSQL使用確認は元のセッションを使います。SQL期限・rotationの既存拒否も維持しています。独立レビューに実ロジックの所見はなく、説明と空白を除く正本差分は承認3点だけと照合しました。

Python・実Bicepの配備XML・保護された読戻しsourceの一致、Azure名・旧名alias・同名式所有と構文を確認しました。合成CM構成の完成フラグメントはconnection 29,814bytes、session-use 32,598bytes、outbound 32,357bytesで32,768bytes未満です。session-useの余裕は170bytesで、説明や処理を追加する際は再測定が必要です。配備とsourceの展開サイズもそれぞれ2,013,530bytes／1,921,965bytesで2,800,000bytesの予算内でした。

CM全体と工程1・2全体は未完了、Azure未反映・実CMでの受入は未確認です。

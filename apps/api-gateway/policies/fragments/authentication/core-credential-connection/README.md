# CMの本人接続

共通認証が確定した呼出者IDから、宣言済みのCredential Manager接続を選びます。個人接続ではBrokerの本人セッション、接続所有者、CMが取得したトークンのGraph本人ID、期限・失効を照合します。AzureのフラグメントIDは`core-credential-connection`です。

同名C#は`caller`／`selection`／`authorize-result`／`refresh-unavailable`／`claims-required`／`identity-status`の6区画を所有します。各区画を固定BEGIN/ENDと個別markerで展開し、XMLには外部通信・分岐・監査・応答のフローと短い式を保持します。Pythonの`render_connection("credential-connection.xml", ...)`は旧名の論理aliasとしてこの正本を読みます。

MOD-132で既存callerの全文読取りを区画読取りへ切り替え、残る5検査式だけを同名C#へ結線しました。準備XML全体はコピーせず、現在の対象式だけをmarkerへ置き換えています。full／compact／omitの変更前生成物と、実行時の構造・属性・C#トークン・string/char値が一致しました。既存のCM関連77件は70.136秒で全件成功し、Broker／Graph／セッションと本人照合、既存エラー、全protocolの配備／保護source・容量・構文・区画所有を確認しました。ローカル整理済みでAzure未反映です。

## MOD-76の変更

共通認証・APIの認証結果引き渡しが確認済みの`gatewayDecision.callerId`をそのまま使います。社員JSONの読取り、schemaVersion・GUIDの再検査／再整形、認証成功印と購読の再確認、接続選択冒頭でのdecision再解析・method／callerIdの再照合を削除します。`credentialCallerId`と`subscription:...`／`user:<tid>:<oid>`の表記は維持します。

HTTPとCM付き外部MCPでは引き渡し失敗が先に500で停止し、CM到達まで認証結果を上書きしません。生成MCPの入口にはCMを追加せず、現在の正式設定は生成MCP付きRESTのCM宣言も拒否します。WebSocketは対象外です。

ローカル実装・関連検証済みです。初回129件（62.994s）は126件成功・CM3モジュールのimportでエラーでした。試験用のimportパスを補い、その3モジュールだけ再実行した35件（21.096s）は34件成功・追加raw XML Razorクラス1件スキップでした。スキップは公式System.Web.Razor.dll未指定によるものです。移設後のCMを共通構文検査へ明示的に戻し、関連8件も成功しました（3.313s）。別の実行を一回の全件成功へまとめません。

実HTTP・外部MCPの引き渡し式と停止条件からCM式を呼び、引き渡し失敗時の500・CM未到達、成功後の社員JSON／認証印の非アクセス、caller呼出し1回・decision読取り1回を確認しました。接続所有者・Broker・Graph・期限・失効等の既存対照、Python／実Bicepの完成fragmentと保護された読戻しsourceの一致、Azure ID・同名所有・容量・説明・構文も確認しました。独立レビューで、承認対象のcallerとselection冒頭以外のXMLが不変であることを確認しています。

## 維持する処理と残件

- 接続設定のenabledの型・有効状態の確認と、service／personalの実行分岐。固定のprovider・mode・service接続名の事前検証はMOD-94で構築時へ集約します。
- 本人セッションの存在・単一値・形式、Broker authorize、tenant・owner・connection・provider・profile・OAuth tenantの照合。
- 固定のGraph本人確認先、CMトークン取得とGraph本人IDの照合、期限・失効・rotationと後続の使用直前確認。
- 内部のcallerIdを読めなければ空IDとし、接続選択で内部500を返して呼出し元APIが監査・応答で止める処理。

MOD-76時点では内部読取り失敗の応答分類も残件でした。MOD-77でその分類を整理し、CM全体と工程1全体の棚卸は引き続き残件です。Azure未反映・実CMでの受入は未確認です。

## MOD-77の変更

承認済みの内部エラー分類はローカル実装・関連検証済みです。既存の空callerIdの停止条件だけをstatus:500へ分け、接続設定の読取りやBroker・CM・Graphを呼びません。接続フラグメント直後、session-use前に[内部エラーsnippet](../../../snippets/authentication/credential-caller-error/README.md)をAPIへ展開し、既存core-gateway-telemetryのpolicy-error／AUTHENTICATION_PROCESSING_ERROR／500と、要求ID・no-store付きの本文なし500応答を実行します。

最初にフラグメント内へ監査を追加した容量precheckは標準33,537bytesで32KiB上限を超えました。API側で既存監査部品を使う配置へ調整したprecheckでは、標準26,762bytes・12pool構成29,894bytesとなり、入れ子のフラグメントを追加せず上限内に収まりました。これはローカルの生成確認で、Azure受入ではありません。接続設定・所有者・session・Graph・期限・失効の条件、読取り失敗時の空ID返却とIDの表記は維持します。MOD-76の403と各検証結果は変更前の記録です。

関連164件が初回から全件成功しました（39.283s、失敗・スキップなし）。実caller→selection→API guard→正規telemetry／HTTP応答を接続し、内部不備6種と親引き渡し後のcaller損失、正常キー／社員と既存403／409／503の対照を確認しました。内部不備ではBroker・CM・Graphの実条件を親から短絡評価して非到達を確認し、後段のsession-use・受付監査へ進みません。外部通信の実測とは区別します。HTTP／外部MCPの完成fragmentとAPIの配備・保護読戻しsource一致、CMなし・WebSocket・生成MCPの非追加、既存CM拒否・容量・正本所有・説明・構文も成功しました。独立レビューに所見はなく、コメントを除くXML差分は空callerの500分類だけと照合しました。工程1全体は未完了、Azure未反映です。


最終の公開情報検査では、合成ARM referenceの既存fixtureを再利用してIDの重複定義を省き、公式のFoundry／Cognitive Services認証先の既存公開定数に試験ファイルの正確なpathを揃えました。該当生成比較1件も再実行で成功しました（4.687s）。これは先の検証とは別の実行です。

## MOD-78の変更

後続で使われないgatewayVerifiedConnectionSubjectのJSON生成を削除し、Broker authorize本文のセッションは保持済みgatewayConnectionRequestSessionを使います。後段の[本人セッション部品](../core-credential-session-use/README.md)も正規配置へ移し、Graph成功の再確認だけを省きました。接続所有者・Graph本人IDの照合とSQLの使用直前確認、更新・再認証は維持します。ローカル実装・関連検証済みです。実行ごとの結果・容量・独立レビューは本人セッション部品へ記録しました。CM全体・工程1／2全体は未完了、Azure未反映です。

## MOD-94の変更

構築時の`normalize_configuration`が、固定のproviderNameの形式・登録済みproviderへの参照、modeがpersonal／serviceであること、serviceの固定connectionNameの形式・登録済み接続への参照を確認します。Bicepは、この検証済みcredentialConnectionをBase64の定数としてHTTPとCM付き外部MCPのInboundへ展開し、各要求でgatewayCredentialConnectionへ代入します。クライアントが送る接続指定ではないため、接続選択の先頭に重複していたproviderNameのRegex、serviceのconnectionNameのRegex、modeがpersonalであることの再確認だけを削除します。

実行時はenabledの型・有効状態と空callerIdの内部500を維持し、serviceでは固定接続、personalでは本人セッションを選ぶ分岐も維持します。本人セッションの存在・単一値・形式、Broker／CM／Graphの応答、接続所有者・provider・profile・tenant、期限・失効、SQLの使用直前確認は、外部入力やその時点の状態を確かめるため引き続き実行します。接続設定の読取り失敗と応答Bodyがnullの場合の分類も変更しません。

製品変更を実装しました。関連検証済みです。Azure反映・実CMでの受入は未実施です。

## MOD-95の変更

Broker authorizeとGraph本人確認は、応答の有無とHTTP状態を先に確認し、非200では本文を読みません。Brokerは403を403・409を409、Graphは401を409・403を403へ分類し、その他の非200と応答なしは503です。200だけ本文を必須として従来の本文条件を確認します。本人・接続所有者・provider・tenant・期限・失効の照合は維持します。

認証案内も同じ順序へ揃え、[正規snippet](../../../snippets/authentication/credential-challenge/README.md)へ移設しました。MOD-94で維持した応答Bodyがnullの場合の分類を、この変更で整理しています。関連検証済みです。Azure反映・実CMでの受入は未実施です。

MOD-94の既存関連37件は13.851sで成功しました（失敗・error・skipなし）。新規試験は追加していません。変更前XMLから指定3行を除いた値との完全一致と、12provider容量・配備／保護source一致も確認しています。Azure未反映です。

MOD-95の関連47件は13.156s、別実行の生成source／容量1件は16.226sで成功しました。初回の単体challenge旧path参照漏れは、正規aliasと先行C#展開で修正しています。35の追加合成対照で状態分類と拒否後SQL非到達を確認し、独立レビューの最終所見はありません。Azure未反映です。

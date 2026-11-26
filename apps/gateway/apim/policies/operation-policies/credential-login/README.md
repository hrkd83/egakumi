# CM接続の保護操作

ブラウザー操作は棚卸した順に同じフォルダーへ移設します。`launch.xml`はURL fragmentの起動秘密を消して同一originへPOSTするHTMLを所有します。APIMの多行C#式がないため、同名C#は作りません。

ブラウザー4操作も正規配置へ移設済みです。launch・launch-post・identity-callbackはXMLのみ、callbackは同名C#のinput・transaction・ownerの固定3区画をXMLへ展開します。Pythonはポリシールートと操作フォルダーを明示して分け、旧credential-loginフォルダーは参照しません。CMの監査・接続・認証案内の参照先は従来のポリシールートを維持します。

`start.xml`・`status.xml`・`session-commit.xml`・`logout.xml`・`logout-all.xml`・`disconnect.xml`は、CM接続の6保護操作を管理する正本です。同名C#は確認済み`credentialCallerId`の受取りを所有し、session-commit・logout・logout-all・disconnectは本人セッション要求も固定区画で所有します。BicepとPython rendererが専用markerへ生成時に展開し、新しいAzureフラグメントは追加しません。

## MOD-140の整理

disconnectの現XMLの本人セッション要求の1式だけをmarker化し、既存の呼出者受取りも同名C#の`caller`／`session-request`固定2区画へ揃えました。Python／Bicepは両属性encoderを使い、本人・セッション形式・本文読取りなしの要求・資格情報除去・固定Broker転送・応答／on-errorを維持します。actual-beforeの3mode生成物と構造・全属性・C#tokens・string／char値が一致し、既存47件38.583秒は全件成功しました。実C#・配備／保護source一致・容量・構文・所有を確認し、独立レビューに追加所見はありません。CM保護4操作の区画整理を閉じ、Azure反映・実CM受入は未実施です。

## MOD-139の整理

logout-allの現XMLの本人セッション要求の1式だけをmarker化し、呼出者受取りと合わせて同名C#の`caller`／`session-request`固定2区画へ揃えました。Python／Bicepの両属性encoder、本人・セッション形式・本文読取りなしの要求・資格情報除去・固定転送・応答／on-errorを維持します。actual-beforeの3mode生成物と構造・全属性・C#tokens・string／char値が一致し、既存47件36.856秒は全件成功しました。実C#・配備／保護source一致・容量・構文・所有を確認し、独立レビューに追加所見はありません。ローカル整理済みでAzure未反映です。

## MOD-138の整理

logoutの現XMLにある本人セッション要求の1式だけをmarker化し、既存の呼出者受取りも同名C#の`caller`／`session-request`固定2区画へ揃えました。Python／Bicepは両属性encoderを使い、本人・セッション形式・本文読取りなしの固定要求・資格情報除去・Broker転送・応答／on-errorを維持します。actual-beforeの3mode生成物と実行構造・全属性・C#tokens・string／char値が一致し、既存47件33.500秒は全件成功しました。実C#・配備／保護source一致・容量・構文・所有を確認し、独立レビューに追加所見はありません。ローカル整理済みでAzure未反映です。

## MOD-137の整理

session-commitの現XMLにある本人セッション要求の1式だけをmarker化し、既存の呼出者受取りの全文読取りも同名C#の`caller`／`session-request`固定2区画へ切り替えました。両方はXML属性用encoderでPython／Bicepへ結線します。本人・セッション形式・回転候補・世代・本文上限・読取り・固定Broker転送・エラー応答の条件は維持します。

変更直前のXML／C#と3mode生成物を保存し、full／compact／omitの実行構造・全属性・C#tokens・string／char値が一致しました。既存47件は36.814秒で全件成功し、実C#の本人・セッション要求対照、配備／保護source一致・容量・構文・所有を確認しました。独立レビューに追加所見はありません。ローカル整理済みで、Azure反映・実CM受入は未実施です。

| 操作 | C#の挿入marker |
| --- | --- |
| start | `__CREDENTIAL_LOGIN_START_CALLER_CSHARP__` |
| status | `__CREDENTIAL_LOGIN_STATUS_CALLER_CSHARP__` |
| session-commit | `__CREDENTIAL_LOGIN_SESSION_COMMIT_CALLER_CSHARP__` |
| logout | `__CREDENTIAL_LOGIN_LOGOUT_CALLER_CSHARP__` |
| logout-all | `__CREDENTIAL_LOGIN_LOGOUT_ALL_CALLER_CSHARP__` |
| disconnect | `__CREDENTIAL_LOGIN_DISCONNECT_CALLER_CSHARP__` |

## MOD-96の変更

先行する共通認証の成功印を確認し、キー利用者では確認済みの購読IDから従来の`subscription:<id>`を返します。Bearer利用者ではEntra認証の成功印、購読がないこと、`gatewayUserAuthentication`が存在することを確認して、そのJSONの`callerId`をそのまま受け取ります。社員JSONのschemaVersion、tenant／principalのGUID、IDの組立て、callerId／allocationIdとの再照合は先行認証で確認済みのため削除します。`gatewayDecision`は使いません。

callerIdの欠落・nullは空文字とし、内部読取り失敗のcatchも従来の空文字を返します。XMLの既存の空文字判定と401応答は維持します。XMLは流量制限、固定のBroker接続先、確認済み主体とBroker秘密のヘッダー上書き、クライアント資格情報の削除、30秒の転送、応答ヘッダーとon-errorを引き続き所有します。

正規移設は今回のstartだけです。残るstatus・session-commit・logout・logout-all・disconnectの5保護操作と、launch・launch-post・identity-callback・callbackの4ブラウザー操作は変更しません。Pythonの`POLICY_DIRECTORY`もそれらの旧正本を読むため維持し、`render("start.xml", ...)`だけ正規pathとC#先行展開へ切り替えます。Bicepの配備と保護された読戻し期待sourceは同じstart生成関数を使います。

MOD-96はローカル実装・関連検証済みです。既存のcredential_login、common_authentication_fragments、fragment_source_ownership、entra_publication_protocols、policy_razor_syntax、api_publication_sizeの関連97件が69.573秒で全件成功し、失敗・エラー・スキップはありませんでした。Azure反映・実CMでの受入は未実施です。

## MOD-97の変更

次の1操作としてstatusを同名XML/C#へ移設し、startと同じ確認済み`gatewayUserAuthentication.callerId`の受取りへ整理します。削除するのはschemaVersion・tenant／principalのGUID再解析・ID再構築・callerId／allocationId再照合だけです。共通認証とEntra認証の成功印、キーの購読分岐、Bearerで購読がなく社員JSONが存在する条件、callerIdの欠落・null／catchの空文字、空欄の既存401を維持します。`gatewayDecision`は使いません。

statusの固定Broker転送、主体・Broker秘密のヘッダー上書き、入口資格情報の削除、30秒forward、応答ヘッダー、on-error、本文は変更しません。Bicepの配備と保護された読戻し期待source、Python、合成fixture、Razorと1対1所有検査を同じ正本へ揃えます。既存startの14実式対照をstatusにも適用し、新しい試験メソッドは追加しません。

この変更で扱うのはstatusだけです。他のsession-commit・logout・logout-all・disconnectの4保護操作と4ブラウザー操作は維持し、`POLICY_DIRECTORY`もそれらの正本を読むため残します。MOD-97の関連45件は23.654秒で全件成功しました。既存の実式対照、正本の所有・構文、実Bicepの配備と保護sourceの一致・容量を確認し、失敗・エラー・スキップはありません。Azure反映・実CMでの受入は未実施です。


## MOD-100の変更

session-commitを同名XML/C#へ移設し、確認済みcallerIdを引き継ぐ形へ整理しました。削除したのは社員情報のschemaVersion・GUID再解析・ID再構築・caller/allocation再照合です。成功印、キー/Bearer分岐、欠落・null・読取り失敗時の空文字と既存401、本人sessionの外部検査、固定Brokerと応答・on-errorは維持します。配備・保護source・Python・fixture・Razor・所有検査を同じ正本へ結線しました。

関連45件が20.309秒で全件成功しました。失敗・エラー・スキップなし、Azure反映と実CM受入は未実施です。


## MOD-103の変更

disconnect（MOD-103）を同名XML/C#へ移設し、確認済みcallerIdの受取りへ整理しました。社員情報の形式・GUID・ID再構築・caller/allocation再照合を撤去し、成功印・キー/Bearer分岐・欠落/null/catchの空文字と既存401、本人session・固定Broker・下流応答/on-errorを維持します。配備・保護source・Python・fixture・Razor・所有検査を同じ正本へ結線しました。関連45件が21.564秒で全件成功し、失敗・エラー・スキップなし、Azure反映と実CM受入は未実施です。


## MOD-101の変更

logoutを同名XML/C#へ移設し、確認済みcallerIdを引き継ぐ形へ整理しました。schemaVersion・GUID再解析・ID再構築・caller/allocation再照合だけを撤去し、成功印・キー/Bearer分岐・欠落/null/catchの空文字と既存401、本人session・固定Broker・下流/error/応答を維持します。配備と保護source、Python、fixture、Razor、同名所有を結線しました。初回は関連15件中14件成功・試験importパス不備1件（20.343秒）でした。その31件のCM試験を正しいパスで別実行し、全件成功しました（2.410秒）。製品変更は追加していません。 Azure反映・実CM受入は未実施です。


## MOD-109の変更

callbackの多行3式だけを同名C#へ分離して結線しました。inputはtransactionId／stateとCookieを確認し、transactionはBrokerから得た固定取引・provider・期限・ownerを確認し、ownerは取得tokenによるGraph本人IDを照合します。これらは外部の取引・所有者確認なので維持します。固定宛先、tokenをBrokerへ送らない完了通知、HTML・CSP・Cookie消去・応答／on-errorも維持します。

生成時には固定区画の順・一意性・閉じ方とXMLの挿入点を確認し、区画をXMLへ展開してから設定値を置換します。新たなAPIM実行時検査や読取りは追加しません。Bicepの配備・保護source、Python、fixture、Razor・所有を同じ正本へ結線し、旧XMLを撤去しました。full／compact／omitで移設前との構造・属性・実式トークンが一致しました。

初回29件はPythonのcallback区画展開漏れで1クラスの準備が失敗しました。展開を全操作共通へ揃え、関連53件の再実行は27.837秒で全件成功しました。説明生成の別8件も0.285秒で成功し、別実行を合算した一回の検証とは扱いません。再実行の失敗・エラー・スキップはありません。Azure反映と実接続完了の受入は未実施です。

## MOD-108の変更

identity-callbackを同一内容のXMLで正規操作フォルダーへ移しました。code／stateのクエリとCookieを固定Brokerへ転送し、BrokerのOIDC・取引・session照合、資格情報除去、30秒forward、応答・no-store・on-errorを維持します。多行C#式はないためXMLだけを管理します。配備／保護source・Python・fixture・構文・所有検査を結線し旧XMLを撤去しました。

移設前後のバイト一致と、関連52件の全件成功（25.545秒、失敗・エラー・スキップなし）を確認しました。Azure反映と実OIDCコールバックの受入は未実施です。

## MOD-107の変更

launch-postを同一内容のXMLのまま移設しました。1値のOriginが固定publicOriginと一致する確認、固定Brokerの取引・session照合への転送、ヘッダー上書きと入口資格情報の削除、30秒forward、応答・on-errorを維持します。短いOrigin式はXMLに残し、不要なC#ファイルは作りません。配備・保護source・Python・fixture・構文・所有検査を正規XMLへ揃えて旧XMLを撤去し、移設前後のバイト一致を確認しました。

関連52件が26.703秒で全件成功しました。失敗・エラー・スキップはありません。Azure反映・実起動POSTの受入は未実施です。

## MOD-106の変更

launchを同一内容のXMLのまま正規操作フォルダーへ移し、Bicep・Python・fixture・Razor・所有検査と実JavaScript試験の参照を揃えました。URL fragmentの即時削除、起動値の形式・件数、同一origin POST、Microsoftログイン先のテナント・URL検査、HTML・CSP・no-storeを維持します。旧XMLを撤去し、移設前後のバイト一致を確認しました。

関連52件が26.987秒で全件成功しました。実JavaScriptの正常・失敗分岐とCSP、配備／保護source・容量・構文・所有を確認し、失敗・エラー・スキップはありません。Azure反映と実ブラウザーログインは未実施です。

## MOD-102の変更

logout-allを同名XML/C#へ移設し、確認済みcallerIdを引き継ぐ形へ整理しました。schemaVersion・GUID再解析・ID再構築・caller/allocation再照合だけを撤去し、成功印・キー/Bearer分岐・欠落/null/catchの空文字と既存401、本人session・固定Broker・下流/error/応答を維持します。配備と保護source、Python、fixture、Razor、同名所有を結線しました。コンパイラー設定を引き継がない初回起動で一部がスキップしたため、設定を補って関連45件を再実行し、28.446秒で全件成功しました。失敗・エラー・スキップはありません。 Azure反映・実CM受入は未実施です。

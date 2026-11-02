# 本人接続とCredential Managerプール

## 採用する構成

CMがアクセストークン・リフレッシュトークンを保存・更新し、credential brokerがMicrosoft本人確認、SQLの本人対応とプール割当、本人接続セッションv2を管理する。通常APIの共通認可は購読キー、個人接続APIでは追加で本人セッションを検証する。

- 論理名は`graph-delegated`。物理providerの初期値は`graph-delegated-a`〜`graph-delegated-l`の12個。
- provider当たり8,000件まで新規割当する。処理中の予約も容量へ含め、既存割当は増設で動かさない。
- 本人キーは検証済みの`tenantId + objectId + 論理プロファイル`。UPNは表示・検索・初期命名に限る。
- 接続名はUPN前半を正規化・63文字以内に収め、本人キーのSHA-256先頭16桁を付ける。UPN変更後も既存接続名を維持する。
- SQLは本人キーと`provider + 接続名`へ一意制約を設け、本人割当とpool容量予約を同じトランザクションで確定する。
- 認証取引、本人接続、利用セッションを分ける。ログアウトでCM接続を削除しない。
- brokerとAPIMのMIへ、providerスコープで組み込み`API Management Service Contributor`を割り当てる。カスタムロールは作らない。
- SQLはEntra専用認証。スキーマ更新IDと通常実行IDを分け、実行IDにDDLやユーザー管理を許可しない。

## 本人セッションv2

セッション値は`cs2.<SessionIdの64桁hex>.<秘密の64桁hex>`。識別子・秘密はそれぞれ暗号学的な乱数で生成する。SQLは秘密の検証ハッシュだけを保存し、平文を再取得するAPIを設けない。短命の`transactionId/pollSecret`と継続利用値は独立させる。

| テーブル | 責務 |
| --- | --- |
| `broker.Transactions` | 数分のOIDC/CM認証取引、仮セッションIDと検証ハッシュ、ブラウザーとの結び付け、CM照合時の`ConnectionEpochAtVerification` |
| `broker.Connections` | 本人とCM接続の対応、プール予約、接続状態と失効番号 |
| `broker.Sessions` | 購読・本人・接続への結び付け、認証完了/利用時刻、期限、現行/旧値ハッシュ、世代と失効 |
| `broker.SessionRotationCandidates` | 有限期間の更新候補ID・ハッシュ・対象世代 |
| `broker.SubjectSecurity` | 本人単位の全セッション失効番号 |

無操作7日、最長30日、値の更新間隔24時間を既定とし、`gateway.authentication.credentialLogin`の設定で変更する。起点は認証開始時刻ではなく、本人確認が完了した時刻。実期限は無操作期限と最長期限の早い方。最長期限は日次更新でも固定する。

SQLが毎回期限・失効を確認するため、ログアウト・全失効・接続解除は次の要求から反映する。現行値・旧値・更新候補が同じ失効に従う。既に開始したGraph処理の取り消しは保証しない。SQLの`rowversion`は失効番号の代わりに使わない。

CM接続を準備・照合したときの失効番号を認証取引へ保存し、認証完了時にも一致を検査する。本人確認の途中で接続解除された場合に、古いブラウザーの完了処理からその接続を復活させない。

## 認証から業務APIまで

1. 業務APIへ購読キーと保存済み`X-Core-Connection-Session`を送る。初回はセッションなし。
2. APIMは検証済み購読IDでBrokerへセッション検証を依頼し、CMのtokenを取得してGraphの本人IDを照合する。
3. Brokerが再度セッション・本人・接続の失効を確認して業務利用を確定し、無操作期限を更新する。途中のCM/Graph通信中はSQLロックを保持しない。
4. APIMが業務APIを実行する。ポーリング・ヘルス・更新確定・失効操作をこの延長経路へ入れない。
5. 認証が必要なら409`ConnectionRequired`と`authentication`を返す。バックエンドは独立した仮セッションを保存してから`loginUrl`だけを利用者へ案内する。
6. Microsoft OIDCの署名・issuer・audience・state・nonce・PKCEを検証し、tenantとoidを確定する。同じ本人のCM接続があれば再利用し、再認証が必要ならその接続でCMログインする。
7. CMの戻り先でCM tokenのGraph本人IDを照合し、初めてセッションを有効化する。ブラウザーには閉じてよい旨を表示する。
8. statusは状態・本人・期限だけを返す。アプリは自身が確認した本人と照合し、保存済み仮値で元の読み取りGETを一度再開する。statusからセッション平文を取得しない。

アプリの保存キーは`Gateway origin + 購読ID + 論理プロファイル + 検証済みtid/oid`。購読キーの所有者グループとGraph本人の委任資格情報を混同しない。別人・別購読へ使い回さず、UPNを送るだけで接続を選択しない。

## 24時間ごとの更新と通信消失

更新時期を過ぎた業務応答へ、APIMが専用ヘッダーで更新候補を付ける。本文は変えない。バックエンドは共有ロックと版数比較を使い、候補を保存してから`POST /auth/connections/session/commit`へ確定要求を送る。SQLは最初の一件だけを確定し、旧値の猶予を確定時から固定5分にする。

- 候補の応答を失っても現行値は維持する。別の候補を発行でき、平文復元は不要。
- 確定応答を失った場合は保存済み候補そのものを証明として再確認する。候補が既に現行なら、旧値の5分猶予を超えても確定済みの応答を再取得できる。
- 候補が未確定なら現行値で確定する。候補だけ期限切れなら`SessionRotationExpired`とし、現行値を残して候補だけを捨てる。
- `SessionRotationSuperseded`は共有保存先から新世代を再読する。Microsoft認証へ戻さず、古い応答で保存値を巻き戻さない。
- 更新を無視して同じ値を使い続けられないように、引き渡し猶予後は業務実行前に`SessionUpdateRequired`を返す。この場合だけ確定後に業務を再試行する。
- 成功済みの業務は日次更新のために再実行しない。更新通信の失敗だけを次の利用へ持ち越す。

契約のヘッダー・JSON・エラーコードは[本人接続の使い方](../setup/credential-manager-login.md)、言語別の保存・更新処理は[クライアント実装](connection-session-clients.md)を参照する。

## Brokerとの内部契約

内部要求は専用Key Vault鍵の`X-Core-Broker-Key`で認証する。APIMが購読・秘密を上書き付与し、外部入力の同名ヘッダーを信頼しない。Cookie・Origin・queryは必要な公開ルートだけから転送する。Brokerは内部ACA環境に置く。

| 内部操作 | 処理 |
| --- | --- |
| `POST /v1/start`・`/v1/challenge` | 短命の取引と独立した仮セッションを発行。challengeは業務API専用 |
| `POST /v1/launch`・`GET /v1/identity-callback` | ブラウザーの一時秘密、Cookie、OIDCを検証 |
| `GET /v1/status` | pending/completeとメタデータのみ。セッション値を返さない |
| `POST /v2/session/authorize` | 読み取り検証。無操作期限を延長しない |
| `POST /v2/session/use` | Graph本人照合後の再検証、業務利用確定、必要な更新候補 |
| `POST /v2/session/commit` | 保存済み候補の比較更新・確定。同じ確定の再送は冪等 |
| `POST /v1/logout`・`/v2/session/logout-all`・`/v2/session/disconnect` | 現在セッション・本人の全セッション・CM接続を対象に失効 |
| `POST /v2/connection/status` | CM接続状態とセッションを再検証。背景延命には使用しない |
| `POST /v1/callback-context`・`/v1/complete` | CMブラウザー取引とGraph本人照合の完了確認 |
| `/health` | 内容を含まないヘルスチェック |

内部ルート名・受信モデルはBrokerの`app.py`を正とする。クライアントは内部Operationへ直接要求せず、公開の`/auth/connections/*`を使う。

## 障害・条件付きアクセス

無効キーは401、改ざん・別購読は403。再認証で回復する状態だけを409の認証案内にする。認証開始は購読単位で制限し、超過は429`ConnectionRateLimited`。SQL・CM・Graphの一時障害は503等の障害として扱う。

Graphの通常の権限不足と、条件付きアクセスの追加認証要求を区別する。未知のCM状態は503で停止する。CMの公開ログインリンクAPIで任意claimsの引き渡しを保証できないため、claims challengeは403`ConnectionClaimsRequired`として利用を停止し、自動ログインを繰り返さない。CMの認証で満たせる範囲は実テナントの結合試験で確認する。Entraの失効が独自SQLセッションへ即時反映されるとは扱わない。Graph拒否・CM更新不能の検知にはトークン利用・更新に応じた遅延がある。期限内でもEntraのMFA・サインイン頻度が要求されれば従う。

## 配備・復旧

`azd up`は共通SQLサーバー・SQL初期化ID・ネットワーク・ACA環境・ACR等を準備し、`postup`でGateway→Portalを配備する。個別更新は同じ`deploy_gateway.py --config <params> --execute`を使う。SQL初期化→Broker起動→APIM登録の順。CM tokenはSQLやイメージへ含めない。イメージは管理VMから非公開ACRへ登録し、一時的な`AcrPush`とRun Commandを回収する。

| リソース | 名前・配置 |
| --- | --- |
| Broker Container App | `ca-<stem>-credential-broker` / `-svc` |
| 共通SQL・credentials DB | `sql-<stem>`・`sqldb-<stem>-credentials` / `-svc` |
| Broker・初期化用ID | `id-<stem>-credential-broker`・`id-<stem>-credential-schema` / `-id` |
| SQL初期化・定期照合ジョブ | `caj-<stem>-schema`・`caj-<stem>-recon` / `-svc` |
| SQL Private Endpoint | `pe-<stem>-sql` / `-nw` |

SQLはサービス管理TDE、Entra専用認証、Private Endpointを使い、公開アクセスを無効とする。容量初期値はGeneral Purposeの2vCore、本番はゾーン冗長化と35日バックアップ、検証は14日。保管枠96,000件は同時性能の保証ではなく、SQL・CM・APIMの負荷試験で性能を確認する。

v2初回配備は旧取引・旧セッションを破棄し、セッション関連SQLを置き換える。旧形式との互換層は設けない。CM接続と本人・プールの対応は維持する。

定期照合は消失・切断した接続を利用未確認にする。CM側だけの接続を確認したpoolは`AllocationBlocked`で新規割当を止め、通常の照合や再配備で解除しない。CM・SQL・容量の対応を調査してから運用者が解除する。作成からの経過だけで永続CM接続を自動削除しない。

SQL復元はCMを巻き戻さない。Brokerと本人接続APIを止め、復元した全セッション・途中取引を失効させる。CMと本人対応の照合、再認証・Graph本人確認後に再開する。[Gatewayの配備と復旧](../setup/credential-sql.md)を参照する。

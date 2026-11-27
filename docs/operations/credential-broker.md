# Credential Brokerの運用

初期配備は[第11章](../setup/11-deploy-gateway.md)で行う。

## 台帳と保守権限

SQLの主要テーブルは`broker.Pools`、`broker.Connections`、`broker.Transactions`、`broker.Sessions`。資格情報のトークンはCM内に保持する。通常実行用IDにはDDLやユーザー管理の権限を付けない。スキーマ変更は初期化用IDのジョブで行う。

本人セッションv2は`broker.SubjectSecurity`と`broker.SessionRotationCandidates`も使用する。`Sessions`には認証完了時刻、最終業務利用時刻、無操作・最長期限、現行/旧値の検証ハッシュ、世代と失効状態を保持する。`SubjectSecurity`は本人単位の全失効、`Connections`の失効番号は接続解除と関連セッション失効に使う。`rowversion`はSQLの比較更新用であり、通常の時刻更新でも変わるため失効番号とは区別する。仮値・現行値・候補の平文やCMトークンを台帳へ保存しない。

`Transactions.ConnectionEpochAtVerification`はCM接続を準備・照合した時点の接続失効番号であり、認証完了時にも一致を確認する。認証途中の接続解除と、古いコールバックの完了が競合しても接続を復活させない。

期限はconfigの`gateway.authentication.credentialLogin`の`idleTimeoutSeconds`（既定7日）、`absoluteLifetimeSeconds`（30日）、`rotationIntervalSeconds`（24時間）等で指定する。SQLの時刻で失効・期限を判定し、検証済み業務APIでだけ最終利用を更新する。既存セッションの最長期限は設定変更や値の更新で伸ばさない。[本人セッションの契約](../reference/identity/credential-manager-login.md)を参照する。

SQLの実行用ユーザーはマネージドIDのクライアントIDをSIDとして登録する。Azure RBACの割当で使うオブジェクトIDとは用途が異なる。[Microsoft公式のCREATE USERの例](https://learn.microsoft.com/en-us/sql/t-sql/statements/create-user-transact-sql?view=azuresqldb-current#k-create-a-contained-database-user-from-a-microsoft-entra-principal-without-validation)に従う。

管理VMからの直接保守は、担当者本人のEntra IDに、DBAが承認済みの構築DBとオブジェクトのSQL権限を手動付与し、終了時に回収する。初期構築ではスキーマジョブと通常アプリのIDに必要なSQL権限を用意する。本人への付与と回収、初回構築直後のDBA権限、回収後の接続確認は[管理VMからのSQL保守手順](management-sql-access.md)に従う。

## 接続の照合と回収

照合ジョブは毎時17分にCMとSQLを突き合わせ、消失・切断した接続を利用未確認に戻す。同じジョブで60日未使用の接続も回収し、Entra同期や追加ジョブは作らない。CM側だけの接続、pool上限、照合不能は件数として調査し、観測だけで容量を解放しない。

接続の未使用期間は`Connections.LastUsedAt`からSQL時刻で判定する。新規予約時を起点とし、その後は本人照合に成功した許可済み業務利用だけで更新する。ログイン・認証完了・照合・値の更新確定は延長しない。60×86400秒以上を回収対象とし、有効セッションがあるだけでは除外しない。有効なleaseや未完了の認証は保護し、直前の利用や再認証との競合は所有者・`rowversion`・leaseをSQLで再確認する。

1回の実行は最大100件、開始から300秒まで新しい回収を受け付ける。各接続を300秒のleaseで確保し、SQLで先に`deleting`と古い証明の無効化を確定する。CMの個別GETで対象のID・型・名前を確認し、SQLの確保状態を再検証して`If-Match`付きDELETEを送る。有効なETagはその値を使い、ヘッダー自体がない場合だけ、明示されたSQL検証が成功すれば`If-Match: *`を使う。空・不正・複数・ワイルドカード値のETagをこの代替へ切り替えない。新規割当には別の世代名を使い、既存対応の接続名は維持する。CMの個別GET 404と同じ回収lease・接続をSQLで確認した後だけ、対応表の削除と容量減算を一つのトランザクションで確定する。CMの接続状態値は未使用期間の判定元にしない。SQL検証不成立・不正ETag・412・通信失敗・削除結果不明・削除保留では枠を保持し、`LastCheckedAt`の試行順で次回に再試行する。利用者が解除を要求済みの`deleting`接続は、60日未満でも同じ確認手順で再試行する。

`reconcile`の計画モードは読取・件数集計だけで、定期ジョブは`reconcile --execute`で回収まで実行する。単体テストの偽SQL/HTTPで確認した処理順と、実SQLのロック・実CMの条件付き削除・正式ジョブの実行履歴を区別して記録する。結果の`cmCleanupAttempted`は削除確認callbackの論理的な試行であり、HTTP DELETEの実送信回数とは区別する。

CM側だけに残る接続を確認したpoolは`broker.Pools.AllocationBlocked=1`として、新規割当を止める。CMの実在接続・本人対応表・`Allocated`件数を運用者が照合・修復してから解除する。通常の照合や`migrate`では解除しない。既存の正常な接続はGraph本人照合を継続する。

`loginCallsPerMinute`は検証済み購読またはユーザー単位の開始回数制限である。同時サインイン数、SQLの待ち時間、CM管理APIの制限を測定して設定する。`pool.count × pool.capacity`は保管枠であり、同時処理性能の保証ではない。

## SQLの時点復元

CM側は復元時点のSQLとずれるため、本人接続の再開前に照合する。

1. 本人接続APIとbrokerを停止し、新規セッション発行を止める。
2. バックアップを別名のSQL DBへ復元し、台帳・スキーマを確認する。
3. 復元DBの`broker.Sessions`と`broker.Transactions`を全て失効させる。過去のセッションを再使用させない。
4. CMと対応表を照合する。紐付けが不明な接続は利用を止め、担当者が確認する。
5. DBAが稼働先のDB名と接続先を確認し、復元DBからの切替を行う。DB名は構築時に導出され、通常のGateway再配備で復元先は切り替わらない。再開前にSQL初期化ジョブで稼働先の権限とスキーマを検査する。
6. 再サインイン・Graph本人照合を確認して本人接続APIを再開する。

検証環境で復元訓練を実施し、復元可能時点と復旧所要時間を記録する。ゾーン障害、DBの論理破損、CMとの不一致を別々の試験として扱う。

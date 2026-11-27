# Document解析の操作別ポリシー

MOD-127では、正式な操作設定で必須のAPI版をString・非空として内部設定処理中に取得し、提供元の版と照合します。内部と外部の両方が欠落して`null == null`で通る経路を閉じました。内部の欠落・null・型不備・空・空白は、応答本文を読む前に既存500と監査へ停止します。外部だけ版が欠落する場合の502・応答読取り2回と、正常結果・前後サイズ・所有者条件を維持します。関連52件は44.862秒で全件成功し、内部5不備×外部版の有無の10対照は500・読取り0でした。ローカル整理済みで、Azure反映・実Document受入は未実施です。

MOD-126では、状態取得の既存`maxResponseBytes`のint取得を内部設定の処理中へ移し、原応答と公開投影のサイズ比較へ同じ取得値を使います。欠落・null・変換不備・int超過の4対照は、応答本文を読む前に既存500・`OPERATION_SETTINGS_INVALID`へ停止します。型・範囲の独自検査は追加せず、前後の2サイズ検査と非200の状態分類を維持します。関連52件は71.849秒で全件成功し、内部不備の応答読取り0・正常2・原サイズ超過1、チケット・結果・監査、生成source・容量・構文・所有を確認しました。API版の取得は次の別項目です。Azure反映・実Document受入は未実施です。

MOD-123の状態取得3区画は、full／compact／omitで変更前の構造・属性・実C#トークンと文字列／文字リテラルが一致しました。関連52件は54.153秒で全件成功しました。チケットの所有者・署名・期限・固定取得先、提供元結果・版・サイズ・公開投影、401／403／410／500・その他の安全な失敗、監査、Razor・所有・配備／保護source・容量を確認済みです。開始のMOD-122とは別実行の記録です。ローカル結線済みで、Azure反映・今回の実Document受入は未実施です。

開始操作はdocument-analysis.xml、状態取得はdocument-analysis-status.xmlを正本とします。各XMLは共通ポリシーの継承、転送、応答と監査の順序を所有し、同名C#はそこから分離する多行式だけを固定の名前付き区画で管理します。単式はXMLに残し、新しいAzureフラグメントは追加しません。

## 開始操作のC#結線

MOD-122で、現在のXMLの対応4式だけをmarkerへ置き換え、document-analysis.csをBicep／Pythonの固定区画rendererへ結線しました。統合直前に現在XMLと同名C#の実式を照合し、追加説明を除いて全文一致を確認しています。区画は`// BEGIN <name>`と`// END <name>`で囲み、それぞれ同名XMLの1式へ展開します。C#全文を各式へ繰り返し埋め込みません。

| XMLの式 | C#区画 | 挿入marker |
| --- | --- | --- |
| documentOperationId | operation-id | `__DOCUMENT_ANALYSIS_OPERATION_ID_CSHARP__` |
| documentAccepted | accepted | `__DOCUMENT_ANALYSIS_ACCEPTED_CSHARP__` |
| documentStatus | status | `__DOCUMENT_ANALYSIS_STATUS_CSHARP__` |
| documentRetryAfter | retry-after | `__DOCUMENT_ANALYSIS_RETRY_AFTER_CSHARP__` |

各markerは元のvalue属性の`@{`と`}`の間へ6空白の字下げで挿入します。C#を区画別にXML属性用へ変換して展開してから、従来のDocument API版等の設定を置換します。operation-id区画の`__DOCUMENT_API_VERSION__`もその置換対象です。単式の応答本文とon-errorの状態選択等はXMLに残します。

固定の受付URL・モデル・API版・処理ID、確認済み所有者の引渡し、接続先／API／所有者へのscope、HMACの署名領域と24時間期限、公開受付項目、提供元状態の分類、Retry-Afterの条件を変更しません。共通401、回数制限、既存監査、on-error、本文を変換しない転送と再送しない処理もXMLのまま維持します。

## 状態取得操作のC#結線

MOD-123で、現在のdocument-analysis-status.xmlの所有者チケット照合・照合後の状態選択・提供元結果の評価という3式だけをmarker化しました。統合直前に元式と同名C#を照合して、追加説明を除く全文一致を確認し、開始と同じ固定区画rendererへ結線しています。

| XMLの式 | C#区画 | 挿入marker |
| --- | --- | --- |
| documentOwner | owner | `__DOCUMENT_ANALYSIS_STATUS_OWNER_CSHARP__` |
| documentOwnerStatus | owner-status | `__DOCUMENT_ANALYSIS_STATUS_OWNER_STATUS_CSHARP__` |
| documentPollEvaluation | poll-evaluation | `__DOCUMENT_ANALYSIS_STATUS_POLL_EVALUATION_CSHARP__` |

各markerは元のvalue属性の`@{`と`}`の間へ6空白の字下げで挿入します。ジョブID・モデルの引継ぎ、固定GETのURI組立て、要求本文の削除、公開応答本文、on-errorの状態選択等の単式はXMLに残します。

チケットの単一ヘッダー、D形式のジョブID、長さ・Base64url形式、Document専用HMAC、署名全体の照合、確認済み所有者・接続先・APIへのscope、モデル、発行時刻・24時間期限を変更しません。内部認証情報の引継ぎ不備の500、拒否403、期限切れ410と、提供元の状態分類、結果の投影・サイズ確認、Retry-After、公開エラー、共通401・監査・on-errorも維持します。

開始と状態取得の7区画は中央結線・現在XMLのmarker置換を完了しました。XMLの短い単式は保持し、配備と保護sourceは同じ完成XMLを生成します。Azure反映・今回の実受入は未実施です。

MOD-122の開始4区画は、full／compact／omitで構造・属性・実C#トークンと文字列／文字リテラルが変更前と一致しました。関連52件は46.983秒で全件成功し、実受付・所有者チケット・安全な結果・共通認証エラー、Razor・区画所有・説明、配備／保護source一致・容量を確認しました。MOD-122時点で開始操作のローカル結線を完了し、状態取得の3区画は次のMOD-123へ分けました。現在は上記のとおり両操作とも結線済みです。Azure反映・今回の実Document受入は未実施です。

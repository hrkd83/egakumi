# LiveのWebRTC開始

`live-create.xml`は既存のLive開始操作へ登録する正本です。同名C#は要求変換と応答変換の2式を所有し、固定の`BEGIN request`／`END request`と`BEGIN answer`／`END answer`で区画を選んでXMLへ展開します。ファイル全体を一つのC#式として使いません。

| 区画 | XMLの変数 | marker |
|---|---|---|
| `request` | `liveSessionRequest` | `__LIVE_CREATE_REQUEST_CSHARP__` |
| `answer` | `liveSessionAnswer` | `__LIVE_CREATE_ANSWER_CSHARP__` |

入力はSDPと任意の`instructions`／`voice`です。許可項目、SDPの開始行、任意項目の型、声の既定値と許可値を確認し、準備済み操作設定の固定モデルで提供元の`session`／`transport`を作ります。入力のモデル指定やdelegation指定は受け付けず、本文・SDPをログへ出しません。

2xxの提供元応答だけ、既存のsession ID形式とSDP answerを確認して公開の`sessionId`／`sdp`へ変換します。読取りには`preserveContent:true`を使います。外部応答の型・形式確認と失敗時502は維持し、成功時は`Location`を削除してJSON応答へ揃えます。

初回の移設では2つの実行式とその他のXMLを維持し、両式とmarker以外のXMLが変更前と一致することを比較しました。入力不備400の既存監査、APIの入口認証・scope・回数枠、backend／on-errorの継承、正常応答の変換後に共通outboundを継承する順序は変更しません。

移設確認後の独立した判断として、短絡する400／502へ`Cache-Control: no-store`だけを追加しました。これらは共通outboundまで進まないため、この操作の応答にも要求ID付きエラーをHTTPキャッシュへ保存しない意図を明示します。状態コード、理由文、本文なし、要求ID、監査、拒否条件は維持します。Foundryの`store:false`とは別のHTTP応答ヘッダーです。

MOD-112で、提供元の2xx応答を変換できない502へ専用監査を追加しました。共通outboundへ進まないため、return-response直前に`policy-error`／`INVALID_LIVE_SESSION_ANSWER`／502を既存telemetryへ渡します。本文なし502・理由文・要求ID・no-storeは維持します。本文・session ID・SDP・例外詳細や、追加で読み取った提供元要求IDは記録しません。正常変換と2xx以外の応答は従来どおり後続の共通outboundへ進みます。

関連23件が21.793秒で全件成功しました。既存の不正answer試験で、変換失敗結果と監査→returnの順、固定phase／code／status、本文なしを確認しました。配備／保護source・容量・構文・所有も確認し、失敗・エラー・スキップはありません。Azure反映と実監査の到達確認は未実施です。

MOD-111で同名XML／C#をBicepの配備／保護source、Python renderer、fixture・Razor・区画所有検査へ結線し、直下の旧XMLを撤去しました。full／compact／omitで、400／502のno-storeヘッダー2つ以外の構造・属性・実式トークンが移設前と一致しました。

関連59件が45.014秒で全件成功しました。失敗・エラー・スキップはありません。ローカル移設・検証済みで、Azure反映と実LiveのWebRTC受入は未実施です。当時は操作設定読取りと本文不備が同じcatchで400になる分類を維持し、MOD-128で次のように整理しました。

MOD-128は要求本文の読取り→操作設定JSON→入力の項目・型→声の一覧と既存の短絡評価→固定モデル取得の順序を維持し、内部設定の取得・既存castの失敗だけを再送出します。固定PolicyId `core-live-session-request-settings`と完全一致するon-errorだけ500／`OPERATION_SETTINGS_INVALID`／`policy-error`を記録し、本文なし・要求ID・no-storeで停止します。その他は既存の`base`分類へ渡します。追加のJSON読取り、設定の新しい型・範囲検査、例外詳細の出力は加えません。声一覧の欠落・null・非配列・空、モデルの欠落・null・数値から文字列への既存変換は維持します。

関連30件は37.282秒で全件成功しました。flagの連続代入を省く小整理後、Liveの実式6件を再実行し3.107秒で全件成功しました。内部設定の8不備は本文read1・設定read1・限定500・監査1回、壊れた本文は設定read0で400、入力不備と空白voice・先頭choice一致の短絡、既存silent値、完全一致／近似／null／空ID、choose→base順を確認しました。配備／保護source一致・容量・構文・区画所有も確認しました。ローカル整理済みで、Azure反映と実Live受入は未実施です。

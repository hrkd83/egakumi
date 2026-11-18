// 目的：認証に入る前の要求情報をまとめ、後の監査ログから同じ要求を追跡できるようにします。
/* 監査用の要求情報を準備します。本文と資格情報は読み取りません。 */
// snapshotはJSONオブジェクトです。["項目名"] = 値の形で、監査へ渡す項目を追加します。
var snapshot = new JObject {
    // APIMが要求ごとに付けたIDです。利用者のIDではなく、処理やログを結び付けるために使います。
    ["requestId"] = context.RequestId.ToString(),
    // ?.は、APIや操作が存在するときだけIdを読み、存在しないときはnull（値なし）にする書き方です。
    ["apiId"] = context.Api?.Id,
    ["operationId"] = context.Operation?.Id,
    // 受信時刻を、日時とタイムゾーンを表せる"O"形式の文字列にして記録します。
    ["requestReceivedAt"] = context.Timestamp.ToString("O")
};

// ?? ""は、HTTPメソッドがnullだったときに空文字を使い、後のLength確認で例外が出ないようにします。
var method = context.Request.Method ?? "";
// 1～20文字のメソッドだけを候補にします。&&は条件を左から確認し、途中でfalseなら後ろを評価しません。
snapshot["httpMethod"] = method.Length > 0 && method.Length <= 20
    // Allは全文字を確認します。「条件 ? method : null」により、大文字A～Zだけなら保存し、それ以外は値なしにします。
    && method.All(c => c >= 'A' && c <= 'Z') ? method : null;

/* 実際のURLではなく操作のひな形を使い、クエリ部分を記録しません。 */
// 操作の定義にあるパスのひな形を取得します。操作やひな形がなければ空文字にします。
var template = context.Operation?.UrlTemplate ?? "";
// IndexOfは最初の「?」の位置を返します。見つからなければ-1なので、クエリがないことを判別できます。
var queryAt = template.IndexOf("?");
// 三項演算子「条件 ? 値1 : 値2」で、「?」がなければ全文の長さ、あれば「?」より前の長さを選びます。
var templateLength = queryAt < 0 ? template.Length : queryAt;
// JSONへ保存できる文字列に作り直します。ひな形のクエリ部分や制御文字は記録しません。
snapshot["operationTemplate"] = new string(
    // Math.Minでパス部分と512文字の短い方を選び、Takeでその範囲の文字だけを取り出します。
    template.Take(Math.Min(templateLength, 512))
        // Whereで改行などの制御文字を除き、ToArrayでnew stringに渡す文字の配列へ変換します。
        .Where(c => !Char.IsControl(c)).ToArray());

// TryParseが解析したIPアドレスを受け取る変数です。元の文字列とは別に、IPアドレス型で扱います。
System.Net.IPAddress peer;
// APIMと直接通信した相手のIPです。AppGW経由では通常AppGW側のIPとなり、社員PCのIPとは限りません。
var address = context.Request.IpAddress ?? "";
// 入力を64文字以下に限定してからIP形式を確認します。長すぎる場合はTryParseを呼びません。
snapshot["apimPeerIp"] = address.Length <= 64
    // TryParseは成功・失敗を返し、成功時はout peerへ解析結果を渡します。成功なら整形したIP、失敗ならnullを保存します。
    && System.Net.IPAddress.TryParse(address, out peer) ? peer.ToString() : null;

/* ヘッダーは1値だけを扱います。先に512文字へ制限してから文字を除きます。 */
// headersはヘッダー名から値を取り出す辞書です。同じヘッダーに複数値があり得るため、値は文字列の配列です。
var headers = context.Request.Headers;
// TryGetValueのout引数に使い、見つかったヘッダーの値をこの配列で受け取ります。
string[] values;
// 欠落や複数値のヘッダーは記録対象にしません。その場合に残る初期値をnullにします。
snapshot["reportedUserAgent"] = null;
// TryGetValueはヘッダーがあればtrueを返し、out valuesへ値を渡します。&&の後ろで、値が1つだけか確認します。
if (headers.TryGetValue("User-Agent", out values) && values.Length == 1) {
    // 唯一の値を取り出します。User-Agentはクライアントの申告であり、本人確認の証拠ではありません。
    var agent = values[0] ?? "";
    // 元の全文ではなく、文字数と文字の種類を制限した申告情報として記録します。
    snapshot["reportedUserAgent"] = new string(
        // 最初の512文字だけを取り出します。後で文字を除いても、それより先の文字を補充しません。
        agent.Take(512)
            // 制御文字とUTF-16のサロゲート文字を除きます。絵文字なども除かれるため、元のヘッダー全文とは異なる場合があります。
            .Where(c => !Char.IsControl(c) && !Char.IsSurrogate(c)).ToArray());
}

/* TraceIdはログの照合候補です。本人確認の根拠には使いません。 */
// AppGW側のログと照合する候補のIDです。ヘッダーが欠落・複数・不正形式ならnullのままにします。
snapshot["appGatewayTraceId"] = null;
// User-Agentと同じく、ヘッダーが存在し、値が1つだけの場合に限って内容を調べます。
if (headers.TryGetValue("x-appgw-trace-id", out values) && values.Length == 1) {
    // 唯一の値を文字列として取り出し、nullなら空文字にします。
    var trace = values[0] ?? "";
    // Guid.TryParseが解析したIDを、out traceIdで受け取るための変数です。
    Guid traceId;
    // 64文字以下・GUID形式・全ゼロではないIDの3条件を確認します。失敗しても例外にせず、初期値nullを保ちます。
    if (trace.Length <= 64 && Guid.TryParse(trace, out traceId) && traceId != Guid.Empty) {
        // "D"はハイフン付きGUIDの標準形式です。表記を統一し、別のログと照合しやすくします。
        snapshot["appGatewayTraceId"] = traceId.ToString("D");
    }
}

// APIM変数へ渡せるJSON文字列として返します。Formatting.Noneは見た目用の改行・空白を追加しない指定です。
return snapshot.ToString(Newtonsoft.Json.Formatting.None);

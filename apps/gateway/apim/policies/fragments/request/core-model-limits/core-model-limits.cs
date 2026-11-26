// 目的：選択済みの操作設定に従い、本文サイズを確認します。
// この同名C#をcore-model-limits.xmlへ展開して、Azureの同名フラグメントを作ります。
// 結果はgatewayLimitDecisionへ保存し、監査とHTTP応答は呼び出し元のAPIが行います。
// 応答本文は読まず、要求本文も後続へ渡せるように保持します。

// 設定を使えない場合の初期結果を用意します。すべての検査を通るまでallowed=falseにします。
var result = new JObject(new JProperty("allowed", false), new JProperty("status", 500), new JProperty("code", "LIMITS_NOT_CONFIGURED"));
// 先行処理が選んだ内部設定JSONを読みます。クライアントの本文とは別の情報です。
JObject constraint;
try {
    // 変数の取得・JSON解析に失敗した場合は、初期結果の500とLIMITS_NOT_CONFIGUREDを返します。
    constraint = JObject.Parse((string)context.Variables["gatewayOperationConstraint"]);
} catch {
    return result.ToString(Newtonsoft.Json.Formatting.None);
}

// 本文形式の既定値は配備時に補完します。ここでは準備済みの文字列だけを使います。
var configuredBodyFormat = constraint["bodyFormat"];
var format = configuredBodyFormat?.Type == JTokenType.String ? (string)configuredBodyFormat : null;
// 検査方法を選べない欠落・不正値は、本文を読む前に設定不備500を返します。
if (format != "json" && format != "binary" && format != "multipart"
    && format != "text" && format != "none") { return result.ToString(Newtonsoft.Json.Formatting.None); }
// noneは、この部品で本文を検査しない設定です。本文の存在を禁止する意味ではありません。
// 本文と、その検査に使うmaxRequestBytesを読まずに成功を返します。
if (format == "none") {
    result["allowed"] = true; result["status"] = 200; result["code"] = "NO_BODY_CONTRACT";
    return result.ToString(Newtonsoft.Json.Formatting.None);
}
// 本文を扱う4形式だけ、maxRequestBytesの整数設定を一度だけlongへ変換します。
var configuredMaxBytes = constraint["maxRequestBytes"];
long maxBytes;
// 変換できない値や桁あふれ、1〜500 MiBの範囲外は、本文を読む前に設定不備500を返します。
if (configuredMaxBytes == null || configuredMaxBytes.Type != JTokenType.Integer
    || !Int64.TryParse(configuredMaxBytes.ToString(), out maxBytes)
    || maxBytes < 1 || maxBytes > 524288000) { return result.ToString(Newtonsoft.Json.Formatting.None); }
// バイナリ・ファイル送信・テキストでは、操作設定に従って本文サイズを確認します。
// 受付形式の判断は提供元に任せ、この部品はContent-Typeの許可一覧を持ちません。
if (format == "binary" || format == "multipart" || format == "text") {
    // Content-Lengthは本文の申告バイト数です。ヘッダーの構文はHTTPの受信処理に任せます。
    var declaredLength = context.Request.Headers.GetValueOrDefault("Content-Length", "");
    long length;
    // 上限超過の申告なら、この部品で本文をバイト配列へ読み出す前に413を返します。
    // 本文の読取りと後続処理を省きます。HTTP受信層での受信・保持は別の処理です。
    // 省略・数値化できない値・上限以下は、次の操作設定による検査へ進みます。
    if (Int64.TryParse(declaredLength, out length) && length > maxBytes) {
        result["status"] = 413; result["code"] = "REQUEST_TOO_LARGE";
        return result.ToString(Newtonsoft.Json.Formatting.None);
    }
    // declaredは申告サイズだけを使う指定です。この部品では本文を読み出さず転送へ進みます。
    // 未指定またはactualは、これまでどおり実受信サイズも確認します。
    var declaredSizeOnly = (string)constraint["requestBodySizeCheck"] == "declared";
    if (!declaredSizeOnly) {
        // APIMから本文の全バイト数を読み、上限と比較します。後続へ渡す元の本文を保持します。
        var uploaded = context.Request.Body == null ? new byte[0] : context.Request.Body.As<byte[]>(preserveContent: true);
        if (uploaded.Length > maxBytes) {
            result["status"] = 413; result["code"] = "REQUEST_TOO_LARGE";
            return result.ToString(Newtonsoft.Json.Formatting.None);
        }
    }
    // 成功結果は、申告サイズのみの方式と実サイズも確認した方式を区別します。
    result["allowed"] = true; result["status"] = 200;
    result["code"] = declaredSizeOnly ? "DECLARED_SIZE_ONLY" : "BOUNDED_UPLOAD";
    return result.ToString(Newtonsoft.Json.Formatting.None);
}
// JSONのサイズ確認には、モデル選択で測定済みの元本文バイト数があれば使います。
// 先行処理からここまで本文を書き換えないため、サイズだけの全量再読取りを省けます。
long? measuredBytes = null;
try {
    if (context.Variables.ContainsKey("gatewayOperationSelection")) {
        measuredBytes = (long?)JObject.Parse((string)context.Variables["gatewayOperationSelection"])["requestBodyBytes"];
    }
} catch {
    // 内部結果の読取り失敗は設定不備500です。クライアントの本文解析とは区別します。
    return result.ToString(Newtonsoft.Json.Formatting.None);
}
// モデル選択で本文を読まない操作など、測定値がなければ従来どおり元本文を測定します。
var receivedBytes = measuredBytes ?? (context.Request.Body == null ? 0L : context.Request.Body.As<byte[]>(preserveContent: true).Length);
// 操作の種類から、PIIの受信サイズ枠を選びます。
var kind = (string)constraint["kind"];
// PIIはJSONのエスケープで送信サイズが増えるため、設定枠の6倍を受信枠にします。
// その場合もAPIMの500 MiBを超えません。PII操作側で解析後のサイズを別に確認します。
var receivedMaxBytes = kind == "pii" ? Math.Min(524288000L, 6L * maxBytes) : maxBytes;
if (receivedBytes > receivedMaxBytes) {
    result["status"] = 413;
    result["code"] = "REQUEST_TOO_LARGE";
    return result.ToString(Newtonsoft.Json.Formatting.None);
}

// すべての検査を通ったので、後続へ進める結果と200を返します。
result["allowed"] = true;
result["status"] = 200;
result["code"] = "REQUEST_WITHIN_LIMITS";
return result.ToString(Newtonsoft.Json.Formatting.None);

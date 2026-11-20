// 目的：HTTP操作またはMCP受付の設定が参照する観測ラベルを選び、URLクエリの概要を返します。
// この式は監査へ渡すJSONだけを作ります。要求の許可・拒否やHTTP応答は変更しません。
// 本文やクエリの名前・値は読まず、モデルやツールの入力・機能も変更しません。

// 1. 観測結果の初期値を用意します。noneは対象入口なし、unknownはまだ観測していない項目です。
// モデルやツールの実行成功を表す値ではありません。
var result = new JObject(new JProperty("route", "none"),
    new JProperty("httpMethod", "unknown"), new JProperty("presence", "unknown"),
    new JProperty("duplicates", "unknown"));

// 2. APIMが適用先のAPIを特定できたか確認します。
// 認証完了の確認は、この部品より先に実行するcore-api-request-preparationへ一本化します。
// この部品は認証結果を再確認しません。APIを特定できない場合だけ、クエリを観測せず初期結果を返します。
if (context.Api == null) {
    return result.ToString(Newtonsoft.Json.Formatting.None);
}

// 3. 呼出元APIが準備した種類と制御JSONを読みます。追加の有効化フラグやAPI許可一覧は使いません。
// gatewayApiSettingsの先頭行がhttp／mcp、gatewayControlSettingsがAPI定義から生成した観測ラベルの参照を持ちます。
var method = context.Request.Method;
var route = "none";
JToken selected = null;
try {
    var apiType = ((string)context.Variables["gatewayApiSettings"]).Split('\n')[0];
    var controls = JObject.Parse((string)context.Variables["gatewayControlSettings"]);

    // 4. HTTP操作とMCP受付を区別し、それぞれが明示した観測ラベルだけを選びます。
    if (apiType == "http" && context.Operation != null) {
        // HTTPはAPIMが選択済みの操作IDでqueryObservationsを引きます。
        // queryObservation未指定なら対象外です。サービス名・モデル形式・入力制約kindから推測しません。
        selected = controls["queryObservations"]?[context.Operation.Id];
    } else if (apiType == "mcp") {
        // Api.PathはAPIMが選択したAPIのsuffixです。書換え前のPathから、そのAPI内の相対入口を得ます。
        // prefixの直後が「/」の場合だけ除くため、名前の前半が同じ別パスを同じAPIとして扱いません。
        var suffix = (context.Api.Path ?? "").Trim('/');
        var prefix = suffix.Length == 0 ? "" : "/" + suffix;
        var publicPath = context.Request.OriginalUrl.Path ?? "";
        var relativePath = publicPath.StartsWith(prefix + "/", StringComparison.Ordinal)
            ? publicPath.Substring(prefix.Length) : "";
        if (relativePath.Length == 0) { return result.ToString(Newtonsoft.Json.Formatting.None); }
        // 生成MCPも外部MCPも、「HTTPメソッド + 空白 + 公開相対path」で同じ辞書を引きます。
        // 同じpathのGETとPOSTも別受付として選べます。メソッドの種類や元RESTのkindはここで推測しません。
        selected = controls["mcpQueryObservations"]?[method + " " + relativePath];
        if (selected == null && relativePath == "/mcp/") {
            // 標準/mcpの末尾スラッシュは従来どおり同じ入口として扱います。他のpathへは広げません。
            selected = controls["mcpQueryObservations"]?[method + " /mcp"];
        }
    }
} catch { return result.ToString(Newtonsoft.Json.Formatting.None); }
// 観測ラベルの参照は文字列だけを採用します。数字等を自動で文字列へ変換せず、未指定・不正値は対象外にします。
if (selected == null || selected.Type != JTokenType.String) { return result.ToString(Newtonsoft.Json.Formatting.None); }
route = (string)selected;
// 設定と同じASCII64文字以内の規約で確認します。none／unknownは観測・監査用の予約値です。
if (route == "none" || route == "unknown"
    || !System.Text.RegularExpressions.Regex.IsMatch(route, @"\A[A-Za-z][A-Za-z0-9_-]{0,63}\z")) {
    return result.ToString(Newtonsoft.Json.Formatting.None);
}
// routeはログ互換のための既存フィールド名で、ここからは選んだ観測ラベルを表します。
result["route"] = route;
result["httpMethod"] = method;

// 5. APIMが解析したクエリの有無と、同じ引数に複数値があるかをまとめます。
try {
    var query = context.Request.Url.Query;
    if (query == null) { throw new Exception("Query shape unavailable"); }
    // Countは解析済み項目の数です。末尾の「?」だけで項目がない場合はpresent=falseになります。
    var present = query.Count > 0;
    var duplicate = false;
    foreach (var argument in query) {
        // 文字列の内容には触れず、APIMが用意した値配列の長さだけを確認します。
        var values = argument.Value;
        duplicate = duplicate || (values != null && values.Length > 1);
    }
    result["presence"] = present ? "present" : "none";
    result["duplicates"] = duplicate ? "yes" : "no";
} catch {
    // 観測失敗はunknownとし、queryなしの正常利用の証拠にしません。
    // 要求を拒否せず、例外の内容やクエリの名前・値も出力しません。
}

// Formatting.Noneで見た目用の空白を追加しないJSON文字列を返し、XMLの要求変数へ保存します。
return result.ToString(Newtonsoft.Json.Formatting.None);

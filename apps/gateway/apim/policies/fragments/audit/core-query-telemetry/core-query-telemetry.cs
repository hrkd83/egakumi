// このファイルは同名XMLの2つの式を管理します。ファイル全体を一つのC#メソッドとして実行しません。
// BEGIN/ENDは生成時の区画指定です。predicateは記録するかの判定、messageは記録するJSONを作ります。

// BEGIN predicate
// 目的：APIの設定から選ばれた観測ラベルがある場合だけ、クエリ概要の監査を出力します。
try {
    if (!context.Variables.ContainsKey("gatewayQueryObservation")) { return false; }
    var observation = JObject.Parse((string)context.Variables["gatewayQueryObservation"]);
    // routeはURLではなく観測ラベルです。文字列型・長さ・ASCIIの書式を確認します。
    var value = observation["route"];
    if (value?.Type != JTokenType.String) { return false; }
    var route = (string)value;
    // noneは対象なし、unknownは未確認の予約値なので、有効な観測ラベルとして採用しません。
    return !String.IsNullOrEmpty(route) && route.Length <= 64 && route != "none" && route != "unknown"
        && ((route[0] >= 'A' && route[0] <= 'Z') || (route[0] >= 'a' && route[0] <= 'z'))
        && route.Skip(1).All(c => (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')
            || (c >= '0' && c <= '9') || c == '_' || c == '-');
// 型やJSONが不正なら記録対象にしません。例外の内容はログへ転記しません。
} catch { return false; }
// END predicate

// BEGIN message
// 1. この要求を他の監査記録と照合するため、APIMが確定した要求・API・操作のIDを用意します。
var requestContext = new JObject(new JProperty("requestId", context.RequestId.ToString()),
    new JProperty("apiId", context.Api == null ? null : context.Api.Id),
    new JProperty("operationId", context.Operation == null ? null : context.Operation.Id));
// 共通ポリシーが作った要求情報から、既存の許可項目だけを引き継ぎます。
// 任意の項目をコピーせず、JSONの大きさと文字列の長さを確認します。
if (context.Variables.ContainsKey("gatewayRequestContext")) {
    try {
        var snapshot = (string)context.Variables["gatewayRequestContext"];
        if (snapshot.Length <= 4096) {
            var stored = JObject.Parse(snapshot);
            foreach (var field in new [] { "requestReceivedAt", "httpMethod", "operationTemplate", "apimPeerIp", "reportedUserAgent", "appGatewayTraceId" }) {
                var value = stored[field];
                // JSON解析で日時に変換された時刻は、既存と同じISO形式へ戻します。
                if (field == "requestReceivedAt" && value?.Type == JTokenType.Date) { requestContext[field] = ((DateTime)value).ToString("O"); }
                // User-Agentが日時に見える場合も、元の文字列を使う既存処理を維持します。
                else if (field == "reportedUserAgent" && value?.Type == JTokenType.Date) {
                    var text = Newtonsoft.Json.JsonConvert.DeserializeObject<System.Collections.Generic.Dictionary<string,string>>(snapshot)[field];
                    requestContext[field] = text != null && text.Length <= 512 ? text : null;
                }
                else { requestContext[field] = value?.Type == JTokenType.String && ((string)value).Length <= 512 ? value : null; }
            }
        }
    // 読取に失敗しても、APIMのIDを残して記録を続けます。例外の詳細は出しません。
    } catch { }
}

// 2. 共通認証で確認した利用者情報を、既存の監査用検査で読み取ります。
// 生成時に共通のC#と説明をここへ組み込みます。キーやトークンを再検証する処理ではありません。
__AUDIT_SUBJECT_OBSERVATION_CSHARP__

// 3. 要求情報・利用者情報と、クエリの有無・複数値の有無を記録します。
// 件数区分とmodeを削除した観測専用の形式はschemaVersion=2です。イベント名query-guardとauditSchemaVersion=2は維持します。
var record = new JObject(new JProperty("schemaVersion", 2), new JProperty("auditSchemaVersion", 2),
    new JProperty("event", "query-guard"), new JProperty("requestId", requestContext["requestId"]),
    new JProperty("apiId", requestContext["apiId"]), new JProperty("operationId", requestContext["operationId"]),
    new JProperty("requestContext", requestContext), new JProperty("authenticatedSubject", authenticatedSubject));
// 不正な観測JSONや型変換の失敗を、正常な観測結果にしないため、まずunknownにします。
foreach (var field in new [] { "route", "httpMethod", "phase", "presence", "duplicates", "statusClass" }) { record[field] = "unknown"; }
try {
    var observation = JObject.Parse((string)context.Variables["gatewayQueryObservation"]);
    // 記録対象の判定とは別の式なので、出力する観測ラベルも同じ型・長さ・書式で確認します。
    var routeValue = observation["route"];
    // オブジェクト等の変換失敗は、従来どおり下のcatchで全概要項目をunknownにします。
    var route = (string)routeValue;
    if (routeValue?.Type != JTokenType.String || String.IsNullOrEmpty(route) || route.Length > 64 || route == "none" || route == "unknown"
        || !((route[0] >= 'A' && route[0] <= 'Z') || (route[0] >= 'a' && route[0] <= 'z'))
        || !route.Skip(1).All(c => (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')
            || (c >= '0' && c <= '9') || c == '_' || c == '-')) { route = "unknown"; }
    // HTTPメソッドは3種類に限定せず、1〜20文字のASCII大文字だけを記録します。
    var methodValue = observation["httpMethod"];
    var method = (string)methodValue;
    if (methodValue?.Type != JTokenType.String || String.IsNullOrEmpty(method) || method.Length > 20 || !method.All(c => c >= 'A' && c <= 'Z')) { method = "unknown"; }
    // 観測専用の3段階だけを記録します。後続の拒否や機能の成功を推測しません。
    var phase = context.Variables.ContainsKey("gatewayQueryPhase") ? (string)context.Variables["gatewayQueryPhase"] : "unknown";
    if (!(new string[] { "received", "backend-response", "policy-error" }).Contains(phase)) { phase = "unknown"; }
    var presence = (string)observation["presence"];
    if (presence != "none" && presence != "present") { presence = "unknown"; }
    var duplicate = (string)observation["duplicates"];
    if (duplicate != "yes" && duplicate != "no") { duplicate = "unknown"; }
    // 転送後や他のポリシーエラー段階だけ、既存の監査ステータスから応答区分を作ります。
    // クエリ観測自体の拒否ステータスはなく、ここで原因や成功を推測しません。
    var status = phase == "backend-response" || phase == "policy-error"
        ? (context.Variables.ContainsKey("gatewayAuditStatus") ? (int)context.Variables["gatewayAuditStatus"] : 0) : 0;
    var statusClass = status >= 200 && status < 300 ? "2xx" : status >= 300 && status < 400 ? "3xx"
        : status >= 400 && status < 500 ? "4xx" : status >= 500 && status < 600 ? "5xx" : "unknown";
    record["route"] = route; record["httpMethod"] = method;
    record["phase"] = phase; record["presence"] = presence;
    record["duplicates"] = duplicate; record["statusClass"] = statusClass;
// 観測の読取や変換に失敗した場合はunknownを維持します。例外の内容や観測JSON全体は出しません。
} catch { }
// 見た目用の空白を付けないJSONをtraceへ返します。HTTP 200をモデル・ツール完了の証明にはしません。
return record.ToString(Newtonsoft.Json.Formatting.None);
// END message

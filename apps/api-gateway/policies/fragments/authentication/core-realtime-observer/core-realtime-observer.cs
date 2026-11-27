// 目的：今回の確認済み所有者が、署名チケットで示した通話の補助接続へ入れることを確認します。
// 内部データは外部チケットのcatchより前で受け取り、onHandshakeの専用500へ渡します。
var owner = JObject.Parse((string)context.Variables["realtimeOwnerContext"]);
var version = (int)owner["v"];
var subscriptionId = (string)owner["subscriptionId"];
var caller = (string)owner["callerId"];
var ttl = (int)context.Variables["realtimeSessionTtlSeconds"];
try {
    var ticket = context.Request.Headers.GetValueOrDefault("X-Core-Call-Ticket", "");
    if (context.Request.Url.Query.Count != 1 || !context.Request.Url.Query.ContainsKey("call_id")
        || context.Request.Url.Query["call_id"].Length != 1) { return false; }
    var callId = context.Request.Url.Query["call_id"][0];
    if (ticket.Length > 4096 || !System.Text.RegularExpressions.Regex.IsMatch(callId, @"^[A-Za-z0-9_-]{1,160}$")) { return false; }
    var parts = ticket.Split('.');
    if (parts.Length != 2) { return false; }
    var key = System.Text.Encoding.UTF8.GetBytes("{{core-realtime-ticket-key}}");
    string expected;
    using (var hmac = new System.Security.Cryptography.HMACSHA256(key)) {
        expected = Convert.ToBase64String(hmac.ComputeHash(System.Text.Encoding.UTF8.GetBytes(parts[0])))
            .TrimEnd('=').Replace('+', '-').Replace('/', '_');
    }
    // 署名長と全文字の比較を維持します。署名が一致する前にpayloadを信用しません。
    if (expected.Length != parts[1].Length) { return false; }
    var difference = 0;
    for (var i = 0; i < expected.Length; i++) { difference |= expected[i] ^ parts[1][i]; }
    if (difference != 0) { return false; }
    var encoded = parts[0].Replace('-', '+').Replace('_', '/');
    encoded += new string('=', (4 - encoded.Length % 4) % 4);
    var payload = JObject.Parse(System.Text.Encoding.UTF8.GetString(Convert.FromBase64String(encoded)));
    var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
    // 別要求から届くチケットは、方式・呼出者・購読・call ID・期限をすべて照合します。
    return (int?)payload["v"] == version && (string)payload["callId"] == callId
        && (string)payload["subscriptionId"] == subscriptionId && (string)payload["callerId"] == caller
        && (long?)payload["exp"] > now && (long?)payload["exp"] <= now + ttl;
} catch {
    // 外部チケットの不正形式やJSON不成立は、従来の本文なし403へ返します。
    return false;
}

// このファイルはcore-common-authentication.xmlだけが所有する5つのAPIM式です。
// BEGINとENDは生成時の区画指定です。ファイル全体を一つのC#メソッドとして実行するものではありません。
// XMLが処理順を決め、各区画の本文を該当するset-variableの式へ差し込みます。

// BEGIN key-request-validation

    // 目的：APIMの購読検証結果とキーの送信形式を確認します。不正なキーをBearerで救済しません。
    // 設定されたキー用ヘッダー名を取得します。キー自体を変数や監査ログへ保存する処理ではありません。
    var keyHeader = (string)context.Variables["gatewayKeyHeader"];
    // URLのキー指定は受け付けないため、クエリにキー用の項目があるだけで拒否理由を返します。
    if (context.Request.Url.Query.ContainsKey("subscription-key") || context.Request.Url.Query.ContainsKey("api-key")) { return "Credentials must use the configured headers"; }
    /* 空値を先に確認し、ヘッダーがない場合も後ろの配列参照を実行せず拒否します。 */
    if (context.Subscription == null || String.IsNullOrWhiteSpace(context.Subscription.Id)
        || String.IsNullOrWhiteSpace(context.Request.Headers.GetValueOrDefault(keyHeader, ""))
        || context.Request.Headers[keyHeader].Length != 1) { return "Invalid subscription key"; }
    return "";

// END key-request-validation

// BEGIN bearer-request-validation

    // 目的：キー不要Productの経路とAuthorizationの形式を確認します。トークン本体の検証はXMLの標準ポリシーが担当します。
    // この分岐はキーを提示していない要求用です。購読が混ざる場合や、社員用Product以外の経路は拒否します。
    if (context.Subscription != null || context.Product == null
        || context.Product.Id != "__ENTRA_OPEN_PRODUCT_ID__") { return "Invalid bearer context"; }
    if (context.Request.Url.Query.ContainsKey("subscription-key") || context.Request.Url.Query.ContainsKey("api-key")) { return "Credentials must use the configured headers"; }
    // Authorizationは1値のBearer形式を要求します。欠落・複数値・空トークンでは標準検証へ進めません。
    if (!context.Request.Headers.ContainsKey("Authorization")) { return "Bearer token required"; }
    if (context.Request.Headers["Authorization"].Length != 1) { return "Invalid bearer token"; }
    var header = context.Request.Headers.GetValueOrDefault("Authorization", "");
    if (!header.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase) || String.IsNullOrWhiteSpace(header.Substring(7))) { return "Invalid bearer token"; }
    return "";

// END bearer-request-validation

// BEGIN entra-identity

    // 目的：標準検証に成功したトークンから本人の情報を保存します。会社設定の不備でも本人を監査から追えるようにします。
    var invalidAuthentication = "{\"errorCode\":\"INVALID_AUTHENTICATION\"}";
    var processingError = "{\"errorCode\":\"AUTHENTICATION_PROCESSING_ERROR\"}";
    try {
      // トークンは直前の標準検証が保存したものを取得します。未検証のヘッダーを自分で解読しません。
      var jwt = (Jwt)context.Variables["gatewayValidatedToken"];
      if (jwt == null || jwt.Claims == null) { return processingError; }
      /* verの値とscpの権限は、直前のAPIM標準トークン検証で確認済みです。
         ここでは各claimが単一の空でない値であることと、監査・利用枠に使う本人識別を確認します。 */
      foreach (var name in new [] { "tid", "oid", "ver", "scp" }) {
        if (!jwt.Claims.ContainsKey(name) || jwt.Claims[name] == null || jwt.Claims[name].Length != 1 || String.IsNullOrWhiteSpace(jwt.Claims[name][0])) { return invalidAuthentication; }
      }
      Guid tenant, principal;
      // tenantと本人のIDは空でないGUIDを要求し、後続では統一した文字列表記を使います。
      if (!Guid.TryParse(jwt.Claims["tid"][0], out tenant) || tenant == Guid.Empty
          || !Guid.TryParse(jwt.Claims["oid"][0], out principal) || principal == Guid.Empty) { return invalidAuthentication; }
      /* azpは検証済みtokenに単一の有効GUIDがある場合だけ監査へ渡します。
         client appの制限や、claimの欠落による追加の認証拒否は行いません。 */
      var clientId = ""; Guid client;
      if (jwt.Claims.ContainsKey("azp") && jwt.Claims["azp"] != null && jwt.Claims["azp"].Length == 1
          && Guid.TryParse(jwt.Claims["azp"][0], out client) && client != Guid.Empty) { clientId = client.ToString("D"); }
      var caller = "user:" + tenant.ToString("D") + ":" + principal.ToString("D");
      // この要求と標準検証に結び付いた本人snapshotを返します。ここではまだAPI利用の認証完了にしません。
      return new JObject(new JProperty("schemaVersion", 1), new JProperty("requestId", context.RequestId.ToString()),
        new JProperty("proof", "core-entra-token-validation"), new JProperty("callerId", caller),
        new JProperty("allocationId", caller), new JProperty("tenantId", tenant.ToString("D")),
        new JProperty("principalId", principal.ToString("D")), new JProperty("clientId", clientId)).ToString(Newtonsoft.Json.Formatting.None);
    } catch { return processingError; }

// END entra-identity

// BEGIN entra-authentication-result

    // 目的：本人snapshotと会社設定・利用枠を確認し、正常な7項目または失敗理由だけを返します。ここでは認証完了マーカーを設定しません。
    var invalidAuthentication = "{\"errorCode\":\"INVALID_AUTHENTICATION\"}";
    var invalidConfiguration = "{\"errorCode\":\"INVALID_GATEWAY_CONFIGURATION\"}";
    var processingError = "{\"errorCode\":\"AUTHENTICATION_PROCESSING_ERROR\"}";
    try {
      // 先行処理が本人確認の失敗を返していれば、会社設定の検査へ進まずその分類を引き継ぎます。
      var identity = JObject.Parse((string)context.Variables["gatewayEntraIdentityResult"]);
      var error = (string)identity["errorCode"];
      if (error != null) { return error == "INVALID_AUTHENTICATION" ? invalidAuthentication : processingError; }
      object proof;
      // 同じ要求で標準トークン検証を通ったsnapshotであることを、検証証拠と要求IDで確認します。
      if (!context.Variables.TryGetValue("gatewayEntraTokenValidationCompleted", out proof) || !(proof is bool) || !(bool)proof
          || (string)identity["requestId"] != context.RequestId.ToString() || (string)identity["proof"] != "core-entra-token-validation") { return processingError; }
      /* 先行処理の完成した本人snapshotだけを使います。不完全な内部結果を認証成功にしません。 */
      var names = new [] { "requestId", "proof", "callerId", "allocationId", "tenantId", "principalId", "clientId" };
      if (identity.Properties().Count() != 8 || identity["schemaVersion"]?.Type != JTokenType.Integer || (int)identity["schemaVersion"] != 1
          || !names.All(n => identity[n]?.Type == JTokenType.String && ((string)identity[n]).Length <= 253)) { return processingError; }
      Guid tenant, principal, client;
      var tid = (string)identity["tenantId"]; var oid = (string)identity["principalId"]; var azp = (string)identity["clientId"];
      if (!Guid.TryParse(tid, out tenant) || tenant == Guid.Empty || tid != tenant.ToString("D")
          || !Guid.TryParse(oid, out principal) || principal == Guid.Empty || oid != principal.ToString("D")
          || (azp != "" && (!Guid.TryParse(azp, out client) || client == Guid.Empty || azp != client.ToString("D")))
          || (string)identity["callerId"] != "user:" + tid + ":" + oid || (string)identity["allocationId"] != (string)identity["callerId"]) { return processingError; }
      Guid configuredTenant;
      // 会社設定そのものがGUIDでなければ500、確認済み本人と異なるtenantであれば401に分類します。
      if (!Guid.TryParse((string)context.Variables["gatewayEntraTenantId"], out configuredTenant) || configuredTenant == Guid.Empty) { return invalidConfiguration; }
      if (configuredTenant.ToString("D") != (string)identity["tenantId"]) { return invalidAuthentication; }
      /* JSON解析の失敗だけを会社設定の不備として扱います。取得や他の処理の例外は外側で区別します。 */
      var rawLimits = (string)context.Variables["gatewayEntraUserLimits"];
      JObject limits;
      try { limits = JObject.Parse(rawLimits); } catch { return invalidConfiguration; }
      if (limits == null || limits.Properties().Count() != 3) { return invalidConfiguration; }
      var rate = limits["requestRate"]; var tpm = limits["tokensPerMinute"]; var quota = limits["tokenQuota"];
      if (rate == null || tpm == null || quota == null) { return invalidConfiguration; }
      /* TryParseは桁あふれでも例外を出さずfalseを返します。型と許容範囲も従来どおり確認します。 */
      long calls, renewal, tokensPerMinute, quotaTokens;
      // requestRate・TPM・累計tokenQuotaは、nullなら制限なし、値があれば従来の型と範囲を検査します。
      if (rate.Type != JTokenType.Null && (rate.Type != JTokenType.Object || ((JObject)rate).Properties().Count() != 2
          || rate["calls"]?.Type != JTokenType.Integer || !Int64.TryParse(rate["calls"].ToString(), out calls) || calls < 1 || calls > 1000000
          || rate["renewalPeriodSeconds"]?.Type != JTokenType.Integer || !Int64.TryParse(rate["renewalPeriodSeconds"].ToString(), out renewal) || renewal < 1 || renewal > 300)) { return invalidConfiguration; }
      if (tpm.Type != JTokenType.Null && (tpm.Type != JTokenType.Integer || !Int64.TryParse(tpm.ToString(), out tokensPerMinute) || tokensPerMinute < 1 || tokensPerMinute > 2147483647)) { return invalidConfiguration; }
      if (quota.Type != JTokenType.Null && (quota.Type != JTokenType.Object || ((JObject)quota).Properties().Count() != 2
          || quota["tokens"]?.Type != JTokenType.Integer || !Int64.TryParse(quota["tokens"].ToString(), out quotaTokens) || quotaTokens < 1 || quotaTokens > 2147483647
          || quota["period"]?.Type != JTokenType.String
          || !new [] { "Hourly", "Daily", "Weekly", "Monthly", "Yearly" }.Contains((string)quota["period"]))) { return invalidConfiguration; }
      // 後続APIが使う7項目のJSONを返します。認証完了マーカーはXML側が成功結果を確認してから設定します。
      return new JObject(new JProperty("schemaVersion", 1), new JProperty("callerId", identity["callerId"]),
        new JProperty("allocationId", identity["allocationId"]), new JProperty("tenantId", identity["tenantId"]),
        new JProperty("principalId", identity["principalId"]), new JProperty("clientId", identity["clientId"]),
        new JProperty("limits", limits)).ToString(Newtonsoft.Json.Formatting.None);
    } catch { return processingError; }

// END entra-authentication-result

// BEGIN entra-failure-subject

    // 目的：失敗監査へ渡す本人を再確認します。未確認・不整合の場合はIDを出力せず、状態だけを返します。
    // 初期値は本人未確認です。確認済みの本人snapshotがなければ、識別IDを推測して補いません。
    var subject = new JObject(new JProperty("status", "unverified"));
    try {
      object proof, raw;
      if (!context.Variables.TryGetValue("gatewayEntraIdentityResult", out raw)) { return subject.ToString(Newtonsoft.Json.Formatting.None); }
      subject["status"] = "inconsistent";
      if (!(raw is string) || ((string)raw).Length > 1600) { return subject.ToString(Newtonsoft.Json.Formatting.None); }
      var identity = JObject.Parse((string)raw);
      // 本人認証に失敗したsnapshotには本人IDがないため、監査でも未確認として扱います。
      if (identity.Properties().Count() == 1 && identity["errorCode"]?.Type == JTokenType.String) {
        return new JObject(new JProperty("status", "unverified")).ToString(Newtonsoft.Json.Formatting.None);
      }
      // 本人は確認できたが会社設定・内部処理で500になった場合だけ、本人を失敗監査へ引き継ぎます。
      // キーの混在や標準検証の未完了があれば、確認済み利用者として採用しません。
      if ((int)context.Variables["gatewayAuditStatus"] != 500
          || !new [] { "INVALID_GATEWAY_CONFIGURATION", "AUTHENTICATION_PROCESSING_ERROR" }.Contains((string)context.Variables["gatewayAuditCode"])
          || context.Subscription != null || context.Variables.ContainsKey("gatewaySubscriptionId")
          || context.Request.Headers.ContainsKey("{{core-subscription-key-header}}")
          || !context.Variables.TryGetValue("gatewayEntraTokenValidationCompleted", out proof) || !(proof is bool) || !(bool)proof) { return subject.ToString(Newtonsoft.Json.Formatting.None); }
      var names = new [] { "requestId", "proof", "callerId", "allocationId", "tenantId", "principalId", "clientId" };
      if (identity.Properties().Count() != 8 || identity["schemaVersion"]?.Type != JTokenType.Integer || (int)identity["schemaVersion"] != 1
          || !names.All(n => identity[n]?.Type == JTokenType.String && ((string)identity[n]).Length <= 253)
          || (string)identity["requestId"] != context.RequestId.ToString() || (string)identity["proof"] != "core-entra-token-validation") { return subject.ToString(Newtonsoft.Json.Formatting.None); }
      Guid tenant, principal, client;
      var tid = (string)identity["tenantId"]; var oid = (string)identity["principalId"]; var azp = (string)identity["clientId"];
      if (!Guid.TryParse(tid, out tenant) || tenant == Guid.Empty || tid != tenant.ToString("D")
          || !Guid.TryParse(oid, out principal) || principal == Guid.Empty || oid != principal.ToString("D")
          || (azp != "" && (!Guid.TryParse(azp, out client) || client == Guid.Empty || azp != client.ToString("D")))) { return subject.ToString(Newtonsoft.Json.Formatting.None); }
      var caller = "user:" + tid + ":" + oid;
      // tenant・本人IDから組み立てた識別値と一致したものだけをverifiedとして返します。
      if ((string)identity["callerId"] != caller || (string)identity["allocationId"] != caller) { return subject.ToString(Newtonsoft.Json.Formatting.None); }
      subject = new JObject(new JProperty("status", "verified"), new JProperty("kind", "entraUser"),
        new JProperty("method", "user"), new JProperty("callerId", caller), new JProperty("allocationId", caller),
        new JProperty("tenantId", tid), new JProperty("principalId", oid), new JProperty("clientId", azp));
    } catch { }
    return subject.ToString(Newtonsoft.Json.Formatting.None);

// END entra-failure-subject

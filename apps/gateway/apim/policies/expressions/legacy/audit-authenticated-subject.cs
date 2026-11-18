/* 共通認証の確認済み主体。認可の成否から独立します。 */
// 目的：先行する共通認証の結果から、監査ログへ残せる「誰の利用か」を組み立てます。
// この部品はJWTの署名・期限・権限を検証せず、認証を済ませた処理の結果を参照します。
// JObjectはJSONの入れ物、JPropertyは項目名と値です。証拠を確認できるまではunverifiedにします。
var authenticatedSubject = new JObject(new JProperty("status", "unverified"));
// 1. 認証結果と識別情報を確認します。JSON解析などが失敗しても、末尾のcatchで監査情報へ結果を残します。
try {
  // context.Variablesはこの要求の先行処理が保存した値です。型が未確認なので、まずobject型で受け取ります。
  object authC, authE;
  // TryGetValueは「変数が存在したか」を返し、outで取り出した値をauthCへ入れます。
  // authHasCは値の有無、authCは共通認証の結果です。両者を区別して確認します。
  var authHasC = context.Variables.TryGetValue("gatewayCommonAuthenticationValidated", out authC);
  // 同じ方法で、Bearer経路のEntraトークン検証が成功したことを示す値も取り出します。
  var authHasE = context.Variables.TryGetValue("gatewayEntraAuthenticationValidated", out authE);
  // is boolで真偽値の型を確認してから(bool)で読みます。&&は左がfalseなら右を評価しないため、不正な型変換を防げます。
  var authCommon = authHasC && authC is bool && (bool)authC;
  var authEntra = authHasE && authE is bool && (bool)authE;
  // 値があるのに真偽値でない、またはEntra検証だけ成功して共通認証が成功していない場合は、結果の組み合わせが不整合です。
  // inconsistentは監査用の証拠が一致しない状態です。この部品で認証や認可の結果を変更する意味ではありません。
  if ((authHasC && !(authC is bool)) || (authHasE && !(authE is bool))
      || (authEntra && !authCommon)) {
    authenticatedSubject["status"] = "inconsistent";
  // 共通認証が成功した場合だけ、購読または社員の識別情報を検査します。成功が確認できなければunverifiedのままです。
  } else if (authCommon) {
    authenticatedSubject["status"] = "inconsistent";
    // 2. 先にinconsistentへしておき、この後の識別情報まで一致した場合だけverifiedへ置き換えます。
    // 購読の証拠があればキー経路を優先します。購読情報が不正でも、Bearer経路へ切り替えて取り繕いません。
    /* native Subscriptionを先に検査し、Entraへ救済しません。 */
    if (context.Subscription != null || context.Variables.ContainsKey("gatewaySubscriptionId")) {
      // context.SubscriptionはAPIMが確認した購読です。存在しない場合は空文字にして、下の検査を成功させません。
      var authSub = context.Subscription == null ? "" : context.Subscription.Id;
      object authSaved;
      // 購読IDが空でなく240文字以内であり、先行認証が保存したgatewaySubscriptionIdも文字列で同じIDかを確認します。
      var authKeyValid = !String.IsNullOrWhiteSpace(authSub) && authSub.Length <= 240
        && context.Variables.TryGetValue("gatewaySubscriptionId", out authSaved)
        && authSaved is string && (string)authSaved == authSub;
      // verifiedは「先行認証の結果と監査用の識別情報が一致した」という意味です。秘密のキー値は保存しません。
      if (authKeyValid) {
        // subscriptionIdは購読の識別子です。社員本人のIDや、同じ購読のprimary／secondaryキーの区別にはなりません。
        // callerIdへsubscription:を付け、社員用のuser:という識別子と混ざらないようにします。
        authenticatedSubject = new JObject(new JProperty("status", "verified"),
          new JProperty("kind", "apimSubscription"), new JProperty("method", "subscriptionKey"),
          new JProperty("callerId", "subscription:" + authSub), new JProperty("subscriptionId", authSub));
      }
    // 3. 購読の証拠がない場合だけ、Entraで検証済みのBearer経路の社員情報を確認します。
    } else if (authEntra) {
      // authRawは先行認証が作った社員情報のJSON、authCfgは共通認証に設定されたテナントIDです。トークン本体は読みません。
      object authRaw = null, authCfg = null;
      // 両方とも文字列であることを確認し、社員情報は4096文字、設定テナントIDは64文字までに限定して読みます。
      var authHasUser = context.Variables.TryGetValue("gatewayUserAuthentication", out authRaw)
        && authRaw is string && ((string)authRaw).Length <= 4096
        && context.Variables.TryGetValue("gatewayEntraTenantId", out authCfg)
        && authCfg is string && ((string)authCfg).Length <= 64;
      // 必要な値と型がそろった場合だけJSONとして解析します。欠落や長さ超過ならinconsistentのままです。
      if (authHasUser) {
        // JObject.ParseはJSON文字列を項目別に参照できる形へ変換します。不正なJSONなら例外となり、catchへ進みます。
        var authUser = JObject.Parse((string)authRaw);
        // ?.は項目がない場合にも参照できる書き方です。JSON上の型が文字列である項目だけを取り出します。
        // tenantIdは所属テナント、principalIdは社員のObject ID、clientIdは呼び出したクライアントアプリのIDです。
        var authTid = authUser["tenantId"]?.Type == JTokenType.String ? (string)authUser["tenantId"] : "";
        var authOid = authUser["principalId"]?.Type == JTokenType.String ? (string)authUser["principalId"] : "";
        var authAzp = authUser["clientId"]?.Type == JTokenType.String ? (string)authUser["clientId"] : null;
        // Guid型はIDを解析・比較するための型です。Guid.Emptyは全桁ゼロのIDで、有効な利用者IDとして受け入れません。
        Guid authT = Guid.Empty, authO = Guid.Empty, authA = Guid.Empty, authCfgT = Guid.Empty;
        // 社員情報の形式を表すschemaVersionが整数の1であることを確認します。異なる形式の情報を読み違えないためです。
        var authValid = authUser["schemaVersion"]?.Type == JTokenType.Integer && (int)authUser["schemaVersion"] == 1
          // テナントIDと社員IDは36文字で、TryParseに成功し、ゼロIDでなく、ToString("D")の正規形と一致することを確認します。
          // "D"は小文字の16進数とハイフンによるGUID表記です。同じIDの表記揺れでログの識別子が分かれることを防ぎます。
          && authTid.Length == 36 && Guid.TryParse(authTid, out authT) && authT != Guid.Empty && authTid == authT.ToString("D")
          && authOid.Length == 36 && Guid.TryParse(authOid, out authO) && authO != Guid.Empty && authOid == authO.ToString("D")
          // 設定された認証先のテナントと、社員情報に入ったテナントが同じであることを確認します。
          && Guid.TryParse((string)authCfg, out authCfgT) && authCfgT == authT
          // クライアントIDは文字列項目が必要です。空文字は許容し、値がある場合は社員IDと同じGUID形式を確認します。
          // これはIDの形式確認だけであり、許可するクライアントアプリの一覧を制御する処理ではありません。
          && authAzp != null && (authAzp == "" || authAzp.Length == 36
            && Guid.TryParse(authAzp, out authA) && authA != Guid.Empty && authAzp == authA.ToString("D"));
        // 社員は「テナントID＋Object ID」の組み合わせで識別します。clientIdは利用アプリのIDであり、社員本人のIDではありません。
        var authCaller = "user:" + authT.ToString("D") + ":" + authO.ToString("D");
        // 先行認証が保存したcallerIdと利用枠のallocationIdも、ここで組み立てた社員識別子に完全一致することを確認します。
        // 別の利用者や購読の利用枠へ取り違えて記録することを防ぎます。
        authValid = authValid && authUser["callerId"]?.Type == JTokenType.String
          && (string)authUser["callerId"] == authCaller && authUser["allocationId"]?.Type == JTokenType.String
          && (string)authUser["allocationId"] == authCaller;
        // すべて一致した場合だけ、確認済みの社員ID・テナントID・クライアントIDを監査情報へ格納します。
        if (authValid) {
          authenticatedSubject = new JObject(new JProperty("status", "verified"),
            new JProperty("kind", "entraUser"), new JProperty("method", "user"), new JProperty("callerId", authCaller),
            new JProperty("allocationId", authCaller), new JProperty("tenantId", authTid),
            new JProperty("principalId", authOid), new JProperty("clientId", authAzp));
        }
      }
    }
  }
// 4. JSON解析や型変換などで例外が起きた場合は、部分的な識別情報を残さずinconsistentだけを記録します。
// このcatchは本部品の監査情報づくりを完結させる処理であり、トークンを再検証したり別の認証方式で救済したりしません。
} catch { authenticatedSubject = new JObject(new JProperty("status", "inconsistent")); }

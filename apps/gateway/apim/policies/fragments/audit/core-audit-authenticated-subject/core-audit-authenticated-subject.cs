/* 共通認証の確認済み主体。認可の成否から独立します。 */
// 目的：STEP4が確認した購読または社員を、監査で追跡するためのJSONへまとめます。
// 処理：認証完了と識別情報を照合し、一致した場合だけverified（確認済み）にします。
// このC#を同名XMLへ生成時に埋め込みます。本文・生トークンを読み直したり、APIの認可結果を変えたりしません。
// 初期値はunverified（未確認）です。CMのブラウザー受付など、共通認証で本人を確認していない要求もこの状態になります。
var authenticatedSubject = new JObject(new JProperty("status", "unverified"));
try {
  // 1. この要求の認証完了結果を取得します。変数がなければfalse、bool以外なら末尾のcatchで不整合にします。
  var authCommon = context.Variables.GetValueOrDefault<bool>("gatewayCommonAuthenticationValidated", false);
  var authEntra = context.Variables.GetValueOrDefault<bool>("gatewayEntraAuthenticationValidated", false);
  // Bearer認証だけ完了し、共通認証が完了していない組合せは採用しません。
  if (authEntra && !authCommon) {
    authenticatedSubject["status"] = "inconsistent";
  } else if (authCommon) {
    // 2. 共通認証の成功だけで本人IDを採用せず、識別情報まで一致した場合だけverifiedへ置き換えます。
    // 以下の確認に失敗した場合はinconsistent（不整合）だけを残し、API要求は継続します。
    authenticatedSubject["status"] = "inconsistent";
    // 購読の証拠がある要求はキー経路で照合します。不整合な購読をBearerで補いません。
    /* native Subscriptionを先に検査し、Entraへ救済しません。 */
    if (context.Subscription != null || context.Variables.ContainsKey("gatewaySubscriptionId")) {
      // APIMが確認した購読IDと、STEP4が保存したIDを取得します。保存値の欠落はnull、string以外はcatchで不整合になります。
      var authSub = context.Subscription == null ? "" : context.Subscription.Id;
      var authSaved = context.Variables.GetValueOrDefault<string>("gatewaySubscriptionId", null);
      // 空・空白のIDを採用せず、256文字以内と完全一致を確認します。callerIdは接頭辞13文字を含め最大269文字です。
      var authKeyValid = !String.IsNullOrWhiteSpace(authSub) && authSub.Length <= 256 && authSaved == authSub;
      if (authKeyValid) {
        // 購読をsubscription:で識別します。社員本人やprimary／secondaryキーを識別する値ではありません。
        authenticatedSubject = new JObject(new JProperty("status", "verified"),
          new JProperty("kind", "apimSubscription"), new JProperty("method", "subscriptionKey"),
          new JProperty("callerId", "subscription:" + authSub), new JProperty("subscriptionId", authSub));
      }
    } else if (authEntra) {
      // 3. 購読の証拠がないBearer経路では、STEP4の社員JSONと設定tenantを取得します。トークン自体は読みません。
      var authRaw = context.Variables.GetValueOrDefault<string>("gatewayUserAuthentication", null);
      var authCfg = context.Variables.GetValueOrDefault<string>("gatewayEntraTenantId", null);
      // 4096／64は内部変数を読む上限です。アクセストークン、API本文、モデルの入力長の制限ではありません。
      var authHasUser = authRaw != null && authRaw.Length <= 4096 && authCfg != null && authCfg.Length <= 64;
      if (authHasUser) {
        // JSONを解析し、string型のIDだけを取り出します。clientIdの項目欠落・型不正は、空文字とは区別します。
        var authUser = JObject.Parse(authRaw);
        var authTid = authUser["tenantId"]?.Type == JTokenType.String ? (string)authUser["tenantId"] : "";
        var authOid = authUser["principalId"]?.Type == JTokenType.String ? (string)authUser["principalId"] : "";
        var authAzp = authUser["clientId"]?.Type == JTokenType.String ? (string)authUser["clientId"] : null;
        Guid authT = Guid.Empty, authO = Guid.Empty, authA = Guid.Empty, authCfgT = Guid.Empty;
        // 内部JSONの形式番号は整数の1です。Entraトークンのv2とは別の番号です。
        var authValid = authUser["schemaVersion"]?.Type == JTokenType.Integer && (int)authUser["schemaVersion"] == 1;

        // 4. tenant IDは36文字の非ゼロGUIDで、小文字とハイフンの正規形であることを確認します。
        // authValid && ...により、前の確認が失敗した場合は右側を実行せず、従来の確認順を保ちます。
        authValid = authValid && authTid.Length == 36 && Guid.TryParse(authTid, out authT)
          && authT != Guid.Empty && authTid == authT.ToString("D");
        // 社員のObject IDも、同じ形式であることを確認します。
        authValid = authValid && authOid.Length == 36 && Guid.TryParse(authOid, out authO)
          && authO != Guid.Empty && authOid == authO.ToString("D");
        // 設定tenantはGUIDとして比較します。文字列の表記差だけでは不一致にしません。
        authValid = authValid && Guid.TryParse(authCfg, out authCfgT) && authCfgT == authT;
        // clientIdは空文字を許容し、値があれば同じGUID形式を要求します。クライアント許可リストとの照合はしません。
        authValid = authValid && authAzp != null && (authAzp == "" || authAzp.Length == 36
          && Guid.TryParse(authAzp, out authA) && authA != Guid.Empty && authAzp == authA.ToString("D"));

        // 5. 確認したtenant IDと社員IDから、この社員を識別する値を組み立てます。
        var authCaller = "user:" + authT.ToString("D") + ":" + authO.ToString("D");
        // callerId（呼出者）が、この社員と完全一致することを確認します。
        authValid = authValid && authUser["callerId"]?.Type == JTokenType.String
          && (string)authUser["callerId"] == authCaller;
        // allocationId（利用枠の帰属先）も、現在の社員単位の契約では同じ社員である必要があります。
        authValid = authValid && authUser["allocationId"]?.Type == JTokenType.String
          && (string)authUser["allocationId"] == authCaller;
        // すべて一致した場合だけ、監査で使う社員・tenant・clientの識別情報を採用します。
        if (authValid) {
          authenticatedSubject = new JObject(new JProperty("status", "verified"),
            new JProperty("kind", "entraUser"), new JProperty("method", "user"), new JProperty("callerId", authCaller),
            new JProperty("allocationId", authCaller), new JProperty("tenantId", authTid),
            new JProperty("principalId", authOid), new JProperty("clientId", authAzp));
        }
      }
    }
  }
// 6. 型不一致・JSON解析などの例外では、JSONを作り直してinconsistentだけを残します。
// 部分的なID、生JSON、例外本文、原因コードは出力せず、認証完了や利用枠を変更しないままAPI処理へ戻ります。
} catch { authenticatedSubject = new JObject(new JProperty("status", "inconsistent")); }

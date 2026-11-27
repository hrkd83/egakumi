// 同名XMLの監査JSON生成全体と、確認済み主体の投影処理を一つの式として管理します。
      // 説明：最初に、呼出元が指定した処理段階を取得します。未指定なら不明にします。
      var phase = context.Variables.ContainsKey("gatewayAuditPhase") ? (string)context.Variables["gatewayAuditPhase"] : "unknown";
      // 説明：決めた4種類以外の処理段階は、unknown（不明）に置き換えます。
      if (!(new string[] { "accepted", "rejected", "backend-response", "policy-error" }).Contains(phase)) { phase = "unknown"; }
      var code = context.Variables.ContainsKey("gatewayAuditCode") ? (string)context.Variables["gatewayAuditCode"] : "UNSPECIFIED";
      // 説明：理由コードは大文字で始まる64文字以内に限り、不正なら未指定にします。
      if (!System.Text.RegularExpressions.Regex.IsMatch(code ?? "", "^[A-Z][A-Z0-9_]{0,63}$")) { code = "UNSPECIFIED"; }
      // 説明：呼出元の状態コードを優先し、未指定なら応答の状態を使います。
      var status = context.Variables.ContainsKey("gatewayAuditStatus") ? (int)context.Variables["gatewayAuditStatus"] : (context.Response == null ? 0 : context.Response.StatusCode);
      /* 共通入口が用意する識別情報。継承前の操作guardや早期エラーではAPIM contextを使います。 */
      // 説明：要求・API・操作のIDから、最低限の追跡情報を先に作ります。
      var requestContext = new JObject(
        new JProperty("requestId", context.RequestId.ToString()),
        new JProperty("apiId", context.Api == null ? null : context.Api.Id),
        new JProperty("operationId", context.Operation == null ? null : context.Operation.Id));
      // 説明：STEP1の要求情報があれば追加します。読取失敗時は最低限の情報を残します。
      if (context.Variables.ContainsKey("gatewayRequestContext")) {
        try {
          var snapshot = (string)context.Variables["gatewayRequestContext"];
          // 説明：4096文字以内の要求情報だけを、JSONの項目として読み取ります。
          if (snapshot.Length <= 4096) {
            var stored = JObject.Parse(snapshot);
            // 説明：foreachで決めた6項目を順に取り出し、余分な項目は写しません。
            foreach (var field in new [] { "requestReceivedAt", "httpMethod", "operationTemplate", "apimPeerIp", "reportedUserAgent", "appGatewayTraceId" }) {
              var value = stored[field];
              // 説明：受信時刻は、日時を表す共通の文字列形式へ戻して記録します。
              if (field == "requestReceivedAt" && value?.Type == JTokenType.Date) { requestContext[field] = ((DateTime)value).ToString("O"); }
              // 説明：User-Agentが日時として解釈された場合は、元の文字列を読み直します。
              else if (field == "reportedUserAgent" && value?.Type == JTokenType.Date) {
                var text = Newtonsoft.Json.JsonConvert.DeserializeObject<System.Collections.Generic.Dictionary<string,string>>(snapshot)[field];
                requestContext[field] = text != null && text.Length <= 512 ? text : null;
              }
              // 説明：その他は512文字以内の文字列だけ採用し、条件外はnull（値なし）にします。
              else { requestContext[field] = value?.Type == JTokenType.String && ((string)value).Length <= 512 ? value : null; }
            }
          }
        } catch { }
      }
      // 説明：同名C#の部品で、共通認証が保存した主体情報を読み取り、整合性を確認します。
      // 目的：共通認証が作った本人・購読の識別情報を、監査ログへ渡す前に形式と項目の整合性を再確認します。
// ここで新たにキーやトークンを検証するのではなく、gatewayAuthenticatedSubjectへ保存された共通認証の結果だけを扱います。
/* 共通主体の投影。 */
// authenticatedSubjectは監査へ出力するJSONです。情報が取得できない場合のstatusをunverified（未確認）にします。
var authenticatedSubject=new JObject { ["status"]="unverified" };
// APIM変数には異なる型の値が入り得るため、取得した値をまずobject型のobsVで受け取ります。
object obsV;
// TryGetValueは変数があればtrueを返し、out obsVへ値を渡します。変数がなければ初期状態unverifiedのままです。
if(context.Variables.TryGetValue("gatewayAuthenticatedSubject",out obsV)) {
  // 変数があるだけでは確認済みにしません。内容の確認に通らない場合に残るstatusをinconsistent（不整合）にします。
  authenticatedSubject["status"]="inconsistent";
  // 不正なJSONや型変換による例外が、監査のために通常のAPI処理を中断しないようtry/catchで囲みます。
  try {
    // as stringは値が文字列ならその文字列、別の型ならnullを返します。型が違うだけでは例外にしません。
    var obsR=obsV as string;
    // 文字列であり1600文字以下の場合だけJSONとして解析します。nullや長すぎる値は不整合のままです。
    if(obsR!=null&&obsR.Length<=1600) {
      // JObject.ParseでJSON文字列をオブジェクトに戻し、status項目を文字列として取り出します。
      var obs=JObject.Parse(obsR); var obsS=(string)obs["status"];
      // 未確認・不整合という既知の状態はstatusだけを引き継ぎます。付随する任意の項目は監査へ転記しません。
      if(obsS=="unverified"||obsS=="inconsistent") { authenticatedSubject["status"]=obsS; }
      // verified（確認済み）と書かれていても、そのまま採用せず、以下で項目と識別子の対応を確認します。
      else if(obsS=="verified") {
        // kindがentraUserなら社員本人の経路です。その他の値は、後の条件でAPIM購読の経路かどうかを確認します。
        var obsU=(string)obs["kind"]=="entraUser";
        // 共通4項目に、本人なら利用枠・テナント・本人・クライアントのID、購読なら購読IDを加えた必須項目名の配列を作ります。
        // 「条件 ? 本人用の文字列 : 購読用の文字列」で項目を選び、Split(',')でカンマ区切りを配列へ変換します。
        var obsN=("status,kind,method,callerId,"+(obsU?"allocationId,tenantId,principalId,clientId":"subscriptionId")).Split(',');
        // 本人の経路はmethod=user、購読の経路はkind=apimSubscriptionかつmethod=subscriptionKeyである必要があります。
        // 本人の識別と購読の識別を混在させないよう、三項演算子で経路ごとの組み合わせを選んで確認します。
        if((obsU?(string)obs["method"]=="user":(string)obs["kind"]=="apimSubscription"&&(string)obs["method"]=="subscriptionKey")
          // Properties().Count()で余分な項目がないこと、Allで全必須項目の存在・文字列型・項目ごとの長さを確認します。
          // 購読IDは256文字、購読のcallerIdは接頭辞subscription:の13文字を足して269文字まで扱います。その他の上限は253文字のままです。
          // obs[n]?.Typeの?.は項目がないときにnullを返すため、その項目を「文字列型ではない」として拒否できます。
          &&obs.Properties().Count()==obsN.Length
          &&obsN.All(n=>obs[n]?.Type==JTokenType.String
            &&((string)obs[n]).Length<=(n=="subscriptionId"?256:!obsU&&n=="callerId"?269:253))) {
          // callerIdの期待値を作り直します。本人はテナントID＋本人ID、購読は購読IDを使い、同じ識別子として混同しません。
          var obsC=obsU?"user:"+(string)obs["tenantId"]+":"+(string)obs["principalId"]:"subscription:"+(string)obs["subscriptionId"];
          // callerIdが期待値と一致し、本人の経路ではallocationIdも同じ利用者を指す場合だけ、検査したJSON全体を採用します。
          // !obsU || ...は「購読なら利用枠IDの比較は不要、本人なら比較が必要」という条件です。
          if((string)obs["callerId"]==obsC&&(!obsU||(string)obs["allocationId"]==obsC)) { authenticatedSubject=obs; }
        }
      }
    }
  // 解析や変換に失敗しても例外の詳細はログへ転記せず、status=inconsistentを保って後の監査処理へ進みます。
  } catch { }
}
// この後の監査処理はauthenticatedSubjectを出力します。本人確認結果を新たに変更する処理ではありません。

      // 説明：要求情報・処理結果・認証主体を、出力する１件のJSONへまとめます。
      var record = new JObject(
        // 説明：２つの版番号で既存形式と追加した監査項目を区別し、イベント名も付けます。
        new JProperty("schemaVersion", 1),
        new JProperty("auditSchemaVersion", 2),
        new JProperty("event", "gateway-request"),
        new JProperty("requestId", requestContext["requestId"]),
        new JProperty("apiId", requestContext["apiId"]),
        new JProperty("operationId", requestContext["operationId"]),
        new JProperty("phase", phase),
        new JProperty("code", code),
        new JProperty("status", status),
        // 説明：APIの利用許可は未確認から始めます。本人の認証結果とは別の項目です。
        new JProperty("authorization", "unconfirmed"),
        new JProperty("requestContext", requestContext),
        new JProperty("authenticatedSubject", authenticatedSubject));
      // 説明：API側の利用許可結果がある場合だけ、許可・拒否と利用者を関連付けます。
      if (context.Variables.ContainsKey("gatewayDecision")) {
        try {
          var decision = JObject.Parse((string)context.Variables["gatewayDecision"]);
          // 説明：allowedが真偽値のtrueなら許可、それ以外は拒否としてログへ記録します。
          var allowed = decision["allowed"]?.Type == JTokenType.Boolean && (bool)decision["allowed"];
          // 説明：ここで記録するのはAPIの利用許可です。後続の照合に失敗すれば未確認へ戻します。
          record["authorization"] = allowed ? "allowed" : "denied";
          /* 呼出主体はAPIMが検証した購読IDから組み立てます。API固有の本人トークンも購読へ合算します。 */
          // 説明：キー利用では、APIMが検証した購読IDを使います。キーの値は記録しません。
          var subscription = context.Subscription == null ? "" : context.Subscription.Id;
          // 説明：キーによる許可結果と、APIMの購読IDから作る呼出主体が一致するか確認します。
          if (allowed && (string)decision["method"] == "subscriptionKey"
              && !String.IsNullOrWhiteSpace(subscription) && subscription.Length <= 256
              && (string)decision["callerId"] == "subscription:" + subscription) {
            // 説明：一致した購読ID・呼出主体・認証方式を、監査の項目へ写します。
            record["subscriptionId"] = subscription;
            record["callerId"] = "subscription:" + subscription;
            record["method"] = "subscriptionKey";
            // 説明：選択した操作・モデル設定の監査用ラベルprofileIdは、256文字以内の文字列だけ記録します。
            if (decision["profileId"]?.Type == JTokenType.String && ((string)decision["profileId"]).Length <= 256) {
              record["profileId"] = (string)decision["profileId"];
            }
          } /* core-user-audit-start */ else if (allowed && (string)decision["method"] == "user"
              && context.Variables.ContainsKey("gatewayEntraAuthenticationValidated") && (bool)context.Variables["gatewayEntraAuthenticationValidated"]) {
            // 説明：Bearer利用では、共通認証が確認した社員情報を読みます。トークンは読みません。
            var user = JObject.Parse((string)context.Variables["gatewayUserAuthentication"]);
            Guid tenant = Guid.Empty, principal = Guid.Empty;
            // 説明：テナントと社員のIDが空ではなく、GUID形式であることを再確認します。
            var valid = Guid.TryParse((string)user["tenantId"], out tenant) && tenant != Guid.Empty
              && Guid.TryParse((string)user["principalId"], out principal) && principal != Guid.Empty;
            // 説明：テナントIDと社員IDから、同じ社員を指す呼出主体の文字列を作ります。
            var caller = valid ? "user:" + tenant.ToString("D") + ":" + principal.ToString("D") : "";
            // 説明：認証結果と利用許可結果の主体・利用枠・各IDが一致しなければ、未確認に戻します。
            if (!valid || (string)decision["callerId"] != caller || (string)user["callerId"] != caller
                || (string)decision["allocationId"] != caller || (string)user["allocationId"] != caller
                || (string)decision["verifiedTenantId"] != tenant.ToString("D")
                || (string)decision["verifiedPrincipalId"] != principal.ToString("D")
                || (string)decision["verifiedClientId"] != ((string)user["clientId"] ?? "")) { record["authorization"] = "unconfirmed"; }
            else {
              // 説明：照合できた社員の呼出主体と利用枠を記録し、認証方式をuserとします。
              record["callerId"] = caller; record["allocationId"] = caller; record["method"] = "user";
              record["verifiedTenantId"] = tenant.ToString("D"); record["verifiedPrincipalId"] = principal.ToString("D");
              // 説明：検証済みのクライアントIDを写します。User-Agentから推測しません。
              record["verifiedClientId"] = ((string)user["clientId"] ?? "");
              if (decision["profileId"]?.Type == JTokenType.String && ((string)decision["profileId"]).Length <= 256) { record["profileId"] = (string)decision["profileId"]; }
            }
          // 説明：許可でも認証方式が想定外の場合は、利用許可を未確認として残します。
          } /* core-user-audit-end */ else if (allowed) { record["authorization"] = "unconfirmed"; }
        } catch { record["authorization"] = "unconfirmed"; }
      }
      /* 認可済み設定/別fragmentの検査結果だけを使います。traceでは本文やheaderを読みません。 */
      // 説明：要求IDを、下流ログとの照合情報として記録します。
      record["backendRequestId"] = requestContext["requestId"];
      // 説明：使用量の情報は「なし」から始めます。成功しただけで消費量を推測しません。
      record["usageStatus"] = "none";
      record["usageSource"] = "none";
      // 説明：APIが用意した監査設定から、接続先と使用量の照合方法を追加します。
      if (context.Variables.ContainsKey("gatewayAuditSettings")) {
        try {
          var settings = JObject.Parse((string)context.Variables["gatewayAuditSettings"]);
          var serviceClass = (string)settings["serviceClass"];
          // 説明：サービスの分類は、platformとsampleという既知の値だけ記録します。
          if (serviceClass == "platform" || serviceClass == "sample") { record["serviceClass"] = serviceClass; }
          // 説明：設定された接続先サービス・下流の認証方式・計測上の役割を写します。
          record["backendService"] = (string)settings["backendService"];
          record["backendAuthentication"] = context.Variables.ContainsKey("gatewayGraphObo") && (bool)context.Variables["gatewayGraphObo"]
            ? "entraObo" : (string)settings["backendAuthentication"];
          record["measurementRole"] = (string)settings["measurementRole"];
          var target = (string)settings["targetAgent"];
          // 説明：対象エージェント名は、決めた文字で100文字以内の場合だけ記録します。
          if (!String.IsNullOrEmpty(target) && System.Text.RegularExpressions.Regex.IsMatch(target, "^[A-Za-z0-9_-]{1,100}$")) {
            record["targetAgent"] = target;
          }
          // 説明：別の検査が用意したモデル名を使います。ここで要求本文は読みません。
          var model = context.Variables.ContainsKey("gatewayAuditModel") ? (string)context.Variables["gatewayAuditModel"] : "";
          if (!String.IsNullOrEmpty(model)) {
            record["targetModel"] = model;
            // 説明：モデルの別名は設定との一致を確認します。拒否した任意の入力は記録しません。
            /* Only a configured alias in the selected operation settings is recorded.
               Never log an arbitrary rejected request model or request body. */
            if (context.Variables.ContainsKey("gatewayOperationSelection")) {
              var selected = JObject.Parse((string)context.Variables["gatewayOperationSelection"]);
              var requested = (string)selected["requestedModel"];
              var aliases = selected["constraint"]?["modelAliases"] as JObject;
              if ((bool?)selected["allowed"] == true && (string)selected["effectiveModel"] == model
                  && requested != model && System.Text.RegularExpressions.Regex.IsMatch(requested ?? "", "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
                  && (string)aliases?[requested] == model) {
                record["requestedModel"] = requested;
                record["modelAliasApplied"] = true;
              }
            }
            var controls = JObject.Parse((string)context.Variables["gatewayControlSettings"]);
            var operation = String.IsNullOrEmpty((string)controls["operationId"]) ? context.Operation.Id : (string)controls["operationId"];
            // 説明：LLM操作なら、使用量はAPIM標準のLLMログとの照合で確認すると記録します。
            if (((JArray)settings["llmOperationIds"]).Any(o => (string)o == operation)) {
              record["usageStatus"] = "native-log-join-required";
              record["usageSource"] = "ApiManagementGatewayLlmLog";
            }
          }
        } catch { }
      }
      // 説明：下流応答のIDがあれば追加し、要求とモデル側の応答を追跡できるようにします。
      if (context.Variables.ContainsKey("gatewayBackendResponseIds")) {
        try { record["backendResponseIds"] = JObject.Parse((string)context.Variables["gatewayBackendResponseIds"]); } catch { }
      }
      // 説明：親要求との関連は検査結果から記録します。candidateは照合候補であり確定ではありません。
      if (context.Variables.ContainsKey("gatewayAgentTrace")) {
        try {
          var link = JObject.Parse((string)context.Variables["gatewayAgentTrace"]);
          record["parentLinkStatus"] = (string)link["status"];
          if ((string)link["status"] == "candidate") {
            record["claimedParentId"] = (string)link["claimedParentId"];
            record["parentClaimActorId"] = (string)link["actorId"];
          }
        } catch { }
      }
      // 接続先が明示した絶対期限だけを記録します。未提供なら項目を省略し、更新成功と推測しません。
      // 説明：接続の期限はlong型と日時の範囲を確認し、条件を満たす値だけ追加します。
      object connectionExpiry;
      if (context.Variables.TryGetValue("gatewayConnectionExpiresOn", out connectionExpiry) && connectionExpiry is long) {
        var expiry = (long)connectionExpiry;
        if (expiry >= 946684800L && expiry <= 4102444800L) { record["connectionExpiresOn"] = expiry; }
      }
      // 説明：最後に改行なしのJSON文字列を返し、traceから既存の監査基盤へ渡します。
      return record.ToString(Newtonsoft.Json.Formatting.None);

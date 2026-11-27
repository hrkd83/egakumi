// 目的：API固有の監査設定を取り出し、通常HTTPの操作別接続情報を監査に反映します。
// このファイルはcore-api-settings.xmlのgatewayAuditSettings式へ展開されます。

// 復元した3行を分けます。順番はAPI種類、監査JSON、制御JSONで、配備前に検査します。
// 各JSONは生成時に1行へ整形されるため、JSON内の文字列の改行と区切りは混ざりません。
var settings = ((string)context.Variables["gatewayApiSettings"]).Split('\n');
// 監査JSON文字列を取り出します。入力制約・計数設定はXML側で別の変数へ格納します。
var auditSource = settings[1];

// 通常HTTPの入口では、要求で選ばれた操作に専用backendがあるか確認します。
// WebSocketとMCPでは元のJSON文字列をそのまま返し、再整形による値の変化を避けます。
if (settings[0] != "http") {
    return auditSource;
}

// HTTPだけ従来と同じ方法で監査JSONを読み、選ばれた操作の専用backendを調べます。
var audit = JObject.Parse(auditSource);
var backend = audit["operationBackends"]?[context.Operation.Id];
// 専用backendがある場合だけ、監査に記録する接続先サービスと認証方式を選びます。
// この代入で通信先や認証処理を変更することはありません。
if (backend != null) {
    audit["backendService"] = backend["backendService"];
    audit["backendAuthentication"] = backend["backendAuthentication"];
}

// 後続の共通監査・API処理が読めるよう、監査情報をJSON文字列で返します。
return audit.ToString(Newtonsoft.Json.Formatting.None);

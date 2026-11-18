// 役割：この条件式はAppGWのHTTP入口ヘッダーを判定し、拒否が必要な場合にtrueを返します。
// 展開：配備時に、同名XMLのwhen要素にあるconditionへこの条件式を組み込みます。
// 監査の再利用：core-gateway-telemetry.xmlと同名C#の監査コードは、生成処理が同名XMLの拒否監査位置へ展開します。
var grpc = context.Variables.ContainsKey("coreNativeGrpc") && (bool)context.Variables["coreNativeGrpc"];
return __L7_INGRESS_REQUIRED__ && !grpc && (context.Request.Headers.GetValueOrDefault("X-Core-L7-Ingress", "") != "__L7_INGRESS_EXPECTED__" || context.Request.Headers["X-Core-L7-Ingress"].Length != 1);

// API 定義を APIM のバックエンド・API・Product・操作・ポリシーへ変換します。
// Model / Tool / Agentの分類を維持し、購読キーの認可と各Operationの入力制約を登録します。
// 基盤構築とは別に明示実行します。指定 API だけを反映し、このモジュールのAPI登録成功を確認した後、公開スクリプトが共通のキー認可を統合します。

targetScope = 'resourceGroup'

@description('親モジュールで確定したサービス名の組。別モジュールでも同じ資源を参照するために使います。')
param names object
@description('検証済みの公開 API 契約一覧。パス・認証・接続先・操作制約をまとめて渡します。')
param apiDefinitions array = []
@description('個人接続を使うAPI向けのCredential Manager補助設定。購読キーの認可後に利用します。')
param credentialLogin object = {}
@description('基盤と各APIで照合済みの購読キーヘッダー名。診断の秘密マスクにも使います。')
param subscriptionKeyHeader string = 'api-key'
@description('APIMが検証した購読IDごとの共通要求回数制限。')
param rateLimits object = { user: { enabled: false, calls: 60, renewalPeriodSeconds: 60 }, subscriptionKey: { enabled: false, calls: 60, renewalPeriodSeconds: 60, overrides: [] }, application: { enabled: false, calls: 60, renewalPeriodSeconds: 60, overrides: [] } }
@description('この明示的な API 登録処理の指定です。APIM 全体の公開・停止スイッチではありません。')
param publishApis bool = false
@description('実行前検査で基盤の準備済み Key Vault 参照を照合する URI。登録処理は共通 Named Value を更新しません。')
#disable-next-line secure-secrets-in-params no-unused-params // The API-only plan verifies foundation-owned references before execution; Bicep never updates them.
param l7IngressSecretIdentifier string = ''
@description('実行前検査で基盤の準備済み署名鍵参照を照合する URI。登録処理は共通 Named Value を更新しません。')
#disable-next-line secure-secrets-in-params no-unused-params // Verification metadata only; the signing key remains in Key Vault.
param realtimeTicketSecretIdentifier string = ''

// APIキー・本人接続の秘密・一時URLのqueryを診断で隠します。
// 参照: https://learn.microsoft.com/azure/templates/microsoft.apimanagement/service/apis/diagnostics
// ヘッダーはMaskで値を伏せ、queryはHideで項目を隠します。APIMはヘッダーのHideを受け付けません。
var diagnosticRequest = {
  headers: ['x-ms-client-request-id', 'x-appgw-trace-id']
  body: { bytes: 0 }
  dataMasking: {
    headers: map([subscriptionKeyHeader, 'Authorization', 'Cookie', 'X-Core-Broker-Key', 'X-Core-Connection-Session', 'X-Core-Connection-Poll', 'X-Core-Connection-Update', 'X-Core-Connection-Update-Id'], name => { value: name, mode: 'Mask' })
    queryParams: map(['launch', 'state', 'code', 'session_state', 'error_description', 'transactionId', 'api-key', 'subscription-key'], name => { value: name, mode: 'Hide' })
  }
}
// 更新候補を応答ヘッダーで渡しますが、診断では取得せず将来の設定変更にも備えてマスクします。
var diagnosticResponse = {
  headers: []
  body: { bytes: 0 }
  dataMasking: {
    headers: map(['X-Core-Connection-Update', 'X-Core-Connection-Update-Id'], name => { value: name, mode: 'Mask' })
  }
}

// XML 属性・本文へ入れる公開文字列を escape します。URL やヘッダー値を XML の別要素として解釈させません。
func xmlText(value string) string => replace(replace(replace(replace(value, '&', '&amp;'), '<', '&lt;'), '>', '&gt;'), '"', '&quot;')

// 接続先・認証・追加headerは同じ登録済みbackendから一体で選びます。
func backendAuthenticationPolicy(backend object, secretName string) string => backend.backendAuthentication.mode == 'none' ? ''
  : backend.backendAuthentication.mode == 'keyVaultHeader'
    ? '<set-header name="${backend.backendAuthentication.headerName}" exists-action="override"><value>${xmlText(backend.backendAuthentication.headerValuePrefix)}{{${secretName}}}</value></set-header>'
  : backend.backendAuthentication.mode == 'speechEntra'
    ? '<authentication-managed-identity resource="${xmlText(backend.backendAudience)}" output-token-variable-name="speechBackendToken" /><set-header name="Authorization" exists-action="override"><value>@("Bearer aad#${xmlText(backend.backendAuthentication.resourceId)}#" + (string)context.Variables["speechBackendToken"])</value></set-header>'
  : '<authentication-managed-identity resource="${xmlText(backend.backendAudience)}" />'
func backendHeaderPolicy(backend object) string => join(map(items(backend.backendHeaders), header => '<set-header name="${header.key}" exists-action="override"><value>${xmlText(header.value)}</value></set-header>'), '')
func operationBackendId(apiName string, operationName string) string => 'op-${uniqueString(apiName, operationName)}-backend'
func operationBackendPolicy(apiName string, operation object) string => '<set-backend-service backend-id="${operationBackendId(apiName, operation.name)}" />${backendAuthenticationPolicy(operation.backendOverride, '${operationBackendId(apiName, operation.name)}-secret')}${backendHeaderPolicy(operation.backendOverride)}'
func websocketFixedParameters(api object) object => api.websocket.?queryParameters ?? {}
// 入力profileの検査で選んだIDから、登録済みの固定URIへ転送します。
// FLUXのflex/proのように、公開Operationを共有するモデルの上流を選択します。
func profileRoutingPolicy(constraint object) string => !contains(constraint, 'profiles') ? '' : empty(filter(constraint.profiles, profile => contains(profile, 'backendUrlTemplate'))) ? ''
  : '<!-- 検証済みprofileと同じ固定表から、モデルの上流パスを選びます。 --><choose>${join(map(constraint.profiles, profile => '<when condition="@((string)JObject.Parse((string)context.Variables[&quot;gatewayProfileSelection&quot;])[&quot;profileId&quot;] == &quot;${xmlText(profile.profileId)}&quot;)"><rewrite-uri template="${xmlText(profile.backendUrlTemplate)}" copy-unmatched-params="false" /></when>'), '')}<otherwise><return-response><set-status code="500" reason="Profile route is not configured" /><set-header name="apim-request-id" exists-action="override"><value>@(context.RequestId.ToString())</value></set-header></return-response></otherwise></choose>'
func operationBackendSelection(api object) string => empty(filter(api.operations, operation => contains(operation, 'backendOverride'))) ? ''
  : '<choose>${join(map(filter(api.operations, operation => contains(operation, 'backendOverride')), operation => '<when condition="@(context.Operation.Id == &quot;${xmlText(operation.name)}&quot;)">${operationBackendPolicy(api.name, operation)}</when>'), '')}<otherwise><set-backend-service backend-id="${api.name}-backend" />${backendAuthenticationPolicy(api, '${api.name}-backend-secret')}${backendHeaderPolicy(api)}</otherwise></choose>'
// Only the canonical raw Speech synthesis contract accepts SSML. Its literal
// DTD/entity guard must precede inherited authentication and backend execution.
func requiresSpeechSsmlGuard(entry object) bool => entry.apiName == 'azure-speech-v1' && entry.operation.name == 'tts' && entry.operation.method == 'POST' && entry.operation.urlTemplate == '/synthesize' && entry.constraint.kind == 'registeredOperation' && (entry.constraint.?bodyFormat ?? '') == 'text' && (entry.constraint.?contentTypes ?? []) == ['application/ssml+xml']
var speechSsmlGuardPolicy = replace(replace(loadTextContent('policies/speech-ssml-guard.xml'), '<fragment>', ''), '</fragment>', '')

// Agentの外側endpointと内側MI登録を同じ公開設定から結びます。
// 任意のcaller headerを本人/契約へ変換せず、監査の追跡候補だけに使います。
func hostedAgentName(api object) string => length(filter(items(api.authorization.operationConstraints), entry => entry.value.kind == 'hostedAgentPost')) > 0 && contains(api.backendUrl, '/agents/')
  ? first(split(last(split(api.backendUrl, '/agents/')), '/')) : ''
// 初期の数値計測対象は公式手順が明示するOpenAI形式Chat CompletionsのHTTP操作に限定します。
// Anthropicや音声/PII等へ対応を推測して拡大しません。LLMの本文保存は指定しません。
func nativeLlmOperations(api object) array => api.apiType == 'http' && api.backendService == 'foundryOpenAi'
  ? map(filter(api.operations, operation => endsWith(operation.urlTemplate, '/chat/completions') && api.authorization.operationConstraints[operation.name].kind == 'bodyModel'), operation => operation.name) : []
// native token policyが対応するOpenAI形式の操作です。音声・画像・埋め込みは推測で計数しません。
func tokenLimitOperations(api object) array => api.apiType == 'http' && api.backendService == 'foundryOpenAi'
  ? map(filter(api.operations, operation => (endsWith(operation.urlTemplate, '/chat/completions') && api.authorization.operationConstraints[operation.name].kind == 'bodyModel') || api.authorization.operationConstraints[operation.name].kind == 'statelessResponses'), operation => operation.name) : []
func auditSettings(api object, operation string, generatedMcp bool) object => {
  apiId: api.name
  serviceClass: api.?serviceClass ?? 'platform'
  backendService: api.backendService
  // HTTP本人接続はgateway.xmlのchooseで基底のMI等を使わず、CM接続のtokenを送ります。
  // disabledの接続も拒否されるだけでMIへ戻らないため、設定された最終方式を記録します。
  // 生成MCPの外側はREST側の本人接続処理を直接実行しないので、この表示を流用しません。
  backendAuthentication: !generatedMcp && contains(api, 'credentialConnection') ? 'credentialConnection' : api.backendAuthentication.mode
  operationId: operation
  operationConstraints: api.authorization.operationConstraints
  operationBackends: toObject(filter(api.operations, operation => contains(operation, 'backendOverride')), operation => operation.name, operation => {
    backendService: operation.backendOverride.backendService
    backendAuthentication: operation.backendOverride.backendAuthentication.mode
  })
  llmOperationIds: generatedMcp ? [] : nativeLlmOperations(api)
  tokenLimitOperationIds: generatedMcp ? [] : tokenLimitOperations(api)
  websocketModel: contains(api, 'websocket') ? contains(api.websocket, 'queryParameters') ? api.websocket.queryParameters.?model ?? '' : '' : ''
  targetAgent: generatedMcp ? '' : hostedAgentName(api)
  measurementRole: generatedMcp ? 'facade' : contains(api, 'websocket') ? contains(api.websocket, 'realtimeObserver') ? 'observation' : 'execution' : 'execution'
}
// 公開設定を書き込む既存 APIM を参照します。
resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = { name: names.apim }

// 個人接続用の入口を登録します。購読キーは呼出元サーバー、OAuth tokenはCMが保持します。
var credentialLoginEnabled = publishApis && !empty(credentialLogin)
var credentialSettings = union(credentialLogin, { apimPrincipalId: apim.identity.principalId })
// 業務APIの入口と下流応答で、同じ認証URL案内を使います。
var credentialChallengeXml = replace(replace(loadTextContent('policies/credential-challenge.xml'), '<fragment>', ''), '</fragment>', '')
func renderCredentialLogin(template string, settings object) string => reduce(items({
  __CREDENTIAL_LOGIN_SETTINGS__: base64(string(settings))
  __CREDENTIAL_LOGIN_ORIGIN_BASE64__: base64(settings.?publicOrigin ?? '')
  __CREDENTIAL_IDENTITY_TENANTS__: base64(string(map(settings.?providers ?? [], provider => provider.oauthTenantId)))
  __CREDENTIAL_LOGIN_ORIGIN__: settings.?publicOrigin ?? ''
  __CREDENTIAL_TRANSACTION_TTL__: string(settings.?transactionLifetimeSeconds ?? 300)
  __CREDENTIAL_LOGIN_CALLS__: string(settings.?loginCallsPerMinute ?? 10)
  __CREDENTIAL_KEY_HEADER__: settings.?keyHeader ?? 'api-key'
}), template, (value, entry) => replace(value, entry.key, entry.value))
resource credentialLoginApi 'Microsoft.ApiManagement/service/apis@2024-05-01' = if (credentialLoginEnabled) {
  parent: apim
  name: 'credential-manager-login'
  properties: {
    displayName: 'Personal connection sign-in'
    path: 'auth/connections'
    protocols: ['http', 'https']
    // 開始・状態確認・終了は、APIMが有効な購読とProductの範囲を検証します。
    subscriptionRequired: true
    subscriptionKeyParameterNames: { header: credentialLogin.?keyHeader ?? 'api-key', query: 'subscription-key' }
    type: 'http'
  }
}
// ブラウザーの認証開始・戻り先は、取引stateとHttpOnly Cookieで照合します。
resource credentialBrowserApi 'Microsoft.ApiManagement/service/apis@2024-05-01' = if (credentialLoginEnabled) {
  parent: apim
  name: 'credential-manager-browser'
  properties: {
    displayName: 'Personal connection browser callback'
    path: 'auth/connections/browser'
    protocols: ['http', 'https']
    subscriptionRequired: false
    type: 'http'
  }
}
var credentialLoginOperations = [
  { apiId: 'credential-manager-login', name: 'start', path: 'start', method: 'POST', policy: loadTextContent('policies/credential-login/start.xml') }
  { apiId: 'credential-manager-login', name: 'status', path: 'status', method: 'GET', policy: loadTextContent('policies/credential-login/status.xml') }
  { apiId: 'credential-manager-browser', name: 'launch', path: 'launch', method: 'GET', policy: loadTextContent('policies/credential-login/launch.xml') }
  { apiId: 'credential-manager-browser', name: 'launch-post', path: 'launch', method: 'POST', policy: loadTextContent('policies/credential-login/launch-post.xml') }
  { apiId: 'credential-manager-browser', name: 'identity-callback', path: 'identity-callback', method: 'GET', policy: loadTextContent('policies/credential-login/identity-callback.xml') }
  { apiId: 'credential-manager-browser', name: 'callback', path: 'callback', method: 'GET', policy: loadTextContent('policies/credential-login/callback.xml') }
  { apiId: 'credential-manager-login', name: 'logout', path: 'logout', method: 'POST', policy: loadTextContent('policies/credential-login/logout.xml') }
  { apiId: 'credential-manager-login', name: 'session-commit', path: 'session/commit', method: 'POST', policy: loadTextContent('policies/credential-login/session-commit.xml') }
  { apiId: 'credential-manager-login', name: 'logout-all', path: 'logout-all', method: 'POST', policy: loadTextContent('policies/credential-login/logout-all.xml') }
  { apiId: 'credential-manager-login', name: 'disconnect', path: 'disconnect', method: 'POST', policy: loadTextContent('policies/credential-login/disconnect.xml') }
]
resource credentialLoginOperation 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = [for item in credentialLoginOperations: if (credentialLoginEnabled) {
  name: '${apim.name}/${item.apiId}/${item.name}'
  dependsOn: [credentialLoginApi, credentialBrowserApi]
  properties: { displayName: item.name, method: item.method, urlTemplate: '/${item.path}', responses: [] }
}]
resource credentialLoginOperationPolicy 'Microsoft.ApiManagement/service/apis/operations/policies@2024-05-01' = [for (item, index) in credentialLoginOperations: if (credentialLoginEnabled) {
  parent: credentialLoginOperation[index]
  name: 'policy'
  properties: { format: 'rawxml', value: renderCredentialLogin(item.policy, credentialSettings) }
}]
// 一時URL・cookie・tokenを診断ログへ記録せず、要求の追跡IDだけを残します。
resource credentialLoginDiagnostics 'Microsoft.ApiManagement/service/apis/diagnostics@2025-09-01-preview' = [for apiName in ['credential-manager-login', 'credential-manager-browser']: if (credentialLoginEnabled) {
  name: '${apim.name}/${apiName}/azuremonitor'
  dependsOn: [credentialLoginApi, credentialBrowserApi]
  properties: {
    alwaysLog: 'allErrors'
    loggerId: azureMonitorLogger.id
    logClientIp: false
    verbosity: 'information'
    sampling: { samplingType: 'fixed', percentage: 100 }
    frontend: { request: diagnosticRequest, response: diagnosticResponse }
    backend: { request: { headers: [], body: { bytes: 0 } }, response: { headers: [], body: { bytes: 0 } } }
  }
}]

// ポリシー本文は読みやすい XML ファイルで管理し、契約固有の箇所だけを Bicep が置換します。
// cache-lookup-valueはfragmentに置けないため、固定の照会本文をAPIポリシーへ展開します。
var subscriptionProfileXml = replace(replace(loadTextContent('policies/subscription-profile.xml'), '<fragment>', ''), '</fragment>', '')
var gatewayPolicyTemplate = replace(loadTextContent('policies/gateway.xml'), '__SUBSCRIPTION_PROFILE_XML__', subscriptionProfileXml)
var protocolPolicyTemplate = replace(loadTextContent('policies/protocol.xml'), '__SUBSCRIPTION_PROFILE_XML__', subscriptionProfileXml)
var piiPolicyTemplate = replace(loadTextContent('policies/pii.xml'), '__RATE_LIMIT_ERROR_XML__', rateLimitErrorXml)
var documentAnalysisPolicyTemplate = replace(loadTextContent('policies/document-analysis.xml'), '__RATE_LIMIT_ERROR_XML__', rateLimitErrorXml)
var documentAnalysisStatusPolicyTemplate = replace(loadTextContent('policies/document-analysis-status.xml'), '__RATE_LIMIT_ERROR_XML__', rateLimitErrorXml)
var realtimeCreatePolicyTemplate = loadTextContent('policies/realtime-create.xml')
var realtimeHangupPolicyTemplate = loadTextContent('policies/realtime-hangup.xml')
var liveCreatePolicyTemplate = loadTextContent('policies/live-create.xml')
var mcpPolicyTemplate = loadTextContent('policies/mcp.xml')
// APIM fragments cannot include another fragment. Reuse the reviewed trace
// body by inlining it at deployment time in Credential Manager failures.
var gatewayTelemetryBody = replace(replace(loadTextContent('policies/gateway-telemetry.xml'), '<fragment>', ''), '</fragment>', '')
// fragment入れ子を作らず、固定429処理を各on-errorへ同じ本文で埋め込みます。
var rateLimitErrorXml = replace(replace(replace(loadTextContent('policies/rate-limit-error.xml'), '<fragment>', ''), '</fragment>', ''), '__GATEWAY_TELEMETRY_XML__', gatewayTelemetryBody)
var mcpRateCleanupXml = replace(replace(loadTextContent('policies/mcp-rate-cleanup.xml'), '<fragment>', ''), '</fragment>', '')
// Backend authentication is declared independently of public caller auth.
// Key material remains in Key Vault-backed named values.
var backendAuthenticationXml = [for api in apiDefinitions: empty(operationBackendSelection(api))
  ? backendAuthenticationPolicy(api, '${api.name}-backend-secret') : operationBackendSelection(api)]
var backendHeadersXml = [for api in apiDefinitions: empty(operationBackendSelection(api)) ? backendHeaderPolicy(api) : '']
// API ごとの認可契約を Base64 JSON として埋め込みます。Base64 は暗号化ではなく、安全な構文境界のためです。秘密は含めません。
// XML全文を加工する途中の結果は配列変数へ保存せず、関数で最終結果までつなぎます。
// ARMは各変数の展開後のサイズも数えるため、API数分のXMLを段階ごとに保存すると、
// ソースが小さくても配備要求の上限を超えます。認可・転送・監査の内容は変えません。
func baseApiPolicy(api object, policyTemplate string, backendAuthentication string, backendHeaders string) string => replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(policyTemplate, '__GRPC_INGRESS_XML__', api.apiType == 'grpc' ? '<set-variable name="coreNativeGrpc" value="@(true)" />' : ''),
  '__AUTHORIZATION_BASE64__', base64(string(api.authorization))),
  '__WEBSOCKET_SETTINGS_BASE64__', api.apiType == 'websocket' ? base64(string(api.websocket)) : ''),
  '__KEY_HEADER__', api.subscriptionKeyHeader),
  '__BACKEND_ID__', '${api.name}-backend'),
  '__DEFAULT_BACKEND_XML__', empty(operationBackendSelection(api)) ? '<set-backend-service backend-id="${api.name}-backend" />' : ''),
  '__BACKEND_AUDIENCE__', '{{${api.name}-backend-audience}}'),
  '__CREDENTIAL_CONNECTION_BASE64__', contains(api, 'credentialConnection') ? base64(string(api.credentialConnection)) : ''),
  '__BACKEND_AUTHENTICATION_XML__', backendAuthentication),
  '__BACKEND_HEADERS_XML__', backendHeaders),
  '__HTTP_VERSION__', api.apiType == 'grpc' ? '2' : '1'),
  '__TOKEN_LIMIT_XML__', empty(tokenLimitOperations(api)) ? '' : '<include-fragment fragment-id="core-token-limits" />')

// WebSocketはAPIのwss接続先を維持し、HTTP用backend切替の１行だけを除きます。
// ここでもXMLの中間配列を作らず、一つのAPIの最終ポリシーを計算します。
func selectBackendPolicy(api object, policy string) string => api.apiType == 'websocket'
  ? replace(policy, '<set-backend-service backend-id="${api.name}-backend" />', '')
  : policy

var protocolQueriesXml = [for api in apiDefinitions: api.apiType == 'websocket' && (contains(api.websocket, 'queryParameters') || contains(api.websocket, 'allowedModels'))
  ? '${join(map(filter(items(websocketFixedParameters(api)), item => item.key != 'model' || !contains(api.websocket, 'allowedModels')), item => '<set-query-parameter name="${item.key}" exists-action="override"><value>${xmlText(item.value)}</value></set-query-parameter>'), '')}${contains(api.websocket, 'allowedModels') ? '<set-query-parameter name="model" exists-action="override"><value>@((string)context.Variables["gatewayProtocolModel"])</value></set-query-parameter>' : ''}' : '']
var observerApis = filter(apiDefinitions, api => api.apiType == 'websocket' && contains(api.websocket, 'realtimeObserver'))
var observerPoliciesXml = [for api in apiDefinitions: api.apiType == 'websocket' && contains(api.websocket, 'realtimeObserver')
  ? '<set-variable name="gatewayOperationConstraint" value="@(System.Text.Encoding.UTF8.GetString(Convert.FromBase64String(&quot;${base64(string(api.websocket.realtimeObserver))}&quot;)))" /><include-fragment fragment-id="core-realtime-observer" />' : '']
// MCPの接続パスと外部サーバーのパスを分けます。公開の接続先は /mcp または /sse です。
// 従来SSEのPOST先は、サーバーが返す相対パスにAPIMが公開prefixを付けるので、
// 登録済みmessage.uriTemplateと同じsuffixを公開します。サーバーへGateway専用設定を要求しません。
// 外部サーバーへ転送するときは、その登録済みの相対パスだけを使います。
// backendだけを選び直すと公開側の/mcpが残り、/mcp-sse等の別接続先を使えないため、転送URIも明示します。
// queryは従来SSEのsession_id等に必要です。URI本体は事前検証した設定だけから作り、呼出元の値で変更しません。
var mcpProxyRoutingXml = [for api in apiDefinitions: api.apiType != 'mcp' ? ''
  : api.mcpProxy.transport == 'streamable'
    ? '<rewrite-uri template="${xmlText(first(filter(api.mcpProxy.endpoints, endpoint => endpoint.name == 'message')).uriTemplate)}" copy-unmatched-params="true" />'
    : '<choose><when condition="@(context.Request.OriginalUrl.Path == &quot;/${xmlText(api.path)}/sse&quot; &amp;&amp; context.Request.Method == &quot;GET&quot;)"><rewrite-uri template="${xmlText(first(filter(api.mcpProxy.endpoints, endpoint => endpoint.name == 'sse')).uriTemplate)}" copy-unmatched-params="true" /></when><when condition="@(context.Request.OriginalUrl.Path == &quot;/${xmlText(api.path)}${xmlText(first(filter(api.mcpProxy.endpoints, endpoint => endpoint.name == 'message')).uriTemplate)}&quot; &amp;&amp; context.Request.Method == &quot;POST&quot;)"><rewrite-uri template="${xmlText(first(filter(api.mcpProxy.endpoints, endpoint => endpoint.name == 'message')).uriTemplate)}" copy-unmatched-params="true" /></when><otherwise><return-response><set-status code="404" reason="MCP endpoint is not registered" /><set-header name="apim-request-id" exists-action="override"><value>@(context.RequestId.ToString())</value></set-header></return-response></otherwise></choose>']
// WebSocketはAPI本体のserviceUrl=wss://を使います。HTTP用backend entityの
// https://へ切り替えると、APIMはWebSocket型に無効な接続先として握手前に拒否します。
// WSの固定した1行だけを除き、認可・query・MI・header・監査・forward-requestは保ちます。
// HTTP/gRPC/MCPは従来のbackend entity参照をそのまま使います。
var protocolRateCleanupXml = [for api in apiDefinitions: api.apiType == 'mcp' ? mcpRateCleanupXml
  : api.apiType == 'websocket' && contains(api.?websocket ?? {}, 'realtimeObserver')
    ? '<set-variable name="gatewayRateLimitExempt" value="@(true)" />'
    : '<set-variable name="gatewayRateLimitExempt" value="@(false)" />']
// MCPの予約済みメタデータパスを下流へ転送せず、通常の404を返します。
var mcpMetadataGuardXml = '<choose><when condition="@(context.Request.OriginalUrl.Path.IndexOf(&quot;/.well-known/oauth-protected-resource&quot;, StringComparison.OrdinalIgnoreCase) &gt;= 0)"><return-response><set-status code="404" reason="Not Found" /><set-header name="apim-request-id" exists-action="override"><value>@(context.RequestId.ToString())</value></set-header></return-response></when></choose>'
var policyValues = [for (api, index) in apiDefinitions: replace(replace(replace(replace(replace(replace(replace(selectBackendPolicy(api, replace(replace(replace(baseApiPolicy(api, api.apiType == 'http' ? gatewayPolicyTemplate : protocolPolicyTemplate, backendAuthenticationXml[index], backendHeadersXml[index]), '__RATE_LIMIT_ERROR_XML__', rateLimitErrorXml), '__RATE_LIMIT_CLEANUP_XML__', protocolRateCleanupXml[index]), '__AUDIT_SETTINGS_BASE64__', base64(string(auditSettings(api, '', false))))), '__PROTOCOL_QUERY_XML__', protocolQueriesXml[index]), '__WEBSOCKET_QUERY_BASE64__', api.apiType == 'websocket' && (contains(api.websocket, 'queryParameters') || contains(api.websocket, 'allowedModels')) ? base64(string(api.websocket.?queryParameters ?? {})) : ''), '__PROTOCOL_SESSION_XML__', observerPoliciesXml[index]), '__MCP_PROXY_ROUTE_XML__', mcpProxyRoutingXml[index]), '__MCP_PRM_GUARD_XML__', api.apiType == 'mcp' ? mcpMetadataGuardXml : ''), '__CREDENTIAL_CONNECTION_INBOUND_XML__', api.apiType == 'mcp' && contains(api, 'credentialConnection') ? '<set-variable name="gatewayCredentialConnection" value="${base64(string(api.credentialConnection))}" /><include-fragment fragment-id="core-credential-connection" /><include-fragment fragment-id="core-credential-session-use" />' : backendAuthenticationXml[index]), '__CREDENTIAL_CONNECTION_OUTBOUND_XML__', api.apiType == 'mcp' && contains(api, 'credentialConnection') ? '<include-fragment fragment-id="core-credential-connection-outbound" />' : '')]
// プロトコルごとの API と操作を分類し、同じ定義から Product・認可・公開操作の対応を作ります。
var httpApis = filter(apiDefinitions, api => api.apiType == 'http')
var operationEntries = flatten(map(httpApis, api => map(api.operations, operation => { apiName: api.name, backendUrl: contains(operation, 'backendOverride') ? operation.backendOverride.backendUrl : api.backendUrl, operation: operation, constraint: api.authorization.operationConstraints[operation.name] })))
var operationBackendEntries = flatten(map(httpApis, api => map(filter(api.operations, operation => contains(operation, 'backendOverride')), operation => { apiName: api.name, operationName: operation.name, backend: operation.backendOverride })))
// Product ごとの subscription 登録を集約し、重複を除いて作成します。認可は引き続き API/操作単位で行います。
var allSubscriptionEntries = flatten(map(apiDefinitions, api => map(api.keySubscriptions, entry => { productId: api.productId, entry: entry })))
var subscriptionEntries = union(allSubscriptionEntries, [])
// 通常キーは公開済みAPIを一つの製品から利用します。既存製品は検証用に維持します。
var releasedProductId = 'released-apis'
var productIds = union(map(apiDefinitions, api => api.productId), [releasedProductId])
var releasedApis = filter(apiDefinitions, api => api.?released ?? false)
var piiApis = filter(apiDefinitions, api => length(filter(api.operations, operation => api.authorization.operationConstraints[operation.name].kind == 'pii')) > 0)
var documentApis = filter(apiDefinitions, api => length(filter(api.operations, operation => api.authorization.operationConstraints[operation.name].kind == 'documentAnalysis')) > 0)
var mcpApis = filter(apiDefinitions, api => contains(api, 'mcp'))
// MCPも同じ購読キーで認可します。APIのOperation構成は維持します。
var mcpPolicyValues = [for api in mcpApis: replace(replace(replace(mcpPolicyTemplate, '__AUTHORIZATION_BASE64__', base64(string(api.authorization))), '__RATE_LIMIT_ERROR_XML__', rateLimitErrorXml), '__AUDIT_SETTINGS_BASE64__', base64(string(auditSettings(api, api.mcp.operationNames[0], true))))]

// 本人/共有キー/サービスの中央設定を全APIで共有し、同じ枠へ異なる上限を適用しません。
resource rateLimitFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-rate-limit'
  properties: { format: 'rawxml', description: 'Subscription-scoped request limits without response-body buffering.', value: replace(loadTextContent('policies/rate-limit.xml'), '__RATE_LIMITS_BASE64__', base64(string(rateLimits))) }
}
// 本文を扱うnative token policyを、HTTPの対応APIだけから参照します。
resource tokenLimitFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-token-limits'
  properties: { format: 'rawxml', description: 'Optional native LLM token limits for supported HTTP operations only.', value: loadTextContent('policies/token-limit.xml') }
}

// 追跡候補の検査と下流IDの退避。fragment内で本文やtokenをログへ出しません。
resource auditContextFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-audit-context'
  properties: { format: 'rawxml', description: 'Agent-claimed trace candidates and authorized model metadata; never authorization or ownership.', value: loadTextContent('policies/audit-context.xml') }
}
// 本文の受信完了を前提にするHTTPモデル監査だけを独立させます。
// native gRPC/WS/MCPからは参照せず、未実行分岐の本文読取りによるbufferも防ぎます。
resource auditBodyModelFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-audit-body-model'
  properties: { format: 'rawxml', description: 'HTTP-only authorized model-name extraction; never used by native protocol policies.', value: loadTextContent('policies/audit-body-model.xml') }
}
resource backendAuditFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-backend-audit'
  properties: { format: 'rawxml', description: 'Capture allowlisted backend response IDs before global header rewriting; no body reads.', value: loadTextContent('policies/backend-audit.xml') }
}

// 各ポリシーから共通利用する最小限の監査情報出力。本文や認証値は記録しません。
resource gatewayTelemetryFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-gateway-telemetry'
  properties: { format: 'rawxml', description: 'Allowlisted gateway audit metadata; no text, credentials or raw diagnostics.', value: loadTextContent('policies/gateway-telemetry.xml') }
}
// 要求サイズと出力トークン上限を検査する共通部品です。
resource modelLimitsFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-model-limits'
  properties: { format: 'rawxml', description: 'Validate received request sizes and provider input limits without requiring optional output tokens.', value: loadTextContent('policies/model-limits.xml') }
}
// 本人の登録済み OAuth 接続を解決する共通部品です。
resource credentialConnectionFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-credential-connection'
  properties: { format: 'rawxml', description: 'Resolve personal sessions and return Microsoft sign-in guidance when authentication is required.', value: replace(renderCredentialLogin(replace(loadTextContent('policies/credential-connection.xml'), '__CREDENTIAL_CHALLENGE_XML__', credentialChallengeXml), credentialSettings), '__GATEWAY_TELEMETRY_XML__', gatewayTelemetryBody) }
}
// 本人照合後の業務利用確定と認証案内。上のfragmentと同階層で呼び出します。
resource credentialSessionUseFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-credential-session-use'
  properties: { format: 'rawxml', description: 'Finalize verified business use and relay session updates without extending authentication transactions.', value: replace(renderCredentialLogin(replace(loadTextContent('policies/credential-session-use.xml'), '__CREDENTIAL_CHALLENGE_XML__', credentialChallengeXml), credentialSettings), '__GATEWAY_TELEMETRY_XML__', gatewayTelemetryBody) }
}
// 外部資格情報が失効した場合に、安全な再認証案内へ変換する共通部品です。
resource credentialConnectionOutboundFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-credential-connection-outbound'
  properties: { format: 'rawxml', description: 'Start personal reauthorization for revoked downstream credentials and return safe guidance.', value: replace(renderCredentialLogin(replace(loadTextContent('policies/credential-connection-outbound.xml'), '__CREDENTIAL_CHALLENGE_XML__', credentialChallengeXml), credentialSettings), '__GATEWAY_TELEMETRY_XML__', gatewayTelemetryBody) }
}

// Realtime の補助 WebSocket 接続で所有者チケットを照合する共通部品です。
resource realtimeObserverFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = if (!empty(observerApis)) {
  parent: apim
  name: 'core-realtime-observer'
  properties: { format: 'rawxml', description: 'Validate the signed session owner before a Realtime sideband handshake; relay receive-only mode is separate.', value: loadTextContent('policies/realtime-observer.xml') }
}

// 入口の購読キーヘッダー名は基盤の共通設定を参照します。
// 裏側のサービス向け token を取得する audience を API ごとに保存します。
resource backendAudienceNamedValues 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = [for api in apiDefinitions: {
  parent: apim
  name: '${api.name}-backend-audience'
  properties: { displayName: '${api.name}-backend-audience', secret: false, value: api.backendAudience }
}]

// API key が必要なバックエンドだけ Key Vault 参照を登録。キー本体はコードに埋め込みません。
resource backendSecretNamedValues 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = [for api in filter(apiDefinitions, api => api.backendAuthentication.mode == 'keyVaultHeader'): {
  parent: apim
  name: '${api.name}-backend-secret'
  properties: {
    displayName: '${api.name}-backend-secret'
    secret: true
    keyVault: { secretIdentifier: api.backendAuthentication.secretIdentifier }
  }
}]
resource operationBackendSecretNamedValues 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = [for entry in filter(operationBackendEntries, item => item.backend.backendAuthentication.mode == 'keyVaultHeader'): {
  parent: apim
  name: '${operationBackendId(entry.apiName, entry.operationName)}-secret'
  properties: {
    displayName: '${operationBackendId(entry.apiName, entry.operationName)}-secret'
    secret: true
    keyVault: { secretIdentifier: entry.backend.backendAuthentication.secretIdentifier }
  }
}]
// 裏側の接続先 URL と TLS 検査を設定。バックエンド証明書の名前・チェーン検査を維持します。
resource backends 'Microsoft.ApiManagement/service/backends@2024-05-01' = [for api in apiDefinitions: {
  parent: apim
  name: '${api.name}-backend'
  properties: {
    title: api.displayName
    protocol: 'http'
    url: api.apiType == 'websocket' ? replace(api.backendUrl, 'wss://', 'https://') : api.backendUrl
    tls: { validateCertificateChain: true, validateCertificateName: true }
  }
}]
resource operationBackends 'Microsoft.ApiManagement/service/backends@2024-05-01' = [for entry in operationBackendEntries: {
  parent: apim
  name: operationBackendId(entry.apiName, entry.operationName)
  properties: {
    title: '${entry.apiName} / ${entry.operationName}'
    protocol: 'http'
    url: entry.backend.backendUrl
    tls: { validateCertificateChain: true, validateCertificateName: true }
  }
}]
// HTTP / WebSocket / gRPC / MCP の API 本体。認証方式と公開パスを定義から反映します。
resource apis 'Microsoft.ApiManagement/service/apis@2025-09-01-preview' = [for (api, index) in apiDefinitions: {
  parent: apim
  name: api.name
  properties: union({
    displayName: api.displayName
    // native azd/APIだけの再公開とも、同じ公開契約から生成した説明を正本にします。
    // API Centerへ直接descriptionを書かず、APIMからの同期で維持します。
    description: api.?catalogDescription ?? api.displayName
    apiRevision: '1'
    path: api.path
    // 公開 WSS の TLS は AppGW で終端し、FW を通る内部 HTTP 接続は ws になります。
    // Internal APIM の80番でも握手を受け付け、公開側の TLS・NSG・入口証明の検査は維持します。
    protocols: api.apiType == 'websocket' ? ['ws', 'wss'] : api.apiType == 'grpc' ? ['https'] : ['http', 'https']
    subscriptionRequired: true
    subscriptionKeyParameterNames: { header: api.subscriptionKeyHeader, query: 'subscription-key' }
    type: api.apiType
  }, api.apiType == 'websocket' ? { serviceUrl: api.backendUrl }
    : api.apiType == 'grpc' ? { serviceUrl: api.backendUrl, format: 'grpc', value: api.grpc.definition }
    // 公開設定の配列はそのまま使い、Azureへ送るときだけ接続先名をキーにした辞書へ変換します。
    // Learnの配列例と実サービスに差があるため、Microsoft公式APIM拡張/Foundryサンプルに合わせます。
    // 参照: microsoft/vscode-apimanagement/src/commands/passthroughMcpServer.ts
    //       microsoft/Foundry-Agent-Solution-Templates/apim-hosted-agent/infra/modules/apim-tool-learn.bicep
    // Streamableはmcp、従来SSEはsse(GET)とmessages(POST)を別々に登録します。
    // SSEのPOSTだけはサーバーの案内にAPIMが付けるprefixと揃えるため、固定の/messages/へ
    // 読み替えず、明示されたmessageパスを使います。任意のパスを実行時に受け取りません。
    // backendの名前をresource参照で渡し、APIより先にbackendが作成される依存も宣言します。
    : api.apiType == 'mcp' ? {
        backendId: backends[index].name
        mcpProperties: {
          transportType: api.mcpProxy.transport
          endpoints: toObject(api.mcpProxy.endpoints,
            endpoint => endpoint.name == 'sse' ? 'sse' : api.mcpProxy.transport == 'sse' ? 'messages' : 'mcp',
            endpoint => union({ uriTemplate: api.mcpProxy.transport == 'sse' ? endpoint.name == 'sse' ? '/sse' : endpoint.uriTemplate : '/mcp' }, api.mcpProxy.transport == 'sse' ? { method: endpoint.name == 'sse' ? 'GET' : 'POST' } : {}))
        }
      }
    : {})
}]
// Model / Tool / Hosted Agent 等の利用者向け Product 分類を作ります。分類だけで呼出権限は付けません。
resource protectedProducts 'Microsoft.ApiManagement/service/products@2024-05-01' = [for productId in productIds: {
  parent: apim
  name: productId
  properties: {
    displayName: productId == releasedProductId ? 'Released APIs' : productId
    description: productId == releasedProductId ? 'All released business APIs. API-specific validation and personal connection requirements still apply.' : 'Verification API bundle ${productId}; caller authorization remains API-scoped.'
    subscriptionRequired: true
    approvalRequired: true
    state: 'published'
  }
}]
// 公開 API と利用者向け Product の所属を関連付けます。
// 同じProductへAPIを同時追加するとAPIM側の更新世代が競合するため、１件ずつ登録します。
@batchSize(1)
resource protectedProductApiLinks 'Microsoft.ApiManagement/service/products/apis@2024-05-01' = [for api in apiDefinitions: {
  name: '${apim.name}/${api.productId}/${api.name}'
  dependsOn: [apis, protectedProducts]
}]
// released=trueだけを通常製品へ追加します。未指定の追加APIは自動公開しません。
@batchSize(1)
resource releasedProductApiLinks 'Microsoft.ApiManagement/service/products/apis@2024-05-01' = [for api in releasedApis: {
  name: '${apim.name}/${releasedProductId}/${api.name}'
  dependsOn: [apis, protectedProducts, protectedProductApiLinks]
}]
// ネイティブ MCP 表口を対応 Product へ関連付けます。
// 通常APIと同じProductを使うため、上の関連付けが終わってから１件ずつ追加します。
@batchSize(1)
resource mcpProductApiLinks 'Microsoft.ApiManagement/service/products/apis@2024-05-01' = [for api in mcpApis: {
  name: '${apim.name}/${api.productId}/${api.mcp.name}'
  dependsOn: [mcpServers, protectedProducts, protectedProductApiLinks, releasedProductApiLinks]
}]
// 生成MCPのリリース状態は元APIと一体で管理します。
@batchSize(1)
resource releasedMcpProductApiLinks 'Microsoft.ApiManagement/service/products/apis@2024-05-01' = [for api in filter(mcpApis, api => api.?released ?? false): {
  name: '${apim.name}/${releasedProductId}/${api.mcp.name}'
  dependsOn: [mcpServers, protectedProducts, mcpProductApiLinks]
}]
// 同じProductのキーで個人接続の開始・確認操作を使えるようにします。
@batchSize(1)
resource credentialLoginProductLinks 'Microsoft.ApiManagement/service/products/apis@2024-05-01' = [for productId in productIds: if (credentialLoginEnabled) {
  name: '${apim.name}/${productId}/credential-manager-login'
  dependsOn: [credentialLoginApi, protectedProducts, releasedMcpProductApiLinks]
}]
// 承認済みシステム用 APIM subscription を作成。キー値を出力しません。
resource subscriptions 'Microsoft.ApiManagement/service/subscriptions@2024-05-01' = [for entry in subscriptionEntries: {
  parent: apim
  name: entry.entry.subscriptionId
  properties: {
    displayName: entry.entry.displayName
    scope: '${apim.id}/products/${entry.productId}'
    state: entry.entry.enabled ? 'active' : 'suspended'
    allowTracing: false
  }
  dependsOn: [protectedProducts]
}]
// 通常 API の認証・認可・監査・転送規則を登録します。WebSocket は下の onHandshake に設定します。
// 大きいXMLを一斉に書くとDeveloperの管理処理が詰まるため、１件の完了後に次を送ります。
// APIの呼出回数制限ではなく、構築時の管理操作だけを直列にする指定です。
@batchSize(1)
resource apiPolicies 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = [for (api, index) in apiDefinitions: if (api.apiType != 'websocket') {
  parent: apis[index]
  name: 'policy'
  properties: { format: 'rawxml', value: policyValues[index] }
  dependsOn: [auditContextFragment, auditBodyModelFragment, backendAuditFragment, rateLimitFragment, tokenLimitFragment, backends, operationBackends, backendAudienceNamedValues, gatewayTelemetryFragment, modelLimitsFragment, credentialConnectionFragment, credentialSessionUseFragment, credentialConnectionOutboundFragment, backendSecretNamedValues, operationBackendSecretNamedValues, realtimeObserverFragment]
}]
// WebSocket API では API スコープの policy を作れないため、システムが自動生成する
// onHandshake 操作へ同じ接続開始時の認証・認可を設定します。操作本体は作成・変更しません。
// <base/> による全体の公開ガード継承も維持し、確立後のフレームはここで検査しません。
// https://learn.microsoft.com/azure/api-management/websocket-api
// HTTP等のAPIポリシーが終わってから、握手ポリシーも１件ずつ適用します。
@batchSize(1)
resource websocketPolicies 'Microsoft.ApiManagement/service/apis/operations/policies@2024-05-01' = [for (api, index) in apiDefinitions: if (api.apiType == 'websocket') {
  name: '${apim.name}/${api.name}/onHandshake/policy'
  properties: { format: 'rawxml', value: policyValues[index] }
  dependsOn: [apiPolicies, auditContextFragment, backendAuditFragment, rateLimitFragment, apis, backends, backendAudienceNamedValues, gatewayTelemetryFragment, backendSecretNamedValues, realtimeObserverFragment]
}]
// Explicit request/response schemas drive the native REST-backed MCP tool.
// Native Language bodies and entity strings are never the public contract.
// PII API に送る本文の構造を Schema として登録します。
resource piiSchemas 'Microsoft.ApiManagement/service/apis/schemas@2024-05-01' = [for api in piiApis: {
  name: '${apim.name}/${api.name}/pii-public-contract'
  properties: {
    contentType: 'application/vnd.oai.openapi.components+json'
    document: { components: loadJsonContent('schemas/pii.json') }
  }
  dependsOn: [apis]
}]
// REST の各操作を、許可されたメソッドと URL テンプレートに限定します。
resource documentSchemas 'Microsoft.ApiManagement/service/apis/schemas@2024-05-01' = [for api in documentApis: {
  name: '${apim.name}/${api.name}/document-analysis-public-contract'
  properties: {
    contentType: 'application/vnd.oai.openapi.components+json'
    document: { components: loadJsonContent('schemas/document-analysis.json') }
  }
  dependsOn: [apis]
}]
resource operations 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = [for entry in operationEntries: {
  name: '${apim.name}/${entry.apiName}/${entry.operation.name}'
  properties: {
    displayName: entry.operation.displayName
    method: entry.operation.method
    urlTemplate: entry.operation.urlTemplate
    templateParameters: entry.operation.templateParameters
    request: entry.constraint.kind == 'pii' ? {
      description: 'Single text and explicit language. No arbitrary Language parameters.'
      representations: [{ contentType: 'application/json', schemaId: 'pii-public-contract', typeName: 'PiiRedactRequest' }]
    } : entry.constraint.kind == 'documentAnalysis' ? {
      description: 'Binary document submission up to the provider limit. Returns 202 and an owner-bound operation ticket; never resubmits the billable POST.'
      queryParameters: [{ name: 'model', required: true, type: 'string', values: ['prebuilt-read', 'prebuilt-layout'] }]
      representations: map(entry.constraint.contentTypes, mime => { contentType: mime })
    } : entry.constraint.kind == 'documentAnalysisStatus' ? {
      description: 'Authenticated operation status/result. The ticket must belong to the same subscription, operation, API and backend.'
      headers: [{ name: 'X-Core-Document-Ticket', required: true, type: 'string' }]
    } : entry.operation.?request ?? {}
    responses: entry.constraint.kind == 'pii' ? concat([
      { statusCode: 200, description: 'Redacted text and allowlisted metadata only.', representations: [{ contentType: 'application/json', schemaId: 'pii-public-contract', typeName: 'PiiRedactResult' }] }
    ], map([400, 413, 422, 429, 502, 504], statusCode => {
      statusCode: statusCode
      description: 'Safe failure code and correlation ID. No input text or upstream diagnostic.'
      representations: [{ contentType: 'application/json', schemaId: 'pii-public-contract', typeName: 'PiiRedactError' }]
    })) : contains(['documentAnalysis', 'documentAnalysisStatus'], entry.constraint.kind) ? concat(
      entry.constraint.kind == 'documentAnalysis' ? [
        { statusCode: 202, description: 'Accepted. Keep the operation ticket and poll the fixed Gateway status path.', representations: [{ contentType: 'application/json', schemaId: 'document-analysis-public-contract', typeName: 'DocumentAnalysisAccepted' }] }
      ] : [
        { statusCode: 202, description: 'Analysis is running. Honor Retry-After; do not repeat the submission.', representations: [{ contentType: 'application/json', schemaId: 'document-analysis-public-contract', typeName: 'DocumentAnalysisRunning' }] }
        { statusCode: 200, description: 'Native analyzeResult with Gateway correlation ID.', representations: [{ contentType: 'application/json', schemaId: 'document-analysis-public-contract', typeName: 'DocumentAnalysisResult' }] }
      ], map([400, 403, 404, 410, 413, 415, 422, 429, 502, 504], statusCode => {
        statusCode: statusCode
        description: 'Safe failure code and correlation ID. No provider operation URL or diagnostic content.'
        representations: [{ contentType: 'application/json', schemaId: 'document-analysis-public-contract', typeName: 'DocumentAnalysisError' }]
      })) : []
  }
  dependsOn: [apis, piiSchemas, documentSchemas, apiPolicies]
}]
// 操作固有の制約を追加。モデル・PII・Realtime 等の用途別検証を切り替えます。
// APIと握手の設定後に１件ずつ書き、別ループの重いポリシー適用とも重ねません。
@batchSize(1)
resource operationPolicies 'Microsoft.ApiManagement/service/apis/operations/policies@2024-05-01' = [for (entry, index) in operationEntries: {
  parent: operations[index]
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: entry.constraint.kind == 'pii'
      ? replace(piiPolicyTemplate, '__PII_SETTINGS_BASE64__', base64(string(entry.constraint)))
      : entry.constraint.kind == 'documentAnalysis' ? replace(replace(documentAnalysisPolicyTemplate, '__DOCUMENT_SETTINGS_BASE64__', base64(string(entry.constraint))), '__DOCUMENT_BASE_URL__', xmlText(entry.backendUrl))
      : entry.constraint.kind == 'documentAnalysisStatus' ? replace(replace(documentAnalysisStatusPolicyTemplate, '__DOCUMENT_SETTINGS_BASE64__', base64(string(entry.constraint))), '__DOCUMENT_BASE_URL__', xmlText(entry.backendUrl))
      // 短期 token の取得先は、登録済み Foundry backend から固定生成します。
      // 利用者の入力 URL や公開 params に取得先を追加しません。
      : entry.constraint.kind == 'realtimeCreate' ? replace(realtimeCreatePolicyTemplate, '__REALTIME_CLIENT_SECRETS_URL__', xmlText('${entry.backendUrl}/realtime/client_secrets'))
      : entry.constraint.kind == 'realtimeHangup' ? realtimeHangupPolicyTemplate
      : entry.constraint.kind == 'liveCreate' ? liveCreatePolicyTemplate
      : join(['<policies><!-- APIの認証・権限・入力上限を継承し、このOperationの要求形式と上流URIを適用します。 --><inbound>'
          '<base />'
          // Authenticate and enforce received bytes before scanning large XML.
          requiresSpeechSsmlGuard(entry) ? speechSsmlGuardPolicy : ''
          // The shared fragment checks media type and received bytes. validate-content
          // has a separate 100 KiB policy ceiling and cannot enforce large uploads.
          contains(entry.operation, 'staticResponse') ? '<return-response><set-status code="200" reason="OK" /><set-header name="apim-request-id" exists-action="override"><value>@(context.RequestId.ToString())</value></set-header><set-header name="Content-Type" exists-action="override"><value>application/json</value></set-header><set-body>${xmlText(string(entry.operation.staticResponse.body))}</set-body></return-response>' : ''
          profileRoutingPolicy(entry.constraint)
          contains(entry.operation, 'backendUrlTemplate') ? '<!-- 登録済みの上流URIへ書き換えます。転送queryは定義した値に揃えます。 --><rewrite-uri template="${xmlText(entry.operation.backendUrlTemplate)}" copy-unmatched-params="false" />' : ''
          '</inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>'], '')
  }
  dependsOn: [apiPolicies, websocketPolicies]
}]
// Official REST-backed MCP resource model. No serviceUrl/transport backend is
// invented: the single tool references the reviewed REST operation directly.
// 承認済み REST 操作を native MCP Server の表口として公開します。
resource mcpServers 'Microsoft.ApiManagement/service/apis@2025-09-01-preview' = [for api in mcpApis: {
  parent: apim
  name: api.mcp.name
  properties: {
    type: 'mcp'
    displayName: api.mcp.displayName
    description: api.mcp.?catalogDescription ?? 'PII tool generated from the protected public REST operation.'
    path: api.mcp.path
    protocols: ['http', 'https']
    subscriptionRequired: true
    subscriptionKeyParameterNames: { header: api.subscriptionKeyHeader, query: 'subscription-key' }
  }
}]
// MCP で公開する tool を承認済み REST 操作に関連付けます。
resource mcpTools 'Microsoft.ApiManagement/service/apis/tools@2025-09-01-preview' = [for (api, index) in mcpApis: {
  parent: mcpServers[index]
  name: api.mcp.operationNames[0]
  properties: {
    displayName: api.mcp.operationNames[0]
    description: 'Mask one Japanese or English text. Returns only redacted text and safe metadata.'
    operationId: '${apim.id}/apis/${api.name}/operations/${api.mcp.operationNames[0]}'
  }
  dependsOn: [operationPolicies]
}]
// MCP表口にも共通の購読キーと登録済み操作の制約を適用します。
// MCP表口が増えた場合も、管理処理へXMLをまとめて送らないよう１件ずつ適用します。
@batchSize(1)
resource mcpPolicies 'Microsoft.ApiManagement/service/apis/policies@2025-09-01-preview' = [for (api, index) in mcpApis: {
  parent: mcpServers[index]
  name: 'policy'
  properties: { format: 'rawxml', value: mcpPolicyValues[index] }
  dependsOn: [auditContextFragment, backendAuditFragment, gatewayTelemetryFragment, mcpTools]
}]
// Azure Monitorへの送信を表すAPIM内のloggerを先に作成します。
// 診断設定のloggerIdを文字列で書くだけでは、この送信先は作成されません。
// Application Insights用のloggerとは異なり、接続文字列や秘密鍵は不要です。
// ログのWorkspaceへの配送先は、別途Microsoft.Insightsの診断設定が管理します。
resource azureMonitorLogger 'Microsoft.ApiManagement/service/loggers@2024-05-01' = {
  parent: apim
  name: 'azuremonitor'
  properties: {
    loggerType: 'azureMonitor'
    isBuffered: true
  }
}
// APIM 診断で本文・資格情報を記録しない基本設定を適用します。
resource globalDiagnostic 'Microsoft.ApiManagement/service/diagnostics@2025-09-01-preview' = {
  parent: apim
  name: 'azuremonitor'
  properties: {
    alwaysLog: 'allErrors'
    loggerId: azureMonitorLogger.id
    logClientIp: false
    verbosity: 'information'
    largeLanguageModel: { logs: 'disabled' }
    sampling: { samplingType: 'fixed', percentage: 100 }
    frontend: { request: diagnosticRequest, response: diagnosticResponse }
    backend: { request: { headers: [], body: { bytes: 0 } }, response: { headers: [], body: { bytes: 0 } } }
  }
}
// API ごとに監査設定をそろえ、本文の取得を無効にします。
resource apiDiagnostics 'Microsoft.ApiManagement/service/apis/diagnostics@2025-09-01-preview' = [for (api, index) in apiDefinitions: {
  parent: apis[index]
  name: 'azuremonitor'
  properties: {
    alwaysLog: 'allErrors'
    loggerId: azureMonitorLogger.id
    logClientIp: false
    verbosity: 'information'
    largeLanguageModel: { logs: empty(nativeLlmOperations(api)) ? 'disabled' : 'enabled' }
    sampling: { samplingType: 'fixed', percentage: 100 }
    frontend: { request: diagnosticRequest, response: diagnosticResponse }
    backend: { request: { headers: [], body: { bytes: 0 } }, response: { headers: [], body: { bytes: 0 } } }
  }
}]
// MCP 表口にも同じ本文・資格情報非収集の診断設定を適用します。
resource mcpDiagnostics 'Microsoft.ApiManagement/service/apis/diagnostics@2025-09-01-preview' = [for (api, index) in mcpApis: {
  parent: mcpServers[index]
  name: 'azuremonitor'
  properties: {
    alwaysLog: 'allErrors'
    loggerId: azureMonitorLogger.id
    logClientIp: false
    verbosity: 'information'
    largeLanguageModel: { logs: 'disabled' }
    sampling: { samplingType: 'fixed', percentage: 100 }
    frontend: { request: diagnosticRequest, response: diagnosticResponse }
    backend: { request: { headers: [], body: { bytes: 0 } }, response: { headers: [], body: { bytes: 0 } } }
  }
}]
output publishedApiResourceIds array = publishApis ? concat(map(apiDefinitions, api => resourceId('Microsoft.ApiManagement/service/apis', names.apim, api.name)), map(mcpApis, api => resourceId('Microsoft.ApiManagement/service/apis', names.apim, api.mcp.name)), credentialLoginEnabled ? [credentialLoginApi!.id, credentialBrowserApi!.id] : []) : []
output mcpServerPaths array = publishApis ? map(mcpApis, api => '/${api.mcp.path}/mcp') : []

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
@description('Managed Entra settings; controller opens Product membership only after raw readback.')
param entraAuthentication object = {}
@description('基盤と各APIで照合済みの購読キーヘッダー名。診断の秘密マスクにも使います。')
param subscriptionKeyHeader string = 'api-key'
@description('APIMが検証した購読IDごとの共通要求回数制限。')
param rateLimits object = { user: { enabled: false, calls: 60, renewalPeriodSeconds: 60 }, subscriptionKey: { enabled: false, calls: 60, renewalPeriodSeconds: 60, overrides: [] }, application: { enabled: false, calls: 60, renewalPeriodSeconds: 60, overrides: [] } }
@description('Live・Realtimeの作成とnative PII MCPのquery処理。入口ごとに観測から拒否へ切り替えます。')
param queryGuards object = { liveCreate: 'observe', realtimeCreate: 'observe', piiMcp: 'observe' }
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
// XML属性の改行が空白になっても実行コードを隠さないよう、説明行を安全なブロックコメントへ変換します。
func sourceCommentLine(line string) string => startsWith(trim(line), '//') ? '${substring(line, 0, indexOf(line, '//'))}/*${replace(substring(line, indexOf(line, '//') + 2), '*/', '* /')} */' : line
func sourceWithExplanations(source string) string => join(map(split(replace(source, '\r\n', '\n'), '\n'), line => sourceCommentLine(line)), '\n')
// 複製する式だけは実行コードを保持し、説明の正本への案内にまとめられるようにします。
func withoutSourceLineComments(source string) string => join(filter(split(source, '\n'), line => !startsWith(trim(line), '//')), '\n')
func compactObservationSource(source string) string => '/* 監査の説明はフラグメント core-gateway-telemetry を参照。 */\n${withoutSourceLineComments(replace(source, '\r\n', '\n'))}'
// 監査の正本へ追加した説明だけを、複製時に省きます。既存コメントと実行コードは保持します。
func withoutTelemetryExplanationLines(template string) string => join(filter(split(template, '\n'), line => !startsWith(trim(line), '/* 説明：')), '\n')

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
// 監査用のAPI固有情報。要求/API/操作の実行時IDは全体ポリシーが取得します。
// 入力制約とトークン制限の設定はcontrolSettingsへ分け、監査名の変数で制御しません。
func auditSettings(api object, operation string, generatedMcp bool) object => {
  serviceClass: api.?serviceClass ?? 'platform'
  backendService: api.backendService
  // HTTP本人接続はgateway.xmlのchooseで基底のMI等を使わず、CM接続のtokenを送ります。
  // disabledの接続も拒否されるだけでMIへ戻らないため、設定された最終方式を記録します。
  // 生成MCPの外側はREST側の本人接続処理を直接実行しないので、この表示を流用しません。
  backendAuthentication: !generatedMcp && contains(api, 'credentialConnection') ? 'credentialConnection' : api.backendAuthentication.mode
  operationBackends: toObject(filter(api.operations, operation => contains(operation, 'backendOverride')), operation => operation.name, operation => {
    backendService: operation.backendOverride.backendService
    backendAuthentication: operation.backendOverride.backendAuthentication.mode
  })
  llmOperationIds: generatedMcp ? [] : nativeLlmOperations(api)
  websocketModel: contains(api, 'websocket') ? contains(api.websocket, 'queryParameters') ? api.websocket.queryParameters.?model ?? '' : '' : ''
  targetAgent: generatedMcp ? '' : hostedAgentName(api)
  measurementRole: generatedMcp ? 'facade' : contains(api, 'websocket') ? contains(api.websocket, 'realtimeObserver') ? 'observation' : 'execution' : 'execution'
}
func controlSettings(api object, operation string, generatedMcp bool) object => {
  // 生成MCPだけ、公開操作と対応するREST操作のIDを明示します。
  // 通常APIは共通側の実行時operationIdを使用します。
  operationId: operation
  operationConstraints: api.authorization.operationConstraints
  tokenLimitOperationIds: generatedMcp ? [] : tokenLimitOperations(api)
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
// Protected connection operations inherit the same generated global policy.
// Browser launch/callback keep their independent transaction authentication.
var credentialLoginApiPolicyXml = '<policies><inbound><base /></inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>'
resource credentialLoginApiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = if (credentialLoginEnabled) {
  parent: credentialLoginApi
  name: 'policy'
  properties: { format: 'rawxml', value: credentialLoginApiPolicyXml }
}
// Keep CM metadata light: selecting individual templates avoids a second
// expanded copy of every full XML inside the operation metadata array.
var credentialLoginStartPolicyTemplate = loadTextContent('policies/credential-login/start.xml')
var credentialLoginStatusPolicyTemplate = loadTextContent('policies/credential-login/status.xml')
var credentialLoginLaunchPolicyTemplate = loadTextContent('policies/credential-login/launch.xml')
var credentialLoginLaunchPostPolicyTemplate = loadTextContent('policies/credential-login/launch-post.xml')
var credentialLoginIdentityCallbackPolicyTemplate = loadTextContent('policies/credential-login/identity-callback.xml')
var credentialLoginCallbackPolicyTemplate = loadTextContent('policies/credential-login/callback.xml')
var credentialLoginLogoutPolicyTemplate = loadTextContent('policies/credential-login/logout.xml')
var credentialLoginSessionCommitPolicyTemplate = loadTextContent('policies/credential-login/session-commit.xml')
var credentialLoginLogoutAllPolicyTemplate = loadTextContent('policies/credential-login/logout-all.xml')
var credentialLoginDisconnectPolicyTemplate = loadTextContent('policies/credential-login/disconnect.xml')
var credentialLoginOperations = [
  { apiId: 'credential-manager-login', name: 'start', path: 'start', method: 'POST' }
  { apiId: 'credential-manager-login', name: 'status', path: 'status', method: 'GET' }
  { apiId: 'credential-manager-browser', name: 'launch', path: 'launch', method: 'GET' }
  { apiId: 'credential-manager-browser', name: 'launch-post', path: 'launch', method: 'POST' }
  { apiId: 'credential-manager-browser', name: 'identity-callback', path: 'identity-callback', method: 'GET' }
  { apiId: 'credential-manager-browser', name: 'callback', path: 'callback', method: 'GET' }
  { apiId: 'credential-manager-login', name: 'logout', path: 'logout', method: 'POST' }
  { apiId: 'credential-manager-login', name: 'session-commit', path: 'session/commit', method: 'POST' }
  { apiId: 'credential-manager-login', name: 'logout-all', path: 'logout-all', method: 'POST' }
  { apiId: 'credential-manager-login', name: 'disconnect', path: 'disconnect', method: 'POST' }
]
resource credentialLoginOperation 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = [for item in credentialLoginOperations: if (credentialLoginEnabled) {
  name: '${apim.name}/${item.apiId}/${item.name}'
  dependsOn: [credentialLoginApi, credentialBrowserApi]
  properties: { displayName: item.name, method: item.method, urlTemplate: '/${item.path}', responses: [] }
}]
resource credentialLoginOperationPolicy 'Microsoft.ApiManagement/service/apis/operations/policies@2024-05-01' = [for (item, index) in credentialLoginOperations: if (credentialLoginEnabled) {
  parent: credentialLoginOperation[index]
  name: 'policy'
  properties: { format: 'rawxml', value: renderCredentialLogin(item.name == 'start' ? credentialLoginStartPolicyTemplate : item.name == 'status' ? credentialLoginStatusPolicyTemplate : item.name == 'launch' ? credentialLoginLaunchPolicyTemplate : item.name == 'launch-post' ? credentialLoginLaunchPostPolicyTemplate : item.name == 'identity-callback' ? credentialLoginIdentityCallbackPolicyTemplate : item.name == 'callback' ? credentialLoginCallbackPolicyTemplate : item.name == 'logout' ? credentialLoginLogoutPolicyTemplate : item.name == 'session-commit' ? credentialLoginSessionCommitPolicyTemplate : item.name == 'logout-all' ? credentialLoginLogoutAllPolicyTemplate : credentialLoginDisconnectPolicyTemplate, credentialSettings) }
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
// 原文だけを変数へ保持し、差し込みは使用先と生成元で同じ関数を呼びます。
// 原文と置換済みXMLの二重保存を避け、説明コメントと実行コードを保ったまま容量を減らします。
var gatewayPolicyTemplate = loadTextContent('policies/gateway.xml')
var protocolPolicyTemplate = loadTextContent('policies/protocol.xml')
var piiPolicyTemplate = loadTextContent('policies/pii.xml')
var documentAnalysisPolicyTemplate = loadTextContent('policies/document-analysis.xml')
var documentAnalysisStatusPolicyTemplate = loadTextContent('policies/document-analysis-status.xml')
var realtimeCreatePolicyTemplate = loadTextContent('policies/realtime-create.xml')
var realtimeHangupPolicyTemplate = loadTextContent('policies/realtime-hangup.xml')
var liveCreatePolicyTemplate = loadTextContent('policies/live-create.xml')
var mcpPolicyTemplate = loadTextContent('policies/mcp.xml')
func configuredApiTemplate(http bool, gateway string, protocol string, profile string, guards object) string => http
  ? replace(replace(gateway, '__SUBSCRIPTION_PROFILE_XML__', profile), '__QUERY_GUARD_MODES_BASE64__', base64(string(guards)))
  : replace(protocol, '__SUBSCRIPTION_PROFILE_XML__', profile)
func configuredOperationTemplate(template string, errorXml string) string => replace(template, '__RATE_LIMIT_ERROR_XML__', errorXml)
func configuredMcpTemplate(template string, guards object) string => replace(template, '__QUERY_GUARD_MODES_BASE64__', base64(string(guards)))
// Compact connection-failure audit retains the verified key/user identity and
// fits the APIM fragment budget. Normal business audit remains the full fragment.
// One shared subject reader is XML-escaped before it enters any trace
// template. Both deployment assignments and protected source use these values.
// 未展開の正本は各1回だけ読み、同じ原文が配備と生成元で二重に読み込まれるのを防ぎます。
var auditObservationSource = loadTextContent('policies/expressions/legacy/audit-subject-observation.cs')
var requestContextSource = loadTextContent('policies/fragments/audit/core-request-context/core-request-context.cs')
var authenticatedSubjectSource = loadTextContent('policies/fragments/audit/core-audit-authenticated-subject/core-audit-authenticated-subject.cs')
var trustedIngressSource = loadTextContent('policies/fragments/ingress/core-trusted-ingress/core-trusted-ingress.cs')
var browserExceptionSource = loadTextContent('policies/fragments/authentication/core-credential-browser-exception/core-credential-browser-exception.cs')
var commonAuthenticationSource = loadTextContent('policies/fragments/authentication/core-common-authentication/core-common-authentication.cs')
var gatewayTelemetrySource = loadTextContent('policies/fragments/audit/core-gateway-telemetry/core-gateway-telemetry.cs')
var requestContextTemplate = loadTextContent('policies/fragments/audit/core-request-context/core-request-context.xml')
var authenticatedSubjectTemplate = loadTextContent('policies/fragments/audit/core-audit-authenticated-subject/core-audit-authenticated-subject.xml')
var trustedIngressTemplate = loadTextContent('policies/fragments/ingress/core-trusted-ingress/core-trusted-ingress.xml')
var browserExceptionTemplate = loadTextContent('policies/fragments/authentication/core-credential-browser-exception/core-credential-browser-exception.xml')
var commonAuthenticationTemplate = loadTextContent('policies/fragments/authentication/core-common-authentication/core-common-authentication.xml')
var entraAuthenticationErrorTemplate = loadTextContent('policies/fragments/authentication/core-entra-authentication-error/core-entra-authentication-error.xml')
var gatewayTelemetryTemplate = loadTextContent('policies/fragments/audit/core-gateway-telemetry/core-gateway-telemetry.xml')
var credentialConnectionTemplate = loadTextContent('policies/credential-connection.xml')
var credentialSessionUseTemplate = loadTextContent('policies/credential-session-use.xml')
var credentialConnectionOutboundTemplate = loadTextContent('policies/credential-connection-outbound.xml')
var realtimeObserverTemplate = loadTextContent('policies/realtime-observer.xml')
var auditSubjectObservationCompactXml = replace(xmlText(compactObservationSource(auditObservationSource)), '\'', '&apos;')
// 共通入口のフローから、監査データの組み立てだけを呼び出せるようにします。
// C#の正本は別ファイルで管理し、ここでXML属性用に変換します。外部通信は追加しません。
// 配備と生成元は同じ関数を呼び、C#とXMLの全文を中間変数へ重複して保存しません。
func auditSourceXml(source string) string => replace(xmlText(sourceWithExplanations(source)), '\'', '&apos;')
func requestContextFragmentValue(template string, source string) string => replace(template, '__CORE_REQUEST_CONTEXT_CSHARP__', replace(trim(replace(auditSourceXml(source), '\r\n', '\n')), '\n', '\n      '))
func authenticatedSubjectFragmentValue(template string, source string) string => replace(template, '__CORE_AUTHENTICATED_SUBJECT_CSHARP__', auditSourceXml(source))
func trustedIngressConditionValue(template string, source string) string => replace(template, '__CORE_TRUSTED_INGRESS_CSHARP__', replace(trim(auditSourceXml(source)), '\n', '\n        '))
func browserExceptionFragmentValue(template string, source string, configured bool) string => replace(replace(template, '__CORE_CREDENTIAL_BROWSER_EXCEPTION_CSHARP__', replace(trim(replace(auditSourceXml(source), '\r\n', '\n')), '\n', '\n      ')), '__CREDENTIAL_BROWSER_CONFIGURED__', string(configured))
func gatewayTelemetryFragmentValue(template string, source string, compact bool) string => replace(compact ? withoutTelemetryExplanationLines(template) : template, '__CORE_GATEWAY_TELEMETRY_CSHARP__', replace(xmlText(compact ? compactObservationSource(source) : sourceWithExplanations(source)), '\'', '&apos;'))
func observationFragmentValue(template string, source string, compact bool) string => replace(template, '__AUDIT_SUBJECT_OBSERVATION_CSHARP__', replace(xmlText(compact ? compactObservationSource(source) : sourceWithExplanations(source)), '\'', '&apos;'))
var credentialGatewayTelemetryBody = replace(replace(replace(loadTextContent('policies/credential-telemetry.xml'), '__AUDIT_SUBJECT_OBSERVATION_CSHARP__', auditSubjectObservationCompactXml), '<fragment>', ''), '</fragment>', '')
// This handler is inlined only into API/operation policies, never registered as
// a fragment. Refer to the full reviewed audit fragment without copying its
// body into every API and protected source entry; fragment nesting is avoided.
var rateLimitErrorXml = replace(replace(replace(loadTextContent('policies/rate-limit-error.xml'), '<fragment>', ''), '</fragment>', ''), '__GATEWAY_TELEMETRY_XML__', '<include-fragment fragment-id="core-gateway-telemetry" />')
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
// CM未設定のHTTP APIは、到達しないfragment参照を生成元にも残しません。
// 接続設定のあるAPIは元のguard/接続/session/outbound処理をそのまま使います。
func replaceCredentialBranch(policy string, startMarker string, endMarker string, fallback string) string => '${split(policy, startMarker)[0]}${fallback}${split(policy, endMarker)[1]}'
func selectHttpCredentialPolicy(api object, policy string) string => api.apiType != 'http' || contains(api, 'credentialConnection') ? policy
  : replaceCredentialBranch(replaceCredentialBranch(policy, '<!-- core-http-credential-inbound-start -->', '<!-- core-http-credential-inbound-end -->', '__BACKEND_AUTHENTICATION_XML__'), '<!-- core-http-credential-outbound-start -->', '<!-- core-http-credential-outbound-end -->', '')
func baseApiPolicy(api object, policyTemplate string, backendAuthentication string, backendHeaders string) string => replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(selectHttpCredentialPolicy(api, policyTemplate),
  '__AUTHORIZATION_BASE64__', base64(string(api.authorization))),
  '__WEBSOCKET_SETTINGS_BASE64__', api.apiType == 'websocket' ? base64(string(api.websocket)) : ''),
  '__KEY_HEADER__', api.subscriptionKeyHeader),
  '__BACKEND_ID__', '${api.name}-backend'),
  '__DEFAULT_BACKEND_XML__', empty(operationBackendSelection(api)) ? '<set-backend-service backend-id="${api.name}-backend" />' : ''),
  '__BACKEND_AUDIENCE__', '{{${api.name}-backend-audience}}'),
  '__CREDENTIAL_CONNECTION_BASE64__', contains(api, 'credentialConnection') ? base64(string(api.credentialConnection)) : ''),
  '__BACKEND_AUTHENTICATION_XML__', backendAuthentication),
  '__BACKEND_HEADERS_XML__', backendHeaders),
  '__HTTP_VERSION__', '1'),
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
// HTTP/MCPは従来のbackend entity参照をそのまま使います。
var protocolRateCleanupXml = [for api in apiDefinitions: api.apiType == 'mcp' ? mcpRateCleanupXml
  : api.apiType == 'websocket' && contains(api.?websocket ?? {}, 'realtimeObserver')
    ? '<set-variable name="gatewayRateLimitExempt" value="@(true)" />'
    : '<set-variable name="gatewayRateLimitExempt" value="@(false)" />']
// MCPの予約済みメタデータパスを下流へ転送せず、通常の404を返します。
var mcpMetadataGuardXml = '<choose><when condition="@(context.Request.OriginalUrl.Path.IndexOf(&quot;/.well-known/oauth-protected-resource&quot;, StringComparison.OrdinalIgnoreCase) &gt;= 0)"><return-response><set-status code="404" reason="Not Found" /><set-header name="apim-request-id" exists-action="override"><value>@(context.RequestId.ToString())</value></set-header></return-response></when></choose>'
// Keep only small per-API substitutions in variables. Storing every final XML
// here and again in resources/source would exceed ARM's expanded size budget.
var policySettings = [for (api, index) in apiDefinitions: {
  authentication: backendAuthenticationXml[index]
  headers: backendHeadersXml[index]
  cleanup: protocolRateCleanupXml[index]
  queries: protocolQueriesXml[index]
  websocketQuery: api.apiType == 'websocket' && (contains(api.websocket, 'queryParameters') || contains(api.websocket, 'allowedModels')) ? base64(string(api.websocket.?queryParameters ?? {})) : ''
  observer: observerPoliciesXml[index]
  mcpRoute: mcpProxyRoutingXml[index]
  metadataGuard: api.apiType == 'mcp' ? mcpMetadataGuardXml : ''
  credentialInbound: api.apiType == 'mcp' && contains(api, 'credentialConnection') ? '<set-variable name="gatewayCredentialConnection" value="${base64(string(api.credentialConnection))}" /><include-fragment fragment-id="core-credential-connection" /><include-fragment fragment-id="core-credential-session-use" />' : backendAuthenticationXml[index]
  credentialOutbound: api.apiType == 'mcp' && contains(api, 'credentialConnection') ? '<include-fragment fragment-id="core-credential-connection-outbound" />' : ''
}]
func apiPolicyValue(api object, template string, errorXml string, values object) string =>
  replace(replace(replace(replace(replace(replace(replace(selectBackendPolicy(api,
    replace(replace(replace(replace(baseApiPolicy(api, template, values.authentication, values.headers),
      '__RATE_LIMIT_ERROR_XML__', errorXml), '__RATE_LIMIT_CLEANUP_XML__', values.cleanup),
      '__AUDIT_SETTINGS_BASE64__', base64(string(auditSettings(api, '', false)))),
      '__CONTROL_SETTINGS_BASE64__', base64(string(controlSettings(api, '', false))))),
    '__PROTOCOL_QUERY_XML__', values.queries), '__WEBSOCKET_QUERY_BASE64__', values.websocketQuery),
    '__PROTOCOL_SESSION_XML__', values.observer), '__MCP_PROXY_ROUTE_XML__', values.mcpRoute),
    '__MCP_PRM_GUARD_XML__', values.metadataGuard), '__CREDENTIAL_CONNECTION_INBOUND_XML__', values.credentialInbound),
    '__CREDENTIAL_CONNECTION_OUTBOUND_XML__', values.credentialOutbound)
// プロトコルごとの API と操作を分類し、同じ定義から Product・認可・公開操作の対応を作ります。
// HTTPの抽出結果を別の配列変数にも保存せず、操作と接続先の集約時に同じ条件で抽出します。
// API定義の重複を配備テンプレートから減らし、公開する操作・接続先は変えません。
var operationEntries = flatten(map(filter(apiDefinitions, api => api.apiType == 'http'), api => map(api.operations, operation => { apiName: api.name, backendUrl: contains(operation, 'backendOverride') ? operation.backendOverride.backendUrl : api.backendUrl, operation: operation, constraint: api.authorization.operationConstraints[operation.name] })))
var operationBackendEntries = flatten(map(filter(apiDefinitions, api => api.apiType == 'http'), api => map(filter(api.operations, operation => contains(operation, 'backendOverride')), operation => { apiName: api.name, operationName: operation.name, backend: operation.backendOverride })))
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
// 最終XMLを配列変数にも複製せず、配備先と生成元へ同じ内容を直接渡します。
func mcpApiPolicyValue(api object, template string, errorXml string) string => replace(replace(replace(replace(template, '__AUTHORIZATION_BASE64__', base64(string(api.authorization))), '__RATE_LIMIT_ERROR_XML__', errorXml), '__AUDIT_SETTINGS_BASE64__', base64(string(auditSettings(api, api.mcp.operationNames[0], true)))), '__CONTROL_SETTINGS_BASE64__', base64(string(controlSettings(api, api.mcp.operationNames[0], true))))

// Share individual fragment XML with protected outputs without another full-XML object.
var rateLimitFragmentXml = replace(loadTextContent('policies/rate-limit.xml'), '__RATE_LIMITS_BASE64__', base64(string(rateLimits)))
var tokenLimitFragmentXml = loadTextContent('policies/token-limit.xml')
var entraUserProfileFragmentXml = loadTextContent('policies/entra-user-profile.xml')
var auditContextFragmentXml = loadTextContent('policies/audit-context.xml')
var auditBodyModelFragmentXml = loadTextContent('policies/audit-body-model.xml')
var backendAuditFragmentXml = loadTextContent('policies/backend-audit.xml')
// 入口確認に失敗した要求の監査は、従来の共通拒否監査と同じ内容を展開します。
// 認証前なのでAPIのdecisionを参照する区間だけを除き、fragmentの入れ子は作りません。
// 中間のXML全文を変数へ保存せず、ARM展開後の重複した容量を増やしません。
func preAuthenticationTelemetry(telemetry string) string => '${split(telemetry, ' /* core-user-audit-start */')[0]}${split(telemetry, ' /* core-user-audit-end */')[1]}'
// 同名CSの固定5区画を同名XMLだけへ差し込みます。BEGIN/ENDは生成用で、APIMへは本文だけを渡します。
// 区画名・重複・閉じ忘れはPythonの配備前検査でも拒否し、Bicepと生成元の完成XMLを照合します。
func authenticationSection(source string, name string) string => split(split(replace(source, '\r\n', '\n'), '// BEGIN ${name}\n')[1], '\n// END ${name}')[0]
func authenticationExpressionsValue(template string, source string) string => replace(replace(replace(replace(replace(template,
  '__CORE_COMMON_AUTHENTICATION_KEY_REQUEST_VALIDATION_CSHARP__', auditSourceXml(authenticationSection(source, 'key-request-validation'))),
  '__CORE_COMMON_AUTHENTICATION_BEARER_REQUEST_VALIDATION_CSHARP__', auditSourceXml(authenticationSection(source, 'bearer-request-validation'))),
  '__CORE_COMMON_AUTHENTICATION_ENTRA_IDENTITY_CSHARP__', auditSourceXml(authenticationSection(source, 'entra-identity'))),
  '__CORE_COMMON_AUTHENTICATION_ENTRA_AUTHENTICATION_RESULT_CSHARP__', auditSourceXml(authenticationSection(source, 'entra-authentication-result'))),
  '__CORE_COMMON_AUTHENTICATION_ENTRA_FAILURE_SUBJECT_CSHARP__', auditSourceXml(authenticationSection(source, 'entra-failure-subject')))
func authenticationBranchValue(template string, source string, enabled bool) string => replace(enabled
  ? authenticationExpressionsValue(template, source)
  : replaceCredentialBranch(authenticationExpressionsValue(template, source), '<!-- core-entra-branch-start -->', '<!-- core-entra-branch-end -->', ''),
  '__ENTRA_OPEN_PRODUCT_ID__', 'core-entra-model-users')
// 共通の監査ソースを生成時に展開します。フラグメント同士を実行時に呼ぶ入れ子は作りません。
func authenticationTelemetryBody(template string, source string) string => preAuthenticationTelemetry(replace(replace(gatewayTelemetryFragmentValue(template, source, true), '<fragment>', ''), '</fragment>', ''))
func commonAuthenticationFragmentValue(template string, source string, enabled bool, telemetryTemplate string, telemetrySource string) string => replace(replace(
  authenticationBranchValue(template, source, enabled), '__AUTHENTICATION_TELEMETRY_XML__', authenticationTelemetryBody(telemetryTemplate, telemetrySource)),
  '__ENTRA_FAILURE_TELEMETRY_XML__', replace(authenticationTelemetryBody(telemetryTemplate, telemetrySource), '&quot;gatewayAuthenticatedSubject&quot;', '&quot;gatewayEntraFailureSubject&quot;'))
func entraAuthenticationErrorFragmentValue(template string, telemetryTemplate string, telemetrySource string) string => replace(template, '__AUTHENTICATION_TELEMETRY_XML__', authenticationTelemetryBody(telemetryTemplate, telemetrySource))
// 配備先と生成元に同じXMLを渡し、展開済みの全文を中間変数へ重複保存しません。
func trustedIngressFragmentValue(template string, conditionSource string, telemetryTemplate string, telemetrySource string) string => replace(replace(replace(trustedIngressConditionValue(template, conditionSource), '__AUTHENTICATION_TELEMETRY_XML__', preAuthenticationTelemetry(replace(replace(gatewayTelemetryFragmentValue(telemetryTemplate, telemetrySource, false), '<fragment>', ''), '</fragment>', ''))), '__L7_INGRESS_REQUIRED__', '&quot;{{core-l7-ingress-enabled}}&quot; == &quot;true&quot;'), '__L7_INGRESS_EXPECTED__', '{{core-l7-ingress-secret}}')
var queryGuardFragmentXml = loadTextContent('policies/query-guard.xml')
var queryTelemetryFragmentXml = replace(loadTextContent('policies/query-telemetry.xml'), '__AUDIT_SUBJECT_OBSERVATION_CSHARP__', auditSubjectObservationCompactXml)
var modelLimitsFragmentXml = loadTextContent('policies/model-limits.xml')

// All APIsの共通ポリシーが参照する入口確認と監査のleaf部品。fragment同士の入れ子は作りません。
resource requestContextFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-request-context'
  properties: { format: 'rawxml', description: '共通入口で監査用の要求情報を準備する。本文や資格情報を記録しない。', value: requestContextFragmentValue(requestContextTemplate, requestContextSource) }
}
resource trustedIngressFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-trusted-ingress'
  properties: { format: 'rawxml', description: 'AppGWのHTTP入口を確認し、不正な経路を監査して403で拒否する。', value: trustedIngressFragmentValue(trustedIngressTemplate, trustedIngressSource, gatewayTelemetryTemplate, gatewayTelemetrySource) }
}
resource browserExceptionFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-credential-browser-exception'
  properties: { format: 'rawxml', description: '接続ログインの開始とログイン後の戻り先の受付だけ、共通認証の限定例外として判定する。取引と本人の確認は後続処理が担当する。', value: browserExceptionFragmentValue(browserExceptionTemplate, browserExceptionSource, credentialLoginEnabled) }
}
resource commonAuthenticationFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-common-authentication'
  properties: { format: 'rawxml', description: '共通入口でキーまたはBearerを検証し、本人・会社設定・内部処理の失敗を区別して監査する。', value: commonAuthenticationFragmentValue(commonAuthenticationTemplate, commonAuthenticationSource, entraAuthentication.?enabled ?? false, gatewayTelemetryTemplate, gatewayTelemetrySource) }
}
resource entraAuthenticationErrorFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = if (entraAuthentication.?enabled ?? false) {
  parent: apim
  name: 'core-entra-authentication-error'
  properties: { format: 'rawxml', description: '標準Entraトークン検証の失敗を監査し、要求IDとBearer認証の案内を付けて401で終了する。', value: entraAuthenticationErrorFragmentValue(entraAuthenticationErrorTemplate, gatewayTelemetryTemplate, gatewayTelemetrySource) }
}
resource authenticatedSubjectFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-audit-authenticated-subject'
  properties: { format: 'rawxml', description: '共通認証後に確認済みの購読または社員の識別情報を監査用に整理する。', value: authenticatedSubjectFragmentValue(authenticatedSubjectTemplate, authenticatedSubjectSource) }
}

// 本人/共有キー/サービスの中央設定を全APIで共有し、同じ枠へ異なる上限を適用しません。
resource rateLimitFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-rate-limit'
  properties: { format: 'rawxml', description: 'Subscription-scoped request limits without response-body buffering.', value: rateLimitFragmentXml }
}
// 本文を扱うnative token policyを、HTTPの対応APIだけから参照します。
resource tokenLimitFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-token-limits'
  properties: { format: 'rawxml', description: 'Optional native LLM token limits for supported HTTP operations only.', value: tokenLimitFragmentXml }
}
// The API-inlined subscription profile includes this standalone fragment;
// keep employee-only validation once instead of duplicating every API XML.
resource entraUserProfileFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-entra-user-profile'
  properties: { format: 'rawxml', description: 'Verified employee request/token limits; no subscription lookup or nested fragments.', value: entraUserProfileFragmentXml }
}

// 追跡候補の検査と下流IDの退避。fragment内で本文やtokenをログへ出しません。
resource auditContextFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-audit-context'
  properties: { format: 'rawxml', description: 'Agent-claimed trace candidates and authorized model metadata; never authorization or ownership.', value: auditContextFragmentXml }
}
// 本文の受信完了を前提にするHTTPモデル監査だけを独立させます。
// WebSocket/MCPからは参照せず、未実行分岐の本文読取りによるbufferも防ぎます。
resource auditBodyModelFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-audit-body-model'
  properties: { format: 'rawxml', description: 'HTTP-only authorized model-name extraction; never used by native protocol policies.', value: auditBodyModelFragmentXml }
}
resource backendAuditFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-backend-audit'
  properties: { format: 'rawxml', description: 'Capture allowlisted backend response IDs before global header rewriting; no body reads.', value: backendAuditFragmentXml }
}

// 各ポリシーから共通利用する最小限の監査情報出力。本文や認証値は記録しません。
resource gatewayTelemetryFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-gateway-telemetry'
  properties: { format: 'rawxml', description: '要求の追跡情報と認証・利用許可の結果を監査ログへ出力する。本文や資格情報を記録しない。', value: gatewayTelemetryFragmentValue(gatewayTelemetryTemplate, gatewayTelemetrySource, false) }
}
// 固定入口のquery概要と判定だけを扱い、本文やquery名・値を保持しません。
resource queryGuardFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-query-guard'
  properties: { format: 'rawxml', description: 'Fixed-entry query presence and rejection decisions without body reads.', value: queryGuardFragmentXml }
}
resource queryTelemetryFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-query-telemetry'
  properties: { format: 'rawxml', description: 'Fixed query summaries with authenticated subject and request context; no query names or values.', value: queryTelemetryFragmentXml }
}
// 要求サイズと出力トークン上限を検査する共通部品です。
resource modelLimitsFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-model-limits'
  properties: { format: 'rawxml', description: 'Validate received request sizes and provider input limits without requiring optional output tokens.', value: modelLimitsFragmentXml }
}
// 本人の登録済み OAuth 接続を解決する共通部品です。
resource credentialConnectionFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-credential-connection'
  properties: { format: 'rawxml', description: 'Resolve personal sessions and return Microsoft sign-in guidance when authentication is required.', value: replace(renderCredentialLogin(replace(credentialConnectionTemplate, '__CREDENTIAL_CHALLENGE_XML__', credentialChallengeXml), credentialSettings), '__GATEWAY_TELEMETRY_XML__', credentialGatewayTelemetryBody) }
}
// 本人照合後の業務利用確定と認証案内。上のfragmentと同階層で呼び出します。
resource credentialSessionUseFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-credential-session-use'
  properties: { format: 'rawxml', description: 'Finalize verified business use and relay session updates without extending authentication transactions.', value: replace(renderCredentialLogin(replace(credentialSessionUseTemplate, '__CREDENTIAL_CHALLENGE_XML__', credentialChallengeXml), credentialSettings), '__GATEWAY_TELEMETRY_XML__', credentialGatewayTelemetryBody) }
}
// 外部資格情報が失効した場合に、安全な再認証案内へ変換する共通部品です。
resource credentialConnectionOutboundFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-credential-connection-outbound'
  properties: { format: 'rawxml', description: 'Start personal reauthorization for revoked downstream credentials and return safe guidance.', value: replace(renderCredentialLogin(replace(observationFragmentValue(credentialConnectionOutboundTemplate, auditObservationSource, true), '__CREDENTIAL_CHALLENGE_XML__', credentialChallengeXml), credentialSettings), '__GATEWAY_TELEMETRY_XML__', credentialGatewayTelemetryBody) }
}

// Realtime の補助 WebSocket 接続で所有者チケットを照合する共通部品です。
resource realtimeObserverFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = if (!empty(observerApis)) {
  parent: apim
  name: 'core-realtime-observer'
  properties: { format: 'rawxml', description: 'Validate the signed session owner before a Realtime sideband handshake; relay receive-only mode is separate.', value: realtimeObserverTemplate }
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
// HTTP / WebSocket / MCP の API 本体。認証方式と公開パスを定義から反映します。
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
    protocols: api.apiType == 'websocket' ? ['ws', 'wss'] : ['http', 'https']
    // Native key state/scope and Product policies remain active. The no-key Entra
    // route is opened only by controller-managed membership in the fixed open Product.
    subscriptionRequired: true
    subscriptionKeyParameterNames: { header: api.subscriptionKeyHeader, query: 'subscription-key' }
    type: api.apiType
  }, api.apiType == 'websocket' ? { serviceUrl: api.backendUrl }
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
// No associations are declared here: controller adds them LAST and removes them FIRST.
var entraOpenProductId = 'core-entra-model-users'
var entraOpenProductPolicy = '<policies><inbound><base /></inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>'
resource entraOpenProduct 'Microsoft.ApiManagement/service/products@2024-05-01' = if (entraAuthentication.?enabled ?? false) {
  parent: apim
  name: entraOpenProductId
  properties: {
    displayName: 'Core Entra model users'
    description: 'core-owned-entra-model-users-v1'
    subscriptionRequired: false
    // APIM rejects approvalRequired in the payload of a subscription-free Product.
    state: 'notPublished'
  }
}
// notPublished controls portal visibility; absence of membership is the activation gate.
resource entraOpenProductPolicyResource 'Microsoft.ApiManagement/service/products/policies@2024-05-01' = if (entraAuthentication.?enabled ?? false) {
  name: '${apim.name}/${entraOpenProductId}/policy'
  properties: { format: 'rawxml', value: entraOpenProductPolicy }
  dependsOn: [entraOpenProduct]
}

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
  properties: { format: 'rawxml', value: apiPolicyValue(api, configuredApiTemplate(api.apiType == 'http', gatewayPolicyTemplate, protocolPolicyTemplate, subscriptionProfileXml, queryGuards), rateLimitErrorXml, policySettings[index]) }
  dependsOn: [auditContextFragment, auditBodyModelFragment, backendAuditFragment, rateLimitFragment, tokenLimitFragment, entraUserProfileFragment, backends, operationBackends, backendAudienceNamedValues, gatewayTelemetryFragment, queryGuardFragment, queryTelemetryFragment, modelLimitsFragment, credentialConnectionFragment, credentialSessionUseFragment, credentialConnectionOutboundFragment, backendSecretNamedValues, operationBackendSecretNamedValues, realtimeObserverFragment]
}]
// WebSocket API では API スコープの policy を作れないため、システムが自動生成する
// onHandshake 操作へ同じ接続開始時の認証・認可を設定します。操作本体は作成・変更しません。
// <base/> による全体の公開ガード継承も維持し、確立後のフレームはここで検査しません。
// https://learn.microsoft.com/azure/api-management/websocket-api
// HTTP等のAPIポリシーが終わってから、握手ポリシーも１件ずつ適用します。
@batchSize(1)
resource websocketPolicies 'Microsoft.ApiManagement/service/apis/operations/policies@2024-05-01' = [for (api, index) in apiDefinitions: if (api.apiType == 'websocket') {
  name: '${apim.name}/${api.name}/onHandshake/policy'
  properties: { format: 'rawxml', value: apiPolicyValue(api, configuredApiTemplate(api.apiType == 'http', gatewayPolicyTemplate, protocolPolicyTemplate, subscriptionProfileXml, queryGuards), rateLimitErrorXml, policySettings[index]) }
  dependsOn: [apiPolicies, auditContextFragment, backendAuditFragment, rateLimitFragment, entraUserProfileFragment, apis, backends, backendAudienceNamedValues, gatewayTelemetryFragment, backendSecretNamedValues, realtimeObserverFragment]
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
func operationProperties(entry object) object => {
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
resource operations 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = [for entry in operationEntries: {
  name: '${apim.name}/${entry.apiName}/${entry.operation.name}'
  properties: operationProperties(entry)
  dependsOn: [apis, piiSchemas, documentSchemas, apiPolicies]
}]
// 操作固有の制約を追加。モデル・PII・Realtime 等の用途別検証を切り替えます。
// APIと握手の設定後に１件ずつ書き、別ループの重いポリシー適用とも重ねません。
func operationPolicyValue(entry object, policyTemplate string, ssmlGuard string) string => entry.constraint.kind == 'pii'
      ? replace(policyTemplate, '__PII_SETTINGS_BASE64__', base64(string(entry.constraint)))
      : entry.constraint.kind == 'documentAnalysis' ? replace(replace(policyTemplate, '__DOCUMENT_SETTINGS_BASE64__', base64(string(entry.constraint))), '__DOCUMENT_BASE_URL__', xmlText(entry.backendUrl))
      : entry.constraint.kind == 'documentAnalysisStatus' ? replace(replace(policyTemplate, '__DOCUMENT_SETTINGS_BASE64__', base64(string(entry.constraint))), '__DOCUMENT_BASE_URL__', xmlText(entry.backendUrl))
      // 短期 token の取得先は、登録済み Foundry backend から固定生成します。
      // 利用者の入力 URL や公開 params に取得先を追加しません。
      : entry.constraint.kind == 'realtimeCreate' ? replace(policyTemplate, '__REALTIME_CLIENT_SECRETS_URL__', xmlText('${entry.backendUrl}/realtime/client_secrets'))
      : entry.constraint.kind == 'realtimeHangup' ? policyTemplate
      : entry.constraint.kind == 'liveCreate' ? policyTemplate
      : join(['<policies><!-- APIの認証・権限・入力上限を継承し、このOperationの要求形式と上流URIを適用します。 --><inbound>'
          '<base />'
          // Authenticate and enforce received bytes before scanning large XML.
          requiresSpeechSsmlGuard(entry) ? ssmlGuard : ''
          // The shared fragment checks media type and received bytes. validate-content
          // has a separate 100 KiB policy ceiling and cannot enforce large uploads.
          contains(entry.operation, 'staticResponse') ? '<return-response><set-status code="200" reason="OK" /><set-header name="apim-request-id" exists-action="override"><value>@(context.RequestId.ToString())</value></set-header><set-header name="Content-Type" exists-action="override"><value>application/json</value></set-header><set-body>${xmlText(string(entry.operation.staticResponse.body))}</set-body></return-response>' : ''
          profileRoutingPolicy(entry.constraint)
          contains(entry.operation, 'backendUrlTemplate') ? '<!-- 登録済みの上流URIへ書き換えます。転送queryは定義した値に揃えます。 --><rewrite-uri template="${xmlText(entry.operation.backendUrlTemplate)}" copy-unmatched-params="false" />' : ''
          '</inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>'], '')
// Pass the selected template directly; retaining another object of all full XML
// templates would count the same bytes again in ARM's expanded variables.
func selectOperationTemplate(kind string, pii string, document string, documentStatus string, realtimeCreate string, realtimeHangup string, liveCreate string) string => kind == 'pii' ? pii : kind == 'documentAnalysis' ? document : kind == 'documentAnalysisStatus' ? documentStatus : kind == 'realtimeCreate' ? realtimeCreate : kind == 'realtimeHangup' ? realtimeHangup : kind == 'liveCreate' ? liveCreate : ''

@batchSize(1)
resource operationPolicies 'Microsoft.ApiManagement/service/apis/operations/policies@2024-05-01' = [for (entry, index) in operationEntries: {
  parent: operations[index]
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: operationPolicyValue(entry, selectOperationTemplate(entry.constraint.kind, configuredOperationTemplate(piiPolicyTemplate, rateLimitErrorXml), configuredOperationTemplate(documentAnalysisPolicyTemplate, rateLimitErrorXml), configuredOperationTemplate(documentAnalysisStatusPolicyTemplate, rateLimitErrorXml), realtimeCreatePolicyTemplate, realtimeHangupPolicyTemplate, liveCreatePolicyTemplate), speechSsmlGuardPolicy)
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
  properties: { format: 'rawxml', value: mcpApiPolicyValue(api, configuredMcpTemplate(mcpPolicyTemplate, queryGuards), rateLimitErrorXml) }
  dependsOn: [auditContextFragment, backendAuditFragment, gatewayTelemetryFragment, queryGuardFragment, queryTelemetryFragment, mcpTools]
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

// Actual source XML and local identifiers must stay in protected .local output.
// Use exact resource assignment expressions, never reference() runtime values.
// Source v2 represents every owned business protocol, REST-backed MCP tool and
// protected connection operation. Browser callback transactions remain separate.
// Import-only properties are retained for strict readback; unrepresented imported
// operations/schemas are refused rather than accepted from a runtime inventory.
func sourceApiProperties(api object) object => union({
  type: api.apiType
  path: api.path
  protocols: api.apiType == 'websocket' ? ['ws', 'wss'] : ['http', 'https']
  subscriptionRequired: true
  subscriptionKeyParameterNames: { header: api.subscriptionKeyHeader, query: 'subscription-key' }
}, api.apiType == 'websocket' ? { serviceUrl: api.backendUrl }
  : api.apiType == 'mcp' ? {
      backendId: '${api.name}-backend'
      mcpProperties: {
        transportType: api.mcpProxy.transport
        endpoints: toObject(api.mcpProxy.endpoints,
          endpoint => endpoint.name == 'sse' ? 'sse' : api.mcpProxy.transport == 'sse' ? 'messages' : 'mcp',
          endpoint => union({ uriTemplate: api.mcpProxy.transport == 'sse' ? endpoint.name == 'sse' ? '/sse' : endpoint.uriTemplate : '/mcp' }, api.mcpProxy.transport == 'sse' ? { method: endpoint.name == 'sse' ? 'GET' : 'POST' } : {}))
      }
    } : {})
output entraPublicationSource object = {
  schemaVersion: 2
  apis: concat(
    map(apiDefinitions, api => {
      name: api.name
      operations: api.apiType == 'websocket' ? ['onHandshake'] : api.apiType == 'http' ? map(api.operations, operation => operation.name) : []
      products: union([api.productId], api.?released ?? false ? [releasedProductId] : [])
      properties: sourceApiProperties(api)
    }),
    map(mcpApis, api => {
      name: api.mcp.name
      operations: []
      products: union([api.productId], api.?released ?? false ? [releasedProductId] : [])
      properties: { type: 'mcp', path: api.mcp.path, protocols: ['http', 'https'], subscriptionRequired: true, subscriptionKeyParameterNames: { header: api.subscriptionKeyHeader, query: 'subscription-key' } }
    }),
    credentialLoginEnabled ? [{
      name: 'credential-manager-login'
      operations: map(filter(credentialLoginOperations, item => item.apiId == 'credential-manager-login'), item => item.name)
      products: productIds
      properties: { type: 'http', path: 'auth/connections', protocols: ['http', 'https'], subscriptionRequired: true, subscriptionKeyParameterNames: { header: credentialLogin.?keyHeader ?? 'api-key', query: 'subscription-key' } }
    }] : []
  )
  policies: concat(
    map(apiDefinitions, api => { suffix: api.apiType == 'websocket' ? '/apis/${api.name}/operations/onHandshake/policies/policy' : '/apis/${api.name}/policies/policy', value: apiPolicyValue(api, configuredApiTemplate(api.apiType == 'http', gatewayPolicyTemplate, protocolPolicyTemplate, subscriptionProfileXml, queryGuards), rateLimitErrorXml, policySettings[indexOf(apiDefinitions, api)]) }),
    map(operationEntries, entry => { suffix: '/apis/${entry.apiName}/operations/${entry.operation.name}/policies/policy', value: operationPolicyValue(entry, selectOperationTemplate(entry.constraint.kind, configuredOperationTemplate(piiPolicyTemplate, rateLimitErrorXml), configuredOperationTemplate(documentAnalysisPolicyTemplate, rateLimitErrorXml), configuredOperationTemplate(documentAnalysisStatusPolicyTemplate, rateLimitErrorXml), realtimeCreatePolicyTemplate, realtimeHangupPolicyTemplate, liveCreatePolicyTemplate), speechSsmlGuardPolicy) }),
    map(mcpApis, api => { suffix: '/apis/${api.mcp.name}/policies/policy', value: mcpApiPolicyValue(api, configuredMcpTemplate(mcpPolicyTemplate, queryGuards), rateLimitErrorXml) }),
    credentialLoginEnabled ? concat(
      [{ suffix: '/apis/credential-manager-login/policies/policy', value: credentialLoginApiPolicyXml }],
      map(filter(credentialLoginOperations, item => item.apiId == 'credential-manager-login'), item => {
        suffix: '/apis/credential-manager-login/operations/${item.name}/policies/policy'
        value: renderCredentialLogin(item.name == 'start' ? credentialLoginStartPolicyTemplate : item.name == 'status' ? credentialLoginStatusPolicyTemplate : item.name == 'logout' ? credentialLoginLogoutPolicyTemplate : item.name == 'session-commit' ? credentialLoginSessionCommitPolicyTemplate : item.name == 'logout-all' ? credentialLoginLogoutAllPolicyTemplate : credentialLoginDisconnectPolicyTemplate, credentialSettings)
      })
    ) : [],
    [
      { suffix: '/policyFragments/core-request-context', value: requestContextFragmentValue(requestContextTemplate, requestContextSource) }
      { suffix: '/policyFragments/core-trusted-ingress', value: trustedIngressFragmentValue(trustedIngressTemplate, trustedIngressSource, gatewayTelemetryTemplate, gatewayTelemetrySource) }
      { suffix: '/policyFragments/core-credential-browser-exception', value: browserExceptionFragmentValue(browserExceptionTemplate, browserExceptionSource, credentialLoginEnabled) }
      { suffix: '/policyFragments/core-common-authentication', value: commonAuthenticationFragmentValue(commonAuthenticationTemplate, commonAuthenticationSource, entraAuthentication.?enabled ?? false, gatewayTelemetryTemplate, gatewayTelemetrySource) }
      { suffix: '/policyFragments/core-audit-authenticated-subject', value: authenticatedSubjectFragmentValue(authenticatedSubjectTemplate, authenticatedSubjectSource) }
      { suffix: '/policyFragments/core-rate-limit', value: rateLimitFragmentXml }
      { suffix: '/policyFragments/core-token-limits', value: tokenLimitFragmentXml }
      { suffix: '/policyFragments/core-entra-user-profile', value: entraUserProfileFragmentXml }
      { suffix: '/policyFragments/core-audit-context', value: auditContextFragmentXml }
      { suffix: '/policyFragments/core-audit-body-model', value: auditBodyModelFragmentXml }
      { suffix: '/policyFragments/core-backend-audit', value: backendAuditFragmentXml }
      { suffix: '/policyFragments/core-gateway-telemetry', value: gatewayTelemetryFragmentValue(gatewayTelemetryTemplate, gatewayTelemetrySource, false) }
      { suffix: '/policyFragments/core-query-guard', value: queryGuardFragmentXml }
      { suffix: '/policyFragments/core-query-telemetry', value: queryTelemetryFragmentXml }
      { suffix: '/policyFragments/core-model-limits', value: modelLimitsFragmentXml }
      { suffix: '/policyFragments/core-credential-connection', value: replace(renderCredentialLogin(replace(credentialConnectionTemplate, '__CREDENTIAL_CHALLENGE_XML__', credentialChallengeXml), credentialSettings), '__GATEWAY_TELEMETRY_XML__', credentialGatewayTelemetryBody) }
      { suffix: '/policyFragments/core-credential-session-use', value: replace(renderCredentialLogin(replace(credentialSessionUseTemplate, '__CREDENTIAL_CHALLENGE_XML__', credentialChallengeXml), credentialSettings), '__GATEWAY_TELEMETRY_XML__', credentialGatewayTelemetryBody) }
      { suffix: '/policyFragments/core-credential-connection-outbound', value: replace(renderCredentialLogin(replace(observationFragmentValue(credentialConnectionOutboundTemplate, auditObservationSource, true), '__CREDENTIAL_CHALLENGE_XML__', credentialChallengeXml), credentialSettings), '__GATEWAY_TELEMETRY_XML__', credentialGatewayTelemetryBody) }
    ],
    !empty(observerApis) ? [{ suffix: '/policyFragments/core-realtime-observer', value: realtimeObserverTemplate }] : [],
    (entraAuthentication.?enabled ?? false) ? [{ suffix: '/policyFragments/core-entra-authentication-error', value: entraAuthenticationErrorFragmentValue(entraAuthenticationErrorTemplate, gatewayTelemetryTemplate, gatewayTelemetrySource) }] : []
  )
  backends: concat(
    map(apiDefinitions, api => { suffix: '/backends/${api.name}-backend', properties: { title: api.displayName, protocol: 'http', url: api.apiType == 'websocket' ? replace(api.backendUrl, 'wss://', 'https://') : api.backendUrl, tls: { validateCertificateChain: true, validateCertificateName: true } } }),
    map(operationBackendEntries, entry => { suffix: '/backends/${operationBackendId(entry.apiName, entry.operationName)}', properties: { title: '${entry.apiName} / ${entry.operationName}', protocol: 'http', url: entry.backend.backendUrl, tls: { validateCertificateChain: true, validateCertificateName: true } } })
  )
  namedValues: concat(
    map(apiDefinitions, api => { suffix: '/namedValues/${api.name}-backend-audience', properties: { displayName: '${api.name}-backend-audience', secret: false, value: api.backendAudience } }),
    map(filter(apiDefinitions, api => api.backendAuthentication.mode == 'keyVaultHeader'), api => { suffix: '/namedValues/${api.name}-backend-secret', properties: { displayName: '${api.name}-backend-secret', secret: true, keyVault: { secretIdentifier: api.backendAuthentication.secretIdentifier } } }),
    map(filter(operationBackendEntries, entry => entry.backend.backendAuthentication.mode == 'keyVaultHeader'), entry => { suffix: '/namedValues/${operationBackendId(entry.apiName, entry.operationName)}-secret', properties: { displayName: '${operationBackendId(entry.apiName, entry.operationName)}-secret', secret: true, keyVault: { secretIdentifier: entry.backend.backendAuthentication.secretIdentifier } } })
  )
  apiChildren: concat(
    map(operationEntries, entry => { suffix: '/apis/${entry.apiName}/operations/${entry.operation.name}', properties: operationProperties(entry) }),
    map(piiApis, api => { suffix: '/apis/${api.name}/schemas/pii-public-contract', properties: { contentType: 'application/vnd.oai.openapi.components+json', document: { components: loadJsonContent('schemas/pii.json') } } }),
    map(documentApis, api => { suffix: '/apis/${api.name}/schemas/document-analysis-public-contract', properties: { contentType: 'application/vnd.oai.openapi.components+json', document: { components: loadJsonContent('schemas/document-analysis.json') } } }),
    map(mcpApis, api => { suffix: '/apis/${api.mcp.name}/tools/${api.mcp.operationNames[0]}', properties: { displayName: api.mcp.operationNames[0], description: 'Mask one Japanese or English text. Returns only redacted text and safe metadata.', operationId: '${apim.id}/apis/${api.name}/operations/${api.mcp.operationNames[0]}' } }),
    credentialLoginEnabled ? map(filter(credentialLoginOperations, item => item.apiId == 'credential-manager-login'), item => { suffix: '/apis/credential-manager-login/operations/${item.name}', properties: { displayName: item.name, method: item.method, urlTemplate: '/${item.path}', responses: [] } }) : []
  )
}

// 戻り先APIは社員Productへ追加しません。別の生成元として、取引認証のXMLも照合します。
output credentialBrowserPublicationSource object = credentialLoginEnabled ? {
  schemaVersion: 1
  name: 'credential-manager-browser'
  properties: { type: 'http', path: 'auth/connections/browser', protocols: ['http', 'https'], subscriptionRequired: false }
  apiPolicyAbsent: true
  operations: map(filter(credentialLoginOperations, item => item.apiId == 'credential-manager-browser'), item => {
    name: item.name
    properties: { displayName: item.name, method: item.method, urlTemplate: '/${item.path}', responses: [] }
  })
  policies: map(filter(credentialLoginOperations, item => item.apiId == 'credential-manager-browser'), item => {
    suffix: '/apis/credential-manager-browser/operations/${item.name}/policies/policy'
    value: renderCredentialLogin(item.name == 'launch' ? credentialLoginLaunchPolicyTemplate : item.name == 'launch-post' ? credentialLoginLaunchPostPolicyTemplate : item.name == 'identity-callback' ? credentialLoginIdentityCallbackPolicyTemplate : credentialLoginCallbackPolicyTemplate, credentialSettings)
  })
} : {}

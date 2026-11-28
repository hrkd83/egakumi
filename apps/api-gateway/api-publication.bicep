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
@description('Graph /meのBearer経路に、準備済みの専用OBO署名鍵を使います。')
param graphOboEnabled bool = false
@description('基盤と各APIで照合済みの購読キーヘッダー名。診断の秘密マスクにも使います。')
param subscriptionKeyHeader string = 'api-key'
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
// C#へ埋め込む公開文字列をJSONの文字列リテラルにし、引用符・改行等をコードへ混入させません。
func csharpStringLiteral(value string) string => substring(string([value]), 1, length(string([value])) - 2)
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
// 共通部品が解決したモデル名から、登録済みの固定URIへ転送します。
// FLUXのflex/proのように、公開Operationを共有するモデルの上流を選択します。
func modelRoutingPolicy(constraint object) string => !contains(constraint, 'modelSettings') ? '' : empty(filter(items(constraint.modelSettings), entry => contains(entry.value, 'backendUrlTemplate'))) ? ''
  : '<!-- 解決済みモデルと同じ固定表から、モデルの上流パスを選びます。 --><choose>${join(map(filter(items(constraint.modelSettings), entry => contains(entry.value, 'backendUrlTemplate')), entry => '<when condition="@((string)JObject.Parse((string)context.Variables[&quot;gatewayOperationSelection&quot;])[&quot;effectiveModel&quot;] == &quot;${xmlText(entry.key)}&quot;)"><rewrite-uri template="${xmlText(entry.value.backendUrlTemplate)}" copy-unmatched-params="false" /></when>'), '')}<otherwise><return-response><set-status code="500" reason="Model route is not configured" /><set-header name="apim-request-id" exists-action="override"><value>@(context.RequestId.ToString())</value></set-header></return-response></otherwise></choose>'
// 元の登録済みURIを配備時に式へ変換し、モデルを含むplaceholderの値を一要素として符号化します。
// APIMが照合した値を使い、固定パス・query・API版はそのまま残します。
func pathModelBackendUriPart(part string) string => !contains(part, '}') ? csharpStringLiteral(part)
  : 'Uri.EscapeDataString((string)context.Request.MatchedParameters[${csharpStringLiteral(first(split(part, '}')))}]) + ${csharpStringLiteral(substring(part, indexOf(part, '}') + 1, length(part) - indexOf(part, '}') - 1))}'
func pathModelBackendUriExpression(template string) string => '@(${join(map(split(template, '{'), part => pathModelBackendUriPart(part)), ' + ')})'
func operationBackendSelection(api object) string => empty(filter(api.operations, operation => contains(operation, 'backendOverride'))) ? ''
  : '<choose>${join(map(filter(api.operations, operation => contains(operation, 'backendOverride')), operation => '<when condition="@(context.Operation.Id == &quot;${xmlText(operation.name)}&quot;)">${operationBackendPolicy(api.name, operation)}</when>'), '')}<otherwise><set-backend-service backend-id="${api.name}-backend" />${backendAuthenticationPolicy(api, '${api.name}-backend-secret')}${backendHeaderPolicy(api)}</otherwise></choose>'
// 既存の音声合成の受付契約だけをSSML検査の対象にします。別のXML操作へは広げません。
// API設定に対象操作IDを渡し、共通のサイズ確認直後にSSML検査を呼び出します。
func requiresSpeechSsmlGuard(entry object) bool => entry.apiName == 'azure-speech-v1' && entry.operation.name == 'tts' && entry.operation.method == 'POST' && entry.operation.urlTemplate == '/synthesize' && entry.constraint.kind == 'registeredOperation' && (entry.constraint.?bodyFormat ?? '') == 'text' && (entry.constraint.?contentTypes ?? []) == ['application/ssml+xml']

// Agentの外側endpointと内側MI登録を同じ公開設定から結びます。
// 任意のcaller headerを本人/契約へ変換せず、監査の追跡候補だけに使います。
func hostedAgentName(api object) string => length(filter(items(api.authorization.operationConstraints), entry => entry.value.kind == 'hostedAgentPost')) > 0 && contains(api.backendUrl, '/agents/')
  ? first(split(last(split(api.backendUrl, '/agents/')), '/')) : ''
// 初期の数値計測対象は公式手順が明示するOpenAI形式Chat CompletionsのHTTP操作に限定します。
// Anthropicや音声/PII等へ対応を推測して拡大しません。LLMの本文保存は指定しません。
// 監査の照合対象とAPI単位のLLMログは、操作別backendOverrideを含む実効接続先から選びます。
func nativeLlmOperations(api object) array => api.apiType == 'http'
  ? map(filter(api.operations, operation => (contains(operation, 'backendOverride') ? operation.backendOverride.backendService : api.backendService) == 'foundryOpenAi' && endsWith(operation.urlTemplate, '/chat/completions') && api.authorization.operationConstraints[operation.name].kind == 'bodyModel'), operation => operation.name) : []
// FoundryのChat保存指定を上書きする操作を、実効接続先・メソッド・公開操作の宣言から選びます。
// 操作別backendOverrideを優先し、API名やモデル一覧へ依存させません。監査用の表とは分けます。
func chatStoreDisabledOperations(api object) array => api.apiType == 'http'
  ? map(filter(api.operations, operation => operation.method == 'POST' && operation.urlTemplate == '/chat/completions' && api.authorization.operationConstraints[operation.name].kind == 'bodyModel' && (contains(operation, 'backendOverride') ? operation.backendOverride.backendService : api.backendService) == 'foundryOpenAi'), operation => operation.name)
  : []
// native token policyが対応するOpenAI形式の操作です。音声・画像・埋め込みは推測で計数しません。
// 操作別backendOverrideを優先し、転送先と同じ実効接続先から対象を選びます。
func tokenLimitOperations(api object) array => api.apiType == 'http'
  ? map(filter(api.operations, operation => (contains(operation, 'backendOverride') ? operation.backendOverride.backendService : api.backendService) == 'foundryOpenAi' && ((endsWith(operation.urlTemplate, '/chat/completions') && api.authorization.operationConstraints[operation.name].kind == 'bodyModel') || api.authorization.operationConstraints[operation.name].kind == 'statelessResponses')), operation => operation.name) : []
// 監査用のAPI固有情報。要求/API/操作の実行時IDは全体ポリシーが取得します。
// 入力制約とトークン制限の設定はcontrolSettingsへ分け、監査名の変数で制御しません。
func auditSettings(api object, operation string, generatedMcp bool) object => {
  serviceClass: api.?serviceClass ?? 'platform'
  backendService: api.backendService
  // HTTP本人接続は生成時に選ぶ接続・session処理で基底のMI等を使わず、CM接続のtokenを送ります。
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
// 外部MCPの公開pathは転送先uriTemplateと異なる場合があります。
// API登録とqueryルールの対応表が、この同じ公開pathを使います。
func mcpProxyPublicPath(api object, endpoint object) string => api.mcpProxy.transport == 'sse'
  ? endpoint.name == 'sse' ? '/sse' : endpoint.uriTemplate
  : '/mcp'
// MCPの公開方法に合わせます。SSEの同じpathでもGET受付とPOST受付のルールを区別できます。
func mcpProxyPublicMethods(api object, endpoint object) array => api.mcpProxy.transport == 'sse'
  ? endpoint.name == 'sse' ? ['GET'] : ['POST']
  : ['GET', 'POST', 'DELETE']
func controlSettings(api object, operation string, generatedMcp bool) object => {
  // 生成MCPだけ、公開操作と対応するREST操作のIDを明示します。
  // 通常APIは共通側の実行時operationIdを使用します。
  operationId: operation
  operationConstraints: api.authorization.operationConstraints
  tokenLimitOperationIds: generatedMcp ? [] : tokenLimitOperations(api)
  // 保存指定を強制するChat操作だけを渡します。生成MCP・WebSocket・外部MCPへは追加しません。
  chatStoreDisabledOperationIds: generatedMcp ? [] : chatStoreDisabledOperations(api)
  // 従来のTTS対象条件で選んだ操作だけを、共通APIフローのSSML検査へ渡します。
  // 生成MCPや別の本文形式に、音声合成用の検査を追加しません。
  speechSsmlOperationIds: !generatedMcp && api.apiType == 'http'
    ? map(filter(api.operations, operation => requiresSpeechSsmlGuard({ apiName: api.name, operation: operation, constraint: api.authorization.operationConstraints[operation.name] })), operation => operation.name)
    : []
  // 明示した検査対象の操作だけを空オブジェクトで記録します。API名や本文形式で対象を推測しません。
  // ヘッダーの有無・単一値・非空は共通部品が確認し、MIMEの許可一覧をこの表へ持ち込みません。
  inputContracts: !generatedMcp && api.apiType == 'http'
    ? toObject(filter(api.operations, operation => contains(operation, 'inputContract')), operation => operation.name, operation => {})
    : {}
  // HTTPの操作とMCP受付はそれぞれ観測ラベルを参照します。未指定の入口へ観測を追加しません。
  // 元REST操作の入力制約kindや、サービス名・公開prefixから観測対象を推測しません。
  queryObservations: !generatedMcp && api.apiType == 'http'
    ? toObject(filter(api.operations, operation => contains(operation, 'queryObservation')), operation => operation.name, operation => operation.queryObservation)
    : {}
  mcpQueryObservations: generatedMcp
    ? contains(api.mcp, 'queryObservation') ? toObject(['GET', 'POST', 'DELETE'], method => '${method} /mcp', method => api.mcp.queryObservation) : {}
    : api.apiType == 'mcp'
      ? toObject(flatten(map(filter(api.mcpProxy.endpoints, endpoint => contains(endpoint, 'queryObservation')),
          endpoint => map(mcpProxyPublicMethods(api, endpoint), method => { key: '${method} ${mcpProxyPublicPath(api, endpoint)}', rule: endpoint.queryObservation }))), entry => entry.key, entry => entry.rule)
      : {}
}
// API種別・監査JSON・制御JSONの3行を一つの入力にまとめ、共通部品が用途別に展開します。
// JSONをさらにJSON文字列へ包まず、既存の1行JSONをそのまま渡して二重エスケープを避けます。
// 生成MCPは対応するREST APIと区別し、REST操作の接続先を外側の監査へ流用しません。
func apiSettings(api object, operation string, generatedMcp bool) string => format('{0}\n{1}\n{2}', generatedMcp ? 'mcp' : api.apiType, string(auditSettings(api, operation, generatedMcp)), string(controlSettings(api, operation, generatedMcp)))
// 公開設定を書き込む既存 APIM を参照します。
resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = { name: names.apim }

// 個人接続用の入口を登録します。購読キーは呼出元サーバー、OAuth tokenはCMが保持します。
var credentialLoginEnabled = publishApis && !empty(credentialLogin)
var credentialSettings = union(credentialLogin, { apimPrincipalId: apim.identity.principalId })
// 業務APIの入口と下流応答で、同じ認証URL案内を使います。
// 個別on-errorで共通トークン検証失敗を受ける操作だけに、有効時の固定部品を生成します。
var operationEntraAuthenticationErrorXml = (entraAuthentication.?enabled ?? false) ? '<include-fragment id="core-entra-authentication-error" fragment-id="core-entra-authentication-error" />' : ''
var credentialChallengeTemplate = loadTextContent('policies/snippets/authentication/credential-challenge/credential-challenge.xml')
var credentialChallengeSource = loadTextContent('policies/snippets/authentication/credential-challenge/credential-challenge.cs')
var credentialChallengeXml = replace(replace(renderPolicyTemplate(credentialChallengeTemplate, credentialChallengeSource, credentialChallengeSnippetSlots), '<fragment>', ''), '</fragment>', '')
func renderCredentialLogin(template string, settings object) string => reduce(items({
  __CREDENTIAL_LOGIN_SETTINGS__: base64(string(settings))
  __CREDENTIAL_LOGIN_ORIGIN_BASE64__: base64(settings.?publicOrigin ?? '')
  __CREDENTIAL_IDENTITY_TENANTS__: base64(string(map(settings.?providers ?? [], provider => provider.oauthTenantId)))
  __CREDENTIAL_LOGIN_ORIGIN__: settings.?publicOrigin ?? ''
  __CREDENTIAL_TRANSACTION_TTL__: string(settings.?transactionLifetimeSeconds ?? 300)
  __CREDENTIAL_LOGIN_CALLS__: string(settings.?loginCallsPerMinute ?? 10)
  __CREDENTIAL_KEY_HEADER__: settings.?keyHeader ?? 'api-key'
}), template, (value, entry) => replace(value, entry.key, entry.value))
// Browserにはmarkerがないため変えず、保護6操作だけ固定エラー呼出しを置換します。
func renderCredentialLoginOperation(template string, settings object, entraErrorXml string) string => replace(renderCredentialLogin(template, settings), '__OPERATION_ENTRA_AUTHENTICATION_ERROR_XML__', entraErrorXml)
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
var credentialLoginStartPolicyTemplate = loadTextContent('policies/operation-policies/credential-login/start.xml')
var credentialLoginStartCallerSource = loadTextContent('policies/operation-policies/credential-login/start.cs')
var credentialLoginStatusPolicyTemplate = loadTextContent('policies/operation-policies/credential-login/status.xml')
var credentialLoginStatusCallerSource = loadTextContent('policies/operation-policies/credential-login/status.cs')
var credentialLoginLaunchPolicyTemplate = loadTextContent('policies/operation-policies/credential-login/launch.xml')
var credentialLoginLaunchPostPolicyTemplate = loadTextContent('policies/operation-policies/credential-login/launch-post.xml')
var credentialLoginIdentityCallbackPolicyTemplate = loadTextContent('policies/operation-policies/credential-login/identity-callback.xml')
var credentialLoginCallbackPolicyTemplate = renderPolicyTemplate(loadTextContent('policies/operation-policies/credential-login/callback.xml'), loadTextContent('policies/operation-policies/credential-login/callback.cs'), credentialLoginCallbackPolicySlots)
var credentialLoginLogoutPolicyTemplate = loadTextContent('policies/operation-policies/credential-login/logout.xml')
var credentialLoginLogoutCallerSource = loadTextContent('policies/operation-policies/credential-login/logout.cs')
var credentialLoginSessionCommitPolicyTemplate = loadTextContent('policies/operation-policies/credential-login/session-commit.xml')
var credentialLoginSessionCommitCallerSource = loadTextContent('policies/operation-policies/credential-login/session-commit.cs')
var credentialLoginLogoutAllPolicyTemplate = loadTextContent('policies/operation-policies/credential-login/logout-all.xml')
var credentialLoginLogoutAllCallerSource = loadTextContent('policies/operation-policies/credential-login/logout-all.cs')
var credentialLoginDisconnectPolicyTemplate = loadTextContent('policies/operation-policies/credential-login/disconnect.xml')
var credentialLoginDisconnectCallerSource = loadTextContent('policies/operation-policies/credential-login/disconnect.cs')
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
  dependsOn: [entraAuthenticationErrorFragment]
  properties: { format: 'rawxml', value: renderCredentialLoginOperation(item.name == 'start' ? renderPolicyTemplate(credentialLoginStartPolicyTemplate, credentialLoginStartCallerSource, credentialLoginStartPolicySlots) : item.name == 'status' ? renderPolicyTemplate(credentialLoginStatusPolicyTemplate, credentialLoginStatusCallerSource, credentialLoginStatusPolicySlots) : item.name == 'launch' ? credentialLoginLaunchPolicyTemplate : item.name == 'launch-post' ? credentialLoginLaunchPostPolicyTemplate : item.name == 'identity-callback' ? credentialLoginIdentityCallbackPolicyTemplate : item.name == 'callback' ? credentialLoginCallbackPolicyTemplate : item.name == 'logout' ? renderPolicyTemplate(credentialLoginLogoutPolicyTemplate, credentialLoginLogoutCallerSource, credentialLoginLogoutPolicySlots) : item.name == 'session-commit' ? renderPolicyTemplate(credentialLoginSessionCommitPolicyTemplate, credentialLoginSessionCommitCallerSource, credentialLoginSessionCommitPolicySlots) : item.name == 'logout-all' ? renderPolicyTemplate(credentialLoginLogoutAllPolicyTemplate, credentialLoginLogoutAllCallerSource, credentialLoginLogoutAllPolicySlots) : renderPolicyTemplate(credentialLoginDisconnectPolicyTemplate, credentialLoginDisconnectCallerSource, credentialLoginDisconnectPolicySlots), credentialSettings, operationEntraAuthenticationErrorXml) }
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
// 原文だけを変数へ保持し、差し込みは使用先と生成元で同じ関数を呼びます。
// 原文と置換済みXMLの二重保存を避け、説明コメントと実行コードを保ったまま容量を減らします。
var auditBodyModelErrorXml = replace(trim(replace(replace(loadTextContent('policies/snippets/audit/audit-body-model-error/audit-body-model-error.xml'), '<fragment>', ''), '</fragment>', '')), '\n', '\n    ')
var restPolicyTemplate = replace(renderPolicyTemplate(loadTextContent('policies/api-templates/rest/rest.xml'), loadTextContent('policies/api-templates/rest/rest.cs'), gatewayTemplateSlots), '__AUDIT_BODY_MODEL_ERROR_XML__', auditBodyModelErrorXml)
var sharedStreamPolicyTemplate = renderPolicyTemplate(loadTextContent('policies/api-templates/shared/stream.xml'),
  '${loadTextContent('policies/api-templates/shared/stream.cs')}${loadTextContent('policies/api-templates/ws/ws.cs')}', protocolTemplateSlots)
var websocketPolicySections = loadTextContent('policies/api-templates/ws/ws.xml')
var externalMcpPolicySections = loadTextContent('policies/api-templates/mcp/external.xml')
var streamPolicySlots = [
  { marker: '__STREAM_PRE_BASE_XML__', section: 'pre-base' }
  { marker: '__STREAM_OBSERVE_INBOUND_XML__', section: 'observe-inbound' }
  { marker: '__STREAM_HANDSHAKE_QUERY_XML__', section: 'handshake-query' }
  { marker: '__STREAM_HANDSHAKE_SESSION_XML__', section: 'handshake-session' }
  { marker: '__STREAM_BACKEND_XML__', section: 'backend' }
  { marker: '__STREAM_ROUTE_XML__', section: 'route' }
  { marker: '__STREAM_CREDENTIAL_OUTBOUND_XML__', section: 'credential-outbound' }
  { marker: '__STREAM_OBSERVE_OUTBOUND_XML__', section: 'observe-outbound' }
  { marker: '__STREAM_OBSERVE_ERROR_XML__', section: 'observe-error' }
]
// 通信方式で差し込む固定区画を選び、共通の実行順序とC#式を1つの正本で管理します。
func renderStreamPolicy(sections string) string => reduce(streamPolicySlots, sharedStreamPolicyTemplate, (policy, slot) =>
  replace(policy, slot.marker, contains(sections, '<!-- stream-${slot.section}-start -->')
    ? split(split(sections, '<!-- stream-${slot.section}-start -->')[1], '<!-- stream-${slot.section}-end -->')[0]
    : ''))
var piiPolicyTemplate = renderPolicyTemplate(loadTextContent('policies/operation-policies/pii/pii.xml'), loadTextContent('policies/operation-policies/pii/pii.cs'), piiPolicySlots)
var documentAnalysisPolicyTemplate = renderPolicyTemplate(loadTextContent('policies/operation-policies/document-analysis/document-analysis.xml'), loadTextContent('policies/operation-policies/document-analysis/document-analysis.cs'), documentAnalysisPolicySlots)
var documentAnalysisStatusPolicyTemplate = renderPolicyTemplate(loadTextContent('policies/operation-policies/document-analysis/document-analysis-status.xml'), loadTextContent('policies/operation-policies/document-analysis/document-analysis-status.cs'), documentAnalysisStatusPolicySlots)
var realtimeCreateSource = loadTextContent('policies/operation-policies/realtime/realtime-create.cs')
var realtimeCreatePolicyTemplate = renderPolicyTemplate(loadTextContent('policies/operation-policies/realtime/realtime-create.xml'), realtimeCreateSource, realtimeCreatePolicySlots)
var realtimeCreateTicketSource = policySourceSection(realtimeCreateSource, 'ticket')
var realtimeHangupPolicyTemplate = loadTextContent('policies/operation-policies/realtime/realtime-hangup.xml')
var realtimeHangupOwnerSource = loadTextContent('policies/operation-policies/realtime/realtime-hangup.cs')
var liveCreatePolicyTemplate = renderPolicyTemplate(loadTextContent('policies/operation-policies/live/live-create.xml'), loadTextContent('policies/operation-policies/live/live-create.cs'), liveCreatePolicySlots)
var generatedMcpPolicyTemplate = renderPolicyTemplate(loadTextContent('policies/api-templates/mcp/generated.xml'), loadTextContent('policies/api-templates/mcp/generated.cs'), mcpTemplateSlots)
func configuredApiTemplate(apiType string) string => apiType == 'http' ? restPolicyTemplate : renderStreamPolicy(apiType == 'websocket' ? websocketPolicySections : externalMcpPolicySections)
func configuredOperationTemplate(template string, errorXml string, entraErrorXml string) string => replace(replace(replace(template, '__RATE_LIMIT_ERROR_XML__', errorXml), '__OPERATION_ENTRA_AUTHENTICATION_ERROR_XML__', entraErrorXml), '__AUDIT_BODY_MODEL_ERROR_XML__', auditBodyModelErrorXml)
// Compact connection-failure audit retains the verified key/user identity and
// fits the APIM fragment budget. Normal business audit remains the full fragment.
// One shared subject reader is XML-escaped before it enters any trace
// template. Both deployment assignments and protected source use these values.
// 未展開の正本は各1回だけ読み、同じ原文が配備と生成元で二重に読み込まれるのを防ぎます。
var apiSettingsSource = loadTextContent('policies/fragments/settings/core-api-settings/core-api-settings.cs')
var requestContextSource = loadTextContent('policies/fragments/audit/core-request-context/core-request-context.cs')
var authenticatedSubjectSource = loadTextContent('policies/fragments/audit/core-audit-authenticated-subject/core-audit-authenticated-subject.cs')
var trustedIngressSource = loadTextContent('policies/fragments/ingress/core-trusted-ingress/core-trusted-ingress.cs')
var browserExceptionSource = loadTextContent('policies/fragments/authentication/core-credential-browser-exception/core-credential-browser-exception.cs')
var commonAuthenticationSource = loadTextContent('policies/fragments/authentication/core-common-authentication/core-common-authentication.cs')
var gatewayTelemetrySource = loadTextContent('policies/fragments/audit/core-gateway-telemetry/core-gateway-telemetry.cs')
var backendAuditSource = loadTextContent('policies/fragments/audit/core-backend-audit/core-backend-audit.cs')
var queryObservationSource = loadTextContent('policies/fragments/request/core-query-observation/core-query-observation.cs')
var apiInputValidationSource = loadTextContent('policies/fragments/request/core-api-input-validation/core-api-input-validation.cs')
var apiOperationSettingsSource = loadTextContent('policies/fragments/request/core-api-operation-settings/core-api-operation-settings.cs')
var apiAuthenticationResultSource = loadTextContent('policies/fragments/authentication/core-api-authentication-result/core-api-authentication-result.cs')
var apiTargetValidationSource = loadTextContent('policies/fragments/request/core-api-target-validation/core-api-target-validation.cs')
var chatStoreDisabledSource = loadTextContent('policies/fragments/request/core-chat-store-disabled/core-chat-store-disabled.cs')
var modelLimitsSource = loadTextContent('policies/fragments/request/core-model-limits/core-model-limits.cs')
var rateLimitSource = loadTextContent('policies/fragments/quota/core-rate-limit/core-rate-limit.cs')
var tokenLimitSource = loadTextContent('policies/fragments/quota/core-token-limits/core-token-limits.cs')
var entraUserProfileSource = loadTextContent('policies/fragments/quota/core-entra-user-profile/core-entra-user-profile.cs')
var speechSsmlGuardSource = loadTextContent('policies/fragments/request/core-speech-ssml-guard/core-speech-ssml-guard.cs')
var documentInputValidationSource = loadTextContent('policies/fragments/request/core-document-input-validation/core-document-input-validation.cs')
var queryTelemetrySource = loadTextContent('policies/fragments/audit/core-query-telemetry/core-query-telemetry.cs')
var apiSettingsTemplate = loadTextContent('policies/fragments/settings/core-api-settings/core-api-settings.xml')
var apiRequestPreparationTemplate = loadTextContent('policies/fragments/request/core-api-request-preparation/core-api-request-preparation.xml')
var apiInputValidationTemplate = loadTextContent('policies/fragments/request/core-api-input-validation/core-api-input-validation.xml')
var apiOperationSettingsTemplate = loadTextContent('policies/fragments/request/core-api-operation-settings/core-api-operation-settings.xml')
var apiAuthenticationResultTemplate = loadTextContent('policies/fragments/authentication/core-api-authentication-result/core-api-authentication-result.xml')
var apiTargetValidationTemplate = loadTextContent('policies/fragments/request/core-api-target-validation/core-api-target-validation.xml')
var chatStoreDisabledTemplate = loadTextContent('policies/fragments/request/core-chat-store-disabled/core-chat-store-disabled.xml')
var modelLimitsTemplate = loadTextContent('policies/fragments/request/core-model-limits/core-model-limits.xml')
var rateLimitTemplate = loadTextContent('policies/fragments/quota/core-rate-limit/core-rate-limit.xml')
var tokenLimitTemplate = loadTextContent('policies/fragments/quota/core-token-limits/core-token-limits.xml')
var entraUserProfileTemplate = loadTextContent('policies/fragments/quota/core-entra-user-profile/core-entra-user-profile.xml')
var speechSsmlGuardTemplate = loadTextContent('policies/fragments/request/core-speech-ssml-guard/core-speech-ssml-guard.xml')
var documentInputValidationTemplate = loadTextContent('policies/fragments/request/core-document-input-validation/core-document-input-validation.xml')
var requestContextTemplate = loadTextContent('policies/fragments/audit/core-request-context/core-request-context.xml')
var authenticatedSubjectTemplate = loadTextContent('policies/fragments/audit/core-audit-authenticated-subject/core-audit-authenticated-subject.xml')
var trustedIngressTemplate = loadTextContent('policies/fragments/ingress/core-trusted-ingress/core-trusted-ingress.xml')
var browserExceptionTemplate = loadTextContent('policies/fragments/authentication/core-credential-browser-exception/core-credential-browser-exception.xml')
var commonAuthenticationTemplate = loadTextContent('policies/fragments/authentication/core-common-authentication/core-common-authentication.xml')
var entraAuthenticationErrorTemplate = loadTextContent('policies/fragments/authentication/core-entra-authentication-error/core-entra-authentication-error.xml')
var gatewayTelemetryTemplate = loadTextContent('policies/fragments/audit/core-gateway-telemetry/core-gateway-telemetry.xml')
var backendAuditTemplate = loadTextContent('policies/fragments/audit/core-backend-audit/core-backend-audit.xml')
var queryObservationTemplate = loadTextContent('policies/fragments/request/core-query-observation/core-query-observation.xml')
var queryTelemetryTemplate = loadTextContent('policies/fragments/audit/core-query-telemetry/core-query-telemetry.xml')
// 外部MCPだけに差し込む受付フローです。WebSocketへは追加しません。
var protocolQueryObservationInboundXml = replace(trim(replace(replace(replace(loadTextContent('policies/snippets/audit/query-observation-inbound/query-observation-inbound.xml'), '<fragment>', ''), '</fragment>', ''), '\r\n', '\n')), '\n', '\n    ')
var credentialConnectionTemplate = loadTextContent('policies/fragments/authentication/core-credential-connection/core-credential-connection.xml')
var credentialCallerSource = loadTextContent('policies/fragments/authentication/core-credential-connection/core-credential-connection.cs')
// CMの内部500は呼出し元APIで監査・応答し、CMフラグメント内に監査全文を複製しません。
var credentialCallerErrorXml = trim(replace(replace(loadTextContent('policies/snippets/authentication/credential-caller-error/credential-caller-error.xml'), '<fragment>', ''), '</fragment>', ''))
var credentialSessionUseTemplate = loadTextContent('policies/fragments/authentication/core-credential-session-use/core-credential-session-use.xml')
var credentialUseResultSource = loadTextContent('policies/fragments/authentication/core-credential-session-use/core-credential-session-use.cs')
var credentialConnectionOutboundTemplate = credentialConnectionOutboundFragmentValue(loadTextContent('policies/fragments/authentication/core-credential-connection-outbound/core-credential-connection-outbound.xml'), loadTextContent('policies/fragments/authentication/core-credential-connection-outbound/core-credential-connection-outbound.cs'), true)
var realtimeObserverTemplate = loadTextContent('policies/fragments/authentication/core-realtime-observer/core-realtime-observer.xml')
var realtimeObserverOwnerSource = loadTextContent('policies/fragments/authentication/core-realtime-observer/core-realtime-observer.cs')
var realtimeOwnerContextTemplate = loadTextContent('policies/snippets/authentication/realtime-owner-context/realtime-owner-context.xml')
var realtimeOwnerContextSource = loadTextContent('policies/snippets/authentication/realtime-owner-context/realtime-owner-context.cs')
// 共通入口のフローから、監査データの組み立てだけを呼び出せるようにします。
// C#の正本は別ファイルで管理し、ここでXML属性用に変換します。外部通信は追加しません。
// 配備と生成元は同じ関数を呼び、C#とXMLの全文を中間変数へ重複して保存しません。
func auditSourceXml(source string) string => replace(xmlText(sourceWithExplanations(source)), '\'', '&apos;')
// message本文では&/</>だけを変換し、C#の引用符はそのまま保持します。
func auditMessageSourceXml(source string) string => replace(replace(replace(sourceWithExplanations(source), '&', '&amp;'), '<', '&lt;'), '>', '&gt;')
// 同じ展開処理を共有し、ARMのユーザー定義関数を64個の上限内に保ちます。
// 属性と本文の引用符、trim・字下げは各正本の固定slotに記録します。
func renderPolicySource(source string, body bool, trimmed bool, indent string) string => replace(trimmed
  ? trim(body ? auditMessageSourceXml(source) : auditSourceXml(source))
  : (body ? auditMessageSourceXml(source) : auditSourceXml(source)), '\n', '\n${indent}')
// 配列順にmarkerを展開し、配備資源と保護sourceへ同じXMLを渡します。
func renderPolicyTemplate(template string, source string, slots array) string => reduce(slots, template, (policy, slot) => replace(policy, slot.marker, renderPolicySource(empty(slot.section) ? source : policySourceSection(source, slot.section), slot.body, slot.trimmed, slot.indent)))
// 同じowner受取りを3正本へ複製せず、生成時にinline展開します。Azure fragmentは増やしません。
func realtimeOwnerContextValue(template string, source string) string => trim(join(map(split(replace(replace(replace(replace(template, '\r\n', '\n'), '__REALTIME_OWNER_CONTEXT_CSHARP__', replace(trim(replace(auditSourceXml(source), '\r\n', '\n')), '\n', '\n      ')), '<fragment>', ''), '</fragment>', ''), '\n'), line => startsWith(line, '  ') ? substring(line, 2, length(line) - 2) : line), '\n'))
// 配備と保護sourceは、同じC#正本・owner snippetを同じ字下げで展開します。
func realtimePolicyValue(template string, marker string, source string, ownerTemplate string, ownerSource string, ownerIndent string, sourceIndent string) string => replace(replace(template, marker, replace(trim(replace(auditSourceXml(source), '\r\n', '\n')), '\n', '\n${sourceIndent}')), '__REALTIME_OWNER_CONTEXT_XML__', replace(realtimeOwnerContextValue(ownerTemplate, ownerSource), '\n', '\n${ownerIndent}'))
var backendAuditFragmentSlots = [
  { marker: '__CORE_BACKEND_AUDIT_CSHARP__', section: '', body: false, trimmed: true, indent: '      ' }
]
var rateLimitFragmentSlots = [
  { marker: '__CORE_RATE_LIMIT_CSHARP__', section: '', body: false, trimmed: true, indent: '      ' }
]
var apiSettingsFragmentSlots = [
  { marker: '__CORE_API_SETTINGS_CSHARP__', section: '', body: false, trimmed: true, indent: '    ' }
]
var requestContextFragmentSlots = [
  { marker: '__CORE_REQUEST_CONTEXT_CSHARP__', section: '', body: false, trimmed: true, indent: '      ' }
]
var queryObservationFragmentSlots = [
  { marker: '__CORE_QUERY_OBSERVATION_CSHARP__', section: '', body: false, trimmed: true, indent: '      ' }
]
var apiInputValidationFragmentSlots = [
  { marker: '__CORE_API_INPUT_VALIDATION_CSHARP__', section: '', body: false, trimmed: true, indent: '      ' }
]
// 操作の基本設定へモデル別の差分を重ねる部品を、同名XMLとC#から展開します。
var apiOperationSettingsFragmentSlots = [
  { marker: '__CORE_API_OPERATION_SETTINGS_CSHARP__', section: '', body: false, trimmed: true, indent: '      ' }
]
// 共通認証済みの利用者情報を引き継ぐ部品を、同名XMLとC#から展開します。
var apiAuthenticationResultFragmentSlots = [
  { marker: '__CORE_API_AUTHENTICATION_RESULT_CSHARP__', section: '', body: false, trimmed: true, indent: '      ' }
]
// CM接続の6つの検査区画を同名C#から展開し、通信・分岐・応答のフローはXMLに保ちます。
// 配備値と保護された読戻し期待sourceの両方で、同じ説明付きの展開を使います。
var credentialConnectionFragmentSlots = [
  { marker: '__CORE_CREDENTIAL_CALLER_CSHARP__', section: 'caller', body: false, trimmed: true, indent: '      ' }
  { marker: '__CORE_CREDENTIAL_CONNECTION_SELECTION_CSHARP__', section: 'selection', body: false, trimmed: true, indent: '      ' }
  { marker: '__CORE_CREDENTIAL_CONNECTION_AUTHORIZE_RESULT_CSHARP__', section: 'authorize-result', body: false, trimmed: true, indent: '      ' }
  { marker: '__CORE_CREDENTIAL_CONNECTION_REFRESH_UNAVAILABLE_CSHARP__', section: 'refresh-unavailable', body: false, trimmed: true, indent: '      ' }
  { marker: '__CORE_CREDENTIAL_CONNECTION_CLAIMS_REQUIRED_CSHARP__', section: 'claims-required', body: false, trimmed: true, indent: '      ' }
  { marker: '__CORE_CREDENTIAL_CONNECTION_IDENTITY_STATUS_CSHARP__', section: 'identity-status', body: false, trimmed: true, indent: '      ' }
]
// 本人セッションの使用結果・期限の2区画を、同名C#から説明付きで展開します。
var credentialSessionUseFragmentSlots = [
  { marker: '__CORE_CREDENTIAL_USE_RESULT_CSHARP__', section: 'use-result', body: false, trimmed: true, indent: '      ' }
  { marker: '__CORE_CREDENTIAL_USE_EXPIRES_ON_CSHARP__', section: 'expires-on', body: false, trimmed: true, indent: '      ' }
]
// 認証案内の応答検査・再試行秒・本文の3区画を同名C#からinline展開します。
var credentialChallengeSnippetSlots = [
  { marker: '__CREDENTIAL_CHALLENGE_RESULT_CSHARP__', section: 'result', body: false, trimmed: true, indent: '      ' }
  { marker: '__CREDENTIAL_CHALLENGE_RETRY_AFTER_CSHARP__', section: 'retry-after', body: false, trimmed: true, indent: '      ' }
  { marker: '__CREDENTIAL_CHALLENGE_RESPONSE_BODY_CSHARP__', section: 'response-body', body: true, trimmed: true, indent: '      ' }
]
// startの呼出者ID受取りだけを同名C#から展開し、配備と保護sourceの生成を揃えます。
var credentialLoginStartPolicySlots = [
  { marker: '__CREDENTIAL_LOGIN_START_CALLER_CSHARP__', section: '', body: false, trimmed: true, indent: ' ' }
]
// statusも保護操作の確認済みcallerを受け取り、同じ正本を配備と保護sourceへ展開します。
var credentialLoginStatusPolicySlots = [
  { marker: '__CREDENTIAL_LOGIN_STATUS_CALLER_CSHARP__', section: '', body: false, trimmed: true, indent: ' ' }
]
// disconnectでも確認済みcallerを同名C#から引き継ぎます。
var credentialLoginDisconnectPolicySlots = [
  { marker: '__CREDENTIAL_LOGIN_DISCONNECT_CALLER_CSHARP__', section: 'caller', body: false, trimmed: true, indent: '      ' }
  { marker: '__CREDENTIAL_LOGIN_DISCONNECT_SESSION_REQUEST_CSHARP__', section: 'session-request', body: false, trimmed: true, indent: '      ' }
]
// logout-allでも確認済みcallerを同名C#から引き継ぎます。
var credentialLoginLogoutAllPolicySlots = [
  { marker: '__CREDENTIAL_LOGIN_LOGOUT_ALL_CALLER_CSHARP__', section: 'caller', body: false, trimmed: true, indent: '      ' }
  { marker: '__CREDENTIAL_LOGIN_LOGOUT_ALL_SESSION_REQUEST_CSHARP__', section: 'session-request', body: false, trimmed: true, indent: '      ' }
]
// logoutでも確認済みcallerを同名C#から引き継ぎます。
var credentialLoginLogoutPolicySlots = [
  { marker: '__CREDENTIAL_LOGIN_LOGOUT_CALLER_CSHARP__', section: 'caller', body: false, trimmed: true, indent: '      ' }
  { marker: '__CREDENTIAL_LOGIN_LOGOUT_SESSION_REQUEST_CSHARP__', section: 'session-request', body: false, trimmed: true, indent: '      ' }
]
// セッション確定操作でも確認済みcallerを同名C#から引き継ぎます。
var credentialLoginSessionCommitPolicySlots = [
  { marker: '__CREDENTIAL_LOGIN_SESSION_COMMIT_CALLER_CSHARP__', section: 'caller', body: false, trimmed: true, indent: '      ' }
  { marker: '__CREDENTIAL_LOGIN_SESSION_COMMIT_SESSION_REQUEST_CSHARP__', section: 'session-request', body: false, trimmed: true, indent: '      ' }
]
// HTTP本文から監査用モデル名を読む式を、同名XMLとC#から展開します。
var auditBodyModelFragmentSlots = [
  { marker: '__CORE_AUDIT_BODY_MODEL_CSHARP__', section: '', body: false, trimmed: true, indent: '    ' }
]
// 準備済みの操作設定を使う対象確認を、同名XMLとC#から展開します。
var apiTargetValidationFragmentSlots = [
  { marker: '__CORE_API_TARGET_VALIDATION_CSHARP__', section: '', body: false, trimmed: true, indent: '      ' }
]
// 対象Chatのstoreだけをfalseへ揃える部品を、同名XMLとC#から説明付きで展開します。
var chatStoreDisabledFragmentSlots = [
  { marker: '__CORE_CHAT_STORE_DISABLED_CSHARP__', section: '', body: false, trimmed: true, indent: '      ' }
]
// 本文サイズの確認部品を、同名XMLとC#から説明コメント付きで展開します。
var modelLimitsFragmentSlots = [
  { marker: '__CORE_MODEL_LIMITS_CSHARP__', section: '', body: false, trimmed: true, indent: '      ' }
]
// 確認済みの利用者と利用枠設定から回数制限を選ぶ部品を、同名XMLとC#から説明付きで展開します。
// 確認済み社員IDでトークン枠の計数キーを作る式だけを、同名XMLとC#から説明付きで展開します。
var tokenLimitFragmentSlots = [
  { marker: '__CORE_TOKEN_LIMITS_ENABLED_CSHARP__', section: 'enabled', body: false, trimmed: true, indent: '      ' }
  { marker: '__CORE_TOKEN_LIMITS_CSHARP__', section: 'counter', body: false, trimmed: true, indent: '      ' }
]
// 認証済み社員の利用枠を取得する部品を、同名XMLとC#から説明付きで展開します。
var entraUserProfileFragmentSlots = [
  { marker: '__CORE_ENTRA_USER_PROFILE_CSHARP__', section: '', body: false, trimmed: true, indent: '      ' }
]
// TTSの本文を一度だけ読み、サイズとSSML宣言を続けて検査する部品を、同名XMLとC#から説明付きで展開します。
var speechSsmlGuardFragmentSlots = [
  { marker: '__CORE_SPEECH_SSML_GUARD_CSHARP__', section: '', body: false, trimmed: true, indent: '      ' }
]
// Documentのmodelクエリ確認を、同名XMLとC#から説明付きで展開します。本文は読みません。
var documentInputValidationFragmentSlots = [
  { marker: '__CORE_DOCUMENT_INPUT_VALIDATION_CSHARP__', section: '', body: false, trimmed: true, indent: '      ' }
]
// query監査は同名C#の2区画をXMLへ展開します。利用者情報の共通式にも説明を残します。
func policySourceSection(source string, section string) string => split(split(replace(source, '\r\n', '\n'), '// BEGIN ${section}\n')[1], '\n// END ${section}')[0]
var protocolTemplateSlots = [
  { marker: '__PROTOCOL_MODEL_SELECTION_CSHARP__', section: 'model-selection', body: false, trimmed: true, indent: '      ' }
  { marker: '__PROTOCOL_AUTHENTICATION_RESULT_CSHARP__', section: 'authentication-result', body: false, trimmed: true, indent: '      ' }
  { marker: '__PROTOCOL_AUDIT_MODEL_SETTING_CSHARP__', section: 'audit-model-setting', body: false, trimmed: true, indent: '      ' }
  { marker: '__PROTOCOL_QUERY_VALIDATION_CSHARP__', section: 'query-validation', body: false, trimmed: true, indent: '      ' }
]
var mcpTemplateSlots = [
  { marker: '__MCP_GATEWAY_DECISION_CSHARP__', section: 'gateway-decision', body: false, trimmed: true, indent: '      ' }
  { marker: '__MCP_PII_ARGUMENTS_CSHARP__', section: 'pii-arguments', body: false, trimmed: true, indent: '      ' }
]
var gatewayTemplateSlots = [
  { marker: '__GATEWAY_MODEL_ALIAS_BODY_CSHARP__', section: 'model-alias-body', body: true, trimmed: true, indent: '      ' }
  { marker: '__GATEWAY_HOSTED_BODY_CSHARP__', section: 'hosted-body', body: true, trimmed: true, indent: '      ' }
  { marker: '__GATEWAY_RESPONSES_BODY_CSHARP__', section: 'responses-body', body: true, trimmed: true, indent: '      ' }
]
var realtimeCreatePolicySlots = [
  { marker: '__REALTIME_CREATE_SDP_CSHARP__', section: 'sdp', body: false, trimmed: true, indent: '      ' }
  { marker: '__REALTIME_CREATE_CLIENT_SECRETS_REQUEST_CSHARP__', section: 'client-secrets-request', body: true, trimmed: true, indent: '      ' }
  { marker: '__REALTIME_CREATE_EPHEMERAL_TOKEN_CSHARP__', section: 'ephemeral-token', body: false, trimmed: true, indent: '      ' }
  { marker: '__REALTIME_CREATE_CALL_ID_CSHARP__', section: 'call-id', body: false, trimmed: true, indent: '      ' }
  { marker: '__REALTIME_CREATE_ANSWER_STATUS_CSHARP__', section: 'answer-status', body: false, trimmed: true, indent: '      ' }
]
var documentAnalysisStatusPolicySlots = [
  { marker: '__DOCUMENT_ANALYSIS_STATUS_OWNER_CSHARP__', section: 'owner', body: false, trimmed: true, indent: '      ' }
  { marker: '__DOCUMENT_ANALYSIS_STATUS_OWNER_STATUS_CSHARP__', section: 'owner-status', body: false, trimmed: true, indent: '      ' }
  { marker: '__DOCUMENT_ANALYSIS_STATUS_POLL_EVALUATION_CSHARP__', section: 'poll-evaluation', body: false, trimmed: true, indent: '      ' }
]
var documentAnalysisPolicySlots = [
  { marker: '__DOCUMENT_ANALYSIS_OPERATION_ID_CSHARP__', section: 'operation-id', body: false, trimmed: true, indent: '      ' }
  { marker: '__DOCUMENT_ANALYSIS_ACCEPTED_CSHARP__', section: 'accepted', body: false, trimmed: true, indent: '      ' }
  { marker: '__DOCUMENT_ANALYSIS_STATUS_CSHARP__', section: 'status', body: false, trimmed: true, indent: '      ' }
  { marker: '__DOCUMENT_ANALYSIS_RETRY_AFTER_CSHARP__', section: 'retry-after', body: false, trimmed: true, indent: '      ' }
]
var piiPolicySlots = [
  { marker: '__PII_INPUT_STATUS_CSHARP__', section: 'input', body: false, trimmed: true, indent: '      ' }
  { marker: '__PII_REQUEST_CSHARP__', section: 'request', body: false, trimmed: true, indent: '      ' }
  { marker: '__PII_RETRY_AFTER_CSHARP__', section: 'retry', body: false, trimmed: true, indent: '      ' }
  { marker: '__PII_RESULT_CSHARP__', section: 'result', body: false, trimmed: true, indent: '      ' }
  { marker: '__PII_OUTPUT_CSHARP__', section: 'output', body: true, trimmed: true, indent: '      ' }
  { marker: '__PII_AUDIT_CSHARP__', section: 'audit', body: false, trimmed: true, indent: '      ' }
  { marker: '__PII_REQUEST_SETTINGS_CSHARP__', section: 'request-settings', body: false, trimmed: true, indent: '      ' }
  { marker: '__PII_AUDIT_SETTINGS_CSHARP__', section: 'audit-settings', body: false, trimmed: true, indent: '      ' }
]
var liveCreatePolicySlots = [
  { marker: '__LIVE_CREATE_REQUEST_CSHARP__', section: 'request', body: false, trimmed: true, indent: '      ' }
  { marker: '__LIVE_CREATE_ANSWER_CSHARP__', section: 'answer', body: false, trimmed: true, indent: '        ' }
]
var auditContextPolicySlots = [
  { marker: '__CORE_AUDIT_CONTEXT_TRACE_CSHARP__', section: 'trace', body: false, trimmed: true, indent: '    ' }
  { marker: '__CORE_AUDIT_CONTEXT_MODEL_CSHARP__', section: 'model', body: false, trimmed: true, indent: '    ' }
]
var credentialLoginCallbackPolicySlots = [
  { marker: '__CREDENTIAL_LOGIN_CALLBACK_INPUT_CSHARP__', section: 'input', body: false, trimmed: true, indent: ' ' }
  { marker: '__CREDENTIAL_LOGIN_CALLBACK_TRANSACTION_CSHARP__', section: 'transaction', body: false, trimmed: true, indent: ' ' }
  { marker: '__CREDENTIAL_LOGIN_CALLBACK_OWNER_CSHARP__', section: 'owner', body: false, trimmed: true, indent: ' ' }
]

var queryTelemetryFragmentSlots = [
  { marker: '__CORE_QUERY_TELEMETRY_PREDICATE_CSHARP__', section: 'predicate', body: false, trimmed: false, indent: '        ' }
  { marker: '__CORE_QUERY_TELEMETRY_CSHARP__', section: 'message', body: false, trimmed: false, indent: '            ' }
]
var authenticatedSubjectFragmentSlots = [
  { marker: '__CORE_AUTHENTICATED_SUBJECT_CSHARP__', section: '', body: false, trimmed: false, indent: '' }
]
var trustedIngressConditionSlots = [
  { marker: '__CORE_TRUSTED_INGRESS_CSHARP__', section: '', body: false, trimmed: true, indent: '        ' }
]
// ARMのstring(bool)の表記に依存せず、C#で使える小文字の真偽値を明示します。
func browserExceptionFragmentValue(template string, source string, configured bool) string => replace(replace(template, '__CORE_CREDENTIAL_BROWSER_EXCEPTION_CSHARP__', replace(trim(replace(auditSourceXml(source), '\r\n', '\n')), '\n', '\n      ')), '__CREDENTIAL_BROWSER_CONFIGURED__', configured ? 'true' : 'false')
func gatewayTelemetryFragmentValue(template string, source string, compact bool) string => replace(compact ? withoutTelemetryExplanationLines(template) : template, '__CORE_GATEWAY_TELEMETRY_CSHARP__', replace(trim(auditMessageSourceXml(compact ? compactObservationSource(source) : source)), '\n', '\n      '))
func credentialConnectionOutboundFragmentValue(template string, source string, compact bool) string => replace(template, '__CORE_CREDENTIAL_CONNECTION_OUTBOUND_CSHARP__', replace(trim(auditMessageSourceXml(compact ? compactObservationSource(source) : source)), '\n', '\n    '))
func credentialTelemetrySnippetValue(template string, source string, compact bool) string => replace(template, '__CREDENTIAL_TELEMETRY_CSHARP__', replace(trim(auditMessageSourceXml(compact ? compactObservationSource(source) : source)), '\n', '\n    '))
var credentialGatewayTelemetryBody = replace(replace(credentialTelemetrySnippetValue(loadTextContent('policies/snippets/audit/credential-telemetry/credential-telemetry.xml'), loadTextContent('policies/snippets/audit/credential-telemetry/credential-telemetry.cs'), true), '<fragment>', ''), '</fragment>', '')
// This handler is inlined only into API/operation policies, never registered as
// a fragment. Refer to the full reviewed audit fragment without copying its
// body into every API and protected source entry; fragment nesting is avoided.
var rateLimitErrorPolicyTemplate = loadTextContent('policies/snippets/quota/rate-limit-error/rate-limit-error.xml')
var rateLimitErrorRetryAfterSource = loadTextContent('policies/snippets/quota/rate-limit-error/rate-limit-error.cs')
// Retry-Afterの式だけを同名C#から展開し、配備と保護sourceへ同じinline部品を渡します。
var rateLimitErrorSnippetSlots = [
  { marker: '__RATE_LIMIT_ERROR_RETRY_AFTER_CSHARP__', section: '', body: false, trimmed: true, indent: '        ' }
]
var rateLimitErrorXml = replace(replace(replace(renderPolicyTemplate(rateLimitErrorPolicyTemplate, rateLimitErrorRetryAfterSource, rateLimitErrorSnippetSlots), '<fragment>', ''), '</fragment>', ''), '__GATEWAY_TELEMETRY_XML__', '<include-fragment fragment-id="core-gateway-telemetry" />')
var mcpRateCleanupPolicyTemplate = loadTextContent('policies/snippets/quota/mcp-rate-cleanup/mcp-rate-cleanup.xml')
var mcpRateCleanupSource = loadTextContent('policies/snippets/quota/mcp-rate-cleanup/mcp-rate-cleanup.cs')
// 外部MCPの取消判定を同名C#から展開し、既存の本文保持と計数免除だけを維持します。
var mcpRateCleanupSnippetSlots = [
  { marker: '__MCP_RATE_CLEANUP_CSHARP__', section: '', body: false, trimmed: true, indent: '    ' }
]
var mcpRateCleanupXml = replace(replace(renderPolicyTemplate(mcpRateCleanupPolicyTemplate, mcpRateCleanupSource, mcpRateCleanupSnippetSlots), '<fragment>', ''), '</fragment>', '')
// Backend authentication is declared independently of public caller auth.
// Key material remains in Key Vault-backed named values.
// 生成APIは公開入口のCookieと別名のキーをFoundryへ転送しません。
var inferenceCredentialCleanup = '<set-header name="Cookie" exists-action="delete" /><set-header name="x-api-key" exists-action="delete" /><set-query-parameter name="x-api-key" exists-action="delete" />'
var backendAuthenticationXml = [for api in apiDefinitions: empty(operationBackendSelection(api))
  ? '${api.name == 'openai-inference-v1' ? inferenceCredentialCleanup : ''}${backendAuthenticationPolicy(api, '${api.name}-backend-secret')}'
  : operationBackendSelection(api)]
var backendHeadersXml = [for api in apiDefinitions: empty(operationBackendSelection(api)) ? backendHeaderPolicy(api) : '']
// API ごとの認可契約を Base64 JSON として埋め込みます。Base64 は暗号化ではなく、安全な構文境界のためです。秘密は含めません。
// XML全文を加工する途中の結果は配列変数へ保存せず、関数で最終結果までつなぎます。
// ARMは各変数の展開後のサイズも数えるため、API数分のXMLを段階ごとに保存すると、
// ソースが小さくても配備要求の上限を超えます。認可・転送・監査の内容は変えません。
// CM未設定のHTTP APIは、未使用の設定代入と到達しないfragment参照を生成元にも残しません。
// 接続設定のあるAPIはInboundで接続/sessionを直接呼び、Outboundの既存分岐も維持します。
func replaceCredentialBranch(policy string, startMarker string, endMarker string, fallback string) string => '${split(policy, startMarker)[0]}${fallback}${split(policy, endMarker)[1]}'
func graphOboApi(api object) bool => api.apiType == 'http' && api.name == 'graph-profile-v1'
func graphOboRegistrationMatches(api object) bool => api.path == 'tools/graph/v1' && api.backendUrl == 'https://graph.microsoft.com/v1.0' && api.backendAuthentication.mode == 'none' && (api.?credentialConnection.?providerName ?? '') == 'graph-delegated' && empty(operationBackendSelection(api)) && empty(backendHeaderPolicy(api)) && length(api.operations) == 1 && length(filter(api.operations, operation => operation.name == 'graph_profile_self' && operation.method == 'GET' && operation.urlTemplate == '/me' && (operation.?backendUrlTemplate ?? '') == '/me?$select=displayName,givenName,surname,userPrincipalName,id,jobTitle,companyName,department,employeeId,officeLocation,mail,accountEnabled')) == 1
var graphOboSource = loadTextContent('policies/snippets/authentication/graph-obo/graph-obo.cs')
var graphOboTemplate = loadTextContent('policies/snippets/authentication/graph-obo/graph-obo.xml')
var graphOboSlots = [
  { marker: '__GRAPH_OBO_BROKER_RESULT_CSHARP__', section: 'broker-result', body: false, trimmed: true, indent: '        ' }
  { marker: '__GRAPH_OBO_BACKEND_RESULT_CSHARP__', section: 'backend-result', body: false, trimmed: true, indent: '        ' }
]
var graphOboXml = renderPolicyTemplate(graphOboTemplate, graphOboSource, graphOboSlots)
func graphOboSection(section string) string => split(split(graphOboXml, '<!-- graph-obo-${section}-start -->')[1], '<!-- graph-obo-${section}-end -->')[0]
func wrapGraphOboBranch(policy string, section string, graphBranch string) string => replaceCredentialBranch(policy,
  '<!-- core-http-credential-${section}-start -->', '<!-- core-http-credential-${section}-end -->',
  '<choose><when condition="@((bool)context.Variables[&quot;gatewayGraphObo&quot;])">${graphBranch}</when><otherwise>${split(split(policy, '<!-- core-http-credential-${section}-start -->')[1], '<!-- core-http-credential-${section}-end -->')[0]}</otherwise></choose>')
func selectGraphOboPolicy(api object, policy string, enabled bool) string => enabled && graphOboApi(api) ? replace(
  wrapGraphOboBranch(wrapGraphOboBranch(policy, 'inbound',
    replace(replace(graphOboSection('inbound'), '__GRAPH_OBO_ERROR_XML__', graphOboSection('error')), '__GRAPH_OBO_REGISTRATION_VALID__', string(graphOboRegistrationMatches(api)))),
    'outbound', replace(graphOboSection('outbound'), '__GRAPH_OBO_ERROR_XML__', graphOboSection('error'))),
  '__GRAPH_OBO_ASSERTION_XML__', graphOboSection('assertion')) : replace(policy, '    __GRAPH_OBO_ASSERTION_XML__\n', '')
func selectHttpCredentialPolicy(api object, policy string, graphOboEnabled bool) string => api.apiType != 'http' || contains(api, 'credentialConnection') || (graphOboEnabled && graphOboApi(api)) ? policy
  : replaceCredentialBranch(replaceCredentialBranch(policy, '<!-- core-http-credential-inbound-start -->', '<!-- core-http-credential-inbound-end -->', '__BACKEND_AUTHENTICATION_XML__'), '<!-- core-http-credential-outbound-start -->', '<!-- core-http-credential-outbound-end -->', '')
func baseApiPolicy(api object, policyTemplate string, backendAuthentication string, backendHeaders string, credentialCallerError string, graphOboEnabled bool) string => replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(selectGraphOboPolicy(api, selectHttpCredentialPolicy(api, policyTemplate, graphOboEnabled), graphOboEnabled),
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
  '__TOKEN_LIMIT_XML__', empty(tokenLimitOperations(api)) ? '' : '<include-fragment fragment-id="core-token-limits" />'),
  '__CREDENTIAL_CALLER_ERROR_XML__', credentialCallerError)

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
var mcpMetadataGuardXml = loadTextContent('policies/snippets/request/mcp-metadata-guard/mcp-metadata-guard.xml')
// Keep only small per-API substitutions in variables. Storing every final XML
// here and again in resources/source would exceed ARM's expanded size budget.
var policySettings = [for (api, index) in apiDefinitions: {
  graphOboEnabled: graphOboEnabled
  authentication: backendAuthenticationXml[index]
  headers: backendHeadersXml[index]
  cleanup: protocolRateCleanupXml[index]
  queries: protocolQueriesXml[index]
  websocketQuery: api.apiType == 'websocket' && (contains(api.websocket, 'queryParameters') || contains(api.websocket, 'allowedModels')) ? base64(string(api.websocket.?queryParameters ?? {})) : ''
  observer: observerPoliciesXml[index]
  mcpRoute: mcpProxyRoutingXml[index]
  metadataGuard: api.apiType == 'mcp' ? mcpMetadataGuardXml : ''
  credentialCallerError: api.apiType == 'http' && contains(api, 'credentialConnection') ? credentialCallerErrorXml : ''
  credentialInbound: api.apiType == 'mcp' && contains(api, 'credentialConnection') ? '<set-variable name="gatewayCredentialConnection" value="${base64(string(api.credentialConnection))}" /><include-fragment fragment-id="core-credential-connection" />${credentialCallerErrorXml}<include-fragment fragment-id="core-credential-session-use" />' : backendAuthenticationXml[index]
  credentialOutbound: api.apiType == 'mcp' && contains(api, 'credentialConnection') ? '<include-fragment fragment-id="core-credential-connection-outbound" />' : ''
}]
func apiPolicyValue(api object, template string, errorXml string, values object) string =>
  replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(
    replace(replace(replace(baseApiPolicy(api, template, values.authentication, values.headers, values.credentialCallerError, values.graphOboEnabled),
      '__RATE_LIMIT_ERROR_XML__', errorXml), '__RATE_LIMIT_CLEANUP_XML__', values.cleanup),
      '__API_SETTINGS_BASE64__', base64(apiSettings(api, '', false))),
    '__PROTOCOL_QUERY_XML__', values.queries), '__WEBSOCKET_QUERY_BASE64__', values.websocketQuery),
    '__PROTOCOL_SESSION_XML__', values.observer), '__MCP_PROXY_ROUTE_XML__', values.mcpRoute),
    '__MCP_PRM_GUARD_XML__', values.metadataGuard), '__CREDENTIAL_CONNECTION_INBOUND_XML__', values.credentialInbound),
    '__CREDENTIAL_CONNECTION_OUTBOUND_XML__', values.credentialOutbound),
    '__PROTOCOL_QUERY_OBSERVATION_XML__', api.apiType == 'mcp' ? protocolQueryObservationInboundXml : ''),
    '__PROTOCOL_QUERY_OUTBOUND_XML__', api.apiType == 'mcp' ? '<set-variable name="gatewayQueryPhase" value="backend-response" /><include-fragment fragment-id="core-query-telemetry" />' : ''),
    '__PROTOCOL_QUERY_ERROR_XML__', api.apiType == 'mcp' ? '<set-variable name="gatewayQueryPhase" value="policy-error" /><include-fragment fragment-id="core-query-telemetry" />' : '')
// プロトコルごとの API と操作を分類し、同じ定義から Product・認可・公開操作の対応を作ります。
// HTTPの抽出結果を別の配列変数にも保存せず、操作と接続先の集約時に同じ条件で抽出します。
// API定義の重複を配備テンプレートから減らし、公開する操作・接続先は変えません。
var operationEntries = flatten(map(filter(apiDefinitions, api => api.apiType == 'http'), api => map(api.operations, operation => {
  apiName: api.name
  backendUrl: contains(operation, 'backendOverride') ? operation.backendOverride.backendUrl : api.backendUrl
  operation: operation
  constraint: api.authorization.operationConstraints[operation.name]
})))
var operationBackendEntries = flatten(map(filter(apiDefinitions, api => api.apiType == 'http'), api => map(filter(api.operations, operation => contains(operation, 'backendOverride')), operation => { apiName: api.name, operationName: operation.name, backend: operation.backendOverride })))
// 各APIが指定したキー用Productを重複なく作り、リリース済みAPIを集約するProductを加えます。
// 利用する購読は基盤管理者がAzureポータルで作成します。
var releasedProductId = 'released-apis'
var productIds = union(map(apiDefinitions, api => api.productId), [releasedProductId])
var releasedApis = filter(apiDefinitions, api => api.?released ?? false)
var piiApis = filter(apiDefinitions, api => length(filter(api.operations, operation => api.authorization.operationConstraints[operation.name].kind == 'pii')) > 0)
var documentApis = filter(apiDefinitions, api => length(filter(api.operations, operation => api.authorization.operationConstraints[operation.name].kind == 'documentAnalysis')) > 0)
var mcpApis = filter(apiDefinitions, api => contains(api, 'mcp'))
// MCPも同じ購読キーで認可します。APIのOperation構成は維持します。
// 最終XMLを配列変数にも複製せず、配備先と生成元へ同じ内容を直接渡します。
func mcpApiPolicyValue(api object, template string, errorXml string) string => replace(replace(replace(replace(template, '__AUTHORIZATION_BASE64__', base64(string(api.authorization))), '__RATE_LIMIT_ERROR_XML__', errorXml), '__API_SETTINGS_BASE64__', base64(apiSettings(api, api.mcp.operationNames[0], true))), '__MCP_PRM_GUARD_XML__', mcpMetadataGuardXml)

// Share individual fragment XML with protected outputs without another full-XML object.
var auditContextFragmentXml = renderPolicyTemplate(loadTextContent('policies/fragments/audit/core-audit-context/core-audit-context.xml'), loadTextContent('policies/fragments/audit/core-audit-context/core-audit-context.cs'), auditContextPolicySlots)
var auditBodyModelFragmentTemplate = loadTextContent('policies/fragments/audit/core-audit-body-model/core-audit-body-model.xml')
var auditBodyModelFragmentSource = loadTextContent('policies/fragments/audit/core-audit-body-model/core-audit-body-model.cs')
var auditBodyModelFragmentXml = renderPolicyTemplate(auditBodyModelFragmentTemplate, auditBodyModelFragmentSource, auditBodyModelFragmentSlots)
var backendAuditFragmentXml = renderPolicyTemplate(backendAuditTemplate, backendAuditSource, backendAuditFragmentSlots)
// 入口確認に失敗した要求の監査は、従来の共通拒否監査と同じ内容を展開します。
// 認証前なのでAPIのdecisionを参照する区間だけを除き、fragmentの入れ子は作りません。
// 中間のXML全文を変数へ保存せず、ARM展開後の重複した容量を増やしません。
func preAuthenticationTelemetry(telemetry string) string => '${split(telemetry, ' /* core-user-audit-start */')[0]}${split(telemetry, ' /* core-user-audit-end */')[1]}'
// 同名CSの固定5区画を同名XMLだけへ差し込みます。BEGIN/ENDは生成用で、APIMへは本文だけを渡します。
// 区画名・重複・閉じ忘れはPythonの配備前検査でも拒否し、Bicepと生成元の完成XMLを照合します。

var authenticationExpressionsSlots = [
  { marker: '__CORE_COMMON_AUTHENTICATION_KEY_REQUEST_VALIDATION_CSHARP__', section: 'key-request-validation', body: false, trimmed: false, indent: '' }
  { marker: '__CORE_COMMON_AUTHENTICATION_BEARER_REQUEST_VALIDATION_CSHARP__', section: 'bearer-request-validation', body: false, trimmed: false, indent: '' }
  { marker: '__CORE_COMMON_AUTHENTICATION_ENTRA_IDENTITY_CSHARP__', section: 'entra-identity', body: false, trimmed: false, indent: '' }
  { marker: '__CORE_COMMON_AUTHENTICATION_ENTRA_AUTHENTICATION_RESULT_CSHARP__', section: 'entra-authentication-result', body: false, trimmed: false, indent: '' }
  { marker: '__CORE_COMMON_AUTHENTICATION_ENTRA_FAILURE_SUBJECT_CSHARP__', section: 'entra-failure-subject', body: false, trimmed: false, indent: '' }
]
func authenticationBranchValue(template string, source string, enabled bool) string => replace(enabled
  ? renderPolicyTemplate(template, source, authenticationExpressionsSlots)
  : replaceCredentialBranch(renderPolicyTemplate(template, source, authenticationExpressionsSlots), '<!-- core-entra-branch-start -->', '<!-- core-entra-branch-end -->', ''),
  '__ENTRA_OPEN_PRODUCT_ID__', 'core-entra-model-users')
// 共通の監査ソースを生成時に展開します。フラグメント同士を実行時に呼ぶ入れ子は作りません。
func authenticationTelemetryBody(template string, source string) string => preAuthenticationTelemetry(replace(replace(gatewayTelemetryFragmentValue(template, source, true), '<fragment>', ''), '</fragment>', ''))
func commonAuthenticationFragmentValue(template string, source string, enabled bool, telemetryTemplate string, telemetrySource string) string => replace(replace(
  authenticationBranchValue(template, source, enabled), '__AUTHENTICATION_TELEMETRY_XML__', authenticationTelemetryBody(telemetryTemplate, telemetrySource)),
  '__ENTRA_FAILURE_TELEMETRY_XML__', replace(authenticationTelemetryBody(telemetryTemplate, telemetrySource), '"gatewayAuthenticatedSubject"', '"gatewayEntraFailureSubject"'))
func entraAuthenticationErrorFragmentValue(template string, telemetryTemplate string, telemetrySource string) string => replace(template, '__AUTHENTICATION_TELEMETRY_XML__', authenticationTelemetryBody(telemetryTemplate, telemetrySource))
// 配備先と生成元に同じXMLを渡し、展開済みの全文を中間変数へ重複保存しません。
func trustedIngressFragmentValue(template string, conditionSource string, telemetryTemplate string, telemetrySource string) string => replace(replace(replace(renderPolicyTemplate(template, conditionSource, trustedIngressConditionSlots), '__AUTHENTICATION_TELEMETRY_XML__', preAuthenticationTelemetry(replace(replace(gatewayTelemetryFragmentValue(telemetryTemplate, telemetrySource, false), '<fragment>', ''), '</fragment>', ''))), '__L7_INGRESS_REQUIRED__', '&quot;{{core-l7-ingress-enabled}}&quot; == &quot;true&quot;'), '__L7_INGRESS_EXPECTED__', '{{core-l7-ingress-secret}}')

// 各APIがbaseより前で呼び、API固有の監査・制御設定を準備するleaf部品です。
resource apiSettingsFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-api-settings'
  properties: { format: 'rawxml', description: 'APIから渡された公開設定を読み、監査情報と制御設定に分けて共通ポリシーの実行前に準備する。モデルや要求本文は変更しない。', value: renderPolicyTemplate(apiSettingsTemplate, apiSettingsSource, apiSettingsFragmentSlots) }
}
// 各APIがbaseの直後で呼び、内部用ヘッダーの整理と認証完了の確認を行います。
resource apiRequestPreparationFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-api-request-preparation'
  properties: { format: 'rawxml', description: '共通認証の直後に、クライアント由来の内部用IDを除き、要求IDを設定して認証完了を確認する。本文やモデル機能は変更しない。', value: apiRequestPreparationTemplate }
}
// All APIsの共通ポリシーが参照する入口確認と監査のleaf部品。fragment同士の入れ子は作りません。
resource requestContextFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-request-context'
  properties: { format: 'rawxml', description: '共通入口で監査用の要求情報を準備する。本文や資格情報を記録しない。', value: renderPolicyTemplate(requestContextTemplate, requestContextSource, requestContextFragmentSlots) }
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
  properties: { format: 'rawxml', description: '共通認証後に確認済みの購読または社員の識別情報を監査用に整理する。', value: renderPolicyTemplate(authenticatedSubjectTemplate, authenticatedSubjectSource, authenticatedSubjectFragmentSlots) }
}

// 確認したEntraユーザーの同じ利用枠とカウンターを全APIで共有します。
resource rateLimitFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-rate-limit'
  properties: { format: 'rawxml', description: '認証済みEntraユーザーの利用回数枠を適用し、取消・切断の免除と既存カウンターを維持する。', value: renderPolicyTemplate(rateLimitTemplate, rateLimitSource, rateLimitFragmentSlots) }
}
// 本文を扱うnative token policyを、HTTPの対応APIだけから参照します。
resource tokenLimitFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-token-limits'
  properties: { format: 'rawxml', description: '操作の実効接続先がFoundry OpenAIである対応HTTP操作の毎分・累積トークン枠を、認証済みEntraユーザーの既存カウンターへ渡す。社員の認証と利用枠の検査は先行処理へ集約する。内部対象一覧の不備は既存on-errorへ渡し、正常な空一覧・対象外・免除とnative計数の条件は維持する。', value: renderPolicyTemplate(tokenLimitTemplate, tokenLimitSource, tokenLimitFragmentSlots) }
}
resource entraUserProfileFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-entra-user-profile'
  properties: { format: 'rawxml', description: '認証済み社員JSONを解析し、共通認証で検査済みの利用枠を後段へ渡します。構造・必須項目・型・範囲・期間の検査は先行共通認証へ集約し、取得失敗は補完せず既存on-errorへ渡します。', value: renderPolicyTemplate(entraUserProfileTemplate, entraUserProfileSource, entraUserProfileFragmentSlots) }
}

// 追跡候補の検査と下流IDの退避。fragment内で本文やtokenをログへ出しません。
resource auditContextFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-audit-context'
  properties: { format: 'rawxml', description: '親要求IDを追跡候補として保持し、監査用モデル名を選びます。認証・所有者確認には使いません。', value: auditContextFragmentXml }
}
// 本文の受信完了を前提にするHTTPモデル監査だけを独立させます。
// WebSocket/MCPからは参照せず、未実行分岐の本文読取りによるbufferも防ぎます。
resource auditBodyModelFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-audit-body-model'
  properties: { format: 'rawxml', description: '通常HTTPのモデル入力から監査用のモデル名だけを取得する。本文を保持して下流へ渡し、WebSocket・MCPへは共用しない。', value: auditBodyModelFragmentXml }
}
resource backendAuditFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-backend-audit'
  properties: { format: 'rawxml', description: '提供元の要求IDを共有応答ヘッダーの上書き前に退避します。既知ヘッダーの単一値・形式だけを確認し、本文は読みません。', value: backendAuditFragmentXml }
}

// 各ポリシーから共通利用する最小限の監査情報出力。本文や認証値は記録しません。
resource gatewayTelemetryFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-gateway-telemetry'
  properties: { format: 'rawxml', description: '要求の追跡情報と認証・利用許可の結果を監査ログへ出力する。本文や資格情報を記録しない。', value: gatewayTelemetryFragmentValue(gatewayTelemetryTemplate, gatewayTelemetrySource, false) }
}
// 対象の操作で、本文読取り前にContent-Typeの宣言が一つあることを確認します。
resource apiInputValidationFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-api-input-validation'
  properties: { format: 'rawxml', description: '対象操作について、本文読取り前にContent-Typeの欠落・複数値・空欄を確認する。MIMEの許可一覧は使わず、結果を呼出し元の拒否処理と監査へ渡す。', value: renderPolicyTemplate(apiInputValidationTemplate, apiInputValidationSource, apiInputValidationFragmentSlots) }
}
// 操作の基本設定へモデル別の差分を反映し、後続処理が参照する設定を用意します。
resource apiOperationSettingsFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-api-operation-settings'
  properties: { format: 'rawxml', description: '操作の基本設定を取得し、モデル名と別名を解決してモデル別の差分を重ねる。クライアントのprofile指定は使わず、選択結果と入力・設定不備を呼出し元へ渡す。', value: renderPolicyTemplate(apiOperationSettingsTemplate, apiOperationSettingsSource, apiOperationSettingsFragmentSlots) }
}
// HTTP APIで、共通認証が確認した利用者と監査用ラベルを後続処理へ引き継ぎます。
resource apiAuthenticationResultFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-api-authentication-result'
  properties: { format: 'rawxml', description: '共通認証済みの購読または社員情報を後続へ引き継ぎ、操作・モデル設定の監査用ラベルを添える。認証条件の再検査は行わず、内部情報の引き渡し失敗を呼出し元へ返す。', value: renderPolicyTemplate(apiAuthenticationResultTemplate, apiAuthenticationResultSource, apiAuthenticationResultFragmentSlots) }
}
// HTTP APIで準備済みの操作設定を使い、対象操作・モデル・本文の既存条件を確認します。
resource apiTargetValidationFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-api-target-validation'
  properties: { format: 'rawxml', description: 'HTTPの準備済み操作設定に従い、モデル・対象操作・本文の既存条件を確認する。内部設定の読取り失敗500と既存条件の拒否403を分け、許可・状態・原因を呼出し元の監査と応答へ渡す。', value: renderPolicyTemplate(apiTargetValidationTemplate, apiTargetValidationSource, apiTargetValidationFragmentSlots) }
}
// 宣言から選んだFoundryのChat操作で、保存指定だけをfalseへ上書きする本文を用意します。
resource chatStoreDisabledFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-chat-store-disabled'
  properties: { format: 'rawxml', description: '対象Chatの本文をJSONオブジェクトとして一度だけ読み、storeをfalseへ揃える。model・messages・stream等は維持し、本文読取り不備の監査・400応答はHTTP APIへ委ねる。', value: renderPolicyTemplate(chatStoreDisabledTemplate, chatStoreDisabledSource, chatStoreDisabledFragmentSlots) }
}
// 操作・受付が明示したラベルでクエリの概要を観測します。要求の許可・拒否は変更しません。
resource queryObservationFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-query-observation'
  properties: { format: 'rawxml', description: 'HTTP操作またはMCP受付が明示した観測ラベルを選び、URLクエリの有無と複数値を監査へ渡す。要求の許可・拒否や本文・モデル機能は変更しない。', value: renderPolicyTemplate(queryObservationTemplate, queryObservationSource, queryObservationFragmentSlots) }
}
resource queryTelemetryFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-query-telemetry'
  properties: { format: 'rawxml', description: 'API設定で選んだ観測ラベルのクエリ概要と処理段階を、要求情報と認証済み利用者情報に関連付けて監査へ記録する。本文、URL、クエリの名前や値、資格情報は記録しない。', value: renderPolicyTemplate(queryTelemetryTemplate, queryTelemetrySource, queryTelemetryFragmentSlots) }
}
// 選択済みの操作設定に従い、受信本文のサイズを確認する共通部品です。
resource modelLimitsFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-model-limits'
  properties: { format: 'rawxml', description: '選択済みの操作設定に従い、受信本文のサイズを確認する。内部設定不備500と本文超過413の結果を呼出し元の監査と応答へ渡す。', value: renderPolicyTemplate(modelLimitsTemplate, modelLimitsSource, modelLimitsFragmentSlots) }
}
// 音声合成のSSMLについて、本文サイズとDTD・エンティティ宣言を一度の本文読取りで確認します。
resource speechSsmlGuardFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-speech-ssml-guard'
  properties: { format: 'rawxml', description: '対象の音声合成操作で、本文を一度だけ読み、サイズとDTD・エンティティ宣言を確認する。XMLの解析や外部参照は行わず、結果を呼出し元の監査と応答へ渡す。', value: renderPolicyTemplate(speechSsmlGuardTemplate, speechSsmlGuardSource, speechSsmlGuardFragmentSlots) }
}
// 残すDocument固有の入力確認を、利用回数・下流接続・正常受付の記録より前に実行します。
resource documentInputValidationFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-document-input-validation'
  properties: { format: 'rawxml', description: 'Document解析のmodelの存在・単一値・空欄とURL階層指定を確認し、入力不備400と内部変数の引渡し失敗500を呼出し元へ渡す。モデル一覧や本文は読み取らず、原本と提供元JSONの受付可否は提供元へ任せる。', value: renderPolicyTemplate(documentInputValidationTemplate, documentInputValidationSource, documentInputValidationFragmentSlots) }
}
// 本人の登録済み OAuth 接続を解決する共通部品です。
resource credentialConnectionFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-credential-connection'
  properties: { format: 'rawxml', description: '共通認証が確定した呼出者IDでCM接続を選び、内部IDの引き渡し不備は500として呼出し元APIへ返す。接続所有者・本人セッション・取得トークンのGraph本人ID・期限・失効を照合する。', value: replace(renderCredentialLogin(replace(renderPolicyTemplate(credentialConnectionTemplate, credentialCallerSource, credentialConnectionFragmentSlots), '__CREDENTIAL_CHALLENGE_XML__', credentialChallengeXml), credentialSettings), '__GATEWAY_TELEMETRY_XML__', credentialGatewayTelemetryBody) }
}
// 本人照合後の業務利用確定と認証案内。上のfragmentと同階層で呼び出します。
resource credentialSessionUseFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-credential-session-use'
  properties: { format: 'rawxml', description: '接続所有者・Graph本人照合後にSQLで使用直前の期限・失効を確認し、応答の世代・期限・更新候補を検査して再認証案内と下流認証を準備する。', value: replace(renderCredentialLogin(replace(renderPolicyTemplate(credentialSessionUseTemplate, credentialUseResultSource, credentialSessionUseFragmentSlots), '__CREDENTIAL_CHALLENGE_XML__', credentialChallengeXml), credentialSettings), '__GATEWAY_TELEMETRY_XML__', credentialGatewayTelemetryBody) }
}
// 外部資格情報が失効した場合に、安全な再認証案内へ変換する共通部品です。
resource credentialConnectionOutboundFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'core-credential-connection-outbound'
  properties: { format: 'rawxml', description: '下流claims要求・失効を安全な403／409案内へ変換し、検証済みの接続世代・期限・更新候補だけを返します。', value: replace(renderCredentialLogin(replace(credentialConnectionOutboundTemplate, '__CREDENTIAL_CHALLENGE_XML__', credentialChallengeXml), credentialSettings), '__GATEWAY_TELEMETRY_XML__', credentialGatewayTelemetryBody) }
}

// Realtime の補助 WebSocket 接続で所有者チケットを照合する共通部品です。
resource realtimeObserverFragment 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = if (!empty(observerApis)) {
  parent: apim
  name: 'core-realtime-observer'
  properties: { format: 'rawxml', description: 'Realtime補助接続の前に、確認済み所有者と署名チケットの通話・期限を照合します。本人受取りの内部不成立はonHandshake側で500に分類し、受信専用のBFF中継とは分けます。', value: realtimePolicyValue(realtimeObserverTemplate, '__REALTIME_OBSERVER_OWNER_CSHARP__', realtimeObserverOwnerSource, realtimeOwnerContextTemplate, realtimeOwnerContextSource, '  ', '      ') }
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
    // 通常配備とAPIの再公開では、同じ公開契約からAPIMの説明を生成します。
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
            endpoint => union({ uriTemplate: mcpProxyPublicPath(api, endpoint) }, api.mcpProxy.transport == 'sse' ? { method: endpoint.name == 'sse' ? 'GET' : 'POST' } : {}))
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
    displayName: 'Entra model users'
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

// Model / Tool / Hosted Agent 等のキー用Productを管理者にだけ表示します。
resource protectedProducts 'Microsoft.ApiManagement/service/products@2024-05-01' = [for productId in productIds: {
  parent: apim
  name: productId
  properties: {
    displayName: productId == releasedProductId ? 'Released APIs' : productId
    description: productId == releasedProductId ? 'All released business APIs. API-specific validation and personal connection requirements still apply.' : 'Verification API bundle ${productId}; caller authorization remains API-scoped.'
    subscriptionRequired: true
    approvalRequired: true
    state: 'notPublished'
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
// 通常 API の認証・認可・監査・転送規則を登録します。WebSocket は下の onHandshake に設定します。
// 大きいXMLを一斉に書くとDeveloperの管理処理が詰まるため、１件の完了後に次を送ります。
// APIの呼出回数制限ではなく、構築時の管理操作だけを直列にする指定です。
@batchSize(1)
resource apiPolicies 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = [for (api, index) in apiDefinitions: if (api.apiType != 'websocket') {
  parent: apis[index]
  name: 'policy'
  properties: { format: 'rawxml', value: apiPolicyValue(api, configuredApiTemplate(api.apiType), rateLimitErrorXml, policySettings[index]) }
  dependsOn: [apiSettingsFragment, apiRequestPreparationFragment, apiInputValidationFragment, apiOperationSettingsFragment, apiAuthenticationResultFragment, apiTargetValidationFragment, chatStoreDisabledFragment, auditContextFragment, auditBodyModelFragment, backendAuditFragment, rateLimitFragment, tokenLimitFragment, entraUserProfileFragment, backends, operationBackends, backendAudienceNamedValues, gatewayTelemetryFragment, queryObservationFragment, queryTelemetryFragment, modelLimitsFragment, speechSsmlGuardFragment, documentInputValidationFragment, credentialConnectionFragment, credentialSessionUseFragment, credentialConnectionOutboundFragment, backendSecretNamedValues, operationBackendSecretNamedValues, realtimeObserverFragment]
}]
// WebSocket API では API スコープの policy を作れないため、システムが自動生成する
// onHandshake 操作へ同じ接続開始時の認証・認可を設定します。操作本体は作成・変更しません。
// <base/> による全体の公開ガード継承も維持し、確立後のフレームはここで検査しません。
// https://learn.microsoft.com/azure/api-management/websocket-api
// HTTP等のAPIポリシーが終わってから、握手ポリシーも１件ずつ適用します。
@batchSize(1)
resource websocketPolicies 'Microsoft.ApiManagement/service/apis/operations/policies@2024-05-01' = [for (api, index) in apiDefinitions: if (api.apiType == 'websocket') {
  name: '${apim.name}/${api.name}/onHandshake/policy'
  properties: { format: 'rawxml', value: apiPolicyValue(api, configuredApiTemplate(api.apiType), rateLimitErrorXml, policySettings[index]) }
  dependsOn: [apiPolicies, apiSettingsFragment, apiRequestPreparationFragment, auditContextFragment, backendAuditFragment, rateLimitFragment, entraUserProfileFragment, apis, backends, backendAudienceNamedValues, gatewayTelemetryFragment, backendSecretNamedValues, realtimeObserverFragment]
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
      description: '原本ファイルまたは提供元JSONを変換せず送信する。受付後は202と所有者に束縛した操作チケットを返し、課金対象のPOSTを再送しない。'
      queryParameters: [{ name: 'model', required: true, type: 'string' }]
      representations: map(entry.constraint.contentTypes, mime => { contentType: mime })
    } : entry.constraint.kind == 'documentAnalysisStatus' ? {
      description: '認証済み利用者が解析の状態・結果を取得する。チケットの利用者・操作・API・接続先を照合する。'
      headers: [{ name: 'X-Core-Document-Ticket', required: true, type: 'string' }]
    } : entry.operation.?request ?? {}
    responses: entry.constraint.kind == 'pii' ? concat([
      { statusCode: 200, description: 'Redacted text and allowlisted metadata only.', representations: [{ contentType: 'application/json', schemaId: 'pii-public-contract', typeName: 'PiiRedactResult' }] }
    ], map([400, 413, 422, 429, 500, 502, 504], statusCode => {
      statusCode: statusCode
      description: 'Safe failure code and correlation ID. No input text or upstream diagnostic.'
      representations: [{ contentType: 'application/json', schemaId: 'pii-public-contract', typeName: 'PiiRedactError' }]
    })) : contains(['documentAnalysis', 'documentAnalysisStatus'], entry.constraint.kind) ? concat(
      entry.constraint.kind == 'documentAnalysis' ? [
        { statusCode: 202, description: '解析要求を受け付けた。操作チケットを保存し、固定のGateway状態確認パスを照会する。', representations: [{ contentType: 'application/json', schemaId: 'document-analysis-public-contract', typeName: 'DocumentAnalysisAccepted' }] }
      ] : [
        { statusCode: 202, description: '解析は処理中。Retry-Afterに従って状態を確認し、解析要求を再送しない。', representations: [{ contentType: 'application/json', schemaId: 'document-analysis-public-contract', typeName: 'DocumentAnalysisRunning' }] }
        { statusCode: 200, description: '提供元のanalyzeResultとGatewayの要求IDを返す。', representations: [{ contentType: 'application/json', schemaId: 'document-analysis-public-contract', typeName: 'DocumentAnalysisResult' }] }
      ], map([400, 403, 404, 410, 413, 415, 422, 429, 500, 502, 504], statusCode => {
        statusCode: statusCode
        description: '原因を示す固定エラーコードと要求IDを返す。提供元の操作URL・診断本文・内部設定は返さない。'
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
// DocumentのAPI版は公開スキーマから補完済みの操作設定を使い、開始・取得・照合へ同じ値を渡します。
func operationPolicyValue(entry object, policyTemplate string) string => entry.constraint.kind == 'pii'
      ? replace(policyTemplate, '__PII_SETTINGS_BASE64__', base64(string(entry.constraint)))
      : contains(['documentAnalysis', 'documentAnalysisStatus'], entry.constraint.kind)
        ? replace(replace(policyTemplate, '__DOCUMENT_BASE_URL__', xmlText(entry.backendUrl)), '__DOCUMENT_API_VERSION__', xmlText(entry.constraint.apiVersion))
      // 短期 token の取得先は、登録済み Foundry backend から固定生成します。
      // 利用者の入力 URL や公開 params に取得先を追加しません。
      : entry.constraint.kind == 'realtimeCreate' ? replace(policyTemplate, '__REALTIME_CLIENT_SECRETS_URL__', xmlText('${entry.backendUrl}/realtime/client_secrets'))
      : entry.constraint.kind == 'realtimeHangup' ? policyTemplate
      : entry.constraint.kind == 'liveCreate' ? policyTemplate
      : join(['<policies><!-- APIの認証・権限・入力上限を継承し、このOperationの要求形式と上流URIを適用します。 --><inbound>'
          '<base />'
          // 本文の検査方式は、API側の共通部品と操作設定で選びます。
          // TTSのSSML検査もAPI側で完了してから、この操作ポリシーへ戻ります。
          contains(entry.operation, 'staticResponse') ? '<return-response><set-status code="200" reason="OK" /><set-header name="apim-request-id" exists-action="override"><value>@(context.RequestId.ToString())</value></set-header><set-header name="Content-Type" exists-action="override"><value>application/json</value></set-header><set-body>${xmlText(string(entry.operation.staticResponse.body))}</set-body></return-response>' : ''
          modelRoutingPolicy(entry.constraint)
          contains(entry.operation, 'backendUrlTemplate') ? entry.constraint.kind == 'pathModel'
            ? '<!-- モデルを含むパス値を一要素として符号化し、登録済みの上流URIへ書き換えます。固定queryは保持します。 --><rewrite-uri template="${xmlText(pathModelBackendUriExpression(entry.operation.backendUrlTemplate))}" copy-unmatched-params="false" />'
            : '<!-- 登録済みの上流URIへ書き換えます。転送queryは定義した値に揃えます。 --><rewrite-uri template="${xmlText(entry.operation.backendUrlTemplate)}" copy-unmatched-params="false" />' : ''
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
    value: operationPolicyValue(entry, selectOperationTemplate(entry.constraint.kind, configuredOperationTemplate(piiPolicyTemplate, rateLimitErrorXml, operationEntraAuthenticationErrorXml), configuredOperationTemplate(documentAnalysisPolicyTemplate, rateLimitErrorXml, operationEntraAuthenticationErrorXml), configuredOperationTemplate(documentAnalysisStatusPolicyTemplate, rateLimitErrorXml, operationEntraAuthenticationErrorXml), realtimePolicyValue(realtimeCreatePolicyTemplate, '__REALTIME_CREATE_TICKET_CSHARP__', realtimeCreateTicketSource, realtimeOwnerContextTemplate, realtimeOwnerContextSource, '    ', '            '), realtimePolicyValue(realtimeHangupPolicyTemplate, '__REALTIME_HANGUP_OWNER_CSHARP__', realtimeHangupOwnerSource, realtimeOwnerContextTemplate, realtimeOwnerContextSource, '    ', '      '), liveCreatePolicyTemplate))
  }
  dependsOn: [apiPolicies, websocketPolicies, entraAuthenticationErrorFragment]
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
  properties: { format: 'rawxml', value: mcpApiPolicyValue(api, generatedMcpPolicyTemplate, rateLimitErrorXml) }
  dependsOn: [apiSettingsFragment, apiRequestPreparationFragment, auditContextFragment, backendAuditFragment, gatewayTelemetryFragment, queryObservationFragment, queryTelemetryFragment, mcpTools]
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
          endpoint => union({ uriTemplate: mcpProxyPublicPath(api, endpoint) }, api.mcpProxy.transport == 'sse' ? { method: endpoint.name == 'sse' ? 'GET' : 'POST' } : {}))
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
    map(apiDefinitions, api => { suffix: api.apiType == 'websocket' ? '/apis/${api.name}/operations/onHandshake/policies/policy' : '/apis/${api.name}/policies/policy', value: apiPolicyValue(api, configuredApiTemplate(api.apiType), rateLimitErrorXml, policySettings[indexOf(apiDefinitions, api)]) }),
    map(operationEntries, entry => { suffix: '/apis/${entry.apiName}/operations/${entry.operation.name}/policies/policy', value: operationPolicyValue(entry, selectOperationTemplate(entry.constraint.kind, configuredOperationTemplate(piiPolicyTemplate, rateLimitErrorXml, operationEntraAuthenticationErrorXml), configuredOperationTemplate(documentAnalysisPolicyTemplate, rateLimitErrorXml, operationEntraAuthenticationErrorXml), configuredOperationTemplate(documentAnalysisStatusPolicyTemplate, rateLimitErrorXml, operationEntraAuthenticationErrorXml), realtimePolicyValue(realtimeCreatePolicyTemplate, '__REALTIME_CREATE_TICKET_CSHARP__', realtimeCreateTicketSource, realtimeOwnerContextTemplate, realtimeOwnerContextSource, '    ', '            '), realtimePolicyValue(realtimeHangupPolicyTemplate, '__REALTIME_HANGUP_OWNER_CSHARP__', realtimeHangupOwnerSource, realtimeOwnerContextTemplate, realtimeOwnerContextSource, '    ', '      '), liveCreatePolicyTemplate)) }),
    map(mcpApis, api => { suffix: '/apis/${api.mcp.name}/policies/policy', value: mcpApiPolicyValue(api, generatedMcpPolicyTemplate, rateLimitErrorXml) }),
    credentialLoginEnabled ? concat(
      [{ suffix: '/apis/credential-manager-login/policies/policy', value: credentialLoginApiPolicyXml }],
      map(filter(credentialLoginOperations, item => item.apiId == 'credential-manager-login'), item => {
        suffix: '/apis/credential-manager-login/operations/${item.name}/policies/policy'
        value: renderCredentialLoginOperation(item.name == 'start' ? renderPolicyTemplate(credentialLoginStartPolicyTemplate, credentialLoginStartCallerSource, credentialLoginStartPolicySlots) : item.name == 'status' ? renderPolicyTemplate(credentialLoginStatusPolicyTemplate, credentialLoginStatusCallerSource, credentialLoginStatusPolicySlots) : item.name == 'logout' ? renderPolicyTemplate(credentialLoginLogoutPolicyTemplate, credentialLoginLogoutCallerSource, credentialLoginLogoutPolicySlots) : item.name == 'session-commit' ? renderPolicyTemplate(credentialLoginSessionCommitPolicyTemplate, credentialLoginSessionCommitCallerSource, credentialLoginSessionCommitPolicySlots) : item.name == 'logout-all' ? renderPolicyTemplate(credentialLoginLogoutAllPolicyTemplate, credentialLoginLogoutAllCallerSource, credentialLoginLogoutAllPolicySlots) : renderPolicyTemplate(credentialLoginDisconnectPolicyTemplate, credentialLoginDisconnectCallerSource, credentialLoginDisconnectPolicySlots), credentialSettings, operationEntraAuthenticationErrorXml)
      })
    ) : [],
    [
      { suffix: '/policyFragments/core-api-settings', value: renderPolicyTemplate(apiSettingsTemplate, apiSettingsSource, apiSettingsFragmentSlots) }
      { suffix: '/policyFragments/core-api-request-preparation', value: apiRequestPreparationTemplate }
      { suffix: '/policyFragments/core-api-input-validation', value: renderPolicyTemplate(apiInputValidationTemplate, apiInputValidationSource, apiInputValidationFragmentSlots) }
      { suffix: '/policyFragments/core-api-operation-settings', value: renderPolicyTemplate(apiOperationSettingsTemplate, apiOperationSettingsSource, apiOperationSettingsFragmentSlots) }
      { suffix: '/policyFragments/core-api-authentication-result', value: renderPolicyTemplate(apiAuthenticationResultTemplate, apiAuthenticationResultSource, apiAuthenticationResultFragmentSlots) }
      { suffix: '/policyFragments/core-api-target-validation', value: renderPolicyTemplate(apiTargetValidationTemplate, apiTargetValidationSource, apiTargetValidationFragmentSlots) }
      { suffix: '/policyFragments/core-chat-store-disabled', value: renderPolicyTemplate(chatStoreDisabledTemplate, chatStoreDisabledSource, chatStoreDisabledFragmentSlots) }
      { suffix: '/policyFragments/core-request-context', value: renderPolicyTemplate(requestContextTemplate, requestContextSource, requestContextFragmentSlots) }
      { suffix: '/policyFragments/core-trusted-ingress', value: trustedIngressFragmentValue(trustedIngressTemplate, trustedIngressSource, gatewayTelemetryTemplate, gatewayTelemetrySource) }
      { suffix: '/policyFragments/core-credential-browser-exception', value: browserExceptionFragmentValue(browserExceptionTemplate, browserExceptionSource, credentialLoginEnabled) }
      { suffix: '/policyFragments/core-common-authentication', value: commonAuthenticationFragmentValue(commonAuthenticationTemplate, commonAuthenticationSource, entraAuthentication.?enabled ?? false, gatewayTelemetryTemplate, gatewayTelemetrySource) }
      { suffix: '/policyFragments/core-audit-authenticated-subject', value: renderPolicyTemplate(authenticatedSubjectTemplate, authenticatedSubjectSource, authenticatedSubjectFragmentSlots) }
      { suffix: '/policyFragments/core-rate-limit', value: renderPolicyTemplate(rateLimitTemplate, rateLimitSource, rateLimitFragmentSlots) }
      { suffix: '/policyFragments/core-token-limits', value: renderPolicyTemplate(tokenLimitTemplate, tokenLimitSource, tokenLimitFragmentSlots) }
      { suffix: '/policyFragments/core-entra-user-profile', value: renderPolicyTemplate(entraUserProfileTemplate, entraUserProfileSource, entraUserProfileFragmentSlots) }
      { suffix: '/policyFragments/core-audit-context', value: auditContextFragmentXml }
      { suffix: '/policyFragments/core-audit-body-model', value: auditBodyModelFragmentXml }
      { suffix: '/policyFragments/core-backend-audit', value: backendAuditFragmentXml }
      { suffix: '/policyFragments/core-gateway-telemetry', value: gatewayTelemetryFragmentValue(gatewayTelemetryTemplate, gatewayTelemetrySource, false) }
      { suffix: '/policyFragments/core-query-observation', value: renderPolicyTemplate(queryObservationTemplate, queryObservationSource, queryObservationFragmentSlots) }
      { suffix: '/policyFragments/core-query-telemetry', value: renderPolicyTemplate(queryTelemetryTemplate, queryTelemetrySource, queryTelemetryFragmentSlots) }
      { suffix: '/policyFragments/core-model-limits', value: renderPolicyTemplate(modelLimitsTemplate, modelLimitsSource, modelLimitsFragmentSlots) }
      { suffix: '/policyFragments/core-speech-ssml-guard', value: renderPolicyTemplate(speechSsmlGuardTemplate, speechSsmlGuardSource, speechSsmlGuardFragmentSlots) }
      { suffix: '/policyFragments/core-document-input-validation', value: renderPolicyTemplate(documentInputValidationTemplate, documentInputValidationSource, documentInputValidationFragmentSlots) }
      { suffix: '/policyFragments/core-credential-connection', value: replace(renderCredentialLogin(replace(renderPolicyTemplate(credentialConnectionTemplate, credentialCallerSource, credentialConnectionFragmentSlots), '__CREDENTIAL_CHALLENGE_XML__', credentialChallengeXml), credentialSettings), '__GATEWAY_TELEMETRY_XML__', credentialGatewayTelemetryBody) }
      { suffix: '/policyFragments/core-credential-session-use', value: replace(renderCredentialLogin(replace(renderPolicyTemplate(credentialSessionUseTemplate, credentialUseResultSource, credentialSessionUseFragmentSlots), '__CREDENTIAL_CHALLENGE_XML__', credentialChallengeXml), credentialSettings), '__GATEWAY_TELEMETRY_XML__', credentialGatewayTelemetryBody) }
      { suffix: '/policyFragments/core-credential-connection-outbound', value: replace(renderCredentialLogin(replace(credentialConnectionOutboundTemplate, '__CREDENTIAL_CHALLENGE_XML__', credentialChallengeXml), credentialSettings), '__GATEWAY_TELEMETRY_XML__', credentialGatewayTelemetryBody) }
    ],
    !empty(observerApis) ? [{ suffix: '/policyFragments/core-realtime-observer', value: realtimePolicyValue(realtimeObserverTemplate, '__REALTIME_OBSERVER_OWNER_CSHARP__', realtimeObserverOwnerSource, realtimeOwnerContextTemplate, realtimeOwnerContextSource, '  ', '      ') }] : [],
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

// 公開 HTTPS の入口と WAF を作り、通常の HTTP API を Firewall 経由で内部 APIM へ送ります。
// 証明書は Key Vault をマネージド ID で参照し、秘密鍵をこのファイルや出力に書きません。
// gRPC 有効時は別 TCP/TLS リスナーを使います。この経路は HTTP WAF の検査対象ではありません。

targetScope = 'resourceGroup'

@minLength(1)
@description('全リソース名の製品名部分。例は egakumi です。')
param productName string
@minLength(1)
@description('同じ製品内の環境を区別する名前。test / dev / prod など。名前自体で設定は切り替えません。')
param environmentName string
@description('環境名と製品名をこの順で連結した共通名。作成と更新で同じ値を使います。')
param resourceStem string = '${environmentName}-${productName}'
@description('このリソース群を配置する Azure リージョン。')
param location string
@description('製品・環境・所有者等を整理するタグ。資格情報や個人情報を入れません。')
param tags object = {}
@minLength(1)
@description('この資源の NIC や内部入口を接続するサブネットの完全なリソース ID。')
param subnetId string
@minLength(1)
@description('AppGW に割り当てる Standard / Static Public IP のリソース ID。')
param publicIpId string
@description('確定した AppGW の公開 IPv4。公開 DNS の案内に使い、推測で作りません。')
param publicIpAddress string = ''
@minLength(7)
@description('内部 APIM の作成後に割り当てられたプライベート IP。UDR・許可規則の宛先に使います。')
param apimPrivateIp string
@minLength(4)
@description('内部 APIM の既定ホスト名。通常 API の Host ヘッダーとヘルスチェックに使います。')
param apimHostName string
@description('Core管理ポータルの必須公開ホスト。')
@minLength(4)
param portalHostName string
@description('internal ACA環境の実VIP。AppGWからFirewall経由で接続します。')
@minLength(7)
param portalBackendIp string
@description('ACAアプリの既定FQDN。HTTPSのHostとSNIに使用します。')
@minLength(4)
param portalBackendHost string
@minLength(4)
@description('利用者に案内するAPI公開FQDN。')
param publicHostName string
@minLength(25)
@description('AppGW が読む証明書 secret の URI。版を固定しない URI で更新を取り込みます。')
#disable-next-line secure-secrets-in-params // Reference URI only; no certificate bytes, password, or SAS token.
param keyVaultCertificateSecretId string
@minLength(1)
@description('証明書参照用ユーザー割り当てマネージド ID のリソース ID。Entra アプリの ID ではありません。')
param certificateIdentityId string
@description('診断ログと監視クエリの送信先 Workspace のリソース ID。')
param workspaceId string
@description('長期監査ログの保存先 Storage のリソース ID。')
param auditStorageId string
@description('この段階で監査ログの転送も設定するか。保存先と保護が準備できてから有効にします。')
param enableAuditCollection bool = false
@allowed(['WAF_v2', 'Standard_v2'])
@description('AppGW の SKU。WAF_v2 は HTTP WAF を提供し、gRPC の TCP リスナーには適用されません。')
param skuName string = 'WAF_v2'
@minValue(0)
@maxValue(100)
@description('AppGW 自動拡張の最小インスタンス数。常時費用と耐障害性に関わります。')
param autoscaleMinCapacity int = 2
@minValue(2)
@maxValue(125)
@description('AppGW 自動拡張の最大インスタンス数。想定負荷に合わせて上限を決めます。')
param autoscaleMaxCapacity int = 5
@description('配置する可用性ゾーンの番号。空配列はゾーンを固定せず、3 ゾーン指定は冗長配置に使います。')
param zones array = []
@description('企業管理の既存 WAF ポリシー ID。空欄なら今回の設定から新しく作成します。')
param existingWafPolicyId string = ''
@allowed(['Prevention', 'Detection'])
@description('WAF の動作。Detection は検知記録、Prevention は該当要求の拒否を行います。')
param wafMode string = 'Prevention'
@minValue(8)
@maxValue(2000)
@description('WAF が受け付ける要求本文サイズの上限（KiB）。アップロードとモデル入力の設計に合わせます。')
param wafMaxRequestBodySizeInKb int = 128
@minValue(8)
@maxValue(2000)
@description('WAF が本文検査する長さ（KiB）。受入上限とは別なので両方を確認します。')
param wafRequestBodyInspectLimitInKb int = 128
@minValue(1)
@maxValue(4000)
@description('WAF のファイルアップロード上限（MiB）。モデルやサーバー側にも独自上限があります。')
param wafFileUploadLimitInMb int = 100
@description('企業固有の WAF カスタム規則。標準の検査に追加して使用します。')
param wafCustomRules array = []
@description('WAF の管理規則から除外する属性の一覧。誤検知を確認した範囲だけを指定します。')
param wafExclusions array = []
@description('公開 API 契約から導出した、SSML/WAV を POST する正規の完全パス。この URI だけ Content-Type の誤検知を調整する内部入力です。')
param customMimePaths array = []
@description('固定の文書解析POST/binary/1MiB契約から導出した完全パス。本文上限とMIME規則の調整をこのURIだけに適用します。')
param documentUploadPaths array = []
@description('Azure Speech正本の完全POSTパス。raw SSML/WAVの930110誤検知だけを調整し、URI/query/header/cookieは専用Block規則で検査します。')
param speechRawBodyPaths array = []
@description('native MCP 従来 SSE の登録済み message パスから導出した公開 POST の完全パス。session_id の規則除外をこの URI だけへ限定する内部入力です。')
param legacyMcpMessagePaths array = []
@description('vision 操作契約の完全パス。インライン画像のJSON欄だけをXSS誤検知の調整対象にする内部入力です。')
param inlineImagePaths array = []
@description('pdf 操作契約の完全パス。インラインPDFのJSON欄だけをXSS誤検知の調整対象にする内部入力です。')
param inlinePdfPaths array = []
@description('audio-chat 操作契約の完全パス。インライン音声のJSON欄だけをSQLi誤検知の調整対象にする内部入力です。')
param inlineAudioPaths array = []
@description('公開API契約から有限値へ展開した完全パス。データ欄の除外はこの一覧に存在するURIだけに適用します。')
param publishedApiPaths array = []
@description('WAF ログから除去する情報の追加規則。既定の認証情報除去は維持します。')
param additionalWafLogScrubbingRules array = []
@minValue(1)
@maxValue(86400)
@description('AppGW がバックエンド応答を待つ秒数。長い推論やストリーミングにも影響します。')
param backendRequestTimeoutSeconds int = 600
@description('通常 HTTPS とは別に native gRPC 用の TCP/TLS 経路を作るか。')
param grpcEnabled bool = false
@minValue(1)
@maxValue(64999)
@description('利用者が gRPC で接続する公開 TCP ポート。通常は 8443 です。')
param grpcPublicPort int = 8443
@minValue(1)
@maxValue(65535)
@description('AppGW が内部 APIM の gRPC 入口へ接続する TCP ポート。通常は TLS の 443 です。')
param grpcBackendPort int = 443
@description('AppGW の HTTP 書き換え経路を APIM が見分ける共有値。利用者の認証トークンとは別です。')
@secure()
param l7IngressSecret string = ''

var gatewayName = 'agw-${resourceStem}'
var gatewayId = resourceId('Microsoft.Network/applicationGateways', gatewayName)
// 既存 WAF ポリシーが指定された場合は企業管理の定義を再利用し、標準ポリシーを二重作成しません。
var createWafPolicy = skuName == 'WAF_v2' && empty(existingWafPolicyId)
var documentPaths = filter(documentUploadPaths, path => path == '/tools/documents/v1/analyze')
var useDocumentWafPolicy = createWafPolicy && !empty(documentPaths)
// PythonのWAF reconcileと同じ正本を使用し、基盤再配備でも実測した除外を維持します。
var dataManifest = loadJsonContent('../../apps/gateway/waf-data-exclusions.json')
var dataProfiles = map(filter(dataManifest.profiles, profile => createWafPolicy && (profile.scope == 'portal' || length(filter(profile.paths, path => contains(publishedApiPaths, path) && !contains(documentPaths, path))) > 0)), profile => {
  name: profile.name
  scope: profile.scope
  paths: profile.scope == 'portal' ? profile.paths : filter(profile.paths, path => contains(publishedApiPaths, path) && !contains(documentPaths, path))
  exclusions: profile.exclusions
})
var dataGatewayPaths = flatten(map(filter(dataProfiles, profile => profile.scope == 'gateway'), profile => profile.paths))
var dataPortalPaths = flatten(map(filter(dataProfiles, profile => profile.scope == 'portal'), profile => profile.paths))
var speechPaths = filter(speechRawBodyPaths, path => contains(customMimePaths, path))
var shareSpeechMimePolicy = !empty(speechPaths) && empty(filter(customMimePaths, path => !contains(speechPaths, path)))
var useSeparateSpeechPolicy = createWafPolicy && !empty(speechPaths) && !shareSpeechMimePolicy
var remainingCustomMimePaths = filter(customMimePaths, path => !contains(dataGatewayPaths, path) && !contains(documentPaths, path) && (!useSeparateSpeechPolicy || !contains(speechPaths, path)))
var remainingLegacyMcpPaths = filter(legacyMcpMessagePaths, path => !contains(dataGatewayPaths, path))
// 企業が指定した既存ポリシーを、こちらの既定値で置換しません。
// その場合の Content-Type 調整は企業側ポリシーの管理者が同じ URI 範囲で行います。
var useCustomMimeWafPolicy = createWafPolicy && !empty(remainingCustomMimePaths)
var useLegacyMcpWafPolicy = createWafPolicy && !empty(remainingLegacyMcpPaths)
// A consolidated Chat URI can carry both image and audio profiles. Bind one
// combined policy to that exact URI rather than duplicate path-map entries.
var inlineMediaPaths = filter(inlineImagePaths, path => contains(inlineAudioPaths, path))
var inlineImageOnlyPaths = filter(inlineImagePaths, path => !contains(inlineMediaPaths, path))
var inlineAudioOnlyPaths = filter(inlineAudioPaths, path => !contains(inlineMediaPaths, path))
var remainingInlineMediaPaths = filter(inlineMediaPaths, path => !contains(dataGatewayPaths, path))
var remainingInlineImagePaths = filter(inlineImageOnlyPaths, path => !contains(dataGatewayPaths, path))
var remainingInlinePdfPaths = filter(inlinePdfPaths, path => !contains(dataGatewayPaths, path))
var remainingInlineAudioPaths = filter(inlineAudioOnlyPaths, path => !contains(dataGatewayPaths, path))
var useInlineMediaWafPolicy = createWafPolicy && !empty(remainingInlineMediaPaths)
var useInlineImageWafPolicy = createWafPolicy && !empty(remainingInlineImagePaths)
var useInlinePdfWafPolicy = createWafPolicy && !empty(remainingInlinePdfPaths)
var useInlineAudioWafPolicy = createWafPolicy && !empty(remainingInlineAudioPaths)
// 本人確認callbackはAPIの公開一覧が空でも必要です。企業管理の既存WAFは変更しません。
var useGatewayAuthWafPolicy = createWafPolicy && !contains(dataGatewayPaths, '/auth/connections/browser/identity-callback')
// APIと本人確認callbackの完全パスにだけ、用途別のWAF調整を適用します。
// Consoleは独立したホスティングのため、そのcallbackをCoreのWAF例外へ追加しません。
var useProtocolWafPolicies = useCustomMimeWafPolicy || useSeparateSpeechPolicy || useDocumentWafPolicy || useLegacyMcpWafPolicy || useInlineImageWafPolicy || useInlinePdfWafPolicy || useInlineAudioWafPolicy || useInlineMediaWafPolicy || useGatewayAuthWafPolicy || !empty(dataGatewayPaths)
// WAF ログにも認証値が残らないよう、ヘッダー・cookie・引数の既知の秘密項目をマスクします。
var defaultScrubbingRules = [for variable in ['RequestHeaderNames', 'RequestCookieNames', 'RequestArgNames', 'RequestJSONArgNames', 'RequestPostArgNames']: {
  state: 'Enabled'
  matchVariable: variable
  selectorMatchOperator: 'EqualsAny'
  selector: ''
}]

// URI 別ポリシーは親の設定を継承せず置換するため、保護設定を一つの定義で共有します。
// 通常 API と各プロトコルで本文検査・上限・秘密のログ除去・企業追加規則を同じに保ちます。
var sharedWafProperties = {
    policySettings: {
      state: 'Enabled'
      mode: wafMode
      requestBodyCheck: true
      maxRequestBodySizeInKb: wafMaxRequestBodySizeInKb
      requestBodyInspectLimitInKB: wafRequestBodyInspectLimitInKb
      requestBodyEnforcement: true
      fileUploadLimitInMb: wafFileUploadLimitInMb
      fileUploadEnforcement: true
      logScrubbing: { state: 'Enabled', scrubbingRules: concat(defaultScrubbingRules, additionalWafLogScrubbingRules) }
    }
    managedRules: {
      managedRuleSets: [{ ruleSetType: 'OWASP', ruleSetVersion: '3.2', ruleGroupOverrides: [] }]
      exclusions: wafExclusions
    }
    customRules: wafCustomRules
}

// 既存企業ポリシーが指定されていない場合の、共通 WAF 設定です。
resource wafPolicy 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2024-05-01' = if (createWafPolicy) {
  name: 'wafpol-agw-${resourceStem}'
  location: location
  tags: tags
  properties: sharedWafProperties
}

// Microsoftが発行する不透明な認可コードだけを、実測したSQLi誤検知の2規則から除外します。
// このポリシーはPortalの正規callback完全パスだけに関連付け、他の引数・規則は維持します。
resource portalAuthWafPolicy 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2024-05-01' = if (createWafPolicy) {
  name: 'wafpol-agw-${resourceStem}-portal-auth'
  location: location
  tags: tags
  properties: union(sharedWafProperties, {
    managedRules: union(sharedWafProperties.managedRules, {
      exclusions: concat(wafExclusions, [{
        matchVariable: 'RequestArgValues'
        selectorMatchOperator: 'Equals'
        selector: 'code'
        exclusionManagedRuleSets: [{
          ruleSetType: 'OWASP'
          ruleSetVersion: '3.2'
          ruleGroups: [{
            ruleGroupName: 'REQUEST-942-APPLICATION-ATTACK-SQLI'
            rules: [{ ruleId: '942430' }, { ruleId: '942440' }]
          }]
        }]
      }])
    })
  })
}

// Gatewayの本人確認callbackで、Microsoftの不透明なcode値に発生したSQLi誤検知だけを調整します。
// 専用policyは親policyを継承しないため、本文検査・ログ除去・企業追加規則を同じ設定から複製します。
// codeの引数名・state・他の引数・他の規則は除外せず、下の完全パスだけに関連付けます。
resource gatewayAuthWafPolicy 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2024-05-01' = if (useGatewayAuthWafPolicy) {
  name: 'wafpol-agw-${resourceStem}-gateway-auth'
  location: location
  tags: tags
  properties: union(sharedWafProperties, {
    managedRules: union(sharedWafProperties.managedRules, {
      exclusions: concat(wafExclusions, [{
        matchVariable: 'RequestArgValues'
        selectorMatchOperator: 'Equals'
        selector: 'code'
        exclusionManagedRuleSets: [{
          ruleSetType: 'OWASP'
          ruleSetVersion: '3.2'
          ruleGroups: [{
            ruleGroupName: 'REQUEST-942-APPLICATION-ATTACK-SQLI'
            rules: [{ ruleId: '942430' }, { ruleId: '942440' }, { ruleId: '942450' }]
          }]
        }]
      }])
    })
  })
}

// 正規の SSML/WAV MIME は OWASP 3.2 の920420を調整します。正本Speechだけは
// raw本文の930110も調整し、URI/query/header/cookieのtraversalを4Blockで補完します。
// 独自MIME契約へ930110の調整を広げず、930100を含む他の管理規則を維持します。
// Allow カスタム規則で全検査を通過させず、残りの管理規則と本文検査を有効に保ちます。
// このポリシーは下の exact path にだけ適用し、他の API では 920420 を維持します。
// HTTP 方式と MIME の許可判定は、同じ契約で公開する APIM 側の操作制約が行います。
// 920470（Content-Type 構文）は無効化しません。公開 WAV は単純な audio/wav とし、
// Speech 固有の codec/samplerate は APIM の固定 backendHeaders で付けます。
// https://learn.microsoft.com/azure/web-application-firewall/ag/per-site-policies
var speechControls = loadJsonContent('../../apps/gateway/waf-raw-body-controls.json')
var speechRulePriorities = filter(range(1, 100), candidate => !contains(map(wafCustomRules, rule => rule.priority), candidate))
var speechTraversalRules = [for (rule, index) in speechControls.rules: {
  name: rule.name
  priority: speechRulePriorities[index]
  ruleType: 'MatchRule'
  state: 'Enabled'
  action: 'Block'
  matchConditions: [{
    matchVariables: [{ variableName: rule.variableName }]
    operator: 'Regex'
    negationConditon: false
    matchValues: [speechControls.pattern]
    transforms: speechControls.transforms
  }]
}]
var rawBodyProperties = union(sharedWafProperties, {
  customRules: concat(wafCustomRules, speechTraversalRules)
  managedRules: union(sharedWafProperties.managedRules, {
    managedRuleSets: [{
      ruleSetType: 'OWASP'
      ruleSetVersion: '3.2'
      ruleGroupOverrides: [{
        ruleGroupName: 'REQUEST-920-PROTOCOL-ENFORCEMENT'
        rules: [{ ruleId: '920420', state: 'Disabled' }]
      }, {
        ruleGroupName: 'REQUEST-930-APPLICATION-ATTACK-LFI'
        rules: [{ ruleId: '930110', state: 'Disabled' }]
      }]
    }]
  })
})
resource customMimeWafPolicy 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2024-05-01' = if (useCustomMimeWafPolicy) {
  name: 'wafpol-agw-${resourceStem}-custom-mime'
  location: location
  tags: tags
  properties: shareSpeechMimePolicy ? rawBodyProperties : union(sharedWafProperties, {
    managedRules: union(sharedWafProperties.managedRules, {
      managedRuleSets: [{
        ruleSetType: 'OWASP'
        ruleSetVersion: '3.2'
        ruleGroupOverrides: [{
          ruleGroupName: 'REQUEST-920-PROTOCOL-ENFORCEMENT'
          rules: [{ ruleId: '920420', state: 'Disabled' }]
        }]
      }]
    })
  })
}
resource speechRawWafPolicy 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2024-05-01' = if (useSeparateSpeechPolicy) {
  name: 'wafpol-agw-${resourceStem}-speech-raw-body'
  location: location
  tags: tags
  properties: rawBodyProperties
}

// Gatewayアプリ配備と同名・同設定で永続化し、基盤再配備でも文書受付を失いません。
// 1MiB全体を検査し、正規MIMEの920420とraw本文の930110誤検知を調整します。
// 非本文のtraversalは共通の4Blockで補完し、930100等の管理規則は維持します。
resource documentUploadWafPolicy 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2024-05-01' = if (useDocumentWafPolicy) {
  name: 'wafpol-agw-${resourceStem}-document-upload'
  location: location
  tags: tags
  properties: union(rawBodyProperties, {
    policySettings: union(sharedWafProperties.policySettings, {
      // Permit the public 1 MiB boundary through WAF; APIM enforces <= 1 MiB.
      maxRequestBodySizeInKb: 1025
      requestBodyInspectLimitInKB: 1025
    })
  })
}

// MCP の session_id はサーバー発行・本人に結び付ける識別子で、Referer のない SDK
// 呼出しにも必要です。943120 について、この名前の引数キーだけを評価対象から外します。
// 値を全規則から除外したり、他 URI のセッション規則・本文検査を弱めたりはしません。
// https://learn.microsoft.com/azure/web-application-firewall/ag/application-gateway-waf-configuration
resource legacyMcpWafPolicy 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2024-05-01' = if (useLegacyMcpWafPolicy) {
  name: 'wafpol-agw-${resourceStem}-mcp-legacy'
  location: location
  tags: tags
  properties: union(sharedWafProperties, {
    managedRules: union(sharedWafProperties.managedRules, {
      exclusions: concat(wafExclusions, [{
        matchVariable: 'RequestArgKeys'
        selectorMatchOperator: 'Equals'
        selector: 'session_id'
        exclusionManagedRuleSets: [{
          ruleSetType: 'OWASP'
          ruleSetVersion: '3.2'
          ruleGroups: [{
            ruleGroupName: 'REQUEST-943-APPLICATION-ATTACK-SESSION-FIXATION'
            rules: [{ ruleId: '943120' }]
          }]
        }]
      }])
    })
  })
}

// data:形式の画像/PDFはXSS規則941130/941170で誤検知されます。ログで確認した
// JSONの値の欄だけを、その2規則から外します。キー・他の欄・他の規則は維持します。
// APIMは本人/モデル許可・全体bytes・出力枠を検査しますが、画像/PDFの内容や
// MIMEを検査済みとは扱いません。入力データ自体の妥当性は下流モデルも検査します。
var inlineImageExclusion = {
  matchVariable: 'RequestArgValues'
  selectorMatchOperator: 'Equals'
  selector: 'messages.content.image_url.url'
  exclusionManagedRuleSets: [{
    ruleSetType: 'OWASP'
    ruleSetVersion: '3.2'
    ruleGroups: [{
      ruleGroupName: 'REQUEST-941-APPLICATION-ATTACK-XSS'
      rules: [{ ruleId: '941130' }, { ruleId: '941170' }]
    }]
  }]
}
var inlinePdfExclusion = union(inlineImageExclusion, { selector: 'input.content.file_data' })
resource inlineImageWafPolicy 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2024-05-01' = if (useInlineImageWafPolicy) {
  name: 'wafpol-agw-${resourceStem}-inline-image'
  location: location
  tags: tags
  properties: union(sharedWafProperties, {
    managedRules: union(sharedWafProperties.managedRules, {
      exclusions: concat(wafExclusions, [inlineImageExclusion])
    })
  })
}
resource inlinePdfWafPolicy 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2024-05-01' = if (useInlinePdfWafPolicy) {
  name: 'wafpol-agw-${resourceStem}-inline-pdf'
  location: location
  tags: tags
  properties: union(sharedWafProperties, {
    managedRules: union(sharedWafProperties.managedRules, {
      exclusions: concat(wafExclusions, [inlinePdfExclusion])
    })
  })
}

// OpenAI Audio Chat の音声は標準JSONの input_audio.data にBase64で入ります。
// 実測で誤検知した942450だけを、この欄・このAPIの完全パスに限って調整します。
// 本文サイズ・他のSQLi規則・通常の文字入力は引き続き検査します。音声内容そのものを
// WAFで検査済みとは扱わず、APIMの認証・モデル許可・JSON/bytes制約も維持します。
var inlineAudioExclusion = {
        matchVariable: 'RequestArgNames'
        selectorMatchOperator: 'Equals'
        selector: 'messages.content.input_audio.data'
        exclusionManagedRuleSets: [{
          ruleSetType: 'OWASP'
          ruleSetVersion: '3.2'
          ruleGroups: [{
            ruleGroupName: 'REQUEST-942-APPLICATION-ATTACK-SQLI'
            rules: [{ ruleId: '942450' }]
          }]
        }]
      }

resource inlineAudioWafPolicy 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2024-05-01' = if (useInlineAudioWafPolicy) {
  name: 'wafpol-agw-${resourceStem}-inline-audio'
  location: location
  tags: tags
  properties: union(sharedWafProperties, {
    managedRules: union(sharedWafProperties.managedRules, {
      exclusions: concat(wafExclusions, [inlineAudioExclusion])
    })
  })
}

resource inlineMediaWafPolicy 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2024-05-01' = if (useInlineMediaWafPolicy) {
  name: 'wafpol-agw-${resourceStem}-inline-media'
  location: location
  tags: tags
  properties: union(sharedWafProperties, {
    managedRules: union(sharedWafProperties.managedRules, {
      exclusions: concat(wafExclusions, [inlineImageExclusion, inlineAudioExclusion])
    })
  })
}

// URI別ポリシーは継承されないため、共通保護と既存プロトコル例外を必ず合成します。
// 同じURIを旧media規則と新data規則の両方へ登録せず、一つの完成したpolicyへ関連付けます。
var callbackRuleIds = {
  gateway: ['942430', '942440', '942450']
  portal: ['942430', '942440']
}
var callbackPaths = {
  gateway: '/auth/connections/browser/identity-callback'
  portal: '/api/auth/callback/microsoft'
}
var dataPolicyProperties = [for profile in dataProfiles: union(sharedWafProperties, {
  policySettings: union(sharedWafProperties.policySettings, { mode: 'Prevention' })
  managedRules: union(sharedWafProperties.managedRules, {
    managedRuleSets: profile.scope == 'gateway' && length(filter(profile.paths, path => contains(customMimePaths, path))) > 0 ? [{
      ruleSetType: 'OWASP'
      ruleSetVersion: '3.2'
      ruleGroupOverrides: [{
        ruleGroupName: 'REQUEST-920-PROTOCOL-ENFORCEMENT'
        rules: [{ ruleId: '920420', state: 'Disabled' }]
      }]
    }] : sharedWafProperties.managedRules.managedRuleSets
    exclusions: union(wafExclusions,
      profile.scope == 'gateway' && length(filter(profile.paths, path => contains(inlineImagePaths, path))) > 0 ? [inlineImageExclusion] : [],
      profile.scope == 'gateway' && length(filter(profile.paths, path => contains(inlinePdfPaths, path))) > 0 ? [inlinePdfExclusion] : [],
      profile.scope == 'gateway' && length(filter(profile.paths, path => contains(inlineAudioPaths, path))) > 0 ? [union(inlineAudioExclusion, { matchVariable: 'RequestArgValues' })] : [],
      contains(profile.paths, callbackPaths[profile.scope]) ? [{
        matchVariable: 'RequestArgValues'
        selectorMatchOperator: 'Equals'
        selector: 'code'
        exclusionManagedRuleSets: [{
          ruleSetType: 'OWASP'
          ruleSetVersion: '3.2'
          ruleGroups: [{
            ruleGroupName: 'REQUEST-942-APPLICATION-ATTACK-SQLI'
            rules: map(callbackRuleIds[profile.scope], rule => { ruleId: rule })
          }]
        }]
      }] : [],
      profile.scope == 'gateway' && length(filter(profile.paths, path => contains(legacyMcpMessagePaths, path))) > 0 ? [{
        matchVariable: 'RequestArgKeys'
        selectorMatchOperator: 'Equals'
        selector: 'session_id'
        exclusionManagedRuleSets: [{
          ruleSetType: 'OWASP'
          ruleSetVersion: '3.2'
          ruleGroups: [{
            ruleGroupName: 'REQUEST-943-APPLICATION-ATTACK-SESSION-FIXATION'
            rules: [{ ruleId: '943120' }]
          }]
        }]
      }] : [], profile.exclusions)
  })
})]
resource dataWafPolicies 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2024-05-01' = [for (profile, index) in dataProfiles: {
  name: 'wafpol-agw-${resourceStem}-data-${profile.name}'
  location: location
  tags: tags
  properties: dataPolicyProperties[index]
}]
var dataGatewayPathRules = map(filter(dataProfiles, profile => profile.scope == 'gateway'), profile => {
  name: 'waf-data-${profile.name}'
  properties: {
    paths: profile.paths
    firewallPolicy: { id: resourceId('Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies', 'wafpol-agw-${resourceStem}-data-${profile.name}') }
    backendAddressPool: { id: '${gatewayId}/backendAddressPools/bepool-apim' }
    backendHttpSettings: { id: '${gatewayId}/backendHttpSettingsCollection/behttp-apim' }
    ...(grpcEnabled ? { rewriteRuleSet: { id: '${gatewayId}/rewriteRuleSets/trusted-l7-ingress' } } : {})
  }
})
var dataPortalPathRules = map(filter(dataProfiles, profile => profile.scope == 'portal'), profile => {
  name: 'waf-data-${profile.name}'
  properties: {
    paths: profile.paths
    firewallPolicy: { id: resourceId('Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies', 'wafpol-agw-${resourceStem}-data-${profile.name}') }
    backendAddressPool: { id: '${gatewayId}/backendAddressPools/bepool-portal' }
    backendHttpSettings: { id: '${gatewayId}/backendHttpSettingsCollection/behttp-portal' }
  }
})

// HTTPS 終端・Host 書換え・ヘルスチェック・API 転送。gRPC は別 TCP リスナーとして構成します。
resource gateway 'Microsoft.Network/applicationGateways@2024-05-01' = {
  name: gatewayName
  location: location
  tags: tags
  dependsOn: [dataWafPolicies]
  zones: zones
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${certificateIdentityId}': {} }
  }
  properties: {
    sku: { name: skuName, tier: skuName }
    autoscaleConfiguration: { minCapacity: autoscaleMinCapacity, maxCapacity: autoscaleMaxCapacity }
    ...(skuName == 'WAF_v2' ? { firewallPolicy: { id: createWafPolicy ? wafPolicy!.id : existingWafPolicyId } } : {})
    enableHttp2: true
    globalConfiguration: { enableResponseBuffering: false }
    sslPolicy: { policyType: 'Predefined', policyName: 'AppGwSslPolicy20220101S' }
    sslCertificates: [{ name: 'gateway-tls', properties: { keyVaultSecretId: keyVaultCertificateSecretId } }]
    gatewayIPConfigurations: [{ name: 'agw-ipcfg', properties: { subnet: { id: subnetId } } }]
    frontendIPConfigurations: [{ name: 'agw-frontend-public', properties: { publicIPAddress: { id: publicIpId } } }]
    frontendPorts: concat([
      { name: 'port-80', properties: { port: 80 } }
      { name: 'port-443', properties: { port: 443 } }
    ], grpcEnabled ? [{ name: 'port-grpc', properties: { port: grpcPublicPort } }] : [])
    // TCP/TCP preserves the TLS byte stream and ALPN negotiation with APIM.
    // WAF does not inspect this listener; global APIM edge proof protects all
    // non-gRPC APIs from being reached through it.
    ...(grpcEnabled ? {
      listeners: [{
        name: 'listener-grpc-tcp'
        properties: {
          frontendIPConfiguration: { id: '${gatewayId}/frontendIPConfigurations/agw-frontend-public' }
          frontendPort: { id: '${gatewayId}/frontendPorts/port-grpc' }
          protocol: 'Tcp'
        }
      }]
      backendSettingsCollection: [{
        name: 'betcp-apim-grpc'
        properties: { protocol: 'Tcp', port: grpcBackendPort, timeout: backendRequestTimeoutSeconds }
      }]
      routingRules: [{
        name: 'rule-grpc-tcp'
        properties: {
          priority: 300
          ruleType: 'Basic'
          listener: { id: '${gatewayId}/listeners/listener-grpc-tcp' }
          backendAddressPool: { id: '${gatewayId}/backendAddressPools/bepool-apim' }
          backendSettings: { id: '${gatewayId}/backendSettingsCollection/betcp-apim-grpc' }
        }
      }]
    } : {})
    ...(grpcEnabled ? {
      rewriteRuleSets: [{
        name: 'trusted-l7-ingress'
        properties: {
          // AppGW は一つの転送規則に一つの rewrite set だけを関連付けられます。
          // HTTP経路で確認するL7入口証明を、AppGWの値で設定します。
          rewriteRules: [{
            name: 'replace-untrusted-edge-proof'
            ruleSequence: 100
            actionSet: { requestHeaderConfigurations: [{ headerName: 'X-Core-L7-Ingress', headerValue: l7IngressSecret }] }
          }]
        }
      }]
    } : {})
    httpListeners: concat([
      {
        name: 'listener-http'
        properties: {
          frontendIPConfiguration: { id: '${gatewayId}/frontendIPConfigurations/agw-frontend-public' }
          frontendPort: { id: '${gatewayId}/frontendPorts/port-80' }
          protocol: 'Http'
          hostName: publicHostName
        }
      }
      {
        name: 'listener-https'
        properties: {
          frontendIPConfiguration: { id: '${gatewayId}/frontendIPConfigurations/agw-frontend-public' }
          frontendPort: { id: '${gatewayId}/frontendPorts/port-443' }
          protocol: 'Https'
          hostName: publicHostName
          requireServerNameIndication: true
          sslCertificate: { id: '${gatewayId}/sslCertificates/gateway-tls' }
        }
      }


    ], map([
      { name: 'portal-http', protocol: 'Http', port: 'port-80' }
      { name: 'portal-https', protocol: 'Https', port: 'port-443' }
    ], listener => {
      name: listener.name
      properties: {
        frontendIPConfiguration: { id: '${gatewayId}/frontendIPConfigurations/agw-frontend-public' }
        frontendPort: { id: '${gatewayId}/frontendPorts/${listener.port}' }
        protocol: listener.protocol
        hostName: portalHostName
        ...(listener.protocol == 'Https' ? {
          requireServerNameIndication: true
          sslCertificate: { id: '${gatewayId}/sslCertificates/gateway-tls' }
        } : {})
      }
    }))
    backendAddressPools: concat([{
      name: 'bepool-apim'
      properties: { backendAddresses: [{ ipAddress: apimPrivateIp }] }
    }], [{ name: 'bepool-portal', properties: { backendAddresses: [{ ipAddress: portalBackendIp }] } }])
    backendHttpSettingsCollection: concat([{
      name: 'behttp-apim'
      properties: {
        protocol: 'Http'
        port: 80
        cookieBasedAffinity: 'Disabled'
        requestTimeout: backendRequestTimeoutSeconds
        hostName: apimHostName
        pickHostNameFromBackendAddress: false
        probe: { id: '${gatewayId}/probes/probe-apim' }
      }
    }], [{
      name: 'behttp-portal'
      properties: {
        protocol: 'Https'
        port: 443
        cookieBasedAffinity: 'Disabled'
        requestTimeout: 30
        hostName: portalBackendHost
        pickHostNameFromBackendAddress: false
        probe: { id: '${gatewayId}/probes/probe-portal' }
      }
    }])
    probes: concat([{
      name: 'probe-apim'
      properties: {
        protocol: 'Http'
        port: 80
        host: apimHostName
        path: '/status-0123456789abcdef'
        interval: 30
        timeout: 30
        unhealthyThreshold: 3
        pickHostNameFromBackendHttpSettings: false
        match: { statusCodes: ['200-399'] }
      }
    }], [{
      name: 'probe-portal'
      properties: {
        protocol: 'Https'
        host: portalBackendHost
        // Portal配備後は認証不要のヘルスAPIで稼働状態を判定します。
        // 初期構築の仮イメージは対象外です。認証画面への転送は正常判定に含めません。
        path: '/api/health'
        interval: 30
        timeout: 10
        unhealthyThreshold: 3
        pickHostNameFromBackendHttpSettings: false
        match: { statusCodes: ['200'] }
      }
    }])
    redirectConfigurations: concat([{
      name: 'redirect-http-to-https'
      properties: {
        redirectType: 'Temporary'
        targetListener: { id: '${gatewayId}/httpListeners/listener-https' }
        includePath: true
        includeQueryString: true
      }
    }], [{
      name: 'redirect-portal'
      properties: {
        redirectType: 'Permanent'
        targetListener: { id: '${gatewayId}/httpListeners/portal-https' }
        includePath: true
        includeQueryString: true
      }
    }])
    // プロトコル専用パスでも、既定パスでも転送先・Host・FW 経路は同じです。
    // パス別ポリシーのために L7 の信頼ヘッダー書換えを落とさないよう両方へ結びます。
    ...(useProtocolWafPolicies || createWafPolicy ? {
      urlPathMaps: concat(useProtocolWafPolicies ? [{
        name: 'pathmap-protocols'
        properties: {
          defaultBackendAddressPool: { id: '${gatewayId}/backendAddressPools/bepool-apim' }
          defaultBackendHttpSettings: { id: '${gatewayId}/backendHttpSettingsCollection/behttp-apim' }
          ...(grpcEnabled ? { defaultRewriteRuleSet: { id: '${gatewayId}/rewriteRuleSets/trusted-l7-ingress' } } : {})
          pathRules: concat(dataGatewayPathRules, useGatewayAuthWafPolicy ? [{
            name: 'credential-identity-callback'
            properties: {
              paths: ['/auth/connections/browser/identity-callback']
              firewallPolicy: { id: gatewayAuthWafPolicy!.id }
              backendAddressPool: { id: '${gatewayId}/backendAddressPools/bepool-apim' }
              backendHttpSettings: { id: '${gatewayId}/backendHttpSettingsCollection/behttp-apim' }
              ...(grpcEnabled ? { rewriteRuleSet: { id: '${gatewayId}/rewriteRuleSets/trusted-l7-ingress' } } : {})
            }
          }] : [], useSeparateSpeechPolicy ? [{
            name: 'speech-raw-body'
            properties: {
              paths: speechPaths
              firewallPolicy: { id: speechRawWafPolicy!.id }
              backendAddressPool: { id: '${gatewayId}/backendAddressPools/bepool-apim' }
              backendHttpSettings: { id: '${gatewayId}/backendHttpSettingsCollection/behttp-apim' }
              ...(grpcEnabled ? { rewriteRuleSet: { id: '${gatewayId}/rewriteRuleSets/trusted-l7-ingress' } } : {})
            }
          }] : [], useDocumentWafPolicy ? [{
            name: 'document-analysis-upload'
            properties: {
              paths: documentPaths
              firewallPolicy: { id: documentUploadWafPolicy!.id }
              backendAddressPool: { id: '${gatewayId}/backendAddressPools/bepool-apim' }
              backendHttpSettings: { id: '${gatewayId}/backendHttpSettingsCollection/behttp-apim' }
              ...(grpcEnabled ? { rewriteRuleSet: { id: '${gatewayId}/rewriteRuleSets/trusted-l7-ingress' } } : {})
            }
          }] : [], useCustomMimeWafPolicy ? [{
            name: 'custom-content-type'
            properties: {
              paths: remainingCustomMimePaths
              firewallPolicy: { id: customMimeWafPolicy!.id }
              backendAddressPool: { id: '${gatewayId}/backendAddressPools/bepool-apim' }
              backendHttpSettings: { id: '${gatewayId}/backendHttpSettingsCollection/behttp-apim' }
              ...(grpcEnabled ? { rewriteRuleSet: { id: '${gatewayId}/rewriteRuleSets/trusted-l7-ingress' } } : {})
            }
          }] : [], useLegacyMcpWafPolicy ? [{
            name: 'legacy-mcp-session'
            properties: {
              paths: remainingLegacyMcpPaths
              firewallPolicy: { id: legacyMcpWafPolicy!.id }
              backendAddressPool: { id: '${gatewayId}/backendAddressPools/bepool-apim' }
              backendHttpSettings: { id: '${gatewayId}/backendHttpSettingsCollection/behttp-apim' }
              ...(grpcEnabled ? { rewriteRuleSet: { id: '${gatewayId}/rewriteRuleSets/trusted-l7-ingress' } } : {})
            }
          }] : [], useInlineMediaWafPolicy ? [{
            name: 'inline-media-data'
            properties: {
              paths: remainingInlineMediaPaths
              firewallPolicy: { id: inlineMediaWafPolicy!.id }
              backendAddressPool: { id: '${gatewayId}/backendAddressPools/bepool-apim' }
              backendHttpSettings: { id: '${gatewayId}/backendHttpSettingsCollection/behttp-apim' }
              ...(grpcEnabled ? { rewriteRuleSet: { id: '${gatewayId}/rewriteRuleSets/trusted-l7-ingress' } } : {})
            }
          }] : [], useInlineImageWafPolicy ? [{
            name: 'inline-image-data'
            properties: {
              paths: remainingInlineImagePaths
              firewallPolicy: { id: inlineImageWafPolicy!.id }
              backendAddressPool: { id: '${gatewayId}/backendAddressPools/bepool-apim' }
              backendHttpSettings: { id: '${gatewayId}/backendHttpSettingsCollection/behttp-apim' }
              ...(grpcEnabled ? { rewriteRuleSet: { id: '${gatewayId}/rewriteRuleSets/trusted-l7-ingress' } } : {})
            }
          }] : [], useInlinePdfWafPolicy ? [{
            name: 'inline-pdf-data'
            properties: {
              paths: remainingInlinePdfPaths
              firewallPolicy: { id: inlinePdfWafPolicy!.id }
              backendAddressPool: { id: '${gatewayId}/backendAddressPools/bepool-apim' }
              backendHttpSettings: { id: '${gatewayId}/backendHttpSettingsCollection/behttp-apim' }
              ...(grpcEnabled ? { rewriteRuleSet: { id: '${gatewayId}/rewriteRuleSets/trusted-l7-ingress' } } : {})
            }
          }] : [], useInlineAudioWafPolicy ? [{
            name: 'inline-audio-data'
            properties: {
              paths: remainingInlineAudioPaths
              firewallPolicy: { id: inlineAudioWafPolicy!.id }
              backendAddressPool: { id: '${gatewayId}/backendAddressPools/bepool-apim' }
              backendHttpSettings: { id: '${gatewayId}/backendHttpSettingsCollection/behttp-apim' }
              ...(grpcEnabled ? { rewriteRuleSet: { id: '${gatewayId}/rewriteRuleSets/trusted-l7-ingress' } } : {})
            }
          }] : [])
        }
      }] : [], createWafPolicy ? [{
        name: 'pathmap-portal-auth'
        properties: {
          defaultBackendAddressPool: { id: '${gatewayId}/backendAddressPools/bepool-portal' }
          defaultBackendHttpSettings: { id: '${gatewayId}/backendHttpSettingsCollection/behttp-portal' }
          pathRules: concat(dataPortalPathRules, contains(dataPortalPaths, '/api/auth/callback/microsoft') ? [] : [{
            name: 'microsoft-auth-callback'
            properties: {
              paths: ['/api/auth/callback/microsoft']
              firewallPolicy: { id: portalAuthWafPolicy!.id }
              backendAddressPool: { id: '${gatewayId}/backendAddressPools/bepool-portal' }
              backendHttpSettings: { id: '${gatewayId}/backendHttpSettingsCollection/behttp-portal' }
            }
          }])
        }
      }] : [])
    } : {})
    requestRoutingRules: concat([
      {
        name: 'rule-http-to-https'
        properties: {
          ruleType: 'Basic'
          priority: 100
          httpListener: { id: '${gatewayId}/httpListeners/listener-http' }
          redirectConfiguration: { id: '${gatewayId}/redirectConfigurations/redirect-http-to-https' }
        }
      }
      {
        name: 'rule-https-to-apim'
        properties: {
          ruleType: useProtocolWafPolicies ? 'PathBasedRouting' : 'Basic'
          priority: 200
          httpListener: { id: '${gatewayId}/httpListeners/listener-https' }
          ...(useProtocolWafPolicies ? { urlPathMap: { id: '${gatewayId}/urlPathMaps/pathmap-protocols' } } : {
            backendAddressPool: { id: '${gatewayId}/backendAddressPools/bepool-apim' }
            backendHttpSettings: { id: '${gatewayId}/backendHttpSettingsCollection/behttp-apim' }
            ...(grpcEnabled ? { rewriteRuleSet: { id: '${gatewayId}/rewriteRuleSets/trusted-l7-ingress' } } : {})
          })
        }
      }


    ], [
      {
        name: 'rule-portal-redirect'
        properties: {
          ruleType: 'Basic'
          priority: 110
          httpListener: { id: '${gatewayId}/httpListeners/portal-http' }
          redirectConfiguration: { id: '${gatewayId}/redirectConfigurations/redirect-portal' }
        }
      }
      {
        name: 'rule-portal-https'
        properties: {
          ruleType: createWafPolicy ? 'PathBasedRouting' : 'Basic'
          priority: 210
          httpListener: { id: '${gatewayId}/httpListeners/portal-https' }
          ...(createWafPolicy ? { urlPathMap: { id: '${gatewayId}/urlPathMaps/pathmap-portal-auth' } } : {
            backendAddressPool: { id: '${gatewayId}/backendAddressPools/bepool-portal' }
            backendHttpSettings: { id: '${gatewayId}/backendHttpSettingsCollection/behttp-portal' }
          })
        }
      }
    ])
  }
}

// 既存のWAF監査経路を保持します。Accessは別設定へ分け、生queryのStorage直送を防ぎます。
resource logs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: gateway
  name: 'diag-${resourceStem}-agw'
  properties: {
    workspaceId: workspaceId
    ...(enableAuditCollection && skuName == 'WAF_v2' ? { storageAccountId: auditStorageId } : {})
    logAnalyticsDestinationType: 'Dedicated'
    logs: skuName == 'WAF_v2' ? [{ category: 'ApplicationGatewayFirewallLog', enabled: true }] : [{ category: 'ApplicationGatewayAccessLog', enabled: true }]
  }
}

// AccessはLAWでqueryを除去してからexportします。WAFの同一Storage重複設定を作りません。
resource accessLogs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (skuName == 'WAF_v2') {
  scope: gateway
  name: 'diag-${resourceStem}-agw-access'
  properties: {
    workspaceId: workspaceId
    logAnalyticsDestinationType: 'Dedicated'
    logs: [{ category: 'ApplicationGatewayAccessLog', enabled: true }]
  }
}

// AppGW の数値メトリックを Workspace へ出力します。
resource metrics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: gateway
  name: 'diag-${resourceStem}-agw-metrics'
  properties: {
    workspaceId: workspaceId
    metrics: [{ category: 'AllMetrics', enabled: true }]
  }
}

output applicationGateway object = {
  id: gateway.id
  name: gateway.name
  publicIpId: publicIpId
  publicIp: publicIpAddress
  publicUrl: 'https://${publicHostName}'
  wafPolicyId: skuName == 'WAF_v2' ? (createWafPolicy ? wafPolicy!.id : existingWafPolicyId) : ''
}

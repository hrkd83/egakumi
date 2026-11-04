// 公開bridgeには実値を生成しません。preup/preprovisionが.local内の保護入力を用意します。
// azdは環境変数を渡してbuild-paramsを実行し、配備直前にも再評価します。
using './main.bicep'

// 環境変数は実行ルート基準の.local/<environment>/generated/main.parameters.jsonです。
// loadJsonContentはこのファイルのあるinfraからの相対パスとして解決します。
var input = loadJsonContent('../${readEnvironmentVariable('EGAKUMI_ARM_PARAMETERS_FILE')}')

param configuration = input.parameters.configuration.value
param phase = input.parameters.?phase.?value ?? 'core'
param sshPublicKey = input.parameters.?sshPublicKey.?value ?? ''
param dnsPluginArgumentsJson = input.parameters.?dnsPluginArgumentsJson.?value ?? ''
param preparedPfxBase64 = input.parameters.?preparedPfxBase64.?value ?? ''
param preparedPfxPassword = input.parameters.?preparedPfxPassword.?value ?? ''
param credentialProviderSecrets = input.parameters.?credentialProviderSecrets.?value ?? {}
param l7IngressSecret = input.parameters.?l7IngressSecret.?value ?? ''
param realtimeTicketKey = input.parameters.?realtimeTicketKey.?value ?? ''
param manageAuditImmutabilityPolicies = input.parameters.?manageAuditImmutabilityPolicies.?value ?? true
param retainedAuditImmutabilityPolicyIds = input.parameters.?retainedAuditImmutabilityPolicyIds.?value ?? []
param preserveAuditCollection = input.parameters.?preserveAuditCollection.?value ?? false
param existingNetworkSecurityGroupNames = input.parameters.?existingNetworkSecurityGroupNames.?value ?? []
param existingRouteTableRoutes = input.parameters.?existingRouteTableRoutes.?value ?? {}
param existingApimHostnameConfigurations = input.parameters.?existingApimHostnameConfigurations.?value ?? {}
param existingPortalApp = input.parameters.?existingPortalApp.?value ?? false
param existingWorkspaceDataCollectionRuleResourceId = input.parameters.?existingWorkspaceDataCollectionRuleResourceId.?value ?? ''

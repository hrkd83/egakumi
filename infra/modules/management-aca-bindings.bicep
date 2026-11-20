// 構築されたACA資源だけを既存参照し、管理VM自身のMIへ保守権限を付与します。
// このmoduleのscopeは対象ACA資源が作られたRGです。名前は構築出力から取得します。
targetScope = 'resourceGroup'

@minLength(1)
@description('構築された管理VM自身のSystemAssigned principalId。')
param principalId string
@description('このsubscriptionで構築した保守用カスタムロールのID。')
param roleDefinitionIds object
@description('このRGで実際に構築されたContainer App名。利用者による任意指定ではありません。')
param applicationNames array = []
@description('このRGで実際に構築されたJob名。作成前の予定名は含めません。')
param jobNames array = []
@description('このRGで構築されたmanagedEnvironment名。')
param environmentNames array = []

// 同じAppがCoreとアプリ更新の双方に登場しても同じ割当GUIDを使います。
var uniqueApplications = union(applicationNames, applicationNames)
var uniqueJobs = union(jobNames, jobNames)
var uniqueEnvironments = union(environmentNames, environmentNames)

resource applications 'Microsoft.App/containerApps@2025-01-01' existing = [for name in uniqueApplications: { name: name }]
resource jobs 'Microsoft.App/jobs@2025-01-01' existing = [for name in uniqueJobs: { name: name }]
resource environments 'Microsoft.App/managedEnvironments@2025-01-01' existing = [for name in uniqueEnvironments: { name: name }]

resource applicationLogGrants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (name, i) in uniqueApplications: {
  scope: applications[i]
  name: guid(applications[i].id, principalId, roleDefinitionIds.appLogs)
  properties: { principalId: principalId, principalType: 'ServicePrincipal', roleDefinitionId: roleDefinitionIds.appLogs }
}]
resource applicationExecGrants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (name, i) in uniqueApplications: {
  scope: applications[i]
  name: guid(applications[i].id, principalId, roleDefinitionIds.appExec)
  properties: { principalId: principalId, principalType: 'ServicePrincipal', roleDefinitionId: roleDefinitionIds.appExec }
}]
resource jobLogGrants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (name, i) in uniqueJobs: {
  scope: jobs[i]
  name: guid(jobs[i].id, principalId, roleDefinitionIds.jobLogs)
  properties: { principalId: principalId, principalType: 'ServicePrincipal', roleDefinitionId: roleDefinitionIds.jobLogs }
}]
resource environmentTokenGrants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (name, i) in uniqueEnvironments: {
  scope: environments[i]
  name: guid(environments[i].id, principalId, roleDefinitionIds.environmentLogs)
  properties: { principalId: principalId, principalType: 'ServicePrincipal', roleDefinitionId: roleDefinitionIds.environmentLogs }
}]

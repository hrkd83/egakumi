# Import only worker functions. Never execute deployment entry points while testing.
param([scriptblock]$Transform)
$certificateTokens=$null
$certificateParseErrors=$null
$certificateAst=[Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $PSScriptRoot '../guest/certificate.ps1'),
    [ref]$certificateTokens,[ref]$certificateParseErrors)
if ($certificateParseErrors.Count) {throw 'Certificate worker parse errors'}
foreach ($certificateFunction in $certificateAst.FindAll({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst]
},$false)) {
    $certificateBody=$certificateFunction.Extent.Text
    if ($Transform) {$certificateBody=& $Transform $certificateFunction.Name $certificateBody}
    . ([scriptblock]::Create($certificateBody))
}

# Import only worker functions. Never execute deployment entry points while testing.
$ContainerJob=$false # Fixtures import functions rather than the worker parameters.
$certificateTokens=$null
$certificateParseErrors=$null
$certificateAst=[Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $PSScriptRoot '../../infra/guest/certificate.ps1'),
    [ref]$certificateTokens,[ref]$certificateParseErrors)
if ($certificateParseErrors.Count) {throw 'Certificate worker parse errors'}
foreach ($certificateFunction in $certificateAst.FindAll({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst]
},$false)) {
    . ([scriptblock]::Create($certificateFunction.Extent.Text))
}

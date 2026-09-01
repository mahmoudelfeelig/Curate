[CmdletBinding()]
param(
    [ValidateSet("Plan", "Apply")][string]$Mode = "Plan",
    [Parameter(Mandatory = $true)][string]$RuntimeId,
    [Parameter(Mandatory = $true)][ValidatePattern("^[a-z]{2}(-gov)?-[a-z0-9-]+-\d$")][string]$AwsRegion,
    [Parameter(Mandatory = $true)][string]$AwsProfile,
    [ValidateRange(1, 14)][int]$RetentionDays = 7,
    [AllowEmptyString()][string]$ApplyAcknowledgement = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
$logGroup = "/aws/bedrock-agentcore/runtimes/$RuntimeId-DEFAULT"

Write-Host "Plan: set retention for exact log group '$logGroup' to $RetentionDays days after its first invocation creates it."
if ($Mode -eq "Plan") {
    Write-Host "Plan complete. No AWS API was called."
    return
}
Assert-ExactAcknowledgement -Actual $ApplyAcknowledgement -Expected "SET AGENTCORE LOG RETENTION" -Purpose "CloudWatch retention update"

$groups = Invoke-AwsJson -CommandArguments @(
    "logs", "describe-log-groups",
    "--log-group-name-prefix", $logGroup,
    "--region", $AwsRegion,
    "--profile", $AwsProfile
)
$exact = @($groups.logGroups) | Where-Object { $_.logGroupName -ceq $logGroup }
if (@($exact).Count -ne 1) {
    throw "The exact Runtime log group does not exist yet. Run an explicitly gated smoke invocation first."
}
& aws logs put-retention-policy `
    --log-group-name $logGroup `
    --retention-in-days $RetentionDays `
    --region $AwsRegion `
    --profile $AwsProfile `
    --no-cli-pager
if ($LASTEXITCODE -ne 0) {
    throw "CloudWatch retention update failed with exit code $LASTEXITCODE"
}
Write-Host "Set '$logGroup' retention to $RetentionDays days."

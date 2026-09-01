[CmdletBinding()]
param(
    [ValidateSet("Plan", "Apply")][string]$Mode = "Plan",
    [Parameter(Mandatory = $true)][ValidatePattern("^\d{12}$")][string]$AwsAccountId,
    [Parameter(Mandatory = $true)][ValidatePattern("^[a-z]{2}(-gov)?-[a-z0-9-]+-\d$")][string]$AwsRegion,
    [Parameter(Mandatory = $true)][string]$AwsProfile,
    [switch]$DeleteRuntimeLogGroup,
    [switch]$DeleteRuntimeAsset,
    [AllowEmptyString()][string]$ApplyAcknowledgement = "",
    [AllowEmptyString()][string]$LogDeletionAcknowledgement = "",
    [AllowEmptyString()][string]$AssetDeletionAcknowledgement = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
$stackName = "FeedPassportAgentCore"

Write-Host "Plan: delete exact CloudFormation stack '$stackName' in account $AwsAccountId, region $AwsRegion."
Write-Host "The stack marks its proposal-only Runtime, Gateway, and Cognito resources for deletion."
Write-Host "The shared CDKToolkit stack is never deleted. Runtime logs and the exact CDK asset require explicit cleanup switches."
if ($Mode -eq "Plan") {
    Write-Host "Plan complete. No AWS API was called and nothing was deleted."
    return
}

Assert-ExactAcknowledgement -Actual $ApplyAcknowledgement -Expected "DESTROY FEED PASSPORT AGENTCORE" -Purpose "Feed Passport stack teardown"
if ($DeleteRuntimeLogGroup) {
    Assert-ExactAcknowledgement -Actual $LogDeletionAcknowledgement -Expected "DELETE FEED PASSPORT RUNTIME LOGS" -Purpose "Runtime log deletion"
}
if ($DeleteRuntimeAsset) {
    Assert-ExactAcknowledgement -Actual $AssetDeletionAcknowledgement -Expected "DELETE FEED PASSPORT RUNTIME ASSET" -Purpose "CDK asset deletion"
}
$null = Assert-AwsAccount -ExpectedAccountId $AwsAccountId -AwsRegion $AwsRegion -AwsProfile $AwsProfile
$stack = Get-StackDescription -StackName $stackName -AwsRegion $AwsRegion -AwsProfile $AwsProfile
$outputs = Convert-StackOutputsToMap -Stack $stack
$runtimeId = $outputs.RuntimeId
$runtimeLogGroup = if ([string]::IsNullOrWhiteSpace($runtimeId)) { $null } else { "/aws/bedrock-agentcore/runtimes/$runtimeId-DEFAULT" }
$runtimeAsset = $null
if ($DeleteRuntimeAsset -and -not [string]::IsNullOrWhiteSpace($runtimeId)) {
    $runtime = Invoke-AwsJson -CommandArguments @(
        "bedrock-agentcore-control", "get-agent-runtime",
        "--agent-runtime-id", $runtimeId,
        "--region", $AwsRegion,
        "--profile", $AwsProfile
    )
    $runtimeAsset = $runtime.agentRuntimeArtifact.codeConfiguration.code.s3
    if ($null -eq $runtimeAsset -or [string]::IsNullOrWhiteSpace([string]$runtimeAsset.bucket) -or [string]::IsNullOrWhiteSpace([string]$runtimeAsset.prefix)) {
        throw "Could not resolve the exact direct-code S3 asset before teardown"
    }
}

& aws cloudformation delete-stack `
    --stack-name $stackName `
    --region $AwsRegion `
    --profile $AwsProfile `
    --no-cli-pager
if ($LASTEXITCODE -ne 0) {
    throw "CloudFormation delete-stack failed with exit code $LASTEXITCODE"
}
& aws cloudformation wait stack-delete-complete `
    --stack-name $stackName `
    --region $AwsRegion `
    --profile $AwsProfile `
    --no-cli-pager
if ($LASTEXITCODE -ne 0) {
    throw "Stack deletion did not complete successfully; inspect CloudFormation events"
}
Write-Host "Deleted stack '$stackName'. Its managed demo identity and runtime resources cannot be recovered."

if ($DeleteRuntimeLogGroup -and -not [string]::IsNullOrWhiteSpace($runtimeLogGroup)) {
    $groups = Invoke-AwsJson -CommandArguments @(
        "logs", "describe-log-groups",
        "--log-group-name-prefix", $runtimeLogGroup,
        "--region", $AwsRegion,
        "--profile", $AwsProfile
    )
    if (@($groups.logGroups | Where-Object { $_.logGroupName -ceq $runtimeLogGroup }).Count -eq 1) {
        & aws logs delete-log-group `
            --log-group-name $runtimeLogGroup `
            --region $AwsRegion `
            --profile $AwsProfile `
            --no-cli-pager
        if ($LASTEXITCODE -ne 0) { throw "Exact Runtime log-group deletion failed" }
        Write-Host "Deleted exact Runtime log group '$runtimeLogGroup'; it cannot be recovered."
    }
}

if ($DeleteRuntimeAsset -and $null -ne $runtimeAsset) {
    $assetArguments = @(
        "s3api", "delete-object",
        "--bucket", [string]$runtimeAsset.bucket,
        "--key", [string]$runtimeAsset.prefix,
        "--region", $AwsRegion,
        "--profile", $AwsProfile,
        "--no-cli-pager"
    )
    if (-not [string]::IsNullOrWhiteSpace([string]$runtimeAsset.versionId)) {
        $assetArguments += @("--version-id", [string]$runtimeAsset.versionId)
    }
    & aws @assetArguments
    if ($LASTEXITCODE -ne 0) { throw "Exact Runtime S3 asset deletion failed" }
    Write-Host "Deleted the exact Runtime deployment asset from the shared CDK bucket; rebuild it locally if needed."
}

Write-Host "CDKToolkit remains because it can be shared. inventory.ps1 can confirm that the application stack is gone."

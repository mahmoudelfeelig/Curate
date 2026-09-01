[CmdletBinding()]
param(
    [ValidateSet("Local", "AwsReadOnly")][string]$Mode = "Local",
    [ValidatePattern("^\d{12}$")][string]$AwsAccountId,
    [ValidatePattern("^[a-z]{2}(-gov)?-[a-z0-9-]+-\d$")][string]$AwsRegion,
    [string]$AwsProfile,
    [string]$BedrockModelId,
    [ValidatePattern("^arn:")][string]$BedrockModelArn
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

foreach ($command in @("node", "npm", "docker", "aws")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required local command is unavailable: $command"
    }
}

$runtimeSkeleton = (& aws bedrock-agentcore-control update-agent-runtime --generate-cli-skeleton input) | ConvertFrom-Json -Depth 100
if ($LASTEXITCODE -ne 0 -or $null -eq $runtimeSkeleton.metadataConfiguration) {
    throw "AWS CLI is too old: update-agent-runtime must support metadataConfiguration.requireMMDSV2"
}
if ($runtimeSkeleton.metadataConfiguration.requireMMDSV2 -ne $true) {
    throw "AWS CLI returned an unexpected MMDSv2 input contract"
}

Write-Host "Local AgentCore preflight passed. No AWS API was called."
if ($Mode -eq "Local") {
    return
}

foreach ($required in @("AwsAccountId", "AwsRegion", "AwsProfile", "BedrockModelId", "BedrockModelArn")) {
    if ([string]::IsNullOrWhiteSpace((Get-Variable -Name $required -ValueOnly))) {
        throw "$required is mandatory in AwsReadOnly mode"
    }
}
$modelBinding = [regex]::Match(
    $BedrockModelArn,
    '^arn:aws(?:-us-gov|-cn)?:bedrock:([^:]+)::foundation-model/(.+)$'
)
if (-not $modelBinding.Success) {
    throw "BedrockModelArn must identify a direct foundation-model resource"
}
if ($modelBinding.Groups[1].Value -ne $AwsRegion) {
    throw "BedrockModelArn region must exactly match AwsRegion"
}
if ($modelBinding.Groups[2].Value -cne $BedrockModelId) {
    throw "BedrockModelArn resource must exactly match BedrockModelId"
}

$identity = Assert-AwsAccount -ExpectedAccountId $AwsAccountId -AwsRegion $AwsRegion -AwsProfile $AwsProfile
Write-Host "Verified AWS caller account $($identity.Account) using profile '$AwsProfile'."

$null = Invoke-AwsJson -CommandArguments @(
    "bedrock-agentcore-control", "list-agent-runtimes",
    "--max-results", "1",
    "--region", $AwsRegion,
    "--profile", $AwsProfile
)
Write-Host "AgentCore control-plane read access is available in $AwsRegion."

$modelMetadata = Invoke-AwsJson -CommandArguments @(
    "bedrock", "get-foundation-model",
    "--model-identifier", $BedrockModelId,
    "--region", $AwsRegion,
    "--profile", $AwsProfile
)
if (
    [string]$modelMetadata.modelDetails.modelId -cne $BedrockModelId -or
    [string]$modelMetadata.modelDetails.modelArn -cne $BedrockModelArn
) {
    throw "AWS foundation-model metadata does not exactly match the configured ID and ARN"
}
Write-Host "The configured foundation model metadata is visible. This does not invoke it or prove invocation entitlement."

try {
    $null = Get-StackDescription -StackName "CDKToolkit" -AwsRegion $AwsRegion -AwsProfile $AwsProfile
    Write-Host "The account is already CDK-bootstrapped in $AwsRegion."
}
catch {
    Write-Host "CDKToolkit was not confirmed. Run bootstrap.ps1 in Plan mode, then explicitly apply it if needed."
}

Write-Host "AWS read-only preflight passed. It did not create resources or invoke AgentCore/Bedrock."

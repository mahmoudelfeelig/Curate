[CmdletBinding()]
param(
    [ValidateSet("Plan", "Apply")][string]$Mode = "Plan",
    [Parameter(Mandatory = $true)][ValidatePattern("^\d{12}$")][string]$AwsAccountId,
    [Parameter(Mandatory = $true)][ValidatePattern("^[a-z]{2}(-gov)?-[a-z0-9-]+-\d$")][string]$AwsRegion,
    [Parameter(Mandatory = $true)][string]$AwsProfile,
    [Parameter(Mandatory = $true)][string]$BedrockModelId,
    [Parameter(Mandatory = $true)][ValidatePattern("^arn:")][string]$BedrockModelArn,
    [Parameter(Mandatory = $true)][ValidatePattern("^[a-z0-9-]{1,63}$")][string]$CognitoDomainPrefix,
    [string[]]$CallbackUrls = @("http://127.0.0.1:5173/auth/callback"),
    [string[]]$LogoutUrls = @("http://127.0.0.1:5173/"),
    [string]$ArtifactPath = "artifacts/feed-passport-agentcore.zip",
    [switch]$SkipPackage,
    [switch]$SkipNpmInstall,
    [AllowEmptyString()][string]$ApplyAcknowledgement = "",
    [AllowEmptyString()][string]$BillingAcknowledgement = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
$infraRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if ($Mode -eq "Apply" -and $SkipPackage) {
    throw "Apply mode forbids -SkipPackage; deployment must rebuild the artifact from this clean checkout"
}
$planArguments = @{
    AwsAccountId = $AwsAccountId
    AwsRegion = $AwsRegion
    BedrockModelId = $BedrockModelId
    BedrockModelArn = $BedrockModelArn
    CognitoDomainPrefix = $CognitoDomainPrefix
    CallbackUrls = $CallbackUrls
    LogoutUrls = $LogoutUrls
    ArtifactPath = $ArtifactPath
    SkipPackage = $SkipPackage
    SkipNpmInstall = $SkipNpmInstall
}

& (Join-Path $PSScriptRoot "plan.ps1") @planArguments
if ($LASTEXITCODE -ne 0) {
    throw "Local AgentCore plan failed with exit code $LASTEXITCODE"
}
if ($Mode -eq "Plan") {
    Write-Host "Deployment plan complete. No AWS API was called."
    return
}

Assert-ExactAcknowledgement -Actual $ApplyAcknowledgement -Expected "DEPLOY FEED PASSPORT AGENTCORE" -Purpose "AgentCore deployment"
Assert-ExactAcknowledgement -Actual $BillingAcknowledgement -Expected "AWS CREDITS ARE NOT A HARD SPEND CAP" -Purpose "AgentCore deployment billing risk"

& (Join-Path $PSScriptRoot "preflight.ps1") `
    -Mode AwsReadOnly `
    -AwsAccountId $AwsAccountId `
    -AwsRegion $AwsRegion `
    -AwsProfile $AwsProfile `
    -BedrockModelId $BedrockModelId `
    -BedrockModelArn $BedrockModelArn
if ($LASTEXITCODE -ne 0) {
    throw "AWS preflight failed with exit code $LASTEXITCODE"
}
$null = Get-StackDescription -StackName "CDKToolkit" -AwsRegion $AwsRegion -AwsProfile $AwsProfile

$artifact = [System.IO.Path]::GetFullPath((Join-Path $infraRoot $ArtifactPath))
$deploymentDirectory = Join-Path $infraRoot ".deployment"
New-Item -ItemType Directory -Path $deploymentDirectory -Force | Out-Null
$outputsFile = Join-Path $deploymentDirectory "stack-outputs.json"
$context = @(
    "-c", "awsAccountId=$AwsAccountId",
    "-c", "awsRegion=$AwsRegion",
    "-c", "artifactPath=$artifact",
    "-c", "bedrockModelId=$BedrockModelId",
    "-c", "bedrockModelArn=$BedrockModelArn",
    "-c", "callbackUrls=$($CallbackUrls -join ',')",
    "-c", "logoutUrls=$($LogoutUrls -join ',')",
    "-c", "cognitoDomainPrefix=$CognitoDomainPrefix"
)

Push-Location $infraRoot
try {
    $deployArguments = @(
        "deploy", "FeedPassportAgentCore",
        "--app", "node dist/bin/app.js",
        "--profile", $AwsProfile,
        "--require-approval", "never",
        "--outputs-file", $outputsFile
    ) + $context
    Invoke-ProjectCdk -InfraRoot $infraRoot -CommandArguments $deployArguments
    if ($LASTEXITCODE -ne 0) {
        throw "CDK deploy failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

$outputsDocument = Get-Content -LiteralPath $outputsFile -Raw | ConvertFrom-Json -Depth 20
$outputs = $outputsDocument.FeedPassportAgentCore
if ([string]::IsNullOrWhiteSpace([string]$outputs.RuntimeId)) {
    throw "Deployment completed without a RuntimeId output; do not invoke the runtime"
}

try {
    & (Join-Path $PSScriptRoot "enable-mmdsv2.ps1") `
        -Mode Apply `
        -RuntimeId $outputs.RuntimeId `
        -AwsRegion $AwsRegion `
        -AwsProfile $AwsProfile `
        -ApplyAcknowledgement "ENFORCE AGENTCORE MMDSV2"
    if ($LASTEXITCODE -ne 0) {
        throw "MMDSv2 enforcement failed with exit code $LASTEXITCODE"
    }
}
catch {
    throw "The stack exists, but its mandatory MMDSv2 post-deploy gate failed. Do not invoke it. Run enable-mmdsv2.ps1 or destroy.ps1. Details: $($_.Exception.Message)"
}

Write-Host "AgentCore stack deployed and MMDSv2 verified. No smoke or Bedrock invocation was performed."
Write-Host "Run inventory.ps1 -Mode AwsReadOnly to inspect resources. Invoke only through separately gated smoke.ps1."

[CmdletBinding()]
param(
    [ValidateSet("Plan", "Apply")][string]$Mode = "Plan",
    [Parameter(Mandatory = $true)][string]$RuntimeId,
    [Parameter(Mandatory = $true)][ValidatePattern("^[a-z]{2}(-gov)?-[a-z0-9-]+-\d$")][string]$AwsRegion,
    [Parameter(Mandatory = $true)][string]$AwsProfile,
    [AllowEmptyString()][string]$ApplyAcknowledgement = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

Write-Host "Plan: read Runtime '$RuntimeId', preserve its mutable configuration, set metadataConfiguration.requireMMDSV2=true, and verify READY state."
if ($Mode -eq "Plan") {
    Write-Host "Plan complete. No AWS API was called."
    return
}
Assert-ExactAcknowledgement -Actual $ApplyAcknowledgement -Expected "ENFORCE AGENTCORE MMDSV2" -Purpose "AgentCore Runtime security update"

$runtime = Invoke-AwsJson -CommandArguments @(
    "bedrock-agentcore-control", "get-agent-runtime",
    "--agent-runtime-id", $RuntimeId,
    "--region", $AwsRegion,
    "--profile", $AwsProfile
)
if ($runtime.status -ne "READY") {
    throw "Runtime must be READY before the MMDSv2 update; current status is $($runtime.status)"
}

$update = [ordered]@{
    agentRuntimeId = [string]$runtime.agentRuntimeId
    agentRuntimeArtifact = $runtime.agentRuntimeArtifact
    roleArn = [string]$runtime.roleArn
    networkConfiguration = $runtime.networkConfiguration
    metadataConfiguration = @{ requireMMDSV2 = $true }
    clientToken = [Guid]::NewGuid().ToString()
}
foreach ($property in @(
    "description",
    "authorizerConfiguration",
    "requestHeaderConfiguration",
    "protocolConfiguration",
    "lifecycleConfiguration",
    "environmentVariables",
    "filesystemConfigurations",
    "capacityProviderConfiguration"
)) {
    $member = $runtime.PSObject.Properties[$property]
    if ($null -ne $member -and $null -ne $member.Value) {
        $update[$property] = $member.Value
    }
}

$inputFile = [System.IO.Path]::GetTempFileName()
try {
    $update | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $inputFile -Encoding utf8NoBOM
    $null = Invoke-AwsJson -CommandArguments @(
        "bedrock-agentcore-control", "update-agent-runtime",
        "--cli-input-json", "file://$inputFile",
        "--region", $AwsRegion,
        "--profile", $AwsProfile
    )
}
finally {
    if (Test-Path -LiteralPath $inputFile) {
        Remove-Item -LiteralPath $inputFile -Force
    }
}

$deadline = [DateTime]::UtcNow.AddMinutes(10)
do {
    Start-Sleep -Seconds 5
    $runtime = Invoke-AwsJson -CommandArguments @(
        "bedrock-agentcore-control", "get-agent-runtime",
        "--agent-runtime-id", $RuntimeId,
        "--region", $AwsRegion,
        "--profile", $AwsProfile
    )
    if ($runtime.status -in @("UPDATE_FAILED", "CREATE_FAILED")) {
        throw "AgentCore Runtime MMDSv2 update failed: $($runtime.failureReason)"
    }
} while ($runtime.status -ne "READY" -and [DateTime]::UtcNow -lt $deadline)

if ($runtime.status -ne "READY") {
    throw "Timed out waiting for Runtime '$RuntimeId' to become READY"
}
if (
    $null -eq $runtime.PSObject.Properties["metadataConfiguration"] -or
    $runtime.metadataConfiguration.requireMMDSV2 -ne $true
) {
    throw "Runtime returned READY without metadataConfiguration.requireMMDSV2=true"
}
Write-Host "Verified Runtime '$RuntimeId' requires MMDSv2."

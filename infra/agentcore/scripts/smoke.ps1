[CmdletBinding()]
param(
    [ValidateSet("Validate", "Invoke")][string]$Mode = "Validate",
    [ValidateSet("Health", "PlanFeature")][string]$Operation = "Health",
    [string]$GatewayUrl,
    [string]$PayloadPath,
    [AllowEmptyString()][string]$InvokeAcknowledgement = "",
    [AllowEmptyString()][string]$BedrockAcknowledgement = ""
)

$ErrorActionPreference = "Stop"
$targetName = "curator-runtime"

if ($Operation -eq "PlanFeature") {
    if ([string]::IsNullOrWhiteSpace($PayloadPath)) {
        throw "PayloadPath is required for PlanFeature"
    }
    $resolvedPayload = (Resolve-Path -LiteralPath $PayloadPath).Path
    $payload = Get-Content -LiteralPath $resolvedPayload -Raw | ConvertFrom-Json -Depth 100
    if ([string]$payload.kind -cne "plan_feature") {
        throw "PlanFeature payload must have kind='plan_feature'"
    }
}
else {
    $payload = @{ kind = "health" }
}

Write-Host "Validated a proposal-only '$Operation' request for target '$targetName'."
if ($Mode -eq "Validate") {
    Write-Host "Validation complete. No AWS API, AgentCore Runtime, or Bedrock call was made."
    return
}

if ([string]::IsNullOrWhiteSpace($GatewayUrl)) {
    throw "GatewayUrl is required in Invoke mode; use the CloudFormation GatewayUrl output"
}
$gatewayUri = [Uri]$GatewayUrl
if (-not $gatewayUri.IsAbsoluteUri -or $gatewayUri.Scheme -ne "https") {
    throw "GatewayUrl must be an absolute HTTPS URL"
}
if ($gatewayUri.Host -notmatch "\.gateway\.bedrock-agentcore\.[a-z0-9-]+\.amazonaws\.com(\.cn)?$") {
    throw "GatewayUrl is not an AgentCore Gateway host"
}
if ($InvokeAcknowledgement -cne "INVOKE AGENTCORE MAY INCUR AWS CHARGES") {
    throw "Invoke mode requires the exact acknowledgement: INVOKE AGENTCORE MAY INCUR AWS CHARGES"
}
if ($Operation -eq "PlanFeature" -and $BedrockAcknowledgement -cne "INVOKE BEDROCK MAY INCUR AWS CHARGES") {
    throw "PlanFeature requires the exact acknowledgement: INVOKE BEDROCK MAY INCUR AWS CHARGES"
}

$secureToken = Read-Host "Paste a short-lived Cognito access token (input is hidden)" -AsSecureString
$token = [System.Net.NetworkCredential]::new("", $secureToken).Password
if ([string]::IsNullOrWhiteSpace($token)) {
    throw "A Cognito access token is required"
}
$endpoint = "$($GatewayUrl.TrimEnd('/'))/$targetName/invocations"
try {
    $response = Invoke-RestMethod `
        -Method Post `
        -Uri $endpoint `
        -MaximumRedirection 0 `
        -Headers @{ Authorization = "Bearer $token" } `
        -ContentType "application/json" `
        -Body ($payload | ConvertTo-Json -Depth 100 -Compress)
}
finally {
    $token = $null
    $secureToken.Dispose()
}

$response | ConvertTo-Json -Depth 100
Write-Host "The gated invocation completed. Set seven-day log retention after the first invocation with set-retention.ps1."

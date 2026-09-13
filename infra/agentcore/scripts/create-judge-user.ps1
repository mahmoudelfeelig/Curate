[CmdletBinding()]
param(
    [ValidateSet("Plan", "Apply")][string]$Mode = "Plan",
    [Parameter(Mandatory = $true)][ValidatePattern("^\d{12}$")][string]$AwsAccountId,
    [Parameter(Mandatory = $true)][ValidatePattern("^[a-z]{2}(-gov)?-[a-z0-9-]+-\d$")][string]$AwsRegion,
    [Parameter(Mandatory = $true)][string]$AwsProfile,
    [ValidatePattern("^[A-Za-z0-9_.+@-]{1,128}$")][string]$Username = "curate-judge",
    [string]$CredentialOutputPath = "..\..\artifacts\local\judge-access\curate-judge-credentials.txt",
    [AllowEmptyString()][string]$ApplyAcknowledgement = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
$infraRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $infraRoot "..\..")).Path
$allowedDirectory = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "artifacts\local\judge-access"))
$outputPath = [System.IO.Path]::GetFullPath((Join-Path $infraRoot $CredentialOutputPath))
$allowedPrefix = $allowedDirectory.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if (-not $outputPath.StartsWith($allowedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "CredentialOutputPath must stay inside $allowedDirectory"
}

Write-Host "Plan: create one Cognito judge user '$Username', generate a strong random password, and save the handoff only under ignored artifacts/local/judge-access."
if ($Mode -eq "Plan") {
    Write-Host "Plan complete. No password was generated and no AWS API was called."
    return
}
Assert-ExactAcknowledgement -Actual $ApplyAcknowledgement -Expected "CREATE CURATE JUDGE USER" -Purpose "Cognito judge-user creation"
$null = Assert-AwsAccount -ExpectedAccountId $AwsAccountId -AwsRegion $AwsRegion -AwsProfile $AwsProfile
$stack = Get-StackDescription -StackName "FeedPassportAgentCore" -AwsRegion $AwsRegion -AwsProfile $AwsProfile
$outputs = Convert-StackOutputsToMap -Stack $stack
$userPoolId = $outputs.UserPoolId
$clientId = $outputs.UserPoolClientId
$hostedUi = $outputs.CognitoHostedUiBaseUrl
if ([string]::IsNullOrWhiteSpace($userPoolId) -or [string]::IsNullOrWhiteSpace($clientId) -or [string]::IsNullOrWhiteSpace($hostedUi)) {
    throw "The stack is missing the Cognito outputs required for judge access"
}

$alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#%+-_"
$randomBytes = [byte[]]::new(24)
[System.Security.Cryptography.RandomNumberGenerator]::Fill($randomBytes)
$characters = for ($index = 0; $index -lt $randomBytes.Length; $index += 1) {
    $alphabet[$randomBytes[$index] % $alphabet.Length]
}
$password = "Aa7!" + (-join $characters)
$created = $false
$passwordFile = [System.IO.Path]::GetTempFileName()
try {
    & aws cognito-idp admin-create-user `
        --user-pool-id $userPoolId `
        --username $Username `
        --message-action SUPPRESS `
        --region $AwsRegion `
        --profile $AwsProfile `
        --no-cli-pager | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Cognito judge-user creation failed with exit code $LASTEXITCODE"
    }
    $created = $true

    @{
        UserPoolId = $userPoolId
        Username = $Username
        Password = $password
        Permanent = $true
    } | ConvertTo-Json | Set-Content -LiteralPath $passwordFile -Encoding utf8NoBOM
    & aws cognito-idp admin-set-user-password `
        --cli-input-json "file://$passwordFile" `
        --region $AwsRegion `
        --profile $AwsProfile `
        --no-cli-pager | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Cognito judge password update failed with exit code $LASTEXITCODE"
    }

    $redirectUri = "https://curate.elfeel.me/auth/callback"
    $authorization = "$($hostedUi.TrimEnd('/'))/oauth2/authorize?response_type=code&client_id=$([Uri]::EscapeDataString($clientId))&redirect_uri=$([Uri]::EscapeDataString($redirectUri))&scope=$([Uri]::EscapeDataString('openid feed-passport/invoke'))"
    New-Item -ItemType Directory -Path $allowedDirectory -Force | Out-Null
    @(
        "Curate judge access",
        "Username: $Username",
        "Password: $password",
        "Sign in: $authorization",
        "Created: $([DateTimeOffset]::UtcNow.ToString('O'))",
        "",
        "Keep this ignored local file private and delete or rotate the user after judging."
    ) | Set-Content -LiteralPath $outputPath -Encoding utf8NoBOM
}
catch {
    if ($created) {
        & aws cognito-idp admin-delete-user `
            --user-pool-id $userPoolId `
            --username $Username `
            --region $AwsRegion `
            --profile $AwsProfile `
            --no-cli-pager | Out-Null
    }
    throw
}
finally {
    $password = $null
    [Array]::Clear($randomBytes, 0, $randomBytes.Length)
    if (Test-Path -LiteralPath $passwordFile) {
        Remove-Item -LiteralPath $passwordFile -Force
    }
}
Write-Host "Created Cognito judge user '$Username'. The password was not printed; the ignored local handoff is at $outputPath."


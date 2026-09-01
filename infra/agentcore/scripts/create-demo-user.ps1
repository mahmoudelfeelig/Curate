[CmdletBinding()]
param(
    [ValidateSet("Plan", "Apply")][string]$Mode = "Plan",
    [Parameter(Mandatory = $true)][ValidatePattern("^\d{12}$")][string]$AwsAccountId,
    [Parameter(Mandatory = $true)][ValidatePattern("^[a-z]{2}(-gov)?-[a-z0-9-]+-\d$")][string]$AwsRegion,
    [Parameter(Mandatory = $true)][string]$AwsProfile,
    [Parameter(Mandatory = $true)][ValidatePattern("^[A-Za-z0-9_.+@-]{1,128}$")][string]$Username,
    [AllowEmptyString()][string]$ApplyAcknowledgement = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

Write-Host "Plan: create one Cognito-only dummy user '$Username' with email delivery suppressed and a hidden, operator-entered permanent password."
if ($Mode -eq "Plan") {
    Write-Host "Plan complete. No AWS API was called."
    return
}
Assert-ExactAcknowledgement -Actual $ApplyAcknowledgement -Expected "CREATE FEED PASSPORT DUMMY USER" -Purpose "Cognito dummy-user creation"
$null = Assert-AwsAccount -ExpectedAccountId $AwsAccountId -AwsRegion $AwsRegion -AwsProfile $AwsProfile
$stack = Get-StackDescription -StackName "FeedPassportAgentCore" -AwsRegion $AwsRegion -AwsProfile $AwsProfile
$outputs = Convert-StackOutputsToMap -Stack $stack
$userPoolId = $outputs.UserPoolId
if ([string]::IsNullOrWhiteSpace($userPoolId)) {
    throw "The stack has no UserPoolId output"
}

$passwordSecure = Read-Host "Enter a strong dummy-user password (input is hidden)" -AsSecureString
$password = [System.Net.NetworkCredential]::new("", $passwordSecure).Password
if ($password.Length -lt 12) {
    $password = $null
    $passwordSecure.Dispose()
    throw "Use a dummy-user password with at least 12 characters"
}

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
        throw "Cognito dummy-user creation failed with exit code $LASTEXITCODE"
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
        --no-cli-pager
    if ($LASTEXITCODE -ne 0) {
        throw "Cognito permanent-password update failed with exit code $LASTEXITCODE"
    }
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
    $passwordSecure.Dispose()
    if (Test-Path -LiteralPath $passwordFile) {
        Remove-Item -LiteralPath $passwordFile -Force
    }
}
Write-Host "Created Cognito dummy user '$Username'. No email was sent. Use the Hosted UI Authorization Code + PKCE flow to obtain an access token."

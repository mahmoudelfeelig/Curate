[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidatePattern("^\d{12}$")][string]$AwsAccountId,
    [Parameter(Mandatory = $true)][ValidatePattern("^[a-z]{2}(-gov)?-[a-z0-9-]+-\d$")][string]$AwsRegion,
    [Parameter(Mandatory = $true)][string]$BedrockModelId,
    [Parameter(Mandatory = $true)][ValidatePattern("^arn:")][string]$BedrockModelArn,
    [string[]]$CallbackUrls = @("http://127.0.0.1:5173/auth/callback"),
    [string[]]$LogoutUrls = @("http://127.0.0.1:5173/"),
    [Parameter(Mandatory = $true)][ValidatePattern("^[a-z0-9-]{1,63}$")][string]$CognitoDomainPrefix,
    [string]$ArtifactPath = "artifacts/feed-passport-agentcore.zip",
    [switch]$SkipPackage,
    [switch]$SkipNpmInstall
)

$ErrorActionPreference = "Stop"
$infraRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $infraRoot "..\..")).Path
$artifact = [System.IO.Path]::GetFullPath((Join-Path $infraRoot $ArtifactPath))
$allowedPrefix = $infraRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if (-not $artifact.StartsWith($allowedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "ArtifactPath must stay inside $infraRoot"
}
$sourceCommit = (& git -c safe.directory=$repoRoot -C $repoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceCommit -notmatch "^[0-9a-f]{40,64}$") {
    throw "Could not resolve the exact source commit for artifact validation"
}
$dirty = (& git -c safe.directory=$repoRoot -C $repoRoot status --porcelain=v1 --untracked-files=all --ignore-submodules=none)
if ($LASTEXITCODE -ne 0) {
    throw "Could not verify source worktree cleanliness"
}
if (-not [string]::IsNullOrWhiteSpace(($dirty -join "`n"))) {
    throw "AgentCore planning requires a clean source worktree"
}

foreach ($urlText in @($CallbackUrls + $LogoutUrls)) {
    $uri = [Uri]$urlText
    if (-not $uri.IsAbsoluteUri -or $uri.Scheme -notin @("http", "https")) {
        throw "OAuth URLs must be absolute HTTP(S) URLs: $urlText"
    }
    if ($uri.Scheme -eq "http" -and $uri.Host -notin @("localhost", "127.0.0.1")) {
        throw "Non-loopback OAuth URLs must use HTTPS: $urlText"
    }
}
if ([string]::IsNullOrWhiteSpace($BedrockModelId)) {
    throw "BedrockModelId must be explicit"
}

Push-Location $infraRoot
try {
    if (-not $SkipNpmInstall) {
        & npm ci --ignore-scripts --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed with exit code $LASTEXITCODE" }
    }
    & npm test
    if ($LASTEXITCODE -ne 0) { throw "CDK assertion tests failed with exit code $LASTEXITCODE" }

    if (-not $SkipPackage) {
        & (Join-Path $PSScriptRoot "package.ps1") -OutputPath $ArtifactPath
    }
    if (-not (Test-Path -LiteralPath $artifact)) {
        throw "AgentCore artifact does not exist: $artifact"
    }
    $python = Join-Path $repoRoot "services\curator\.venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        $python = (Get-Command python -ErrorAction Stop).Source
    }
    & $python (Join-Path $PSScriptRoot "validate_package.py") $artifact `
        --expected-source-commit $sourceCommit
    if ($LASTEXITCODE -ne 0) {
        throw "AgentCore artifact provenance validation failed with exit code $LASTEXITCODE"
    }

    & npm run build
    if ($LASTEXITCODE -ne 0) { throw "TypeScript build failed with exit code $LASTEXITCODE" }
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
    & npx cdk synth FeedPassportAgentCore --app "node dist/bin/app.js" --quiet @context
    if ($LASTEXITCODE -ne 0) { throw "CDK synth failed with exit code $LASTEXITCODE" }
    & node dist/scripts/validate-template.js "cdk.out/FeedPassportAgentCore.template.json"
    if ($LASTEXITCODE -ne 0) { throw "Template safety validation failed with exit code $LASTEXITCODE" }

    Write-Host "Local plan complete. No AWS API was called."
    Write-Host "Potentially billable resources if applied: AgentCore Runtime/Gateway, Bedrock, Cognito, S3 assets, and CloudWatch."
    Write-Host "AWS Budgets and credits are not hard spend caps; apply remains separately gated."
}
finally {
    Pop-Location
}

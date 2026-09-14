[CmdletBinding()]
param(
    [string]$StateRoot = "artifacts/local/final-live-session",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$localRoot = [System.IO.Path]::GetFullPath((Join-Path $repo "artifacts/local"))
$stateRootPath = [System.IO.Path]::GetFullPath((Join-Path $repo $StateRoot))
if (-not $stateRootPath.StartsWith($localRoot + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "StateRoot must stay inside artifacts/local"
}
$protectedPath = Join-Path $stateRootPath "protected-state.clixml"
$composePath = Join-Path $repo "compose.public-connect.yml"
$tunnelConfig = Join-Path $repo "artifacts/local/curate-tunnel-docker.yml"
foreach ($requiredPath in @($protectedPath, $composePath, $tunnelConfig)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) { throw "Missing required local input: $requiredPath" }
}

function Unprotect-Value([string]$cipherText) {
    $secure = ConvertTo-SecureString $cipherText
    return [System.Net.NetworkCredential]::new("", $secure).Password
}

$state = Import-Clixml -LiteralPath $protectedPath
$environment = @{}
foreach ($key in $state.Keys) {
    $environment[$key] = Unprotect-Value ([string]$state[$key])
}
$requiredKeys = @(
    "FEED_PASSPORT_CONNECTION_KEY_B64",
    "FEED_PASSPORT_CONNECTION_INDEX_KEY_B64",
    "FEED_PASSPORT_CONNECTION_KEY_ID",
    "FEED_PASSPORT_OAUTH_VAULT_KEY_B64",
    "FEED_PASSPORT_OAUTH_VAULT_KEY_ID",
    "FEED_PASSPORT_YOUTUBE_OAUTH_CLIENT_ID",
    "FEED_PASSPORT_YOUTUBE_OAUTH_CLIENT_SECRET",
    "FEED_PASSPORT_ATPROTO_STORE_KEY_B64",
    "FEED_PASSPORT_ATPROTO_STORE_KEY_ID",
    "FEED_PASSPORT_ATPROTO_SIDECAR_INTERNAL_SECRET",
    "FEED_PASSPORT_ATPROTO_PRIVATE_KEY_ID"
)
foreach ($key in $requiredKeys) {
    if (-not $environment.ContainsKey($key) -or -not [string]$environment[$key]) {
        throw "Protected state is missing $key"
    }
    [Environment]::SetEnvironmentVariable($key, [string]$environment[$key], "Process")
}

$occupied = netstat -ano | Select-String "LISTENING" | Where-Object { $_.Line -match ":(4310|8000)\s" }
if ($occupied) {
    throw "Ports 4310 or 8000 are already in use; stop the previous local harness before starting the persistent services"
}

$composeArguments = @("compose", "-f", $composePath, "up", "-d")
if (-not $SkipBuild) { $composeArguments += "--build" }
& docker @composeArguments
if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed to start Curate public-connect services" }

& docker restart curate-tunnel | Out-Null
if ($LASTEXITCODE -ne 0) { throw "The retained Cloudflare Tunnel failed to restart" }

$deadline = [DateTimeOffset]::UtcNow.AddMinutes(3)
$localReady = $false
while ([DateTimeOffset]::UtcNow -lt $deadline) {
    try {
        $api = Invoke-WebRequest "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 3
        $oauth = Invoke-WebRequest "http://127.0.0.1:4310/health" -UseBasicParsing -TimeoutSec 3
        if ($api.StatusCode -eq 200 -and $oauth.StatusCode -eq 200) { $localReady = $true; break }
    } catch {}
    Start-Sleep -Seconds 2
}
if (-not $localReady) {
    & docker compose -f $composePath ps
    throw "Curate public-connect services did not become healthy"
}

[pscustomobject]@{
    ready = $true
    api = "https://curate-api.elfeel.me"
    oauth = "https://curate-oauth.elfeel.me"
    local_api = "http://127.0.0.1:8000"
    local_oauth = "http://127.0.0.1:4310"
    restart_policy = "unless-stopped"
    secrets_printed = $false
} | ConvertTo-Json -Depth 4

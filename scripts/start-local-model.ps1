[CmdletBinding()]
param(
    [string]$ModelPath = (Join-Path $PSScriptRoot '..\artifacts\local\models\Qwen3-1.7B-Q8_0.gguf'),
    [string]$ServerPath = (Join-Path $PSScriptRoot '..\artifacts\local\runtime\b8184\llama-server.exe'),
    [int]$Port = 8080
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$resolvedModel = [System.IO.Path]::GetFullPath($ModelPath)
$resolvedServer = [System.IO.Path]::GetFullPath($ServerPath)
$expectedModelHash = '061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a'
$expectedServerHash = '94254e58f4f73cdf978dcfaa04007eabc38337c839bc674b54161b5ba3cffd3b'

if (-not (Test-Path -LiteralPath $resolvedServer -PathType Leaf)) {
    throw "llama.cpp server not found at $resolvedServer. Run scripts/acquire-llama-runtime.ps1 first."
}
if (-not (Test-Path -LiteralPath $resolvedModel -PathType Leaf)) {
    throw "Local model not found at $resolvedModel. Run scripts/acquire-local-model.ps1 first."
}
if ($Port -lt 1024 -or $Port -gt 65535) {
    throw 'Choose a non-privileged TCP port between 1024 and 65535.'
}

$modelHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $resolvedModel).Hash.ToLowerInvariant()
if ($modelHash -ne $expectedModelHash) {
    throw "Refusing an unverified local model. Expected SHA-256 $expectedModelHash; received $modelHash."
}
$serverHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $resolvedServer).Hash.ToLowerInvariant()
if ($serverHash -ne $expectedServerHash) {
    throw "Refusing an unverified llama.cpp runtime. Expected SHA-256 $expectedServerHash; received $serverHash."
}

$serverArguments = @(
    '--model', $resolvedModel,
    '--alias', 'feed-passport-local-qwen3-1.7b',
    '--offline',
    '--host', '127.0.0.1',
    '--port', "$Port",
    '--ctx-size', '4096',
    '--parallel', '1',
    '--gpu-layers', 'auto',
    '--flash-attn', 'auto',
    '--jinja',
    '--reasoning-budget', '0',
    '--chat-template-kwargs', '{"enable_thinking":false}',
    '--no-webui',
    '--no-slots',
    '--no-mmproj'
)

Write-Output "Starting verified local inference on http://127.0.0.1:$Port"
Write-Output 'The runtime is offline, loopback-only, single-slot, and uses no paid model service.'
Push-Location (Split-Path -Parent $resolvedServer)
try {
    & $resolvedServer @serverArguments
} finally {
    Pop-Location
}

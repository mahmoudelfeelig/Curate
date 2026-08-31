[CmdletBinding()]
param(
    [string]$ModelDirectory = (Join-Path $PSScriptRoot '..\artifacts\local\models'),
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$modelName = 'Qwen3-1.7B-Q8_0.gguf'
$modelRevision = '90862c4b9d2787eaed51d12237eafdfe7c5f6077'
$modelUrl = "https://huggingface.co/Qwen/Qwen3-1.7B-GGUF/resolve/$modelRevision/${modelName}?download=true"
$expectedBytes = 1834426016
$expectedHash = '061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a'
$resolvedDirectory = [System.IO.Path]::GetFullPath($ModelDirectory)
$modelPath = Join-Path $resolvedDirectory $modelName
$partialPath = "$modelPath.partial"

New-Item -ItemType Directory -Force -Path $resolvedDirectory | Out-Null

if ((Test-Path -LiteralPath $modelPath) -and -not $Force) {
    $existing = Get-Item -LiteralPath $modelPath
    $existingHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $modelPath).Hash.ToLowerInvariant()
    if ($existing.Length -eq $expectedBytes -and $existingHash -eq $expectedHash) {
        Write-Output "Verified existing local model: $modelPath"
        exit 0
    }
    throw "An unverified model already exists at $modelPath. Use -Force only after inspecting it."
}

if (Test-Path -LiteralPath $partialPath) {
    Remove-Item -LiteralPath $partialPath
}

Write-Output 'Downloading the public Apache-2.0 Qwen model directly from its pinned publisher revision.'
& curl.exe --fail --location --retry 3 --output $partialPath $modelUrl
if ($LASTEXITCODE -ne 0) {
    throw "Model download failed with exit code $LASTEXITCODE"
}

$download = Get-Item -LiteralPath $partialPath
$downloadHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $partialPath).Hash.ToLowerInvariant()
if ($download.Length -ne $expectedBytes -or $downloadHash -ne $expectedHash) {
    throw "Model verification failed. Expected $expectedBytes bytes and SHA-256 $expectedHash; received $($download.Length) bytes and $downloadHash."
}

Move-Item -LiteralPath $partialPath -Destination $modelPath -Force:$Force
Write-Output "Verified local model: $modelPath"
Write-Output "SHA-256: $downloadHash"

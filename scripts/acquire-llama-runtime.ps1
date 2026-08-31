[CmdletBinding()]
param(
    [string]$RuntimeDirectory = (Join-Path $PSScriptRoot '..\artifacts\local\runtime')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$runtimeTag = 'b8184'
$runtimeCommit = '319146247e643695f94a558e8ae686277dd4f8da'
$archiveName = "llama-$runtimeTag-bin-win-vulkan-x64.zip"
$archiveUrl = "https://github.com/ggml-org/llama.cpp/releases/download/$runtimeTag/$archiveName"
$expectedArchiveHash = '2d60828f4b90bdd1e93698837c163b54f40e7d682e8018dc40f52eb444c3cceb'
$expectedServerHash = '94254e58f4f73cdf978dcfaa04007eabc38337c839bc674b54161b5ba3cffd3b'
$expectedVulkanHash = '626e3b50d37104169a900fd1d8a6286c1278cb0b535592d1962c8714dab20f60'
$expectedLlamaHash = '847db2ce70ea3a63e45d94230e4a83c38bc688e32d4287f69a3c0155fcff2c57'
$resolvedDirectory = [System.IO.Path]::GetFullPath($RuntimeDirectory)
$archivePath = Join-Path $resolvedDirectory $archiveName
$partialPath = "$archivePath.partial"
$installDirectory = Join-Path $resolvedDirectory $runtimeTag
$serverPath = Join-Path $installDirectory 'llama-server.exe'
$vulkanPath = Join-Path $installDirectory 'ggml-vulkan.dll'
$llamaPath = Join-Path $installDirectory 'llama.dll'

New-Item -ItemType Directory -Force -Path $resolvedDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $installDirectory | Out-Null

$archiveVerified = $false
if (Test-Path -LiteralPath $archivePath -PathType Leaf) {
    $archiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToLowerInvariant()
    $archiveVerified = $archiveHash -eq $expectedArchiveHash
    if (-not $archiveVerified) {
        throw "An unverified llama.cpp archive exists at $archivePath. Expected SHA-256 $expectedArchiveHash; received $archiveHash."
    }
}

if (-not $archiveVerified) {
    if (Test-Path -LiteralPath $partialPath) {
        Remove-Item -LiteralPath $partialPath
    }
    Write-Output "Downloading official llama.cpp $runtimeTag Windows Vulkan runtime from its pinned release."
    & curl.exe --fail --location --retry 3 --output $partialPath $archiveUrl
    if ($LASTEXITCODE -ne 0) {
        throw "llama.cpp runtime download failed with exit code $LASTEXITCODE"
    }
    $downloadHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $partialPath).Hash.ToLowerInvariant()
    if ($downloadHash -ne $expectedArchiveHash) {
        throw "llama.cpp archive verification failed. Expected SHA-256 $expectedArchiveHash; received $downloadHash."
    }
    Move-Item -LiteralPath $partialPath -Destination $archivePath
}

# Keep each pinned release isolated so another local runtime cannot supply stale DLLs.
# Re-expanding the verified archive also repairs any locally modified runtime file.
Expand-Archive -LiteralPath $archivePath -DestinationPath $installDirectory -Force

foreach ($item in @(
    @{ Path = $serverPath; Hash = $expectedServerHash },
    @{ Path = $vulkanPath; Hash = $expectedVulkanHash },
    @{ Path = $llamaPath; Hash = $expectedLlamaHash }
)) {
    if (-not (Test-Path -LiteralPath $item.Path -PathType Leaf)) {
        throw "The verified runtime archive did not contain $($item.Path)."
    }
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $item.Path).Hash.ToLowerInvariant()
    if ($actualHash -ne $item.Hash) {
        throw "Extracted runtime verification failed for $($item.Path). Expected $($item.Hash); received $actualHash."
    }
}

Write-Output "Verified official llama.cpp runtime: $serverPath"
Write-Output "Release: $runtimeTag ($runtimeCommit)"
Write-Output "Archive SHA-256: $expectedArchiveHash"

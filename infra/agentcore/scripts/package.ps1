[CmdletBinding()]
param(
    [string]$OutputPath = "artifacts/feed-passport-agentcore.zip"
)

$ErrorActionPreference = "Stop"
$infraRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $infraRoot "..\..")).Path
$output = [System.IO.Path]::GetFullPath((Join-Path $infraRoot $OutputPath))
$allowedPrefix = $infraRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if (-not $output.StartsWith($allowedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputPath must stay inside $infraRoot"
}

$sourceCommit = (& git -c safe.directory=$repoRoot -C $repoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceCommit -notmatch "^[0-9a-f]{40,64}$") {
    throw "Could not resolve a valid source commit for the package manifest"
}
$dirty = (& git -c safe.directory=$repoRoot -C $repoRoot status --porcelain=v1 --untracked-files=all --ignore-submodules=none)
if ($LASTEXITCODE -ne 0) {
    throw "Could not verify source worktree cleanliness"
}
if (-not [string]::IsNullOrWhiteSpace(($dirty -join "`n"))) {
    throw "AgentCore packaging requires a clean source worktree"
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is required to resolve Python 3.13 Linux ARM64 dependencies without using AWS"
}

$stage = Join-Path $infraRoot ".package-stage"
$resolvedStage = [System.IO.Path]::GetFullPath($stage)
if (-not $resolvedStage.StartsWith($allowedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean an unverified package stage"
}
if (Test-Path -LiteralPath $resolvedStage) {
    Remove-Item -LiteralPath $resolvedStage -Recurse -Force
}
New-Item -ItemType Directory -Path $resolvedStage | Out-Null

try {
    $dockerArgs = @(
        "run", "--rm", "--platform", "linux/arm64",
        "-v", "${repoRoot}:/repo:ro",
        "-v", "${resolvedStage}:/stage",
        "python:3.13-slim",
        "python", "-m", "pip", "install",
        "--disable-pip-version-check", "--no-compile", "--target", "/stage",
        "/repo/services/curator"
    )
    & docker @dockerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Linux ARM64 dependency packaging failed with exit code $LASTEXITCODE"
    }

    Copy-Item -LiteralPath (Join-Path $infraRoot "packaging\agentcore_main.py") -Destination $resolvedStage
    @{
        format = "feed-passport-agentcore-package/v1"
        source_commit = $sourceCommit
        source_worktree_dirty = $false
        python_runtime = "PYTHON_3_13"
        architecture = "linux_arm64"
        entrypoint = "agentcore_main.py"
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $resolvedStage "package-manifest.json") -Encoding utf8NoBOM

    $python = Join-Path $repoRoot "services\curator\.venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        $python = (Get-Command python -ErrorAction Stop).Source
    }
    & $python (Join-Path $PSScriptRoot "build_zip.py") $resolvedStage $output
    if ($LASTEXITCODE -ne 0) {
        throw "Deterministic archive creation failed with exit code $LASTEXITCODE"
    }
    & $python (Join-Path $PSScriptRoot "validate_package.py") $output `
        --expected-source-commit $sourceCommit
    if ($LASTEXITCODE -ne 0) {
        throw "AgentCore archive validation failed with exit code $LASTEXITCODE"
    }
}
finally {
    if (Test-Path -LiteralPath $resolvedStage) {
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    }
}

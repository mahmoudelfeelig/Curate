[CmdletBinding()]
param(
    [switch]$IncludeLinuxArm64Package
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
$infraRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $infraRoot "..\..")).Path
$python = Join-Path $repoRoot "services\curator\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "The curator virtual environment is required for the local dry run: $python"
}

Push-Location (Join-Path $repoRoot "services\curator")
try {
    & $python -m pytest tests\agentcore\test_agentcore_runtime.py tests\agent\test_feature_intent_planner.py tests\agent\test_mission_planner.py -q
    if ($LASTEXITCODE -ne 0) { throw "Proposal-only Python tests failed with exit code $LASTEXITCODE" }
}
finally {
    Pop-Location
}

Push-Location $infraRoot
try {
    Invoke-ProjectNpm -CommandArguments @("test")
    if ($LASTEXITCODE -ne 0) { throw "AgentCore CDK tests failed with exit code $LASTEXITCODE" }
    if ($IncludeLinuxArm64Package) {
        & (Join-Path $PSScriptRoot "package.ps1")
    }
}
finally {
    Pop-Location
}

Write-Host "Local dry run passed. It used scripted/loopback models only and made no AWS call."

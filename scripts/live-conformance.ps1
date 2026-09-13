[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [switch]$IAcknowledgeAuthorizedDummyAccountMutations,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$')]
    [string]$OwnerId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$')]
    [string]$ConnectionId,

    [Parameter(Mandatory = $true)]
    [ValidateSet('bluesky', 'reddit', 'x', 'youtube')]
    [string]$Platform,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string[]]$Action,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$GitRevision,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Z_][A-Z0-9_]*$')]
    [string]$HmacKeyEnvironmentVariable,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$RuntimeFactory,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$OutputPath,

    [string]$ProviderApprovalReference = ''
)

$ErrorActionPreference = 'Stop'

if (-not $IAcknowledgeAuthorizedDummyAccountMutations.IsPresent) {
    throw 'Explicit acknowledgement of authorized dummy-account mutations is required.'
}

if ($Platform -eq 'reddit' -and [string]::IsNullOrWhiteSpace($ProviderApprovalReference)) {
    throw 'Reddit conformance requires a provider approval reference.'
}

if ($Platform -ne 'reddit' -and -not [string]::IsNullOrWhiteSpace($ProviderApprovalReference)) {
    throw 'Provider approval references are accepted only for Reddit.'
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$resolvedRoot = (& git -c "safe.directory=$repoRoot" -C $repoRoot rev-parse --show-toplevel 2>$null).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($resolvedRoot)) {
    throw 'The Feed Passport Git checkout could not be verified.'
}
if (
    -not [System.IO.Path]::GetFullPath($resolvedRoot).Equals(
        [System.IO.Path]::GetFullPath($repoRoot),
        [System.StringComparison]::OrdinalIgnoreCase
    )
) {
    throw 'Live conformance must run from the Feed Passport Git checkout root.'
}
$checkedOutRevision = (& git -c "safe.directory=$repoRoot" -C $repoRoot rev-parse HEAD 2>$null).Trim()
if ($LASTEXITCODE -ne 0 -or $checkedOutRevision -notmatch '^[0-9a-f]{40}$') {
    throw 'The checked-out Git revision could not be verified.'
}
if ($GitRevision -cne $checkedOutRevision) {
    throw 'GitRevision must exactly equal the checked-out HEAD.'
}
$dirtyCheckout = @(& git -c "safe.directory=$repoRoot" -C $repoRoot status --porcelain=v1 --untracked-files=all --ignore-submodules=none 2>$null)
if ($LASTEXITCODE -ne 0) {
    throw 'The Git checkout status could not be verified.'
}
if ($dirtyCheckout.Count -ne 0) {
    throw 'Live conformance requires a clean checkout with no tracked or untracked changes.'
}
$pythonPath = Join-Path $repoRoot 'services\curator\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Feed Passport curator virtual environment was not found at $pythonPath"
}

$arguments = @(
    '-m'
    'feed_passport.runtime.live_conformance_cli'
    '--acknowledge-authorized-dummy-account-mutations'
    '--owner-id'
    $OwnerId
    '--connection-id'
    $ConnectionId
    '--platform'
    $Platform
    '--git-revision'
    $GitRevision
    '--hmac-key-env'
    $HmacKeyEnvironmentVariable
    '--runtime-factory'
    $(if ($RuntimeFactory -eq 'builtin') {
        'feed_passport.runtime.live_conformance_factory:build_builtin_live_conformance_runtime'
    }
    else {
        $RuntimeFactory
    })
    '--output'
    $OutputPath
)

foreach ($actionValue in $Action) {
    $arguments += '--action'
    $arguments += $actionValue
}

if (-not [string]::IsNullOrWhiteSpace($ProviderApprovalReference)) {
    $arguments += '--provider-approval-ref'
    $arguments += $ProviderApprovalReference
}

Push-Location (Join-Path $repoRoot 'services\curator')
try {
    & $pythonPath @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Live conformance failed with exit code $LASTEXITCODE. No certification should be trusted."
    }
}
finally {
    Pop-Location
}

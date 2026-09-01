[CmdletBinding()]
param(
    [switch]$AsJson,
    [switch]$FailOnActionRequired
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$checks = [System.Collections.Generic.List[object]]::new()

function Add-ReadinessCheck {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Area,

        [Parameter(Mandatory = $true)]
        [string]$Check,

        [Parameter(Mandatory = $true)]
        [ValidateSet('ready_local', 'configured_unverified', 'action_required', 'invalid', 'zero_spend_blocked')]
        [string]$Status,

        [Parameter(Mandatory = $true)]
        [string]$Detail
    )

    $checks.Add([pscustomobject]@{
        area = $Area
        check = $Check
        status = $Status
        detail = $Detail
    })
}

function Get-ProcessEnvironmentValue {
    param([Parameter(Mandatory = $true)][string]$Name)

    return [Environment]::GetEnvironmentVariable($Name, 'Process')
}

function Test-ConfiguredValue {
    param([AllowNull()][string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $false
    }
    if ($Value -match '^(replace|example|changeme|your-|111122223333|<)') {
        return $false
    }
    return $Value -notmatch '(exact-|choose-a-|\.(example|invalid)(?:[/:]|$)|/u/dummy_operator|feed-passport-approved-demo)'
}

function Test-UrlSafeBase64Key {
    param([AllowNull()][string]$Value)

    if (-not (Test-ConfiguredValue $Value)) {
        return $false
    }
    try {
        $normalized = $Value.Replace('-', '+').Replace('_', '/')
        $normalized += '=' * ((4 - ($normalized.Length % 4)) % 4)
        return ([Convert]::FromBase64String($normalized).Length -eq 32)
    }
    catch [FormatException] {
        return $false
    }
}

function Test-SidecarOrigin {
    param([AllowNull()][string]$Value)

    if (-not (Test-ConfiguredValue $Value)) {
        return $false
    }
    try {
        $uri = [Uri]$Value
    }
    catch [UriFormatException] {
        return $false
    }
    if (
        -not $uri.IsAbsoluteUri -or
        $uri.Scheme -notin @('http', 'https') -or
        [string]::IsNullOrWhiteSpace($uri.Host) -or
        -not [string]::IsNullOrEmpty($uri.UserInfo) -or
        $uri.AbsolutePath -ne '/' -or
        -not [string]::IsNullOrEmpty($uri.Query) -or
        -not [string]::IsNullOrEmpty($uri.Fragment)
    ) {
        return $false
    }
    if ($uri.Scheme -eq 'https') {
        return $true
    }
    if ($uri.Host -ieq 'localhost') {
        return $true
    }
    $address = $null
    return [System.Net.IPAddress]::TryParse($uri.Host, [ref]$address) -and
        [System.Net.IPAddress]::IsLoopback($address)
}

function Test-LoopbackHost {
    param([AllowNull()][string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $false
    }
    $candidate = $Value.Trim().TrimStart('[').TrimEnd(']')
    if ($candidate -ieq 'localhost') {
        return $true
    }
    $address = $null
    return [System.Net.IPAddress]::TryParse($candidate, [ref]$address) -and
        [System.Net.IPAddress]::IsLoopback($address)
}

function Test-LoopbackOrigin {
    param([AllowNull()][string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $false
    }
    try {
        $uri = [Uri]$Value
    }
    catch [UriFormatException] {
        return $false
    }
    return $uri.IsAbsoluteUri -and
        $uri.Scheme -in @('http', 'https') -and
        (Test-LoopbackHost $uri.Host) -and
        [string]::IsNullOrEmpty($uri.UserInfo) -and
        $uri.AbsolutePath -eq '/' -and
        [string]::IsNullOrEmpty($uri.Query) -and
        [string]::IsNullOrEmpty($uri.Fragment)
}

function Test-HttpsOrigin {
    param([AllowNull()][string]$Value)

    if (-not (Test-HttpsEndpoint $Value)) {
        return $false
    }
    $uri = [Uri]$Value
    return $uri.AbsolutePath -eq '/' -and
        [string]::IsNullOrEmpty($uri.Query)
}

function Test-AppOAuthCallback {
    param([AllowNull()][string]$Value)

    if (-not (Test-ConfiguredValue $Value)) {
        return $false
    }
    try {
        $uri = [Uri]$Value
    }
    catch [UriFormatException] {
        return $false
    }
    $secureTransport = $uri.Scheme -eq 'https' -or
        ($uri.Scheme -eq 'http' -and (Test-LoopbackHost $uri.Host))
    return $uri.IsAbsoluteUri -and
        $secureTransport -and
        [string]::IsNullOrEmpty($uri.UserInfo) -and
        $uri.AbsolutePath -eq '/oauth/callback' -and
        [string]::IsNullOrEmpty($uri.Query) -and
        [string]::IsNullOrEmpty($uri.Fragment)
}

function Test-IdentityCallback {
    param([AllowNull()][string]$Value)

    if (-not (Test-ConfiguredValue $Value)) {
        return $false
    }
    try {
        $uri = [Uri]$Value
    }
    catch [UriFormatException] {
        return $false
    }
    $secureTransport = $uri.Scheme -eq 'https' -or
        ($uri.Scheme -eq 'http' -and (Test-LoopbackHost $uri.Host))
    return $uri.IsAbsoluteUri -and
        $secureTransport -and
        [string]::IsNullOrEmpty($uri.UserInfo) -and
        $uri.AbsolutePath -eq '/auth/callback' -and
        [string]::IsNullOrEmpty($uri.Query) -and
        [string]::IsNullOrEmpty($uri.Fragment)
}

function Test-HttpsEndpoint {
    param([AllowNull()][string]$Value)

    if (-not (Test-ConfiguredValue $Value)) {
        return $false
    }
    try {
        $uri = [Uri]$Value
    }
    catch [UriFormatException] {
        return $false
    }
    return $uri.IsAbsoluteUri -and
        $uri.Scheme -eq 'https' -and
        -not [string]::IsNullOrWhiteSpace($uri.Host) -and
        [string]::IsNullOrEmpty($uri.UserInfo) -and
        [string]::IsNullOrEmpty($uri.Fragment)
}

function Test-AllConfigured {
    param([Parameter(Mandatory = $true)][string[]]$Names)

    foreach ($name in $Names) {
        if (-not (Test-ConfiguredValue (Get-ProcessEnvironmentValue $name))) {
            return $false
        }
    }
    return $true
}

function Test-AnyConfigured {
    param([Parameter(Mandatory = $true)][string[]]$Names)

    foreach ($name in $Names) {
        if (Test-ConfiguredValue (Get-ProcessEnvironmentValue $name)) {
            return $true
        }
    }
    return $false
}

function Add-OAuthProviderCheck {
    param(
        [Parameter(Mandatory = $true)][string]$Platform,
        [Parameter(Mandatory = $true)][string]$Prefix,
        [Parameter(Mandatory = $true)][string]$ExternalRequirement
    )

    $clientIdName = "${Prefix}CLIENT_ID"
    $clientSecretName = "${Prefix}CLIENT_SECRET"
    $clientIdConfigured = Test-ConfiguredValue (Get-ProcessEnvironmentValue $clientIdName)
    $clientSecretConfigured = Test-ConfiguredValue (Get-ProcessEnvironmentValue $clientSecretName)

    if ($clientIdConfigured -and $clientSecretConfigured) {
        Add-ReadinessCheck $Platform 'OAuth client registration' 'configured_unverified' (
            "Client ID and secret are present in the process environment. $ExternalRequirement " +
            'No provider request was made, and no account authorization or live capability is proven.'
        )
        return
    }
    if ($clientIdConfigured -or $clientSecretConfigured) {
        Add-ReadinessCheck $Platform 'OAuth client registration' 'invalid' (
            "${clientIdName} and ${clientSecretName} must be configured together. Secret values were not inspected or printed."
        )
        return
    }
    Add-ReadinessCheck $Platform 'OAuth client registration' 'action_required' (
        "Create the provider registration, then set ${clientIdName} and ${clientSecretName} outside Git. $ExternalRequirement"
    )
}

$requiredFiles = @(
    'package.json',
    'services\curator\pyproject.toml',
    'services\atproto-oauth\package.json',
    'infra\agentcore\package.json',
    'scripts\live-conformance.ps1',
    'services\curator\src\feed_passport\runtime\live_conformance_factory.py'
)
$missingFiles = @($requiredFiles | Where-Object {
    -not (Test-Path -LiteralPath (Join-Path $repoRoot $_) -PathType Leaf)
})
if ($missingFiles.Count -eq 0) {
    Add-ReadinessCheck 'local' 'prepared implementation surfaces' 'ready_local' (
        'Browser, Curator, AT Protocol sidecar, AgentCore plan, and gated conformance entrypoints are present.'
    )
}
else {
    Add-ReadinessCheck 'local' 'prepared implementation surfaces' 'invalid' (
        "Expected repository files are missing: $($missingFiles -join ', ')."
    )
}

$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
if ($null -eq $nodeCommand) {
    Add-ReadinessCheck 'local' 'Node.js' 'action_required' 'Install Node.js 22 or newer for the browser and official AT Protocol OAuth package.'
}
else {
    try {
        $nodeVersionText = (& node --version 2>$null).Trim().TrimStart('v')
        $nodeVersion = [Version]$nodeVersionText
        $nodeStatus = if ($nodeVersion.Major -ge 22) { 'ready_local' } else { 'action_required' }
        $nodeDetail = if ($nodeVersion.Major -ge 22) {
            "Node.js $nodeVersionText is available; no package or network command was run."
        }
        else {
            "Node.js $nodeVersionText is installed, but the AT Protocol sidecar requires version 22 or newer."
        }
        Add-ReadinessCheck 'local' 'Node.js' $nodeStatus $nodeDetail
    }
    catch {
        Add-ReadinessCheck 'local' 'Node.js' 'invalid' 'Node.js was found, but its local version could not be parsed.'
    }
}

$pythonPath = Join-Path $repoRoot 'services\curator\.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $pythonPath -PathType Leaf) {
    Add-ReadinessCheck 'local' 'Curator virtual environment' 'ready_local' 'The repository-local Curator Python environment exists.'
}
else {
    Add-ReadinessCheck 'local' 'Curator virtual environment' 'action_required' 'Create the Python 3.13 Curator virtual environment using the README commands.'
}

$rootNodeModules = Join-Path $repoRoot 'node_modules'
if (Test-Path -LiteralPath $rootNodeModules -PathType Container) {
    Add-ReadinessCheck 'local' 'browser dependencies' 'ready_local' 'Root npm dependencies are installed locally.'
}
else {
    Add-ReadinessCheck 'local' 'browser dependencies' 'action_required' 'Run npm ci from the repository root; this checker never installs packages.'
}

$sidecarNodeModules = Join-Path $repoRoot 'services\atproto-oauth\node_modules'
$sidecarLockfile = Join-Path $repoRoot 'services\atproto-oauth\package-lock.json'
if (-not (Test-Path -LiteralPath $sidecarLockfile -PathType Leaf)) {
    Add-ReadinessCheck 'bluesky' 'official OAuth package dependency lock' 'action_required' (
        'Generate a registry-derived package-lock.json with the documented package-lock-only command, review it, and commit it before npm ci or a container build.'
    )
}
else {
    Add-ReadinessCheck 'bluesky' 'official OAuth package dependency lock' 'ready_local' (
        'The AT Protocol package lock exists locally. The checker did not contact npm or validate registry availability.'
    )
}
if ((Test-Path -LiteralPath $sidecarLockfile -PathType Leaf) -and (Test-Path -LiteralPath $sidecarNodeModules -PathType Container)) {
    Add-ReadinessCheck 'bluesky' 'official OAuth package dependencies' 'ready_local' 'AT Protocol sidecar dependencies are installed locally.'
}
else {
    Add-ReadinessCheck 'bluesky' 'official OAuth package dependencies' 'action_required' (
        'After the registry-derived lock exists, run npm ci in services/atproto-oauth when dependency installation is authorized.'
    )
}

$authMode = (Get-ProcessEnvironmentValue 'FEED_PASSPORT_AUTH_MODE')
if ([string]::IsNullOrWhiteSpace($authMode)) {
    $authMode = 'demo'
}
$authMode = $authMode.Trim().ToLowerInvariant()
$unsafeLoopbackValue = Get-ProcessEnvironmentValue 'FEED_PASSPORT_ALLOW_INSECURE_LOOPBACK_OAUTH'
if ([string]::IsNullOrWhiteSpace($unsafeLoopbackValue)) {
    $unsafeLoopbackValue = '0'
}
$bindHost = Get-ProcessEnvironmentValue 'FEED_PASSPORT_BIND_HOST'
if ([string]::IsNullOrWhiteSpace($bindHost)) {
    $bindHost = '127.0.0.1'
}
$allowedOriginText = Get-ProcessEnvironmentValue 'FEED_PASSPORT_ALLOWED_ORIGINS'
if ([string]::IsNullOrWhiteSpace($allowedOriginText)) {
    $allowedOriginText = 'http://localhost:5173,http://127.0.0.1:5173'
}
$allowedOriginValues = @($allowedOriginText.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$loopbackHarnessValid = $unsafeLoopbackValue -eq '1' -and
    (Test-LoopbackHost $bindHost) -and
    $allowedOriginValues.Count -gt 0 -and
    @($allowedOriginValues | Where-Object { -not (Test-LoopbackOrigin $_) }).Count -eq 0

if ($unsafeLoopbackValue -notin @('0', '1')) {
    Add-ReadinessCheck 'identity' 'Curator owner authentication' 'invalid' (
        'FEED_PASSPORT_ALLOW_INSECURE_LOOPBACK_OAUTH must be exactly 0 or 1.'
    )
}
elseif ($authMode -eq 'demo' -and $unsafeLoopbackValue -eq '1') {
    if ($loopbackHarnessValid) {
        Add-ReadinessCheck 'identity' 'Curator owner authentication' 'configured_unverified' (
            'The explicit one-person dummy-account harness is enabled with a loopback bind and loopback-only origins. It has no user identity boundary, must never be proxied or deployed, and no request was made.'
        )
    }
    else {
        Add-ReadinessCheck 'identity' 'Curator owner authentication' 'invalid' (
            'The insecure dummy-account harness requires a loopback FEED_PASSPORT_BIND_HOST and only root loopback FEED_PASSPORT_ALLOWED_ORIGINS.'
        )
    }
}
elseif ($authMode -eq 'demo') {
    Add-ReadinessCheck 'identity' 'Curator owner authentication' 'action_required' (
        'Demo mode supports only account-free proof. Configure OIDC, or explicitly enable the one-person loopback-only dummy-account harness for a local test.'
    )
}
elseif ($authMode -ne 'oidc') {
    Add-ReadinessCheck 'identity' 'Curator owner authentication' 'invalid' "FEED_PASSPORT_AUTH_MODE must be demo or oidc."
}
elseif ($unsafeLoopbackValue -eq '1') {
    Add-ReadinessCheck 'identity' 'Curator owner authentication' 'invalid' (
        'OIDC mode does not need the insecure loopback override. Set FEED_PASSPORT_ALLOW_INSECURE_LOOPBACK_OAUTH=0 to avoid an unsafe future fallback.'
    )
}
elseif (Test-AllConfigured @(
    'FEED_PASSPORT_OIDC_ISSUER',
    'FEED_PASSPORT_OIDC_AUDIENCE',
    'FEED_PASSPORT_OIDC_JWKS_URL'
)) {
    $backendIssuer = Get-ProcessEnvironmentValue 'FEED_PASSPORT_OIDC_ISSUER'
    $backendAudience = Get-ProcessEnvironmentValue 'FEED_PASSPORT_OIDC_AUDIENCE'
    $backendJwks = Get-ProcessEnvironmentValue 'FEED_PASSPORT_OIDC_JWKS_URL'
    if (
        -not (Test-HttpsEndpoint $backendIssuer) -or
        -not (Test-HttpsEndpoint $backendJwks) -or
        $backendAudience -match '\s'
    ) {
        Add-ReadinessCheck 'identity' 'Curator owner authentication' 'invalid' (
            'OIDC issuer/JWKS must be safe absolute HTTPS URLs and the audience/client ID cannot contain whitespace.'
        )
    }
    else {
        Add-ReadinessCheck 'identity' 'Curator owner authentication' 'configured_unverified' (
            'OIDC issuer, audience, and JWKS URL are present. No discovery, key download, token validation, or browser sign-in was attempted.'
        )
    }
}
else {
    Add-ReadinessCheck 'identity' 'Curator owner authentication' 'invalid' (
        'OIDC mode requires issuer, audience/client ID, and JWKS URL together.'
    )
}

$browserOidcNames = @(
    'VITE_FEED_PASSPORT_OIDC_CLIENT_ID',
    'VITE_FEED_PASSPORT_OIDC_HOSTED_UI_URL',
    'VITE_FEED_PASSPORT_OIDC_ISSUER'
)
$browserOidcConfigured = @($browserOidcNames | Where-Object {
    Test-ConfiguredValue (Get-ProcessEnvironmentValue $_)
})
if ($browserOidcConfigured.Count -eq 0) {
    Add-ReadinessCheck 'identity' 'browser Authorization Code + PKCE client' 'action_required' (
        'Configure the public client ID, hosted UI origin, and exact issuer together before a real account sign-in.'
    )
}
elseif ($browserOidcConfigured.Count -ne $browserOidcNames.Count) {
    Add-ReadinessCheck 'identity' 'browser Authorization Code + PKCE client' 'invalid' (
        'VITE browser OIDC client ID, hosted UI URL, and issuer must be configured together.'
    )
}
elseif (
    (Get-ProcessEnvironmentValue 'VITE_FEED_PASSPORT_OIDC_CLIENT_ID') -notmatch '^[A-Za-z0-9._-]{3,128}$' -or
    -not (Test-HttpsEndpoint (Get-ProcessEnvironmentValue 'VITE_FEED_PASSPORT_OIDC_HOSTED_UI_URL')) -or
    -not (Test-HttpsEndpoint (Get-ProcessEnvironmentValue 'VITE_FEED_PASSPORT_OIDC_ISSUER')) -or
    (
        (Test-ConfiguredValue (Get-ProcessEnvironmentValue 'VITE_FEED_PASSPORT_OIDC_REDIRECT_URI')) -and
        -not (Test-IdentityCallback (Get-ProcessEnvironmentValue 'VITE_FEED_PASSPORT_OIDC_REDIRECT_URI'))
    )
) {
    Add-ReadinessCheck 'identity' 'browser Authorization Code + PKCE client' 'invalid' (
        'The public client ID, hosted UI/issuer HTTPS URLs, or exact /auth/callback URI is malformed.'
    )
}
else {
    Add-ReadinessCheck 'identity' 'browser Authorization Code + PKCE client' 'configured_unverified' (
        'The browser PKCE configuration is present. No authorization, token exchange, issuer match, or API request was attempted.'
    )
}

if ($browserOidcConfigured.Count -eq $browserOidcNames.Count) {
    if ($authMode -ne 'oidc') {
        Add-ReadinessCheck 'identity' 'browser/API OIDC alignment' 'invalid' (
            'Browser OIDC is configured while the API is not in OIDC mode; the visible identity gate would not match the server authority boundary.'
        )
    }
    elseif (
        (Test-ConfiguredValue (Get-ProcessEnvironmentValue 'FEED_PASSPORT_OIDC_ISSUER')) -and
        (Get-ProcessEnvironmentValue 'FEED_PASSPORT_OIDC_ISSUER').TrimEnd('/') -ne
            (Get-ProcessEnvironmentValue 'VITE_FEED_PASSPORT_OIDC_ISSUER').TrimEnd('/')
    ) {
        Add-ReadinessCheck 'identity' 'browser/API OIDC alignment' 'invalid' (
            'Browser and API issuer values do not identify the same exact issuer.'
        )
    }
    else {
        Add-ReadinessCheck 'identity' 'browser/API OIDC alignment' 'configured_unverified' (
            'Browser and API modes are aligned to one issuer. No token or sign-in was attempted.'
        )
    }
}
elseif ($authMode -eq 'oidc') {
    Add-ReadinessCheck 'identity' 'browser/API OIDC alignment' 'invalid' (
        'API OIDC mode requires the complete browser Authorization Code + PKCE configuration for interactive onboarding.'
    )
}
else {
    Add-ReadinessCheck 'identity' 'browser/API OIDC alignment' 'ready_local' (
        'Browser OIDC is intentionally absent from the account-free or explicit loopback-harness build.'
    )
}

$requiredScopeText = Get-ProcessEnvironmentValue 'FEED_PASSPORT_OIDC_REQUIRED_SCOPES'
if ([string]::IsNullOrWhiteSpace($requiredScopeText)) {
    $requiredScopeText = 'feed-passport/invoke'
}
$browserScopeText = Get-ProcessEnvironmentValue 'VITE_FEED_PASSPORT_OIDC_SCOPES'
if ([string]::IsNullOrWhiteSpace($browserScopeText)) {
    $browserScopeText = 'openid feed-passport/invoke'
}
$requiredScopes = @($requiredScopeText.Split(' ', [System.StringSplitOptions]::RemoveEmptyEntries))
$browserScopes = @($browserScopeText.Split(' ', [System.StringSplitOptions]::RemoveEmptyEntries))
$missingRequestedScopes = @($requiredScopes | Where-Object { $_ -notin $browserScopes })
if ($requiredScopes.Count -eq 0 -or 'openid' -notin $browserScopes -or $missingRequestedScopes.Count -gt 0) {
    Add-ReadinessCheck 'identity' 'OIDC scope contract' 'invalid' (
        'Browser scopes must include openid and every API-required application scope.'
    )
}
else {
    Add-ReadinessCheck 'identity' 'OIDC scope contract' 'ready_local' (
        'The browser request includes openid and every configured API-required scope.'
    )
}

$connectionKeyNames = @(
    'FEED_PASSPORT_CONNECTION_KEY_B64',
    'FEED_PASSPORT_CONNECTION_INDEX_KEY_B64'
)
$connectionKeysPresent = @($connectionKeyNames | Where-Object {
    Test-ConfiguredValue (Get-ProcessEnvironmentValue $_)
})
$invalidConnectionKeys = @($connectionKeyNames | Where-Object {
    $value = Get-ProcessEnvironmentValue $_
    (Test-ConfiguredValue $value) -and -not (Test-UrlSafeBase64Key $value)
})
if ($connectionKeysPresent.Count -eq 0) {
    Add-ReadinessCheck 'credentials' 'encrypted connection registry' 'action_required' (
        'Generate two independent 32-byte URL-safe-base64 registry keys and keep them outside Git before any OAuth connection is enabled.'
    )
}
elseif ($connectionKeysPresent.Count -ne $connectionKeyNames.Count) {
    Add-ReadinessCheck 'credentials' 'encrypted connection registry' 'invalid' (
        'Connection metadata and blind-index keys must be configured together.'
    )
}
elseif ($invalidConnectionKeys.Count -gt 0) {
    Add-ReadinessCheck 'credentials' 'encrypted connection registry' 'invalid' (
        'Each connection-registry key must decode to exactly 32 bytes. Values were not printed.'
    )
}
else {
    $uniqueKeys = @($connectionKeyNames | ForEach-Object {
        Get-ProcessEnvironmentValue $_
    } | Select-Object -Unique)
    if ($uniqueKeys.Count -ne $connectionKeyNames.Count) {
        Add-ReadinessCheck 'credentials' 'encrypted connection registry' 'invalid' 'The metadata and blind-index keys must be independently generated.'
    }
    else {
        Add-ReadinessCheck 'credentials' 'encrypted connection registry' 'configured_unverified' (
            'Two independent correctly shaped connection-registry keys are present in process memory. The checker did not open the database or print a key.'
        )
    }
}

$standardOAuthClientNames = @(
    'FEED_PASSPORT_YOUTUBE_OAUTH_CLIENT_ID',
    'FEED_PASSPORT_X_OAUTH_CLIENT_ID',
    'FEED_PASSPORT_REDDIT_OAUTH_CLIENT_ID'
)
$standardOAuthConfigured = Test-AnyConfigured $standardOAuthClientNames
$oauthVaultKey = Get-ProcessEnvironmentValue 'FEED_PASSPORT_OAUTH_VAULT_KEY_B64'
$oauthVaultConfigured = Test-ConfiguredValue $oauthVaultKey
if ($standardOAuthConfigured -and -not $oauthVaultConfigured) {
    Add-ReadinessCheck 'credentials' 'standard OAuth token vault' 'invalid' (
        'A YouTube, X, or Reddit client ID requires its separate 32-byte OAuth-vault key.'
    )
}
elseif (-not $oauthVaultConfigured) {
    Add-ReadinessCheck 'credentials' 'standard OAuth token vault' 'ready_local' (
        'No standard OAuth provider is configured. Bluesky keeps token material in its sidecar and does not require this vault.'
    )
}
elseif (-not (Test-UrlSafeBase64Key $oauthVaultKey)) {
    Add-ReadinessCheck 'credentials' 'standard OAuth token vault' 'invalid' (
        'The standard OAuth-vault key must decode to exactly 32 bytes. Its value was not printed.'
    )
}
elseif ($connectionKeysPresent.Count -ne $connectionKeyNames.Count) {
    Add-ReadinessCheck 'credentials' 'standard OAuth token vault' 'invalid' (
        'The standard OAuth vault requires both connection-registry keys.'
    )
}
elseif ($oauthVaultKey -in @(
    (Get-ProcessEnvironmentValue 'FEED_PASSPORT_CONNECTION_KEY_B64')
    (Get-ProcessEnvironmentValue 'FEED_PASSPORT_CONNECTION_INDEX_KEY_B64')
)) {
    Add-ReadinessCheck 'credentials' 'standard OAuth token vault' 'invalid' (
        'The OAuth-vault key must be independently generated and cannot reuse either connection-registry key.'
    )
}
else {
    Add-ReadinessCheck 'credentials' 'standard OAuth token vault' 'configured_unverified' (
        'A separate correctly shaped OAuth-vault key is present. The checker did not open or decrypt the vault.'
    )
}

$redirects = Get-ProcessEnvironmentValue 'FEED_PASSPORT_OAUTH_REDIRECT_URIS'
if ([string]::IsNullOrWhiteSpace($redirects)) {
    $redirectValues = @('http://127.0.0.1:5173/oauth/callback')
    Add-ReadinessCheck 'oauth' 'redirect allowlist' 'ready_local' (
        'The backend default is the exact loopback callback http://127.0.0.1:5173/oauth/callback.'
    )
}
else {
    $redirectValues = @($redirects.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $invalidRedirects = @($redirectValues | Where-Object {
        -not (Test-AppOAuthCallback $_)
    })
    if ($redirectValues.Count -eq 0 -or $invalidRedirects.Count -gt 0) {
        Add-ReadinessCheck 'oauth' 'redirect allowlist' 'invalid' 'Every callback must use HTTPS except an explicit localhost or 127.0.0.1 development URL.'
    }
    else {
        Add-ReadinessCheck 'oauth' 'redirect allowlist' 'configured_unverified' (
            "$($redirectValues.Count) exact callback URI(s) are configured; provider registrations were not contacted."
        )
    }
}

$browserSocialCallback = Get-ProcessEnvironmentValue 'VITE_FEED_PASSPORT_OAUTH_REDIRECT_URI'
if ([string]::IsNullOrWhiteSpace($browserSocialCallback)) {
    $browserSocialCallback = 'http://127.0.0.1:5173/oauth/callback'
}
if (-not (Test-AppOAuthCallback $browserSocialCallback)) {
    Add-ReadinessCheck 'oauth' 'browser/backend social callback alignment' 'invalid' (
        'The browser social callback must use HTTPS, except for loopback HTTP, and end exactly in /oauth/callback.'
    )
}
elseif ($browserSocialCallback -notin $redirectValues) {
    Add-ReadinessCheck 'oauth' 'browser/backend social callback alignment' 'invalid' (
        'The browser social callback is not present byte-for-byte in the backend redirect allowlist.'
    )
}
else {
    Add-ReadinessCheck 'oauth' 'browser/backend social callback alignment' 'ready_local' (
        'The browser callback matches one backend allowlist entry exactly; provider console registrations remain unverified.'
    )
}

Add-OAuthProviderCheck 'youtube' 'FEED_PASSPORT_YOUTUBE_OAUTH_' (
    'A Google Cloud project, enabled YouTube Data API v3, OAuth consent screen, and dummy Google/YouTube test user remain manual provider steps.'
)
$xClientConfigured = Test-ConfiguredValue (
    Get-ProcessEnvironmentValue 'FEED_PASSPORT_X_OAUTH_CLIENT_ID'
)
$xSecretConfigured = Test-ConfiguredValue (
    Get-ProcessEnvironmentValue 'FEED_PASSPORT_X_OAUTH_CLIENT_SECRET'
)
if (-not $xClientConfigured -and -not $xSecretConfigured) {
    Add-ReadinessCheck 'x' 'OAuth client registration' 'zero_spend_blocked' (
        'X credentials are intentionally absent. Do not create a billable project merely to clear readiness; use twin:x and Guided mode.'
    )
}
elseif ($xClientConfigured -ne $xSecretConfigured) {
    Add-ReadinessCheck 'x' 'OAuth client registration' 'invalid' (
        'The X client ID and secret are partial. Remove the partial setup under zero spend, or configure both only after separate spend authorization.'
    )
}
else {
    Add-ReadinessCheck 'x' 'OAuth client registration' 'configured_unverified' (
        'An X client registration is present, but the checker made no provider request. Do not invoke it while the zero-spend rule remains active.'
    )
}
Add-ReadinessCheck 'x' 'zero-spend live validation' 'zero_spend_blocked' (
    'Do not fund, invoke, or conformance-test the X API unless the user later authorizes potential charges explicitly.'
)
Add-OAuthProviderCheck 'reddit' 'FEED_PASSPORT_REDDIT_OAUTH_' (
    'Reddit Data API approval and an approval reference remain mandatory before live validation.'
)
$redditClientConfigured = Test-ConfiguredValue (
    Get-ProcessEnvironmentValue 'FEED_PASSPORT_REDDIT_OAUTH_CLIENT_ID'
)
$redditUserAgent = Get-ProcessEnvironmentValue 'FEED_PASSPORT_REDDIT_OAUTH_USER_AGENT'
$redditUserAgentConfigured = Test-ConfiguredValue $redditUserAgent
if (-not $redditClientConfigured -and -not $redditUserAgentConfigured) {
    Add-ReadinessCheck 'reddit' 'registered application User-Agent' 'action_required' (
        'After approval, configure a dedicated platform:app-id:version (by /u/dummy_operator) User-Agent; the format example is not a real identity.'
    )
}
elseif ($redditClientConfigured -ne $redditUserAgentConfigured) {
    Add-ReadinessCheck 'reddit' 'registered application User-Agent' 'invalid' (
        'The Reddit client ID and dedicated approved-application User-Agent must be configured together.'
    )
}
elseif (
    $redditUserAgent.Length -lt 10 -or
    $redditUserAgent.Length -gt 256 -or
    $redditUserAgent -match '[^\x20-\x7E]' -or
    $redditUserAgent.Trim().ToLowerInvariant() -in @('python', 'unknown', 'feed-passport/0.1') -or
    $redditUserAgent -notmatch '^[A-Za-z0-9._-]+:[A-Za-z0-9._-]+:[A-Za-z0-9._-]+ \(by /u/[A-Za-z0-9_-]{3,20}\)$'
) {
    Add-ReadinessCheck 'reddit' 'registered application User-Agent' 'invalid' (
        'Use the dedicated printable-ASCII platform:app-id:version (by /u/dummy_operator) value covered by Reddit approval. The value was not printed.'
    )
}
else {
    Add-ReadinessCheck 'reddit' 'registered application User-Agent' 'configured_unverified' (
        'A correctly shaped dedicated application User-Agent is present. Approval and provider acceptance were not contacted or inferred.'
    )
}
Add-ReadinessCheck 'reddit' 'provider approval' 'action_required' (
    'Approval is a human/provider step and cannot be inferred from an OAuth client ID. Guided mode remains available without it.'
)

$sidecarVariableNames = @(
    'FEED_PASSPORT_ATPROTO_SIDECAR_URL',
    'FEED_PASSPORT_ATPROTO_SIDECAR_INTERNAL_SECRET',
    'FEED_PASSPORT_ATPROTO_APP_CALLBACK_URI'
)
$sidecarUrl = Get-ProcessEnvironmentValue $sidecarVariableNames[0]
$sidecarSecret = Get-ProcessEnvironmentValue $sidecarVariableNames[1]
$sidecarCallback = Get-ProcessEnvironmentValue $sidecarVariableNames[2]
$sidecarConfigured = @($sidecarVariableNames | Where-Object {
    Test-ConfiguredValue (Get-ProcessEnvironmentValue $_)
})
if ($sidecarConfigured.Count -eq 0) {
    Add-ReadinessCheck 'bluesky' 'Python-to-sidecar bridge' 'action_required' (
        'Configure the fixed sidecar origin, high-entropy internal service secret, and exact app callback after the sidecar is composed.'
    )
}
elseif ($sidecarConfigured.Count -ne $sidecarVariableNames.Count) {
    Add-ReadinessCheck 'bluesky' 'Python-to-sidecar bridge' 'invalid' (
        'The sidecar URL, internal secret, and AT Protocol app callback URI must be configured together.'
    )
}
elseif ($sidecarSecret.Length -lt 32 -or $sidecarSecret.Length -gt 4096 -or $sidecarSecret -match '[\r\n]') {
    Add-ReadinessCheck 'bluesky' 'Python-to-sidecar bridge' 'invalid' (
        'The internal service secret must contain 32 to 4096 characters. Its value was not printed.'
    )
}
elseif (-not (Test-SidecarOrigin $sidecarUrl)) {
    Add-ReadinessCheck 'bluesky' 'Python-to-sidecar bridge' 'invalid' (
        'The sidecar URL must use HTTPS, except for an exact loopback development origin.'
    )
}
elseif (-not (Test-AppOAuthCallback $sidecarCallback)) {
    Add-ReadinessCheck 'bluesky' 'Python-to-sidecar bridge' 'invalid' (
        'The AT Protocol app callback must use HTTPS, except for loopback HTTP, and end exactly in /oauth/callback.'
    )
}
elseif ($sidecarCallback -ne $browserSocialCallback -or $sidecarCallback -notin $redirectValues) {
    Add-ReadinessCheck 'bluesky' 'Python-to-sidecar bridge' 'invalid' (
        'The AT Protocol app callback must match the browser callback and one backend allowlist entry byte-for-byte.'
    )
}
else {
    Add-ReadinessCheck 'bluesky' 'Python-to-sidecar bridge' 'configured_unverified' (
        'The fixed origin and correctly shaped internal secret are present. No health, metadata, OAuth, PDS, or account request was made.'
    )
}

$sidecarServerNames = @(
    'FEED_PASSPORT_ATPROTO_PUBLIC_ORIGIN',
    'FEED_PASSPORT_ATPROTO_STORE_PATH',
    'FEED_PASSPORT_ATPROTO_STORE_KEY_B64',
    'FEED_PASSPORT_ATPROTO_SIDECAR_INTERNAL_SECRET',
    'FEED_PASSPORT_ATPROTO_PRIVATE_KEY_FILE',
    'FEED_PASSPORT_ATPROTO_PRIVATE_KEY_ID',
    'FEED_PASSPORT_ATPROTO_APP_CALLBACK_URI'
)
$sidecarServerConfigured = @($sidecarServerNames | Where-Object {
    Test-ConfiguredValue (Get-ProcessEnvironmentValue $_)
})
if ($sidecarServerConfigured.Count -eq 0) {
    Add-ReadinessCheck 'bluesky' 'restart-safe single-replica sidecar' 'action_required' (
        'Configure the public HTTPS origin, encrypted store, confidential-client key file, private-hop secret, and app callback before starting the live sidecar.'
    )
}
elseif ($sidecarServerConfigured.Count -ne $sidecarServerNames.Count) {
    Add-ReadinessCheck 'bluesky' 'restart-safe single-replica sidecar' 'invalid' (
        'The required runnable sidecar settings are partial. Use the complete AT Protocol block in .env.example.'
    )
}
else {
    $publicOrigin = Get-ProcessEnvironmentValue 'FEED_PASSPORT_ATPROTO_PUBLIC_ORIGIN'
    $storePath = Get-ProcessEnvironmentValue 'FEED_PASSPORT_ATPROTO_STORE_PATH'
    $storeKey = Get-ProcessEnvironmentValue 'FEED_PASSPORT_ATPROTO_STORE_KEY_B64'
    $privateKeyPath = Get-ProcessEnvironmentValue 'FEED_PASSPORT_ATPROTO_PRIVATE_KEY_FILE'
    $privateKeyId = Get-ProcessEnvironmentValue 'FEED_PASSPORT_ATPROTO_PRIVATE_KEY_ID'
    $sidecarHost = Get-ProcessEnvironmentValue 'FEED_PASSPORT_ATPROTO_HOST'
    if ([string]::IsNullOrWhiteSpace($sidecarHost)) { $sidecarHost = '127.0.0.1' }
    $sidecarPortText = Get-ProcessEnvironmentValue 'FEED_PASSPORT_ATPROTO_PORT'
    if ([string]::IsNullOrWhiteSpace($sidecarPortText)) { $sidecarPortText = '4310' }
    $sidecarPort = 0
    $privateKeyItem = if (Test-Path -LiteralPath $privateKeyPath -PathType Leaf) {
        Get-Item -LiteralPath $privateKeyPath
    }
    else {
        $null
    }
    if (-not (Test-HttpsOrigin $publicOrigin)) {
        Add-ReadinessCheck 'bluesky' 'restart-safe single-replica sidecar' 'invalid' (
            'The AT Protocol public origin must be an exact HTTPS origin without a path, query, or fragment.'
        )
    }
    elseif (-not [System.IO.Path]::IsPathRooted($storePath) -or $storePath -match '[\r\n]') {
        Add-ReadinessCheck 'bluesky' 'restart-safe single-replica sidecar' 'invalid' (
            'The encrypted sidecar store must use an absolute path; keep the real file outside the repository.'
        )
    }
    elseif (-not (Test-UrlSafeBase64Key $storeKey)) {
        Add-ReadinessCheck 'bluesky' 'restart-safe single-replica sidecar' 'invalid' (
            'The AT Protocol store key must decode to exactly 32 bytes. Its value was not printed.'
        )
    }
    elseif (-not [System.IO.Path]::IsPathRooted($privateKeyPath) -or $null -eq $privateKeyItem) {
        Add-ReadinessCheck 'bluesky' 'restart-safe single-replica sidecar' 'invalid' (
            'The confidential-client private key must be an existing regular file at an absolute path; keep it outside the repository.'
        )
    }
    elseif (($privateKeyItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        Add-ReadinessCheck 'bluesky' 'restart-safe single-replica sidecar' 'invalid' (
            'The confidential-client private key file cannot be a symbolic link or reparse point.'
        )
    }
    elseif ($privateKeyItem.Length -lt 32 -or $privateKeyItem.Length -gt 65536) {
        Add-ReadinessCheck 'bluesky' 'restart-safe single-replica sidecar' 'invalid' (
            'The confidential-client private key file must contain between 32 and 65536 bytes.'
        )
    }
    elseif ($privateKeyId -notmatch '^[A-Za-z0-9._-]{1,128}$') {
        Add-ReadinessCheck 'bluesky' 'restart-safe single-replica sidecar' 'invalid' 'The private key ID is malformed.'
    }
    elseif (-not [int]::TryParse($sidecarPortText, [ref]$sidecarPort) -or $sidecarPort -lt 1 -or $sidecarPort -gt 65535) {
        Add-ReadinessCheck 'bluesky' 'restart-safe single-replica sidecar' 'invalid' 'The private listener port must be between 1 and 65535.'
    }
    elseif ($sidecarHost -match '[\s/\\?#@]') {
        Add-ReadinessCheck 'bluesky' 'restart-safe single-replica sidecar' 'invalid' 'The private listener host is malformed.'
    }
    else {
        Add-ReadinessCheck 'bluesky' 'restart-safe single-replica sidecar' 'configured_unverified' (
            'The restart-safe single-process server configuration is locally well-shaped. No listener, HTTPS route, OAuth client, PDS, or dummy account was contacted.'
        )
    }
}
Add-ReadinessCheck 'bluesky' 'authorized live OAuth proof' 'action_required' (
    'A public HTTPS route and an explicitly authorized dummy Bluesky account are still required; local configuration alone is not live proof.'
)

$certificationDirectory = Get-ProcessEnvironmentValue 'FEED_PASSPORT_LIVE_CERTIFICATIONS_DIR'
$certificationKey = Get-ProcessEnvironmentValue 'FEED_PASSPORT_LIVE_CERTIFICATION_HMAC_KEY_B64'
$certificationRevision = Get-ProcessEnvironmentValue 'FEED_PASSPORT_CODE_REVISION'
$certificationDirectoryConfigured = Test-ConfiguredValue $certificationDirectory
$certificationKeyConfigured = Test-ConfiguredValue $certificationKey
$certificationRevisionConfigured = Test-ConfiguredValue $certificationRevision
if (
    -not $certificationDirectoryConfigured -and
    -not $certificationKeyConfigured -and
    -not $certificationRevisionConfigured
) {
    Add-ReadinessCheck 'conformance' 'signed live certifications' 'action_required' (
        'No certification directory/key is configured. This is the expected zero-account state; every unprefixed external adapter must remain Guided.'
    )
}
elseif (
    -not ($certificationDirectoryConfigured -and $certificationKeyConfigured -and $certificationRevisionConfigured)
) {
    Add-ReadinessCheck 'conformance' 'signed live certifications' 'invalid' (
        'The certification directory, independent 32-byte HMAC key, and exact deployed Git revision must be configured together.'
    )
}
elseif (-not (Test-UrlSafeBase64Key $certificationKey)) {
    Add-ReadinessCheck 'conformance' 'signed live certifications' 'invalid' (
        'The certification HMAC key must decode to exactly 32 bytes. Its value was not printed.'
    )
}
elseif ($certificationRevision -notmatch '^[0-9a-f]{40}$') {
    Add-ReadinessCheck 'conformance' 'signed live certifications' 'invalid' (
        'FEED_PASSPORT_CODE_REVISION must be the exact lowercase 40-character deployed Git revision.'
    )
}
elseif ($null -ne (Get-Command git -ErrorAction SilentlyContinue)) {
    $checkedOutRevision = (& git -c "safe.directory=$repoRoot" -C $repoRoot rev-parse HEAD 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or $checkedOutRevision -notmatch '^[0-9a-f]{40}$') {
        Add-ReadinessCheck 'conformance' 'signed live certifications' 'invalid' (
            'The checked-out Git revision could not be resolved for certification binding.'
        )
    }
    elseif ($certificationRevision -ne $checkedOutRevision) {
        Add-ReadinessCheck 'conformance' 'signed live certifications' 'invalid' (
            'FEED_PASSPORT_CODE_REVISION does not match the checked-out Git revision.'
        )
    }
    elseif ((@(& git -c "safe.directory=$repoRoot" -C $repoRoot status --porcelain --untracked-files=normal 2>$null)).Count -gt 0) {
        Add-ReadinessCheck 'conformance' 'signed live certifications' 'invalid' (
            'The checkout has tracked or untracked changes, so its running source is not represented exactly by FEED_PASSPORT_CODE_REVISION.'
        )
    }
    elseif (-not (Test-Path -LiteralPath $certificationDirectory -PathType Container)) {
        Add-ReadinessCheck 'conformance' 'signed live certifications' 'invalid' 'The configured certification directory does not exist.'
    }
    else {
        $certificationFiles = @(Get-ChildItem -LiteralPath $certificationDirectory -Filter '*.json' -File)
        Add-ReadinessCheck 'conformance' 'signed live certifications' 'configured_unverified' (
            "$($certificationFiles.Count) JSON receipt file(s) are present and the configured revision matches this checkout. Runtime signature, expiry, action, and provider-approval validation still decides promotion."
        )
    }
}
elseif (-not (Test-Path -LiteralPath $certificationDirectory -PathType Container)) {
    Add-ReadinessCheck 'conformance' 'signed live certifications' 'invalid' 'The configured certification directory does not exist.'
}
else {
    $certificationFiles = @(Get-ChildItem -LiteralPath $certificationDirectory -Filter '*.json' -File)
    Add-ReadinessCheck 'conformance' 'signed live certifications' 'configured_unverified' (
        "$($certificationFiles.Count) JSON receipt file(s) are present. Runtime signature, expiry, Git-revision, action, and provider-approval validation still decides promotion."
    )
}
Add-ReadinessCheck 'conformance' 'built-in owner-bound runtime factory' 'ready_local' (
    'The gated factory can restore an existing encrypted connection/vault and exact action subset without making a request during construction.'
)
$connectionDatabase = Get-ProcessEnvironmentValue 'FEED_PASSPORT_DB_PATH'
if (-not (Test-ConfiguredValue $connectionDatabase)) {
    Add-ReadinessCheck 'conformance' 'owner-bound connection database' 'action_required' (
        'Set FEED_PASSPORT_DB_PATH to the existing database created by the authenticated OAuth connection flow before conformance.'
    )
}
elseif (-not (Test-Path -LiteralPath $connectionDatabase -PathType Leaf)) {
    Add-ReadinessCheck 'conformance' 'owner-bound connection database' 'invalid' (
        'FEED_PASSPORT_DB_PATH is configured but does not name an existing file.'
    )
}
else {
    Add-ReadinessCheck 'conformance' 'owner-bound connection database' 'configured_unverified' (
        'The configured database file exists. This checker did not open it, resolve an owner, or decrypt a connection.'
    )
}

$awsPlanningVariables = @(
    'AWS_ACCOUNT_ID',
    'AWS_REGION',
    'BEDROCK_MODEL_ID',
    'BEDROCK_MODEL_ARN',
    'COGNITO_DOMAIN_PREFIX'
)
if (Test-AllConfigured $awsPlanningVariables) {
    Add-ReadinessCheck 'agentcore' 'local CDK planning inputs' 'configured_unverified' (
        'All non-secret planning inputs are present. No AWS identity, service, model, quota, credit, or billing check was made.'
    )
}
elseif (Test-AnyConfigured $awsPlanningVariables) {
    Add-ReadinessCheck 'agentcore' 'local CDK planning inputs' 'invalid' (
        'AgentCore plan inputs are partial. Use infra/agentcore/.env.example as the non-secret contract.'
    )
}
else {
    Add-ReadinessCheck 'agentcore' 'local CDK planning inputs' 'action_required' (
        'Choose the target account ID, Region, exact model ID/ARN, and unique Cognito domain prefix before a local synth plan. Local synth does not need an AWS credential or profile.'
    )
}

$awsProfile = Get-ProcessEnvironmentValue 'AWS_PROFILE'
$awsProfileConfigured = Test-ConfiguredValue $awsProfile
if ($null -eq (Get-Command aws -ErrorAction SilentlyContinue)) {
    Add-ReadinessCheck 'agentcore' 'AWS CLI and SSO profile' 'action_required' (
        'Install AWS CLI v2 and let the user complete IAM Identity Center/SSO authentication later. Builder ID is not an AWS CLI credential.'
    )
}
elseif (-not $awsProfileConfigured) {
    Add-ReadinessCheck 'agentcore' 'AWS CLI and SSO profile' 'action_required' (
        'AWS CLI is installed, but no dedicated AWS_PROFILE name is selected. The checker did not inspect other profiles or refresh SSO.'
    )
}
elseif ($awsProfile -notmatch '^[A-Za-z0-9+=,.@_-]{1,128}$') {
    Add-ReadinessCheck 'agentcore' 'AWS CLI and SSO profile' 'invalid' (
        'The dedicated AWS_PROFILE name is malformed. No profile file or SSO session was inspected.'
    )
}
else {
    Add-ReadinessCheck 'agentcore' 'AWS CLI and SSO profile' 'configured_unverified' (
        'AWS CLI and a dedicated profile name are present. The checker deliberately did not read profiles, contact STS, or refresh an SSO session.'
    )
}
Add-ReadinessCheck 'agentcore' 'AWS Builder ID' 'action_required' (
    'The entrant must create and add their own Builder ID in Devpost; it is separate from AWS account deployment access.'
)
Add-ReadinessCheck 'agentcore' 'deployment and invocation' 'zero_spend_blocked' (
    'The checked-in source and template validators can run without AWS. Packaging and full planning additionally require a clean exact-HEAD worktree and Docker; this checker did not assert those gates. SkipPackage proves only archive shape and a self-declared manifest during no-AWS planning, and Apply always rebuilds. Deployment, Runtime health, and Bedrock planning can incur charges and were not attempted.'
)

$report = [pscustomobject]@{
    format = 'feed-passport-external-readiness/v1'
    generated_at = [DateTimeOffset]::UtcNow.ToString('o')
    network_calls_made = 0
    secrets_printed = $false
    checks = @($checks)
}

if ($AsJson) {
    $report | ConvertTo-Json -Depth 5
}
else {
    Write-Output 'Feed Passport external readiness (local inspection only)'
    Write-Output 'No network call was made and no environment value was printed.'
    $checks | Format-Table -AutoSize -Wrap
    $counts = $checks | Group-Object status | Sort-Object Name
    Write-Output (($counts | ForEach-Object { "$($_.Name)=$($_.Count)" }) -join '; ')
}

if ($FailOnActionRequired -and @($checks | Where-Object {
    $_.status -in @('action_required', 'invalid', 'zero_spend_blocked')
}).Count -gt 0) {
    exit 2
}

exit 0

[CmdletBinding()]
param(
    [ValidateSet("Plan", "Apply")][string]$Mode = "Plan",
    [Parameter(Mandatory = $true)][ValidatePattern("^\d{12}$")][string]$AwsAccountId,
    [Parameter(Mandatory = $true)][ValidatePattern("^[a-z]{2}(-gov)?-[a-z0-9-]+-\d$")][string]$AwsRegion,
    [Parameter(Mandatory = $true)][string]$AwsProfile,
    [AllowEmptyString()][string]$ApplyAcknowledgement = "",
    [AllowEmptyString()][string]$BillingAcknowledgement = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
$infraRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$cdkCliPath = Join-Path $infraRoot "node_modules\aws-cdk\bin\cdk"
$target = "aws://$AwsAccountId/$AwsRegion"

Write-Host "Planned command: project-pinned cdk bootstrap $target --profile $AwsProfile --termination-protection"
Write-Host "This creates a shared CDKToolkit stack, including an asset bucket. Storage and AWS services can incur charges."
if ($Mode -eq "Plan") {
    Write-Host "Plan complete. No AWS API was called."
    return
}

Assert-ExactAcknowledgement -Actual $ApplyAcknowledgement -Expected "BOOTSTRAP FEED PASSPORT AWS" -Purpose "CDK bootstrap"
Assert-ExactAcknowledgement -Actual $BillingAcknowledgement -Expected "AWS CREDITS ARE NOT A HARD SPEND CAP" -Purpose "CDK bootstrap billing risk"
$null = Assert-AwsAccount -ExpectedAccountId $AwsAccountId -AwsRegion $AwsRegion -AwsProfile $AwsProfile
if (-not (Test-Path -LiteralPath $cdkCliPath -PathType Leaf)) {
    throw "The project-pinned AWS CDK CLI is unavailable. Install the locked AgentCore dependencies first."
}

$temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
)
$temporaryDirectory = [System.IO.Path]::GetFullPath(
    (Join-Path $temporaryRoot ("feed-passport-cdk-bootstrap-" + [guid]::NewGuid().ToString("N")))
)
if (-not $temporaryDirectory.StartsWith(
    $temporaryRoot + [System.IO.Path]::DirectorySeparatorChar,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "The isolated CDK bootstrap directory escaped the system temporary directory."
}
$null = New-Item -ItemType Directory -Path $temporaryDirectory
Push-Location $temporaryDirectory
try {
    # Bootstrap does not need the Feed Passport application assembly. Running
    # outside infraRoot prevents cdk.json from executing the app and requiring
    # deployment-only context or an already-built runtime artifact.
    & node $cdkCliPath bootstrap $target --profile $AwsProfile --termination-protection
    if ($LASTEXITCODE -ne 0) {
        throw "CDK bootstrap failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
    if (Test-Path -LiteralPath $temporaryDirectory -PathType Container) {
        Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force
    }
}
Write-Host "CDK bootstrap completed. The shared CDKToolkit stack is intentionally not removed by Feed Passport teardown."

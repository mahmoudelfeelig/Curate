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
$target = "aws://$AwsAccountId/$AwsRegion"

Write-Host "Planned command: npx cdk bootstrap $target --profile $AwsProfile --termination-protection"
Write-Host "This creates a shared CDKToolkit stack, including an asset bucket. Storage and AWS services can incur charges."
if ($Mode -eq "Plan") {
    Write-Host "Plan complete. No AWS API was called."
    return
}

Assert-ExactAcknowledgement -Actual $ApplyAcknowledgement -Expected "BOOTSTRAP FEED PASSPORT AWS" -Purpose "CDK bootstrap"
Assert-ExactAcknowledgement -Actual $BillingAcknowledgement -Expected "AWS CREDITS ARE NOT A HARD SPEND CAP" -Purpose "CDK bootstrap billing risk"
$null = Assert-AwsAccount -ExpectedAccountId $AwsAccountId -AwsRegion $AwsRegion -AwsProfile $AwsProfile

Push-Location $infraRoot
try {
    & npx cdk bootstrap $target --profile $AwsProfile --termination-protection
    if ($LASTEXITCODE -ne 0) {
        throw "CDK bootstrap failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
Write-Host "CDK bootstrap completed. The shared CDKToolkit stack is intentionally not removed by Feed Passport teardown."

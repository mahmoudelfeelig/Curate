[CmdletBinding()]
param(
    [ValidateSet("Template", "AwsReadOnly")][string]$Mode = "Template",
    [ValidatePattern("^\d{12}$")][string]$AwsAccountId,
    [ValidatePattern("^[a-z]{2}(-gov)?-[a-z0-9-]+-\d$")][string]$AwsRegion,
    [string]$AwsProfile,
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
$infraRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$manifestPath = Join-Path $infraRoot "resource-inventory.json"
$declared = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json -Depth 100

if ($Mode -eq "Template") {
    $templatePath = Join-Path $infraRoot "cdk.out\FeedPassportAgentCore.template.json"
    $templateTypes = @()
    if (Test-Path -LiteralPath $templatePath) {
        $template = Get-Content -LiteralPath $templatePath -Raw | ConvertFrom-Json -Depth 100
        $templateTypes = @($template.Resources.PSObject.Properties.Value.Type | Sort-Object -Unique)
    }
    $result = [ordered]@{
        mode = "local_template"
        aws_api_called = $false
        declared = $declared
        synthesized_resource_types = $templateTypes
        note = "This is a plan inventory, not proof that any AWS resource exists."
    }
}
else {
    foreach ($required in @("AwsAccountId", "AwsRegion", "AwsProfile")) {
        if ([string]::IsNullOrWhiteSpace((Get-Variable -Name $required -ValueOnly))) {
            throw "$required is mandatory in AwsReadOnly mode"
        }
    }
    $null = Assert-AwsAccount -ExpectedAccountId $AwsAccountId -AwsRegion $AwsRegion -AwsProfile $AwsProfile
    $stack = Get-StackDescription -StackName "FeedPassportAgentCore" -AwsRegion $AwsRegion -AwsProfile $AwsProfile
    $resources = Invoke-AwsJson -CommandArguments @(
        "cloudformation", "list-stack-resources",
        "--stack-name", "FeedPassportAgentCore",
        "--region", $AwsRegion,
        "--profile", $AwsProfile
    )
    $result = [ordered]@{
        mode = "aws_read_only"
        aws_api_called = $true
        account_id = $AwsAccountId
        region = $AwsRegion
        stack_status = [string]$stack.StackStatus
        outputs = Convert-StackOutputsToMap -Stack $stack
        resources = @($resources.StackResourceSummaries | ForEach-Object {
            [ordered]@{
                logical_id = [string]$_.LogicalResourceId
                physical_id = [string]$_.PhysicalResourceId
                type = [string]$_.ResourceType
                status = [string]$_.ResourceStatus
            }
        })
        declared = $declared
        note = "Read-only CloudFormation inventory; it did not invoke Runtime or Bedrock."
    }
}

$json = $result | ConvertTo-Json -Depth 100
if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
    $resolvedOutput = [System.IO.Path]::GetFullPath((Join-Path $infraRoot $OutputPath))
    $allowedPrefix = $infraRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolvedOutput.StartsWith($allowedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "OutputPath must stay inside $infraRoot"
    }
    $parent = Split-Path -Parent $resolvedOutput
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $json | Set-Content -LiteralPath $resolvedOutput -Encoding utf8NoBOM
    Write-Host "Wrote inventory to $resolvedOutput"
}
else {
    $json
}

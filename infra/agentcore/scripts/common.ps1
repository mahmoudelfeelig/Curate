Set-StrictMode -Version Latest

function Assert-ExactAcknowledgement {
    param(
        [AllowEmptyString()][string]$Actual,
        [Parameter(Mandatory = $true)][string]$Expected,
        [Parameter(Mandatory = $true)][string]$Purpose
    )

    if ($Actual -cne $Expected) {
        throw "$Purpose requires the exact acknowledgement: $Expected"
    }
}

function Assert-AwsCliAvailable {
    if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
        throw "AWS CLI v2 is required for this operation"
    }
}

function Invoke-AwsJson {
    param(
        [Parameter(Mandatory = $true)][string[]]$CommandArguments
    )

    Assert-AwsCliAvailable
    $raw = & aws @CommandArguments --output json --no-cli-pager
    if ($LASTEXITCODE -ne 0) {
        throw "AWS CLI failed with exit code ${LASTEXITCODE}: aws $($CommandArguments -join ' ')"
    }
    $text = ($raw -join [Environment]::NewLine).Trim()
    if ([string]::IsNullOrWhiteSpace($text)) {
        return $null
    }
    return $text | ConvertFrom-Json -Depth 100
}

function Assert-AwsAccount {
    param(
        [Parameter(Mandatory = $true)][ValidatePattern("^\d{12}$")][string]$ExpectedAccountId,
        [Parameter(Mandatory = $true)][string]$AwsRegion,
        [Parameter(Mandatory = $true)][string]$AwsProfile
    )

    $identity = Invoke-AwsJson -CommandArguments @(
        "sts", "get-caller-identity",
        "--region", $AwsRegion,
        "--profile", $AwsProfile
    )
    if ([string]$identity.Account -cne $ExpectedAccountId) {
        throw "AWS profile '$AwsProfile' resolves to account $($identity.Account), not $ExpectedAccountId"
    }
    return $identity
}

function Get-StackDescription {
    param(
        [Parameter(Mandatory = $true)][string]$StackName,
        [Parameter(Mandatory = $true)][string]$AwsRegion,
        [Parameter(Mandatory = $true)][string]$AwsProfile
    )

    $description = Invoke-AwsJson -CommandArguments @(
        "cloudformation", "describe-stacks",
        "--stack-name", $StackName,
        "--region", $AwsRegion,
        "--profile", $AwsProfile
    )
    if ($null -eq $description -or @($description.Stacks).Count -ne 1) {
        throw "Expected exactly one CloudFormation stack named '$StackName'"
    }
    return @($description.Stacks)[0]
}

function Convert-StackOutputsToMap {
    param(
        [Parameter(Mandatory = $true)]$Stack
    )

    $values = @{}
    foreach ($output in @($Stack.Outputs)) {
        $values[[string]$output.OutputKey] = [string]$output.OutputValue
    }
    return $values
}

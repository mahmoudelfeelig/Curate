[CmdletBinding()]
param(
    [ValidateSet("Plan", "Apply")][string]$Mode = "Plan",
    [Parameter(Mandatory = $true)][ValidatePattern("^\d{12}$")][string]$AwsAccountId,
    [Parameter(Mandatory = $true)][string]$AwsProfile,
    [ValidateRange(5, 5)][decimal]$LimitUsd = 5,
    [string]$NotificationEmail,
    [AllowEmptyString()][string]$ApplyAcknowledgement = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$budgetName = "FeedPassport-Five-Dollar-Guard"
Write-Host "Plan: create the monthly AWS Cost Budget '$budgetName' with a USD $LimitUsd limit."
Write-Host "Alerts fire at 50% actual, 80% forecast, and 100% actual spend."
Write-Host "AWS Budgets alerts are not a hard cap and cannot guarantee that charges stop at USD $LimitUsd."
if ($Mode -eq "Plan") {
    Write-Host "Plan complete. No AWS API was called and no budget was created."
    return
}

Assert-ExactAcknowledgement `
    -Actual $ApplyAcknowledgement `
    -Expected "CREATE FEED PASSPORT FIVE DOLLAR BUDGET" `
    -Purpose "Feed Passport AWS budget"
if ([string]::IsNullOrWhiteSpace($NotificationEmail)) {
    throw "NotificationEmail is mandatory in Apply mode"
}
try {
    $mailAddress = [System.Net.Mail.MailAddress]::new($NotificationEmail)
}
catch {
    throw "NotificationEmail must be a valid email address"
}
if ($mailAddress.Address -cne $NotificationEmail.Trim()) {
    throw "NotificationEmail must contain only one canonical email address"
}

$null = Assert-AwsAccount -ExpectedAccountId $AwsAccountId -AwsRegion "us-east-1" -AwsProfile $AwsProfile
$budgetList = Invoke-AwsJson -CommandArguments @(
    "budgets", "describe-budgets",
    "--account-id", $AwsAccountId,
    "--max-results", "100",
    "--profile", $AwsProfile
)
$matches = @($budgetList.Budgets | Where-Object { [string]$_.BudgetName -ceq $budgetName })
if ($matches.Count -gt 1) {
    throw "AWS returned more than one budget named '$budgetName'"
}
$existing = if ($matches.Count -eq 1) { $matches[0] } else { $null }
if ($null -ne $existing) {
    $amount = [decimal]$existing.BudgetLimit.Amount
    $unit = [string]$existing.BudgetLimit.Unit
    $timeUnit = [string]$existing.TimeUnit
    $type = [string]$existing.BudgetType
    if ($amount -ne $LimitUsd -or $unit -cne "USD" -or $timeUnit -cne "MONTHLY" -or $type -cne "COST") {
        throw "A budget named '$budgetName' already exists with a different boundary; inspect it manually"
    }
    Write-Host "The exact USD $LimitUsd monthly budget already exists. No duplicate was created."
    return
}

$temporaryDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("feed-passport-budget-" + [guid]::NewGuid().ToString("N"))
$null = New-Item -ItemType Directory -Path $temporaryDirectory
try {
    $budgetPath = Join-Path $temporaryDirectory "budget.json"
    $notificationsPath = Join-Path $temporaryDirectory "notifications.json"
    $budget = [ordered]@{
        BudgetName = $budgetName
        BudgetLimit = [ordered]@{ Amount = ([string]$LimitUsd); Unit = "USD" }
        TimeUnit = "MONTHLY"
        BudgetType = "COST"
        CostTypes = [ordered]@{
            IncludeTax = $true
            IncludeSubscription = $true
            UseBlended = $false
            IncludeRefund = $true
            IncludeCredit = $true
            IncludeUpfront = $true
            IncludeRecurring = $true
            IncludeOtherSubscription = $true
            IncludeSupport = $true
            IncludeDiscount = $true
            UseAmortized = $false
        }
    }
    $subscriber = @([ordered]@{ SubscriptionType = "EMAIL"; Address = $mailAddress.Address })
    $notifications = @(
        [ordered]@{
            Notification = [ordered]@{ NotificationType = "ACTUAL"; ComparisonOperator = "GREATER_THAN"; Threshold = 50; ThresholdType = "PERCENTAGE" }
            Subscribers = $subscriber
        },
        [ordered]@{
            Notification = [ordered]@{ NotificationType = "FORECASTED"; ComparisonOperator = "GREATER_THAN"; Threshold = 80; ThresholdType = "PERCENTAGE" }
            Subscribers = $subscriber
        },
        [ordered]@{
            Notification = [ordered]@{ NotificationType = "ACTUAL"; ComparisonOperator = "GREATER_THAN"; Threshold = 100; ThresholdType = "PERCENTAGE" }
            Subscribers = $subscriber
        }
    )
    $budget | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $budgetPath -Encoding utf8NoBOM
    $notifications | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $notificationsPath -Encoding utf8NoBOM
    & aws budgets create-budget `
        --account-id $AwsAccountId `
        --budget ("file://" + $budgetPath) `
        --notifications-with-subscribers ("file://" + $notificationsPath) `
        --profile $AwsProfile `
        --no-cli-pager
    if ($LASTEXITCODE -ne 0) {
        throw "AWS budget creation failed with exit code $LASTEXITCODE"
    }
}
finally {
    Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force
}

Write-Host "Created the exact USD $LimitUsd monthly budget with three email alerts. Confirm the subscription email if AWS requests it."

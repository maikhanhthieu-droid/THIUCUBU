param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("morning_broad", "afternoon_split", "eod", "test")]
    [string]$Mode,

    [string]$Repo = "maikhanhthieu-droid/THIUCUBU",
    [string]$Workflow = "scanner.yml",
    [string]$Branch = "main",
    [int]$LookbackMinutes = 20,
    [int]$MaxDispatchesInWindow = 2
)

$ErrorActionPreference = "Stop"

function Find-Gh {
    $candidates = @(
        (Join-Path $env:ProgramFiles "GitHub CLI\gh.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\GitHub CLI\gh.exe"),
        "gh"
    )
    foreach ($candidate in $candidates) {
        try {
            $cmd = Get-Command $candidate -ErrorAction Stop
            return $cmd.Source
        } catch {
            continue
        }
    }
    throw "GitHub CLI (gh) not found."
}

function Invoke-GhJson([string[]]$GhArgs) {
    $raw = & $script:Gh @GhArgs
    if ($LASTEXITCODE -ne 0) {
        throw "gh command failed: gh $($GhArgs -join ' ')"
    }
    if (-not $raw) {
        return $null
    }
    return $raw | ConvertFrom-Json
}

$script:Gh = Find-Gh
& $script:Gh auth status | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "gh is not authenticated. Run: gh auth login --web --scopes repo,workflow"
}

$now = Get-Date
$since = $now.AddMinutes(-1 * [Math]::Max(1, $LookbackMinutes))
$runs = Invoke-GhJson @(
    "run", "list",
    "-R", $Repo,
    "--workflow", $Workflow,
    "--limit", "30",
    "--json", "databaseId,event,status,conclusion,createdAt,displayTitle,url"
)

$recent = @(
    $runs |
        Where-Object {
            ([DateTimeOffset]::Parse($_.createdAt).LocalDateTime -ge $since) -and
            ($_.event -in @("workflow_dispatch", "schedule"))
        } |
        Sort-Object { [DateTimeOffset]::Parse($_.createdAt).UtcDateTime } -Descending
)

$active = @($recent | Where-Object { $_.status -in @("queued", "requested", "waiting", "pending", "in_progress") })
$success = @($recent | Where-Object { $_.status -eq "completed" -and $_.conclusion -eq "success" })
$dispatchCount = @($recent | Where-Object { $_.event -eq "workflow_dispatch" }).Count

if ($active.Count -gt 0) {
    $run = $active[0]
    Write-Host "OK: scanner already active for $Mode. run=$($run.databaseId) status=$($run.status) url=$($run.url)"
    exit 0
}

if ($success.Count -gt 0) {
    $run = $success[0]
    Write-Host "OK: scanner already completed successfully for $Mode. run=$($run.databaseId) url=$($run.url)"
    exit 0
}

if ($dispatchCount -ge $MaxDispatchesInWindow) {
    Write-Host "SKIP: $dispatchCount dispatch run(s) already created in the last $LookbackMinutes minutes. Avoiding loop."
    exit 0
}

Write-Host "WATCHDOG: no healthy scanner run for $Mode since $($since.ToString('yyyy-MM-dd HH:mm:ss')). Dispatching fallback..."
& $script:Gh workflow run $Workflow -R $Repo -f "mode=$Mode" --ref $Branch
if ($LASTEXITCODE -ne 0) {
    throw "Fallback dispatch failed for mode=$Mode"
}

Start-Sleep -Seconds 5
$latest = Invoke-GhJson @(
    "run", "list",
    "-R", $Repo,
    "--workflow", $Workflow,
    "--limit", "1",
    "--json", "databaseId,event,status,conclusion,createdAt,url"
)

if ($latest -and $latest.Count -gt 0) {
    $run = $latest[0]
    Write-Host "DISPATCHED: mode=$Mode run=$($run.databaseId) status=$($run.status) url=$($run.url)"
} else {
    Write-Host "DISPATCHED: mode=$Mode, but latest run was not visible yet."
}

# Installs (or reinstalls) the daily session-archive Task Scheduler task for
# the current user. Windows counterpart to install_launchd.sh. Safe to re-run.

$ErrorActionPreference = "Stop"

$ToolDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script = Join-Path $ToolDir "archive_sessions.ps1"
$TaskName = "claude-usage-session-archive"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`""

# Daily at 03:00, same schedule as the Mac launchd agent.
$trigger = New-ScheduledTaskTrigger -Daily -At 3:00AM

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Archives Claude Code session transcripts before Claude Code's ~30-day cleanup." | Out-Null

# Run once immediately, mirroring launchd's RunAtLoad.
Start-ScheduledTask -TaskName $TaskName

Write-Host "Installed and started task '$TaskName' (daily at 03:00)."
Get-ScheduledTask -TaskName $TaskName | Format-Table TaskName, State

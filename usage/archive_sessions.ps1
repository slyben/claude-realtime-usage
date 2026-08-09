# Windows counterpart to archive_sessions.sh (no rsync/bash on Windows).
# Copies Claude Code session logs to a permanent local archive before the
# ~30-day auto-cleanup in ~/.claude/projects deletes them. Uses robocopy
# with /XO (exclude older - skip files where the destination is already
# newer or equal) so it never overwrites with stale data and, since /MIR
# is NOT used, it never deletes anything at the destination either. Safe
# to run repeatedly.

$ErrorActionPreference = "Stop"

$Src = Join-Path $HOME ".claude\projects"
$Dest = Join-Path $HOME "Backups\claude-projects"
$Log = Join-Path $Dest "archive.log"

New-Item -ItemType Directory -Force -Path $Dest | Out-Null

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $Log -Value "=== $timestamp ==="

# /E: recurse incl. empty dirs  /XO: skip if dest is newer/equal  /NFL /NDL /NP: quiet
robocopy $Src $Dest /E /XO /NFL /NDL /NP /NJH /NJS *>> $Log
$rc = $LASTEXITCODE

# robocopy exit codes 0-7 are success (bitflags for copied/skipped/mismatched files); 8+ is failure.
if ($rc -ge 8) {
    Add-Content -Path $Log -Value "error: robocopy exited with code $rc"
    Write-Error "archive sync failed, robocopy exit code $rc"
    exit 1
}

$jsonlCount = (Get-ChildItem -Path $Dest -Filter "*.jsonl" -Recurse -File | Measure-Object).Count
Add-Content -Path $Log -Value "ok, $jsonlCount jsonl files in archive"

param(
    [Parameter(Mandatory = $true)][string]$BackupDir,
    [int]$KeepDays = 30
)

$ErrorActionPreference = "Stop"
if ($KeepDays -lt 1) {
    throw "KeepDays must be >= 1"
}
if (-not (Test-Path -LiteralPath $BackupDir)) {
    throw "Backup directory does not exist"
}

$cutoff = (Get-Date).AddDays(-1 * $KeepDays)
$removed = @()
Get-ChildItem -LiteralPath $BackupDir -Filter "muzi_llm-*.dump" -File | ForEach-Object {
    if ($_.LastWriteTime -lt $cutoff) {
        Remove-Item -LiteralPath $_.FullName -Force
        $removed += $_.Name
    }
}
$removed

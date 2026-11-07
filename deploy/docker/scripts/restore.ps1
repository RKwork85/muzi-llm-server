# 恢复会改写目标库中的对象。必须同时给出备份文件、目标库和 -Confirm YES。
param(
    [Parameter(Mandatory = $true)][string]$BackupFile,
    [Parameter(Mandatory = $true)][string]$TargetDatabase,
    [Parameter(Mandatory = $true)][string]$Confirm
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\common.ps1"

if ($Confirm -ne "YES") {
    throw "Refused. Re-run with -Confirm YES after checking the target database."
}
if ($TargetDatabase -notmatch "^[A-Za-z_][A-Za-z0-9_]{0,62}$") {
    throw "Target database name is invalid"
}

$source = Resolve-Path -LiteralPath $BackupFile
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Backup file does not exist"
}

$dockerDir = Get-MuziDockerDir
$backupDir = Join-Path $dockerDir "backups"
$envFile = Join-Path $dockerDir ".env"
Import-MuziDotEnv $envFile
if (-not $env:POSTGRES_USER) {
    throw "POSTGRES_USER is missing"
}

$remote = "/tmp/muzi-restore.dump"
Write-MuziBackupLog $backupDir "RESTORE start target=$TargetDatabase file=$($source.Path)"

try {
    & docker exec muzi-postgres rm -f $remote
    & docker cp $source.Path "muzi-postgres:${remote}"
    if ($LASTEXITCODE -ne 0) { throw "docker cp failed" }
    & docker exec muzi-postgres pg_restore -U $env:POSTGRES_USER -d $TargetDatabase --clean --if-exists --no-owner --no-privileges --exit-on-error $remote
    if ($LASTEXITCODE -ne 0) { throw "pg_restore failed with exit code $LASTEXITCODE" }
    Write-MuziBackupLog $backupDir "RESTORE OK target=$TargetDatabase"
}
catch {
    Write-MuziBackupLog $backupDir "RESTORE ERROR $($_.Exception.Message)"
    throw
}
finally {
    & docker exec muzi-postgres rm -f $remote | Out-Null
}

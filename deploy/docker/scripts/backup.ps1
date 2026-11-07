# 每天由任务计划程序调用。失败时写日志并返回非零退出码。
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\common.ps1"

$dockerDir = Get-MuziDockerDir
$backupDir = Join-Path $dockerDir "backups"
$envFile = Join-Path $dockerDir ".env"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
Import-MuziDotEnv $envFile

if (-not $env:POSTGRES_DB -or -not $env:POSTGRES_USER) {
    Write-MuziBackupLog $backupDir "ERROR missing POSTGRES_DB or POSTGRES_USER"
    throw "POSTGRES_DB or POSTGRES_USER is missing"
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$fileName = "muzi_llm-$stamp.dump"
$destination = Join-Path $backupDir $fileName
$remote = "/tmp/$fileName"

if (Test-Path -LiteralPath $destination) {
    Write-MuziBackupLog $backupDir "ERROR backup file already exists name=$fileName"
    throw "Backup file already exists"
}

function Invoke-CheckedDocker {
    param([Parameter(Mandatory = $true)][string[]]$Command)
    & docker @Command
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Command[0]) failed with exit code $LASTEXITCODE"
    }
}

try {
    Invoke-CheckedDocker -Command @("inspect", "muzi-postgres") | Out-Null
    Invoke-CheckedDocker -Command @(
        "exec", "muzi-postgres", "pg_dump",
        "-U", $env:POSTGRES_USER,
        "-d", $env:POSTGRES_DB,
        "--format=custom",
        "--file=$remote"
    )
    Invoke-CheckedDocker -Command @("cp", "muzi-postgres:${remote}", $destination)
    if (-not (Test-Path -LiteralPath $destination) -or (Get-Item -LiteralPath $destination).Length -le 0) {
        throw "Backup file is missing or empty"
    }
    & "$PSScriptRoot\retention.ps1" -BackupDir $backupDir -KeepDays 30 | Out-Null
    $size = (Get-Item -LiteralPath $destination).Length
    Write-MuziBackupLog $backupDir "OK file=$fileName bytes=$size"
    Write-Output $destination
}
catch {
    Write-MuziBackupLog $backupDir "ERROR $($_.Exception.Message)"
    if (Test-Path -LiteralPath $destination) {
        Remove-Item -LiteralPath $destination -Force
    }
    throw
}
finally {
    & docker exec muzi-postgres rm -f $remote | Out-Null
}

$ErrorActionPreference = "Stop"

function Get-MuziDockerDir {
    return (Split-Path -Parent $PSScriptRoot)
}

function Import-MuziDotEnv {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Missing env file: $Path"
    }
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        $trim = $line.Trim()
        if ($trim.Length -eq 0 -or $trim.StartsWith("#")) { continue }
        $parts = $trim.Split("=", 2)
        if ($parts.Length -ne 2) { continue }
        $name = $parts[0].Trim()
        if ($name -notmatch "^[A-Za-z_][A-Za-z0-9_]*$") { continue }
        Set-Item -Path "Env:$name" -Value $parts[1]
    }
}

function Write-MuziBackupLog {
    param(
        [Parameter(Mandatory = $true)][string]$BackupDir,
        [Parameter(Mandatory = $true)][string]$Message
    )
    $log = Join-Path $BackupDir "backup.log"
    $stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"
    $safe = $Message
    if ($env:POSTGRES_PASSWORD) { $safe = $safe.Replace($env:POSTGRES_PASSWORD, "***") }
    if ($env:REDIS_PASSWORD) { $safe = $safe.Replace($env:REDIS_PASSWORD, "***") }
    $encoding = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::AppendAllText($log, "$stamp $safe`r`n", $encoding)
}

# 生成 Docker 与服务端 .env 中的数据库密码。已存在的密码文件不会被覆盖。
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\common.ps1"

$dockerDir = Get-MuziDockerDir
$serverRoot = Split-Path -Parent (Split-Path -Parent $dockerDir)
$dockerEnv = Join-Path $dockerDir ".env"
$serverEnv = Join-Path $serverRoot ".env"
$utf8 = New-Object System.Text.UTF8Encoding $false

function New-MuziSecret {
    $bytes = New-Object byte[] 24
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    return ([Convert]::ToBase64String($bytes)).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

if (-not (Test-Path -LiteralPath $dockerEnv)) {
    $postgresPassword = New-MuziSecret
    $redisPassword = New-MuziSecret
    $content = @"
POSTGRES_DB=muzi_llm
POSTGRES_USER=muzi_admin
POSTGRES_PASSWORD=$postgresPassword
POSTGRES_HOST_PORT=5432
REDIS_PASSWORD=$redisPassword
REDIS_HOST_PORT=6379
TZ=Asia/Shanghai
"@
    [System.IO.File]::WriteAllText($dockerEnv, $content.Replace("`r`n", "`n") + "`n", $utf8)
    Write-Output "wrote deploy/docker/.env"
    Remove-Variable postgresPassword, redisPassword
}
else {
    Write-Output "deploy/docker/.env already exists"
}

if (-not (Test-Path -LiteralPath $serverEnv)) {
    throw "Server .env does not exist: $serverEnv"
}

$marker = "muzi-llm database infrastructure"
$serverText = [System.IO.File]::ReadAllText($serverEnv)
if ($serverText.Contains($marker)) {
    Write-Output "server .env already has database settings"
    exit 0
}

Import-MuziDotEnv $dockerEnv
if (-not $env:POSTGRES_PASSWORD -or -not $env:REDIS_PASSWORD) {
    throw "Database passwords are missing from deploy/docker/.env"
}

$block = @"

# $marker
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=$($env:POSTGRES_DB)
POSTGRES_USER=$($env:POSTGRES_USER)
POSTGRES_PASSWORD=$($env:POSTGRES_PASSWORD)
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=$($env:REDIS_PASSWORD)
DATABASE_STARTUP_REQUIRED=true
"@
[System.IO.File]::AppendAllText($serverEnv, $block.Replace("`r`n", "`n") + "`n", $utf8)
Write-Output "updated server .env"

$ErrorActionPreference = "Stop"
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $base
$pidFile = Join-Path $base "server.pid"
$logFile = Join-Path $base "server.log"
$errFile = Join-Path $base "server.log.err"
$python = Join-Path $base ".venv\Scripts\python.exe"
$envFile = Join-Path $base ".env"
$startLog = Join-Path $base "start_service.log"

function Add-StartLog($message) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $startLog -Value "[$stamp] $message" -Encoding UTF8
}

if (-not (Test-Path -LiteralPath $envFile)) { Add-StartLog ".env not found"; exit 1 }
if (-not (Test-Path -LiteralPath $python)) { Add-StartLog "python venv not found"; exit 1 }

$config = @{}
Get-Content -LiteralPath $envFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith('#') -or -not $line.Contains('=')) { return }
    $name, $value = $line.Split('=', 2)
    $config[$name] = $value
}

$missing = @()
if (-not $config.ContainsKey('QA_ADMIN_PASSWORD') -or [string]::IsNullOrWhiteSpace([string]$config.QA_ADMIN_PASSWORD)) { $missing += 'QA_ADMIN_PASSWORD' }
if (-not $config.ContainsKey('QA_SECRET_KEY') -or [string]::IsNullOrWhiteSpace([string]$config.QA_SECRET_KEY)) { $missing += 'QA_SECRET_KEY' }
if ($missing.Count -gt 0) {
    Add-StartLog ("Missing required configuration: " + ($missing -join ', '))
    exit 1
}

$port = if ($config.ContainsKey('QA_PORT') -and $config.QA_PORT) { $config.QA_PORT } else { '5000' }
$dataDir = if ($config.ContainsKey('QA_DATA_DIR') -and $config.QA_DATA_DIR) { $config.QA_DATA_DIR } else { Join-Path $base 'data' }
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

$alreadyRunning = $false
if (Test-Path -LiteralPath $pidFile) {
    $oldPid = (Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($oldPid -match '^\d+$') {
        $existing = Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue
        if ($existing) { $alreadyRunning = $true }
    }
}

if (-not $alreadyRunning) {
    $env:QA_PORT = $port
    $env:QA_ADMIN_USERNAME = if ($config.ContainsKey('QA_ADMIN_USERNAME')) { $config.QA_ADMIN_USERNAME } else { 'shuxing666' }
    $env:QA_ADMIN_PASSWORD = if ($config.ContainsKey('QA_ADMIN_PASSWORD')) { $config.QA_ADMIN_PASSWORD } else { '' }
    $env:QA_ACCESS_PASSWORD = if ($config.ContainsKey('QA_ACCESS_PASSWORD') -and $config.QA_ACCESS_PASSWORD) { $config.QA_ACCESS_PASSWORD } else { $env:QA_ADMIN_PASSWORD }
    $env:QA_SECRET_KEY = if ($config.ContainsKey('QA_SECRET_KEY')) { $config.QA_SECRET_KEY } else { '' }
    $env:QA_ENV = 'production'
    $env:QA_DATA_DIR = $dataDir

    $p = Start-Process -FilePath $python `
        -ArgumentList '-m','waitress',("--listen=0.0.0.0:$port"),'wsgi:app' `
        -WorkingDirectory $base `
        -RedirectStandardOutput $logFile `
        -RedirectStandardError $errFile `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -LiteralPath $pidFile -Value $p.Id -Encoding ASCII
    Add-StartLog "Started QA service PID $($p.Id) on port $port"
} else {
    Add-StartLog "QA service already running"
}

$cfService = Get-Service -Name 'Cloudflared' -ErrorAction SilentlyContinue
if ($cfService) {
    if ($cfService.Status -ne 'Running') {
        Start-Service -Name 'Cloudflared' -ErrorAction SilentlyContinue
        Add-StartLog "Started Cloudflared service"
    } else {
        Add-StartLog "Cloudflared service already running"
    }
}
exit 0

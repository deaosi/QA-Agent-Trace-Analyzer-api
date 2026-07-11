$ErrorActionPreference = "SilentlyContinue"
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$serverPidFile = Join-Path $base "server.pid"
$tunnelPidFile = Join-Path $base "cloudflared.pid"
$envFile = Join-Path $base ".env"
$stopLog = Join-Path $base "stop_service.log"

function Add-StopLog($message) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $stopLog -Value "[$stamp] $message" -Encoding UTF8
}

function Read-EnvValue($name, $defaultValue) {
    if (-not (Test-Path -LiteralPath $envFile)) { return $defaultValue }
    foreach ($line in Get-Content -LiteralPath $envFile) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) { continue }
        $key, $value = $trimmed.Split('=', 2)
        if ($key -eq $name) { return $value }
    }
    return $defaultValue
}

function Add-PidFromFile($set, $path) {
    if (-not (Test-Path -LiteralPath $path)) { return }
    $raw = (Get-Content -LiteralPath $path -ErrorAction SilentlyContinue | Select-Object -First 1)
    $pidValue = 0
    if ([int]::TryParse(($raw -join '').Trim(), [ref]$pidValue)) {
        [void]$set.Add($pidValue)
    }
}

function Add-PidsListeningOnPort($set, $port) {
    $lines = netstat -ano -p tcp 2>$null
    foreach ($line in $lines) {
        $parts = $line.Trim() -split '\s+'
        if ($parts.Count -lt 5) { continue }
        $localAddress = $parts[1]
        $state = $parts[3]
        $pidText = $parts[-1]
        if ($state -ne 'LISTENING') { continue }
        if ($localAddress -notmatch (':' + [regex]::Escape([string]$port) + '$')) { continue }
        $pidValue = 0
        if ([int]::TryParse($pidText, [ref]$pidValue)) {
            [void]$set.Add($pidValue)
        }
    }
}

function Stop-PidSet($set, $label) {
    foreach ($targetPid in @($set)) {
        if (-not $targetPid) { continue }
        $proc = Get-Process -Id ([int]$targetPid) -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        try {
            & taskkill.exe /PID $targetPid /T /F | Out-Null
            Add-StopLog "Stopped $label PID $targetPid"
        } catch {
            Stop-Process -Id ([int]$targetPid) -Force -ErrorAction SilentlyContinue
            Add-StopLog "Stopped $label PID $targetPid with Stop-Process"
        }
    }
}

Add-StopLog "Stop requested"

$cfService = Get-Service -Name 'Cloudflared' -ErrorAction SilentlyContinue
if ($cfService -and $cfService.Status -ne 'Stopped') {
    Stop-Service -Name 'Cloudflared' -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 800
    Add-StopLog "Stopped Cloudflared Windows service"
}

$tunnelPids = New-Object 'System.Collections.Generic.HashSet[int]'
Add-PidFromFile $tunnelPids $tunnelPidFile
Add-PidsListeningOnPort $tunnelPids 9090

$cloudflaredProcesses = Get-CimInstance Win32_Process -Filter "name = 'cloudflared.exe'" -ErrorAction SilentlyContinue
foreach ($p in $cloudflaredProcesses) {
    $cmd = [string]$p.CommandLine
    if ($cmd -match ' tunnel ' -or $cmd -match 'tunnel run' -or $cmd -match '--token' -or $cmd -match '127\.0\.0\.1:5000' -or $cmd -match 'localhost:5000') {
        [void]$tunnelPids.Add([int]$p.ProcessId)
    }
}
Stop-PidSet $tunnelPids "cloudflared tunnel"
Remove-Item -LiteralPath $tunnelPidFile -Force -ErrorAction SilentlyContinue

$port = Read-EnvValue "QA_PORT" "5000"
$servicePids = New-Object 'System.Collections.Generic.HashSet[int]'
Add-PidFromFile $servicePids $serverPidFile
Add-PidsListeningOnPort $servicePids $port
Stop-PidSet $servicePids "QA service"
Remove-Item -LiteralPath $serverPidFile -Force -ErrorAction SilentlyContinue

Start-Sleep -Milliseconds 500
Add-StopLog "Stop finished"
exit 0

param([int]$Port = 41888)

$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$pythonPath = Join-Path $projectDir '.venv\Scripts\python.exe'
$pidPath = Join-Path $projectDir 'api_server.pid'
$stdoutPath = Join-Path $projectDir 'api_server.log'
$stderrPath = Join-Path $projectDir 'api_server.error.log'
$baseUrl = "http://127.0.0.1:$Port"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Python environment is missing. Create .venv and install requirements_for_api.txt first.'
}

$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($listener) {
    try {
        $health = Invoke-RestMethod "$baseUrl/health" -TimeoutSec 3
        if ($health.status -eq 'ok' -and (Test-Path -LiteralPath $pidPath)) {
            $savedProcessId = [int](Get-Content -LiteralPath $pidPath -Raw).Trim()
            $savedProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $savedProcessId"
            $childProcesses = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $savedProcessId")
            $ownedIds = @($savedProcessId) + @($childProcesses.ProcessId)
            if ($savedProcess.ExecutablePath -eq $pythonPath -and ($listener.OwningProcess | Where-Object { $_ -in $ownedIds })) {
                Write-Output "Already running: $baseUrl"
                exit 0
            }
        }
    } catch {}
    throw "Port $Port is already in use."
}

$env:PYTHONUTF8 = '1'
$env:OW_SERVER_HOST = '127.0.0.1'
$env:OW_SERVER_PORT = [string]$Port
$arguments = @('-m', 'uvicorn', 'api_server.main:app', '--host', '127.0.0.1', '--port', [string]$Port, '--workers', '1', '--loop', 'asyncio', '--timeout-keep-alive', '30')
if (Test-Path -LiteralPath (Join-Path $projectDir '.env')) {
    $arguments += @('--env-file', '.env')
}
$serverProcess = Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $projectDir -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
Set-Content -LiteralPath $pidPath -Value $serverProcess.Id -Encoding ascii

for ($attempt = 0; $attempt -lt 90; $attempt++) {
    $serverProcess.Refresh()
    if ($serverProcess.HasExited) {
        Get-Content -LiteralPath $stderrPath -Tail 50
        throw 'The server exited during startup.'
    }
    try {
        $ready = Invoke-RestMethod "$baseUrl/ready" -TimeoutSec 2
        if ($ready.ready) {
            Write-Output "Ready: $baseUrl"
            Write-Output "Docs: $baseUrl/docs"
            Write-Output "Launcher PID: $($serverProcess.Id)"
            exit 0
        }
    } catch {}
    Start-Sleep -Seconds 1
}
throw "Startup is still pending. See $stderrPath."

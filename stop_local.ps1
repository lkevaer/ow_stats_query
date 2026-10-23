$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$pidPath = Join-Path $projectDir 'api_server.pid'
if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Output 'No saved server PID.'
    exit 0
}
$serverProcessId = [int](Get-Content -LiteralPath $pidPath -Raw).Trim()
$serverProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $serverProcessId"
if ($serverProcess) {
    $expectedPython = Join-Path $projectDir '.venv\Scripts\python.exe'
    if ($serverProcess.ExecutablePath -ne $expectedPython -or $serverProcess.CommandLine -notmatch 'uvicorn\s+api_server\.main:app') {
        throw 'Saved PID belongs to a different process. Nothing was stopped.'
    }
    & "$env:SystemRoot\System32\taskkill.exe" /PID $serverProcessId /T /F
    if ($LASTEXITCODE -ne 0) { throw 'Failed to stop the server process tree.' }
    Write-Output "Stopped server process tree $serverProcessId."
} else {
    Write-Output 'Server is already stopped.'
}
Remove-Item -LiteralPath $pidPath

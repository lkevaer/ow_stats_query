$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$envPath = Join-Path $projectDir '.env'
$roleText = (Read-Host 'Your NetEase Dashen role_id (digits)').Trim()
$roleNumber = 0L
if (-not [long]::TryParse($roleText, [ref]$roleNumber) -or $roleNumber -le 0) {
    throw 'role_id must be a positive integer.'
}
$secureToken = Read-Host 'Your Dashen report token (hidden input)' -AsSecureString
$tokenText = ([System.Net.NetworkCredential]::new('', $secureToken)).Password.Trim()
if (-not $tokenText -or $tokenText -match "[\r\n']") {
    throw 'Token is empty or contains unsupported line breaks/quotes.'
}
$lines = @()
if (Test-Path -LiteralPath $envPath) {
    $lines = @(Get-Content -LiteralPath $envPath -Encoding UTF8 | Where-Object { $_ -notmatch '^\s*OW_DASHEN_(ROLE_ID|TOKEN)\s*=' })
}
$lines += "OW_DASHEN_ROLE_ID=$roleNumber"
$lines += "OW_DASHEN_TOKEN='$tokenText'"
[System.IO.File]::WriteAllLines($envPath, [string[]]$lines, [System.Text.UTF8Encoding]::new($false))
$tokenText = $null
$secureToken.Dispose()
Write-Output 'Credentials saved locally. Restarting the local test server...'
& (Join-Path $projectDir 'stop_local.ps1')
& (Join-Path $projectDir 'start_local.ps1')

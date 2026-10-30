$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONPATH = Join-Path $root 'src'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $python = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
}
if (-not (Test-Path -LiteralPath $python)) {
    $python = (Get-Command python.exe -ErrorAction Stop).Source
}

$lockPath = Join-Path $root 'data\bot.lock'
$dataDir = Join-Path $root 'data'

while ($true) {
    $alive = $false
    if (Test-Path -LiteralPath $lockPath) {
        $pidText = (Get-Content -LiteralPath $lockPath -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($pidText -and ($pidText.Trim() -match '^\d+$')) {
            $lockProcess = Get-Process -Id ([int]$pidText.Trim()) -ErrorAction SilentlyContinue
            if ($lockProcess) { $alive = $true }
        }
    }
    if (-not $alive) {
        Get-ChildItem -LiteralPath $dataDir -Filter 'launcher-*.log' -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } |
            Remove-Item -Force -ErrorAction SilentlyContinue
        $log = Join-Path $dataDir ('launcher-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
        & $python -m iphone_monitor.main *> $log
    }
    Start-Sleep -Seconds 15
}

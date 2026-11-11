# Lance ClipForge : build frontend si absent, serveur, puis navigateur quand le serveur repond.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path "$root\frontend\dist\index.html")) {
    Write-Host "Build frontend..." -ForegroundColor Yellow
    Push-Location "$root\frontend"
    bun install
    bun run build
    Pop-Location
}

$server = Start-Process -FilePath "uv" `
    -ArgumentList "run", "uvicorn", "clipforge.main:app", "--host", "127.0.0.1", "--port", "8765" `
    -WorkingDirectory "$root\backend" -NoNewWindow -PassThru

$ready = $false
for ($i = 0; $i -lt 20; $i++) {
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:8765/api/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
        $ready = $true
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}

if ($ready) {
    Start-Process "http://127.0.0.1:8765"
} else {
    Write-Host "Le serveur ne repond pas apres 20 s : navigateur non ouvert." -ForegroundColor Red
}

Wait-Process -Id $server.Id

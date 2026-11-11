# Construit l'installeur MSI de ClipForge.
#
# Pourquoi ce script plutot que `cargo tauri build` seul : sur un Windows ou le
# moteur VBScript n'est pas installe, la validation ICE de WiX echoue avec le
# code 2738 et Tauri n'affiche que « failed to run light.exe ». Le MSI lui-meme
# est deja correct a ce stade ; seule la validation, facultative, echoue. On
# relink alors avec -sval, qui la desactive.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$msiDir = Join-Path $root "src-tauri\target\release\bundle\msi"
# Version lue dans la config, jamais ecrite en dur : sinon un bump de version
# fait chercher un MSI inexistant, et le relink en produit un au nom perime.
$conf = Get-Content (Join-Path $root "src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json
$msi = Join-Path $msiDir ("ClipForge_{0}_x64_en-US.msi" -f $conf.version)

$debut = Get-Date
Push-Location (Join-Path $root "src-tauri")
try { cargo tauri build } catch { Write-Host "cargo tauri build a echoue, on tente le relink" -ForegroundColor Yellow }
Pop-Location

# Un MSI d'un build precedent au meme chemin ne prouve rien : seul un fichier
# ecrit depuis $debut compte, sinon un echec de light.exe passe pour un succes.
if ((Test-Path $msi) -and (Get-Item $msi).LastWriteTime -ge $debut) {
    Write-Host "MSI produit directement par cargo tauri build." -ForegroundColor Green
} else {
    $wixDir = Join-Path $root "src-tauri\target\release\wix\x64"
    $wixobj = Join-Path $wixDir "main.wixobj"
    if (-not (Test-Path $wixobj)) { throw "main.wixobj absent : candle n'a pas tourne, l'echec est ailleurs." }

    $light = Get-ChildItem "$env:LOCALAPPDATA\tauri" -Recurse -Filter "light.exe" -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
    if (-not $light) { throw "light.exe introuvable sous $env:LOCALAPPDATA\tauri" }

    Write-Host "Relink sans validation ICE (VBScript absent du systeme)..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Path $msiDir -Force | Out-Null
    & $light -ext WixUIExtension -ext WixUtilExtension -cultures:en-US `
        -loc (Join-Path $wixDir "locale.wxl") -sval -o $msi $wixobj
    if ($LASTEXITCODE -ne 0) { throw "light.exe a echoue meme avec -sval (code $LASTEXITCODE)" }
}

$f = Get-Item $msi
Write-Host ("MSI : {0} ({1} Mo)" -f $f.FullName, [math]::Round($f.Length / 1MB, 2)) -ForegroundColor Green

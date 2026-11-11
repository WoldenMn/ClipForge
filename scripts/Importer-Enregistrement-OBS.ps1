<#
.SYNOPSIS
    Envoie le dernier enregistrement OBS à ClipForge et lance l'analyse qui en tire des clips verticaux sous-titrés.
.DESCRIPTION
    Prend le fichier le plus récent du dossier d'enregistrement d'OBS (les clips du tampon de relecture, préfixe
    « Clip », sont écartés sauf -Fichier), réveille le moteur de ClipForge s'il dort, inscrit le fichier puis lance
    l'analyse. Une VOD déjà inscrite pour ce fichier est reprise, jamais doublée. Refuse un fichier encore écrit par OBS.
    Les clips de cette VOD sont cadrés par le profil de scène « WOLDENM OBS <hauteur>p », tiré de la scène « Jeux »
    d'OBS et créé s'il manque ; un profil déjà présent n'est jamais réécrit, et le réglage global de ClipForge reste
    celui que tu as choisi.
.PARAMETER Dossier
    Dossier d'enregistrement d'OBS (défaut : D:\WOLDENM\Enregistrements).
.PARAMETER Fichier
    Un fichier précis à envoyer à la place du plus récent.
.PARAMETER SansAnalyse
    Inscrit le fichier sans lancer l'analyse.
.PARAMETER Moteur
    Adresse du moteur de ClipForge (défaut : http://localhost:8765).
.PARAMETER ProfilScene
    Nom d'un profil de scène à utiliser à la place de « WOLDENM OBS <hauteur>p ». Créé d'après la scène « Jeux »
    s'il n'existe pas.
.NOTES
    Titre      : Envoyer le dernier live à ClipForge
    Modifie    : une VOD de plus dans ClipForge ; le profil de scène « WOLDENM OBS <hauteur>p » s'il manque, et alors le profil qui était seul devient le « Profil des clips automatiques » ; l'enregistrement OBS n'est ni déplacé ni modifié
    Reversible : oui, supprimer la VOD dans ClipForge (le fichier source reste)
    Duree      : moins d'une minute pour l'envoi ; l'analyse continue dans ClipForge, environ un huitième de la durée du live
    Risque     : modification
#>
[CmdletBinding()]
param(
    [string]$Dossier = 'D:\WOLDENM\Enregistrements',
    [string]$Fichier,
    [switch]$SansAnalyse,
    [string]$Moteur = 'http://localhost:8765',
    [string]$ProfilScene
)

$ErrorActionPreference = 'Stop'
$Host.UI.RawUI.WindowTitle = 'Envoyer le dernier live à ClipForge'

function Test-Moteur {
    try { Invoke-RestMethod -Uri "$Moteur/api/health" -TimeoutSec 3 | Out-Null; return $true } catch { return $false }
}

function Invoke-Moteur([string]$Methode, [string]$Chemin, $Corps) {
    $octets = [Text.Encoding]::UTF8.GetBytes(($Corps | ConvertTo-Json -Compress))
    Invoke-RestMethod -Method $Methode -Uri "$Moteur$Chemin" -ContentType 'application/json; charset=utf-8' -Body $octets -TimeoutSec 30
}

function Pair([double]$Valeur) {
    $n = [int][Math]::Round($Valeur)
    $n - ($n % 2)
}

if ($Fichier) {
    $cible = Get-Item -LiteralPath $Fichier
} else {
    if (-not (Test-Path -LiteralPath $Dossier)) { Write-Host "Dossier introuvable : $Dossier" -ForegroundColor Red; exit 2 }
    $cible = Get-ChildItem -LiteralPath $Dossier -File |
        Where-Object { $_.Extension -in '.mp4', '.mkv', '.mov' -and $_.Name -notlike 'Clip *' } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $cible) { Write-Host "Aucun enregistrement dans $Dossier." -ForegroundColor Yellow; exit 2 }
}

$age = (Get-Date) - $cible.LastWriteTime
if ((Get-Process obs64 -ErrorAction SilentlyContinue) -and $age.TotalSeconds -lt 20) {
    Write-Host "OBS écrit encore $($cible.Name) : arrête l'enregistrement, puis relance." -ForegroundColor Yellow
    exit 2
}
Write-Host ("Enregistrement : {0} ({1:N1} Go, {2:dd/MM HH:mm})" -f $cible.Name, ($cible.Length / 1GB), $cible.LastWriteTime)

if (-not (Test-Moteur)) {
    $appli = 'C:\Program Files\ClipForge\clipforge.exe'
    if (-not (Test-Path -LiteralPath $appli)) { Write-Host "ClipForge n'est ni lancé ni installé ($appli)." -ForegroundColor Red; exit 1 }
    Write-Host 'Réveil de ClipForge…'
    Start-Process -FilePath $appli | Out-Null
    $limite = (Get-Date).AddSeconds(180)
    while (-not (Test-Moteur)) {
        if ((Get-Date) -gt $limite) { Write-Host 'ClipForge ne répond pas après 3 minutes.' -ForegroundColor Red; exit 1 }
        Start-Sleep -Seconds 2
    }
}

$chemin = $cible.FullName
$existante = $null
foreach ($v in (Invoke-RestMethod -Uri "$Moteur/api/vods" -TimeoutSec 15)) {
    if ($v.source_path -eq $chemin) { $existante = $v; break }
}
if ($existante) {
    $vod = $existante
    Write-Host "Déjà inscrit dans ClipForge (VOD $($vod.id)) : je le reprends."
} else {
    $vod = Invoke-Moteur Post '/api/vods/import-local' @{ path = $chemin }
    Write-Host "Inscrit dans ClipForge : VOD $($vod.id)."
}

if ($SansAnalyse) { Write-Host 'Analyse non lancée (-SansAnalyse).' -ForegroundColor Green; exit 0 }

if ($vod.PSObject.Properties.Name -notcontains 'profile_id') {
    Write-Host 'Ce ClipForge ne sait pas encore cadrer une VOD à part : installe sa dernière version, puis relance.' -ForegroundColor Red
    exit 1
}

if (-not (Get-Command ffprobe -ErrorAction SilentlyContinue)) { Write-Host 'ffprobe introuvable : installe FFmpeg, puis relance.' -ForegroundColor Red; exit 1 }
$sonde = @(& ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 $chemin)
$codeSonde = $LASTEXITCODE
$taille = "$($sonde | Select-Object -First 1)" -split ','
if ($codeSonde -ne 0 -or $taille.Count -lt 2) { Write-Host "ffprobe ne lit pas la taille de $($cible.Name)." -ForegroundColor Red; exit 1 }
$largeur = [int]$taille[0]; $hauteur = [int]$taille[1]
$k = $largeur / 2560
if ([Math]::Abs($hauteur - 1440 * $k) -gt 2) {
    Write-Host "Format $largeur x $hauteur inattendu : le cadrage OBS suppose du 16:9. Trace un profil dans ClipForge et passe-le par -ProfilScene." -ForegroundColor Red
    exit 1
}
$nom = if ($ProfilScene) { $ProfilScene } else { "WOLDENM OBS $($hauteur)p" }
$profils = @(foreach ($p in (Invoke-RestMethod -Uri "$Moteur/api/profiles" -TimeoutSec 15)) { $p })
if (@($profils | Where-Object { $_.name -eq $nom }).Count -gt 0) {
    Write-Host "Cadrage des clips : profil « $nom » (déjà présent, gardé tel quel)."
} else {
    $reglages = Invoke-RestMethod -Uri "$Moteur/api/settings" -TimeoutSec 15
    if (-not $reglages.auto_profile -and $profils.Count -eq 1) {
        Invoke-Moteur Patch '/api/settings' @{ values = @{ auto_profile = $profils[0].name } } | Out-Null
        Write-Host "Tes autres VOD gardent leur cadrage : « $($profils[0].name) » posé dans Réglages, « Profil des clips automatiques »."
    }
    Invoke-Moteur Post '/api/profiles' ([ordered]@{
        name   = $nom
        cam_x  = Pair (1874 * $k); cam_y = Pair (1042 * $k); cam_w = Pair (636 * $k); cam_h = Pair (340 * $k)
        game_x = Pair (701 * $k); game_y = 0; game_w = Pair (1158 * $k); game_h = Pair (1440 * $k)
    }) | Out-Null
    Write-Host "Cadrage des clips : profil « $nom » créé d'après la scène « Jeux » d'OBS."
}

try {
    Invoke-RestMethod -Method Post -Uri "$Moteur/api/vods/$($vod.id)/analyze?profile=$([uri]::EscapeDataString($nom))" -TimeoutSec 30 | Out-Null
    Write-Host 'Analyse lancée : suis-la dans ClipForge, les clips arrivent dans « Prêts à poster ».' -ForegroundColor Green
    exit 0
} catch {
    $code = $_.Exception.Response.StatusCode.value__
    if ($code -eq 409) { Write-Host "ClipForge refuse pour l'instant : $($_.ErrorDetails.Message)" -ForegroundColor Yellow; exit 2 }
    Write-Host "Analyse non lancée : $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

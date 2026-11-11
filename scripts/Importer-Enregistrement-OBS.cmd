@echo off
setlocal
set "FICHE="
for /f "tokens=1,2,*" %%A in ('%SystemRoot%\System32\reg.exe query "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders" /v Personal 2^>nul') do if /i "%%A"=="Personal" call set "FICHE=%%C\PowerShell\Modules\SocleAdmin\boite-a-outils\lancer.ps1"
if not exist "%FICHE%" set "FICHE=%~dp0_fiche\lancer.ps1"
if not exist "%FICHE%" goto direct
pwsh -NoProfile -File "%FICHE%" "%~dp0%~n0.ps1" %*
goto fin
:direct
echo Fiche indisponible (socle SocleAdmin absent) : le script part sans elle.
pwsh -NoProfile -File "%~dp0%~n0.ps1" %*
:fin
set "CODE=%ERRORLEVEL%"
setlocal EnableDelayedExpansion
set "LIGNE=!CMDCMDLINE!"
set "SANS=!LIGNE: /c =!"
if not "!SANS!"=="!LIGNE!" pause
exit /b %CODE%

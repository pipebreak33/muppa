<#
 Avinstallera Muppa på Windows (fungerar även i pwsh på macOS/Linux):
   irm https://raw.githubusercontent.com/pipebreak33/muppa/main/uninstall.ps1 | iex
 Från en lokal kopia:  ./uninstall.ps1

 Tar bort programmet (~/.muppa/app), kommandot muppa och PATH-posten.
 Behåller dina mallar (~/.muppa) och skapade skript i ~/Documents/Muppa.
 $env:MUPPA_PURGE = '1' tar bort även .muppa med dina data. $env:MUPPA_YES = '1' hoppar över frågan.
#>
$ErrorActionPreference = 'Stop'
$Clock = [Diagnostics.Stopwatch]::StartNew()
$OnWindows = ($PSVersionTable.PSEdition -ne 'Core') -or $IsWindows
$Dir = Join-Path $HOME '.muppa'
$Bin = Join-Path $Dir 'bin'
$Shim = if ($OnWindows) { Join-Path $Bin 'muppa.cmd' } else { Join-Path $HOME '.local/bin/muppa' }

# Language: TRUPPA_LANG, else ~/.truppa/config.json (shared by the whole troupe), else English
$TL = $env:TRUPPA_LANG
if (-not $TL) { try { $TL = (Get-Content -Raw -LiteralPath (Join-Path $HOME '.truppa/config.json') -ErrorAction Stop | ConvertFrom-Json).lang } catch { } }
function T([string]$en, [string]$sv) { if ("$TL" -like 'sv*') { $sv } else { $en } }
function Say($m) { Write-Host "  M› $m" -ForegroundColor Magenta }
function Ask($q) { $s = Read-Host "  $q $(T '[y/N]' '[j/N]')"; return ($s -match '^(j|ja|y|yes)$') }

$targets = @()
if ($env:MUPPA_PURGE -eq '1') {
    if (Test-Path $Dir) { $targets += $Dir }
} else {
    foreach ($p in @('app', 'bin')) { $x = Join-Path $Dir $p; if (Test-Path $x) { $targets += $x } }
}
# startkommandot på macOS/Linux: bara om det är vårt (pekar på ~/.muppa)
if (-not $OnWindows -and (Test-Path $Shim) -and ((Get-Content $Shim -Raw) -like "*/.muppa/*")) { $targets += $Shim }
$user = if ($OnWindows) { [Environment]::GetEnvironmentVariable('Path', 'User') } else { '' }
if (-not $user) { $user = '' }
$inPath = ($user -split ';') -contains $Bin

if (-not $targets -and -not $inPath) { Say (T 'Muppa is not installed here. Nothing to remove.' 'Muppa är inte installerad här. Inget att ta bort.'); return }
Write-Host (T "`n  This is removed:" "`n  Det här tas bort:")
$targets | ForEach-Object { Write-Host "    $_" }
if ($inPath) { Write-Host "    $Bin i din PATH" }
if ($env:MUPPA_PURGE -ne '1') { Write-Host (T '  Kept: your templates (~/.muppa) and created scripts in ~/Documents/Muppa.' '  Behålls: dina mallar (~/.muppa) och skapade skript i ~/Documents/Muppa.') }
Write-Host ''
if ($env:MUPPA_YES -ne '1') {
    if (-not (Ask (T 'Uninstall Muppa?' 'Avinstallera Muppa?'))) { Say (T 'Cancelled. Nothing has been removed.' 'Avbrutet. Ingenting har tagits bort.'); return }
}

foreach ($t in $targets) { Remove-Item $t -Recurse -Force }
if ($inPath) {
    [Environment]::SetEnvironmentVariable('Path', (($user -split ';' | Where-Object { $_ -and $_ -ne $Bin }) -join ';'), 'User')
}
if ((Test-Path $Dir) -and -not (Get-ChildItem $Dir -Force)) { Remove-Item $Dir -Force }

$Clock.Stop()
Write-Host (T "`n  ✔ Muppa is uninstalled ($([int]$Clock.Elapsed.TotalSeconds) s).`n" "`n  ✔ Muppa är avinstallerad ($([int]$Clock.Elapsed.TotalSeconds) s).`n") -ForegroundColor Green

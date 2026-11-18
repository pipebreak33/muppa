# Muppa

<p align="center">
  <img src="assets/muppa.svg" alt="Muppa: Hej! Jag är Muppa." width="720">
</p>

Flytta en Windows-dator från en **lokal AD-domän till Entra ID** utan att skriva skriptet själv.
Du svarar på några frågor, och Muppa skapar ett **anpassat PowerShell-skript** och en **checklista** för just den
migreringen. Fungerar likadant på **Windows, macOS och Linux**, så skriptet kan förberedas på din egen dator.
Kusin till [Puppa](https://github.com/pipebreak33/puppa) och [uppa](https://github.com/pipebreak33/uppa).
Maskoten är ett M med ögon. Den blinkar, tittar runt och blir glad när skriptet är sparat.

## Installera (eller uppdatera – kör samma rad igen)

**Windows** (PowerShell):

```powershell
irm https://raw.githubusercontent.com/pipebreak33/muppa/main/install.ps1 | iex
```

**macOS / Linux:**

```bash
curl -fsSL https://raw.githubusercontent.com/pipebreak33/muppa/main/install.sh | sh
```

Installern hittar (eller på Windows installerar) Python 3.8+, lägger Muppa i `~/.muppa/app` och skapar kommandot
`muppa` (`~/.local/bin/muppa`, eller `%USERPROFILE%\.muppa\bin\muppa.cmd` + PATH på Windows). Dina mallar i
`~/.muppa` rörs aldrig vid uppdatering. Styr källan med `MUPPA_REPO=ägare/repo` och `MUPPA_BRANCH=gren`.
Från en lokal kopia: `./install.sh` / `./install.ps1`. `muppa --version` visar versionen.


## Avinstallera

**Windows** (PowerShell):

```powershell
irm https://raw.githubusercontent.com/pipebreak33/muppa/main/uninstall.ps1 | iex
```

**macOS / Linux:**

```bash
curl -fsSL https://raw.githubusercontent.com/pipebreak33/muppa/main/uninstall.sh | sh
```

Tar bort programmet och kommandot `muppa` (och PATH-posten på Windows). Det behåller dina mallar i `~/.muppa` och skapade skript i `~/Documents/Muppa`.
`MUPPA_PURGE=1` tar bort även dina data, och `MUPPA_YES=1` hoppar över frågan.

## Så funkar det

1. **Frågor.** Användare (på Windows listas `C:\Users\*`, annars skriver du namnet), målmapp (standard
   `C:\temp\<användare>`), **vilka mappar som ska kopieras** (kryssrutor: Skrivbord, Dokument, Bilder, Hämtade filer,
   Musik, Videor, Favoriter, Kontakter, Sticky Notes, Outlook-signaturer och egna sökvägar), vad som ska hända med filer
   som bara finns i OneDrive-molnet, webbläsare (Edge/Chrome), BitLocker (pausa, stäng av, bara spara nyckeln), lokalt
   admin-konto, domän/Entra ID och valfria uppgifter (kund, ansvarig, dator, UPN, tekniker, ärende). Är kusinen
   [Puppa](https://github.com/pipebreak33/puppa) installerad kan UPN:en väljas direkt bland tenantens användare, med
   fuzzy-sök och användarnamnet som förslag. Sist väljer du
   **vilka steg som ska ingå**. Alla steg kan väljas bort.
2. **Förhandsvisning.** Bläddra i skriptet och checklistan, ändra svaren, och spara sedan
   `Migrera-<användare>.ps1` + `Checklista-<användare>.md` i `~/Documents/Muppa/`.
3. **Mallar.** Spara valen som en mall, t.ex. *Standard: Skrivbord+Dokument+Bilder+Hämtade · Edge · BitLocker paus*.
   Nästa migrering börjar då med mallen. Mallar (`~/.muppa/mallar.json`) innehåller inga lösenord eller hemligheter.

Guiden styrs som syskonen: piltangenter, siffror som snabbval, mellanslag som kryssar och fuzzy-sökning när du börjar
skriva. Ctrl+C avbryter.

## Kommandon

| | |
|---|---|
| `muppa` | menyn |
| `muppa ny [--mall NAMN]` | guiden |
| `muppa skapa ANVÄNDARE [--mall NAMN] [--mappar skrivbord,dokument,…] [--egen SÖKVÄG] [--webblasare edge,chrome] [--bitlocker pausa\|stang-av\|spara\|ingen] [--doman bada\|doman\|entra\|inget] [--admin NAMN \| --ingen-admin] [--kund …] [--ansvarig …] [--dator …] [--upn …]` | skript + checklista utan frågor |
| `muppa mallar` | lista mallar |
| `--no-anim` | inga animationer (stängs också av av `NO_COLOR` och när utdata pipas) |

## Skriptet

Skriptet är för **Windows PowerShell 5.1** och körs på datorn som migreras. Det körs i fyra faser:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\Migrera-anna.ps1 -Torrkorning   # provkör: visar bara vad som görs (= -WhatIf)
powershell.exe -ExecutionPolicy Bypass -File .\Migrera-anna.ps1                # som admin: förbered
powershell.exe -ExecutionPolicy Bypass -File C:\temp\anna\Migrera-anna.ps1 -Doman    # som lokal admin: lämna domänen
powershell.exe -ExecutionPolicy Bypass -File C:\temp\anna\Migrera-anna.ps1 -Entra    # som lokal admin: anslut till Entra ID
powershell.exe -ExecutionPolicy Bypass -File C:\temp\anna\Migrera-anna.ps1 -Aterlas  # som användaren: läs tillbaka
```

| Fas | Vad som händer |
|---|---|
| förbered | Kontrollerar adminrättigheter, loggar allt (`Start-Transcript`) till målmappen och kopierar skriptet dit. **Mappar:** läser den verkliga sökvägen ur användarens `User Shell Folders` (laddar `NTUSER.DAT` om användaren inte är inloggad), så att mappar som är omdirigerade till OneDrive följs. Mäter storleken, räknar filer som **bara finns i molnet** (Files On-Demand) och kontrollerar ledigt utrymme. Kopierar sedan med `robocopy` och översätter exitkoderna till begripliga meddelanden. **BitLocker:** `manage-bde -protectors -get` till fil och säkerhetskopiering till Entra ID/AD om datorn är ansluten. Sedan `Suspend-BitLocker` eller `Disable-BitLocker`. **Webbläsare:** kopierar `Bookmarks` för varje Edge/Chrome-profil och gör om dem till en HTML-fil som går att importera. Lösenord kan inte exporteras automatiskt, så skriptet öppnar `edge://settings/passwords` / `chrome://password-manager/settings` och väntar medan du sparar CSV:n. **Aktivitetsfältet:** kopierar fästa genvägar och `Taskband`-registret och tar en skärmdump. **Admin-konto:** lösenordet frågas efter med `Read-Host -AsSecureString` när skriptet körs. Det står aldrig i skriptet. |
| `-Doman` | Visar lokala admin-konton, kräver att du skriver **LÄMNA** och kör `Remove-Computer`. Erbjuder omstart. |
| `-Entra` | Öppnar `ms-settings:workplace` och beskriver steget *Anslut den här enheten till Microsoft Entra ID*. Väntar tills `dsregcmd` visar `AzureAdJoined : YES`, säkerhetskopierar BitLocker-nyckeln till Entra ID och slår på BitLocker igen. |
| `-Aterlas` | I den nya profilen: kopierar tillbaka med `robocopy /XO` (skriver inte över nyare filer) till mapparna som `User Shell Folders` pekar på. Kontrollerar OneDrive (konto, process, omdirigering), öppnar import av bokmärken och lösenord, visar skärmdumpen, startar Outlook och Teams och erbjuder att radera lösenordsfilerna. |

Överst i skriptet finns inställningarna (`$Cfg`, `$Steg`, `$Mappar`). Där kan du stänga av ett steg direkt i
filen. Ett steg som misslyckas stoppar inte resten: skriptet frågar om det ska fortsätta.

## Checklistan

Checklistan tar upp de manuella stegen i rätt ordning, med kryssrutor: återställa lösenordet i Entra ID,
OneDrive-backup, spara BitLocker-nyckeln utanför datorn, logga in som lokal admin, Windows Hello (hoppa över vid
första inloggningen), Outlook-profil, Teams, OneDrive-synk, Teams-/SharePoint-bibliotek, fästa ikoner enligt
skärmdumpen, radera lösenordsfilerna och sist *meddela ansvarig att sätta nytt lösenord*. Kund, ansvarig och dator
är valfria fält.

## Säkerhet

- Inga lösenord sparas, varken i skriptet, i mallarna eller i checklistan.
- Lösenords-CSV:erna från webbläsarna innehåller klartext. Lägg målmappen på datorn, inte på en delad disk, och radera
  filerna efter importen. `-Aterlas` frågar.
- BitLocker-nyckeln hamnar i målmappen på samma disk. Spara den också utanför datorn.

## Teknik

En Python-fil (bara standardbiblioteket, 3.8+). Tangenter läses med `msvcrt` på Windows och `termios` på macOS/Linux.
Layouten skalar med terminalen: inga rader radbryts, smal layout under 58 kolumner, maskoten döljs under 44, och allt
ritas om när fönstret ändrar storlek. Det genererade skriptet sparas som UTF-8 med BOM, så att Windows PowerShell 5.1
läser å, ä och ö rätt.

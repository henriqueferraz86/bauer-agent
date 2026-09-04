#Requires -Version 5.1
<#
.SYNOPSIS
    Bauer Agent — instalador Windows

.DESCRIPTION
    Instalação rápida (PowerShell como usuário normal):
        irm https://raw.githubusercontent.com/henriqueferraz86/bauer-agent/master/install.ps1 | iex

    Ou executar localmente:
        Set-ExecutionPolicy -Scope Process Bypass
        .\install.ps1 [-Update] [-Uninstall] [-Extra gateway] [-NoExtra]

.PARAMETER Update
    Atualiza instalação existente sem reinstalar.

.PARAMETER Uninstall
    Remove completamente o Bauer Agent (workspace não é tocado).

.PARAMETER Extra
    Extras pip a instalar (padrão: gateway,voice,voice-kokoro). Use "all" para todos.

.PARAMETER NoExtra
    Instala só dependências core, sem extras.
#>
param(
    [switch]$Update,
    [switch]$Uninstall,
    [string]$Extra    = "gateway,voice,voice-kokoro",
    [switch]$NoExtra
)

$ErrorActionPreference = "Stop"

$Repo       = "https://github.com/henriqueferraz86/bauer-agent.git"
$InstallDir = "$env:LOCALAPPDATA\BauerAgent"
$BinDir     = "$InstallDir\bin"
$VenvDir    = "$InstallDir\.venv"
$BauerCmd   = "$BinDir\bauer.cmd"
$BauerPs1   = "$BinDir\bauer.ps1"

if ($NoExtra) { $Extra = "" }

function Write-Info  { param($msg) Write-Host "[bauer] $msg" -ForegroundColor Cyan }
function Write-Ok    { param($msg) Write-Host "[bauer] v $msg" -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "[bauer] ! $msg" -ForegroundColor Yellow }
function Write-Err   { param($msg) Write-Host "[bauer] x $msg" -ForegroundColor Red; throw $msg }

function Test-ExtraEnabled {
    param([string]$Name)
    if ($Extra.Trim().ToLowerInvariant() -eq "all") { return $true }
    return @($Extra -split "," | ForEach-Object { $_.Trim().ToLowerInvariant() }) -contains $Name
}

function Set-VoiceDefaults {
    if (-not (Test-ExtraEnabled "voice-kokoro")) { return }

    # Persiste a escolha no perfil do usuário para que instalações e updates
    # não dependam de `$env:...` digitado manualmente em cada terminal.
    $provider = [Environment]::GetEnvironmentVariable("BAUER_TTS_PROVIDER", "User")
    if (-not $provider) {
        [Environment]::SetEnvironmentVariable("BAUER_TTS_PROVIDER", "kokoro", "User")
        $env:BAUER_TTS_PROVIDER = "kokoro"
        Write-Info "Provider de voz padrão: Kokoro"
    }
    $voice = [Environment]::GetEnvironmentVariable("BAUER_TTS_KOKORO_VOICE", "User")
    if (-not $voice) {
        [Environment]::SetEnvironmentVariable("BAUER_TTS_KOKORO_VOICE", "pm_alex", "User")
        $env:BAUER_TTS_KOKORO_VOICE = "pm_alex"
    }
}

# ─── Uninstall ───────────────────────────────────────────────────────────────
if ($Uninstall) {
    Write-Info "Desinstalando Bauer Agent..."

    # Remove bin do PATH do usuário
    $userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
    $newPath  = ($userPath -split ";" | Where-Object { $_ -ne $BinDir -and $_ -ne "" }) -join ";"
    [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")

    if (Test-Path $InstallDir) {
        Remove-Item -Recurse -Force $InstallDir
    }
    Write-Ok "Bauer Agent removido."
    Write-Warn "Workspace em %USERPROFILE%\bauer-workspace\ (se existir) não foi tocado."
    return
}

# ─── Checks ──────────────────────────────────────────────────────────────────
try { git --version | Out-Null }
catch { Write-Err "git não encontrado. Instale Git for Windows: https://git-scm.com/download/win" }

# Localiza Python 3.11+
$Python = $null; $PyVersion = $null
foreach ($cmd in @("python3.13","python3.12","python3.11","python3","python","py")) {
    try {
        $ver = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($ver) {
            $parts = $ver.Split(".")
            if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 11) {
                $Python = $cmd; $PyVersion = $ver; break
            }
        }
    } catch {}
}
if (-not $Python) {
    Write-Err "Python 3.11+ não encontrado. Baixe em https://www.python.org/downloads/"
}
Write-Info "Usando $Python $PyVersion"

# ─── Update ──────────────────────────────────────────────────────────────────
if ($Update) {
    if (-not (Test-Path "$InstallDir\.git")) {
        Write-Err "Instalação não encontrada em $InstallDir. Execute sem -Update para instalar."
    }
    Write-Info "Atualizando $InstallDir ..."
    git -C $InstallDir fetch --depth=1 origin master
    git -C $InstallDir reset --hard origin/master

    Write-Info "Atualizando dependências..."
    $pipTarget = if ($Extra) { "$InstallDir\[$Extra]" } else { $InstallDir }
    & "$VenvDir\Scripts\python" -m pip install -q --upgrade -e $pipTarget

    Set-VoiceDefaults

    Write-Ok "Bauer Agent atualizado!"
    try { & $BauerCmd --version } catch {}
    return
}

# ─── Fresh install ───────────────────────────────────────────────────────────
if (Test-Path $InstallDir) {
    Write-Warn "$InstallDir ja existe."
    throw "Use -Update para atualizar ou -Uninstall para remover antes de reinstalar."
}

Write-Host ""
Write-Host "  ██████╗  █████╗ ██╗   ██╗███████╗██████╗ " -ForegroundColor Blue
Write-Host "  ██╔══██╗██╔══██╗██║   ██║██╔════╝██╔══██╗" -ForegroundColor Blue
Write-Host "  ██████╔╝███████║██║   ██║█████╗  ██████╔╝" -ForegroundColor Blue
Write-Host "  ██╔══██╗██╔══██║██║   ██║██╔══╝  ██╔══██╗" -ForegroundColor Blue
Write-Host "  ██████╔╝██║  ██║╚██████╔╝███████╗██║  ██║" -ForegroundColor Blue
Write-Host "  ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝" -ForegroundColor Blue
Write-Host "  Agent — instalador Windows"
Write-Host ""

Write-Info "Clonando bauer-agent em $InstallDir ..."
git clone --depth=1 $Repo $InstallDir

Write-Info "Criando ambiente virtual..."
& $Python -m venv $VenvDir

Write-Info "Atualizando pip..."
& "$VenvDir\Scripts\python" -m pip install -q --upgrade pip

$extrasLabel = if ($Extra) { " [extras: $Extra]" } else { "" }
Write-Info "Instalando dependencias$extrasLabel..."
$pipTarget = if ($Extra) { "$InstallDir\[$Extra]" } else { $InstallDir }
& "$VenvDir\Scripts\python" -m pip install -q -e $pipTarget

Set-VoiceDefaults

# ─── Launchers ───────────────────────────────────────────────────────────────
New-Item -ItemType Directory -Force $BinDir | Out-Null

# Caminho do venv gravado ABSOLUTO (via $VenvDir, expandido AGORA), nao
# %LOCALAPPDATA% em runtime: um launcher que resolve o venv pela env var do
# chamador quebra se rodado por outro usuario Windows. Grava-se o caminho real
# detectado na instalacao. (Mesma correcao do install.sh — ver comentario la.)

# .cmd — funciona em cmd.exe e terminais sem PS
@"
@echo off
"$VenvDir\Scripts\python.exe" -m bauer.cli %*
"@ | Out-File -FilePath $BauerCmd -Encoding ascii

# .ps1 — funciona em PowerShell puro
@"
& "$VenvDir\Scripts\python.exe" -m bauer.cli @args
"@ | Out-File -FilePath $BauerPs1 -Encoding utf8

# ─── PATH ────────────────────────────────────────────────────────────────────
$userPath = [Environment]::GetEnvironmentVariable("PATH", "User") -as [string]
$userEntries = @($userPath -split ";" | Where-Object { $_ -and $_ -ne $BinDir })
$newPath = (@($BinDir) + $userEntries) -join ";"
if ($newPath -ne $userPath) {
    Write-Info "Colocando $BinDir na frente do PATH do usuario..."
    [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
}

# O processo atual nao recarrega o PATH do usuario automaticamente. Reordena
# tambem a copia em memoria para que o comando `bauer` funcione imediatamente,
# mesmo que outro pacote tenha instalado um executavel com o mesmo nome.
$processEntries = @($env:PATH -split ";" | Where-Object { $_ -and $_ -ne $BinDir })
$env:PATH = (@($BinDir) + $processEntries) -join ";"

# ─── Done ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Ok "Bauer Agent instalado com sucesso!"
Write-Host ""
Write-Host "  Executavel : $BauerCmd"
Write-Host "  Instalacao : $InstallDir"
Write-Host ""
Write-Warn "Reinicie o terminal para que o PATH seja atualizado."
Write-Host ""
Write-Host "  Proximos passos:"
Write-Host "    bauer --help"
Write-Host "    bauer gateway init           # configurar Telegram / Discord"
Write-Host "    bauer serve service install  # instalar servidor HTTP como servico"
Write-Host ""

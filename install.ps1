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
    Extras pip a instalar (padrão: gateway). Use "all" para todos.

.PARAMETER NoExtra
    Instala só dependências core, sem extras.
#>
param(
    [switch]$Update,
    [switch]$Uninstall,
    [string]$Extra    = "gateway",
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
function Write-Err   { param($msg) Write-Host "[bauer] x $msg" -ForegroundColor Red; exit 1 }

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
    exit 0
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
    & "$VenvDir\Scripts\pip" install -q --upgrade -e $pipTarget

    Write-Ok "Bauer Agent atualizado!"
    try { & $BauerCmd --version } catch {}
    exit 0
}

# ─── Fresh install ───────────────────────────────────────────────────────────
if (Test-Path $InstallDir) {
    Write-Warn "$InstallDir ja existe."
    Write-Warn "Use -Update para atualizar ou -Uninstall para remover antes de reinstalar."
    exit 1
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
& "$VenvDir\Scripts\pip" install -q -e $pipTarget

# ─── Launchers ───────────────────────────────────────────────────────────────
New-Item -ItemType Directory -Force $BinDir | Out-Null

# .cmd — funciona em cmd.exe e terminais sem PS
@"
@echo off
"%LOCALAPPDATA%\BauerAgent\.venv\Scripts\python.exe" -m bauer.cli %*
"@ | Out-File -FilePath $BauerCmd -Encoding ascii

# .ps1 — funciona em PowerShell puro
@"
& `"`$env:LOCALAPPDATA\BauerAgent\.venv\Scripts\python.exe`" -m bauer.cli @args
"@ | Out-File -FilePath $BauerPs1 -Encoding utf8

# ─── PATH ────────────────────────────────────────────────────────────────────
$userPath = [Environment]::GetEnvironmentVariable("PATH", "User") -as [string]
if ($userPath -notlike "*$BinDir*") {
    Write-Info "Adicionando $BinDir ao PATH do usuario..."
    $newPath = if ($userPath) { "$userPath;$BinDir" } else { $BinDir }
    [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
    $env:PATH += ";$BinDir"
}

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

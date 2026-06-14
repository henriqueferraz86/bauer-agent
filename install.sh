#!/usr/bin/env bash
# Bauer Agent — instalador Linux/macOS
#
# Instalação rápida:
#   curl -fsSL https://raw.githubusercontent.com/henriqueferraz86/bauer-agent/master/install.sh | bash
#
# Opções:
#   --update          Atualiza instalação existente
#   --uninstall       Remove completamente
#   --extra=<extras>  Extras pip (padrão: gateway). Ex: --extra=all
#   --no-extra        Instala só dependências core

set -euo pipefail

REPO="https://github.com/henriqueferraz86/bauer-agent.git"
INSTALL_DIR="$HOME/.local/share/bauer-agent"
BIN_DIR="$HOME/.local/bin"
BAUER_BIN="$BIN_DIR/bauer"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLUE}[bauer]${NC} $*"; }
ok()    { echo -e "${GREEN}[bauer]${NC} ✓ $*"; }
warn()  { echo -e "${YELLOW}[bauer]${NC} ! $*"; }
die()   { echo -e "${RED}[bauer]${NC} ✗ $*" >&2; exit 1; }

DO_UNINSTALL=0; DO_UPDATE=0; EXTRA="gateway"; NO_EXTRA=0
for arg in "$@"; do
    case $arg in
        --uninstall)    DO_UNINSTALL=1 ;;
        --update)       DO_UPDATE=1 ;;
        --extra=*)      EXTRA="${arg#--extra=}" ;;
        --no-extra)     NO_EXTRA=1 ;;
        --help|-h)
            echo "Uso: $0 [--update] [--uninstall] [--extra=all] [--no-extra]"
            exit 0 ;;
        *) die "Opção desconhecida: $arg" ;;
    esac
done
[ "$NO_EXTRA" = 1 ] && EXTRA=""

# ─── Uninstall ───────────────────────────────────────────────────────────────
if [ "$DO_UNINSTALL" = 1 ]; then
    info "Desinstalando Bauer Agent..."
    rm -f "$BAUER_BIN"
    rm -rf "$INSTALL_DIR"
    ok "Removido."
    warn "Workspace (~/.local/share/bauer-agent/workspace/ ou ~/bauer-workspace/) não foi tocado."
    exit 0
fi

# ─── Checks ──────────────────────────────────────────────────────────────────
command -v git >/dev/null 2>&1 || die "git não encontrado. Instale git e tente novamente."

PYTHON=""
for cmd in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
        ver=$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "${major:-0}" -ge 3 ] && [ "${minor:-0}" -ge 11 ]; then
            PYTHON="$cmd"; PYVER="$ver"; break
        fi
    fi
done
[ -n "$PYTHON" ] || die "Python 3.11+ não encontrado. Instale Python 3.11 ou superior."
info "Usando $PYTHON $PYVER"

# ─── Update ──────────────────────────────────────────────────────────────────
if [ "$DO_UPDATE" = 1 ]; then
    [ -d "$INSTALL_DIR/.git" ] || die "Instalação não encontrada em $INSTALL_DIR. Execute sem --update para instalar."
    info "Atualizando $INSTALL_DIR ..."
    git -C "$INSTALL_DIR" fetch --depth=1 origin master
    git -C "$INSTALL_DIR" reset --hard origin/master
    info "Atualizando dependências..."
    if [ -n "$EXTRA" ]; then
        "$INSTALL_DIR/.venv/bin/pip" install -q --upgrade -e "$INSTALL_DIR/[$EXTRA]"
    else
        "$INSTALL_DIR/.venv/bin/pip" install -q --upgrade -e "$INSTALL_DIR/"
    fi
    ok "Bauer Agent atualizado!"
    "$BAUER_BIN" --version 2>/dev/null || true
    exit 0
fi

# ─── Fresh install ───────────────────────────────────────────────────────────
if [ -d "$INSTALL_DIR" ]; then
    warn "$INSTALL_DIR já existe."
    warn "Use --update para atualizar ou --uninstall para remover antes de reinstalar."
    exit 1
fi

echo ""
echo "  ██████╗  █████╗ ██╗   ██╗███████╗██████╗ "
echo "  ██╔══██╗██╔══██╗██║   ██║██╔════╝██╔══██╗"
echo "  ██████╔╝███████║██║   ██║█████╗  ██████╔╝"
echo "  ██╔══██╗██╔══██║██║   ██║██╔══╝  ██╔══██╗"
echo "  ██████╔╝██║  ██║╚██████╔╝███████╗██║  ██║"
echo "  ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝"
echo "  Agent — instalador"
echo ""

info "Clonando bauer-agent em $INSTALL_DIR ..."
git clone --depth=1 "$REPO" "$INSTALL_DIR" 2>&1 | sed 's/^/  /'

info "Criando ambiente virtual..."
"$PYTHON" -m venv "$INSTALL_DIR/.venv"

info "Atualizando pip..."
"$INSTALL_DIR/.venv/bin/pip" install -q --upgrade pip

info "Instalando dependências${EXTRA:+ [extras: $EXTRA]}..."
if [ -n "$EXTRA" ]; then
    "$INSTALL_DIR/.venv/bin/pip" install -q -e "$INSTALL_DIR/[$EXTRA]"
else
    "$INSTALL_DIR/.venv/bin/pip" install -q -e "$INSTALL_DIR/"
fi

# ─── Launcher ────────────────────────────────────────────────────────────────
mkdir -p "$BIN_DIR"
cat > "$BAUER_BIN" << 'LAUNCHER'
#!/usr/bin/env bash
exec "$HOME/.local/share/bauer-agent/.venv/bin/python" -m bauer.cli "$@"
LAUNCHER
chmod +x "$BAUER_BIN"

# ─── PATH ────────────────────────────────────────────────────────────────────
PATH_ADDED=0
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    add_to_rc() {
        local rc="$1"
        [ -f "$rc" ] || return
        grep -qF '.local/bin' "$rc" && return
        printf '\n# Bauer Agent\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
        info "PATH adicionado em $rc"
    }
    add_to_rc "$HOME/.bashrc"
    add_to_rc "$HOME/.zshrc"
    add_to_rc "$HOME/.profile"
    PATH_ADDED=1
fi

# ─── Done ────────────────────────────────────────────────────────────────────
echo ""
ok "Bauer Agent instalado!"
echo ""
echo "  Executável : $BAUER_BIN"
echo "  Instalação : $INSTALL_DIR"
echo ""
if [ "$PATH_ADDED" = 1 ]; then
    warn "Reinicie o terminal ou execute:  source ~/.bashrc"
    echo ""
fi
echo "  Próximos passos:"
echo "    bauer --help"
echo "    bauer gateway init           # configurar Telegram / Discord"
echo "    bauer serve service install  # instalar servidor HTTP como serviço"
echo ""

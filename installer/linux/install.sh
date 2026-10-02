#!/bin/sh
# Instalação do coletor @PRODUCT@ em Linux (Debian/Ubuntu/Raspberry Pi OS ou outra distribuição com systemd).
#
#   curl -fsSL "@SERVER@/api/public/install.sh?code=XXXXXXXX" | sudo sh
#
# Baixa o pacote da arquitetura deste computador (.deb quando há dpkg; senão .tar.gz), cadastra o coletor
# com o código de 8 caracteres gerado no portal, instala e inicia os serviços @AGENT_SERVICE@ e
# @WATCHDOG_SERVICE@ (systemd). Opções: --server URL --code XXXXXXXX --package arquivo --dry-run
set -eu

SERVER="@SERVER@"
CODE="@CODE@"
PACKAGE=""
DRY_RUN=0

fail() { echo "ERRO: $*" >&2; exit 1; }
run() {
    if [ "$DRY_RUN" = 1 ]; then echo "[simulação] $*"; else "$@"; fi
}

while [ $# -gt 0 ]; do
    case "$1" in
        --server) SERVER="$2"; shift 2 ;;
        --code) CODE="$2"; shift 2 ;;
        --package) PACKAGE="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        *) fail "opção desconhecida: $1" ;;
    esac
done

case "$SERVER" in http://*|https://*) ;; *) fail "informe --server https://..." ;; esac
[ ${#CODE} -eq 8 ] || fail "informe --code com o código de 8 caracteres gerado no portal (Coletores → Novo coletor)"
if [ "$DRY_RUN" = 0 ] && [ "$(id -u)" != 0 ]; then fail "rode como root (sudo)"; fi
command -v systemctl >/dev/null 2>&1 || fail "este instalador precisa do systemd"

case "$(uname -m)" in
    x86_64|amd64) ARCH=amd64; DEB_ARCH=amd64 ;;
    i386|i486|i586|i686) ARCH=386; DEB_ARCH=i386 ;;
    aarch64|arm64) ARCH=arm64; DEB_ARCH=arm64 ;;
    armv6l|armv7l|armhf|arm) ARCH=arm; DEB_ARCH=armhf ;;
    *) fail "arquitetura não suportada: $(uname -m)" ;;
esac
if command -v dpkg >/dev/null 2>&1; then FORMAT=deb; else FORMAT=tar; fi
echo "@PRODUCT@: arquitetura $ARCH ($DEB_ARCH), pacote $FORMAT"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
if [ -z "$PACKAGE" ]; then
    PACKAGE="$TMP/pacote.$FORMAT"
    URL="$SERVER/api/public/installer?code=$CODE&platform=linux&arch=$ARCH&format=$FORMAT"
    if command -v curl >/dev/null 2>&1; then
        run curl -fsSL -o "$PACKAGE" "$URL" || fail "download do pacote falhou ($URL)"
    elif command -v wget >/dev/null 2>&1; then
        run wget -q -O "$PACKAGE" "$URL" || fail "download do pacote falhou ($URL)"
    else
        fail "instale curl ou wget"
    fi
fi

if [ "$FORMAT" = deb ]; then
    run dpkg -i "$PACKAGE" || fail "dpkg -i falhou"
else
    run tar -xzf "$PACKAGE" -C /usr/bin dm-agent dm-watchdog dm-tool || fail "extração do pacote falhou"
fi

if [ -f "@DATA_DIR@/config.json" ]; then
    echo "@PRODUCT@: este computador já está cadastrado; mantendo o cadastro."
else
    run dm-agent enroll --server "$SERVER" --code "$CODE" || fail "cadastro recusado (veja a mensagem acima)"
fi
[ -f "/etc/systemd/system/@AGENT_SERVICE@.service" ] || run dm-agent install || fail "instalar o serviço do coletor"
[ -f "/etc/systemd/system/@WATCHDOG_SERVICE@.service" ] || run dm-watchdog install || fail "instalar o serviço do watchdog"
run systemctl daemon-reload
run systemctl restart "@AGENT_SERVICE@" "@WATCHDOG_SERVICE@" || fail "iniciar os serviços"
echo "@PRODUCT@: coletor instalado e em execução. Confira com: sudo dm-agent status"

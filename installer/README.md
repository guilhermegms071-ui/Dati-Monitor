# Instaladores do coletor

Os nomes de serviço e de pastas vêm do `product.json` (raiz do repositório).

## Windows (Inno Setup) — `windows/dati-monitor.iss`

Gerar (sem administrador; o Inno Setup 6 pode ser instalado só para o usuário com
`winget install -e --id JRSoftware.InnoSetup --scope user`):

```powershell
scripts\build-installer.ps1 -Version 1.0.0 -Server https://monitor.daticopy.com.br
scripts\build-installer.ps1 -Version 1.0.0 -Server https://monitor.daticopy.com.br -Sign   # com certificado
```

Saída: `dist\installers\dati-monitor-setup-<versão>.exe`, com os binários de amd64, arm64 e 386. A
arquitetura certa é escolhida na instalação.

O que o instalador faz:

1. **Recusa Windows antigo**: Windows 7, 8, 8.1, Server 2008 e 2012 recebem a mensagem da seção 4.1 do
   PROMPT, a mesma do agente.
2. **Tela de cadastro**: com o servidor gravado no build (`-Server`), pede **só a chave** de 8
   caracteres e mostra o endereço como informação; sem servidor no build, pede os dois. Confere a chave
   na hora (`dm-agent enroll --check-only`). A chave **não é gasta** se a instalação for cancelada.
   `/SERVER=` na linha de comando substitui o endereço gravado.
3. **Instala** `dm-agent`, `dm-watchdog` e `dm-tool` em `C:\Program Files\DatiMonitor`.
4. **Cadastra** o coletor e instala os serviços `DatiMonitorAgent` e `DatiMonitorWatchdog`. Os dois
   ficam com início automático com atraso e recuperação do SCM: reinicia após 5 s, 5 s e 30 s, e zera o
   contador em 1 dia. Depois inicia os dois.
5. **Atualização**: rodar um setup mais novo por cima mantém o cadastro; ele não pede código.
6. **Desinstalação**: remove os serviços, o programa e `C:\ProgramData\DatiMonitor` (credencial, fila e
   logs).

Para instalar sem perguntas (prompt de comando como administrador):

```
setup.exe /VERYSILENT /SERVER=https://monitor.daticopy.com.br /CODE=ABCD1234 /LOG=C:\instalacao.log
```

Códigos de saída:

| Código | Situação |
|---|---|
| 0 | instalado |
| 1 | recusado ou cancelado antes de instalar; a mensagem fica no log |
| 3 | arquivos copiados, mas o cadastro ou os serviços falharam; a mensagem fica no log |

**Assinatura de código**: `-Sign` assina os binários e o setup com o `signtool` (Windows SDK) e um
carimbo de tempo. O certificado vem de uma destas variáveis:

- `DM_SIGN_PFX` + `DM_SIGN_PFX_PASSWORD` (arquivo .pfx);
- `DM_SIGN_THUMBPRINT` (certificado no repositório do Windows ou em token USB).

Sem certificado, o setup sai sem assinatura e o build avisa no final.

### Teste na rede local, sem hospedagem

O servidor roda no PC de desenvolvimento e um coletor de outro PC da mesma rede fala com ele por
`http://IP:8000`:

```powershell
scripts\lan-setup.ps1        # IP do PC no .env (PUBLIC_SERVER_URL/PUBLIC_WS_URL) + Firewall "Dati Monitor dev" (só rede Privada)
scripts\dev.ps1 -Lan         # API e gateway escutando na rede
scripts\build-installer.ps1 -Version 1.0.0 -Server http://10.10.10.25:8000 -InsecureLan
```

- `-InsecureLan` só é aceito com `http://` + **IP de rede privada** (10.x, 172.16–31.x, 192.168.x). O
  coletor é cadastrado com `--insecure-lan`: aceita `http://` só para IP privado; endereço público
  continua exigindo HTTPS. Nome de PC não serve para `http://` (o que ele resolve pode mudar): grave o IP
  e reserve-o no roteador.
- Ao hospedar, gere de novo com `-Server https://...` (sem `-InsecureLan`). Os coletores já instalados
  migram sem reinstalar: no portal, **Coletor → Comandos → Mudar endereço do servidor…** (só admin). O
  pedido vai assinado com a chave do coletor; ele confere a assinatura e se autentica no novo servidor
  com a própria credencial antes de trocar (o watchdog segue em até 30 s). Se o novo servidor não
  reconhecer o coletor, ele fica no atual e o comando falha com o motivo.

**Testes**: `scripts\test-installer.ps1 -Server URL -Code XXXXXXXX`.

- Sem administrador, roda o instalador de **teste** e confere a recusa de Windows antigo e a conferência
  do código.
- Com `-Full`, em terminal **como administrador**, instala de verdade em modo silencioso e confere os
  serviços, a recuperação, o `/health`, a atualização e a desinstalação. O job `windows-installer` do CI
  roda exatamente isso.

## Linux — `linux/`

```powershell
scripts\build-agent.ps1 -Version 1.0.0 -Targets linux/amd64,linux/386,linux/arm64,linux/arm
.venv\Scripts\python scripts\build_linux.py --version 1.0.0
```

O build gera, em `dist\installers\`, um `.deb` (Debian, Ubuntu, Raspberry Pi OS) e um `.tar.gz` (outras
distribuições) para cada arquitetura: amd64, i386, arm64 e armhf. O `.deb` é montado pelo próprio script,
sem `dpkg-deb`, e é reprodutível.

Os scripts de manutenção do pacote:

- `postinst`: se o PC já estiver cadastrado, atualiza e reinicia os serviços; senão, mostra como cadastrar;
- `prerm`: remove os serviços;
- `postrm purge`: apaga `/var/lib/dati-monitor`.

As units do systemd são geradas pelo próprio `dm-agent install`/`dm-watchdog install` (Restart=always,
RestartSec=5, WatchdogSec=60, sd_notify), com o mesmo template de quando não há pacote.

Instalação num cliente (o servidor preenche o endereço e o código; o link vale enquanto o código vale):

```
curl -fsSL "https://monitor.daticopy.com.br/api/public/install.sh?code=ABCD1234" | sudo sh
```

## Publicar

Portal → **Downloads** → Publicar (superadmin): envie o setup.exe, os .deb e os .tar.gz. O portal
oferece a versão mais nova de cada tipo e arquitetura. O diálogo "Novo coletor" mostra:

- o link do instalador com o código (baixa sem login);
- o comando silencioso;
- a linha única do Linux.

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
2. **Tela de cadastro**: pede o servidor e o código de 8 caracteres e confere o código na hora
   (`dm-agent enroll --check-only`). O código **não é gasto** se a instalação for cancelada.
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

# Instaladores

Fica aqui, na **Fase 8**:

- `windows/` — script Inno Setup (`setup.exe`) com tela de código de cadastro, modo silencioso
  `/CODE=XXXX /SERVER=URL`, instalação dos serviços `DatiMonitorAgent` e `DatiMonitorWatchdog`,
  ações de recuperação do SCM, recusa de Windows 7/8/8.1/2008/2012 e desinstalador limpo.
- `linux/` — units systemd, pacote `.deb` e `install.sh`.

Os nomes de serviço e pastas derivam de `product.json` (raiz do repositório).

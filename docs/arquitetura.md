# Arquitetura

Resumo; a especificação completa é o `PROMPT.md` (seções 2 a 9).

```
REDE DO CLIENTE                                  SERVIDOR DATICOPY
Impressoras ◄─ SNMP UDP 161 ─ dm-agent (MASTER) ─ WSS 443 ─► gateway (FastAPI WebSocket)
                              dm-watchdog ─────── HTTPS 443 ─► API (FastAPI REST)
                              dm-agent (STANDBY)               worker (APScheduler)
                                                               PostgreSQL 16 (LISTEN/NOTIFY, sem Redis)
                                                               portal (React, build estático)
```

## Processos do backend

Um só pacote Python (`backend/app`), três processos:

| Processo | Módulo | Porta (dev) | Papel |
|---|---|---|---|
| API | `app.api.main:create_app` | 8000 | REST do portal, dos agentes e do ERP |
| Gateway | `app.gateway.main:create_app` | 8001 | WebSocket persistente dos agentes; entrega de comandos via `LISTEN/NOTIFY` |
| Worker | `app.worker.main` | — | Presença, lease de cluster, alertas, notificações, partições, retenção |

Os três leem a configuração de variáveis de ambiente (`app.core.config.Settings`) e expõem saúde
com teste real do banco (`/api/health` na API, `/health` no gateway; o worker registra no log).

## Coletor

Três binários Go sem CGO, 7 alvos (Windows 10+/Server 2016+, Linux, Raspberry Pi):
`dm-agent` (coleta), `dm-watchdog` (vigia, atualização, rollback; canal HTTPS próprio) e `dm-tool`
(suporte: walk, assinatura de releases). Nomes de serviço e pastas vêm de `product.json`.

## Desenvolvimento local

Tudo nativo no Windows (sem Docker): `scripts\dev.ps1` sobe os processos acima, o portal Vite, o
`smtp_catcher` (captura de e-mails) e uma instância do snmpsim por impressora simulada.
Os arquivos em `deploy/` existem para a hospedagem futura e não participam do fluxo atual.

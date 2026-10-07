# CLAUDE.md — Dati Monitor

Sistema de monitoramento de impressoras da Daticopy (substituto do Datacount). A especificação
completa está em `PROMPT.md`; o andamento, as decisões e como testar estão em `PROGRESS.md`.
Trabalhe **por fases, na ordem da seção 14 do PROMPT**.

## Estrutura

| Pasta | Conteúdo |
|---|---|
| `product.json` | **Fonte única** do nome do produto (nome, slug, prefixo de serviço). Go, Python e portal leem daqui. |
| `agent/` | Go (módulo `github.com/daticopy/dati-monitor/agent`). `cmd/dm-agent`, `cmd/dm-watchdog`, `cmd/dm-tool`; código em `internal/`. |
| `agent/internal/product/` | Constantes geradas de `product.json` (`go generate ./...`) e nomes derivados (serviços, pasta de dados). |
| `agent/internal/cli/` | Despacho de subcomandos comum aos 3 binários. |
| `agent/internal/buildinfo/` | Versão/commit injetados via `-ldflags` pelo `scripts\build-agent.ps1`. |
| `agent/internal/{snmp,profile,printer}` | Cliente SNMP + `.snmprec`/`MemSource`; motor de perfis; identidade/status/suprimentos. |
| `agent/internal/{discovery,collector}` | Varredura das faixas; agendamento das leituras (só o MASTER). `collector/interfaces.go`: IPs com o mesmo serial viram um equipamento, lido pela placa da impressora (controladoras Fiery/EFI/IC-xxx ficam fora). |
| `agent/internal/{store,uploader,api,protocol}` | Fila SQLite; envio em lote; cliente HTTPS do agente; mensagens v1 (espelho de `app/schemas/agent.py`). |
| `agent/internal/{agent,health,svc,secret,config,osinfo,logx}` | Montagem do processo, `/health`, serviço Windows/systemd, DPAPI, `config.json`, SO, logs. |
| `agent/internal/simtest/` | Testes de integração (`-tags integration`) contra o snmpsim real do venv. |
| `backend/app/api/agent/` | Rotas `/api/agent/*` (protocolo do agente, contingência de comandos, uploads). Ingestão em `services/ingest.py`. |
| `backend/app/gateway/` | Processo do WebSocket `/ws/agent`: `hub.py` (conexões e entrega de comandos), `listener.py` (LISTEN/NOTIFY), `main.py`. |
| `backend/app/services/{commands,presence}.py` | Ciclo de vida dos comandos (4.7), uploads, expiração; presença e varredura de offline. |
| `agent/internal/{watchdog,release}` | dm-watchdog (vigia a cada 15 s, canal próprio a cada 60 s, comandos do vigia; alvo serviço ou processo) e versões assinadas (ed25519, troca de binário com rollback automático). Chave pública em `release/public.key`. |
| `backend/app/services/{watchdog,releases,updates,cluster}.py` | Canal do watchdog, publicação/estatística de versões, atualização automática e failover do lease do MASTER (jobs do worker). |
| `agent/internal/{ws,commands,netdiag}` | Canal WebSocket; executor idempotente de comandos; ping/WOL/disco/interfaces. Handlers em `internal/agent/commands.go`. |
| `backend/app/core/` | Config (`pydantic-settings`, lê `.env` da raiz), banco, segurança (argon2/JWT), cripto AES-GCM, `Principal` + escopos, permissões, erros, e-mail, rate limit. |
| `backend/app/models/` | Todas as tabelas (SQLAlchemy 2 tipado). `PARTITIONED_TABLES`/`APPEND_ONLY_TABLES` em `__init__`. |
| `backend/app/schemas/` | Pydantic de entrada/saída da API. |
| `backend/app/services/` | Regras de negócio. **Toda função recebe o `Principal` e aplica `reseller_scope`/`customer_scope`**; escritas chamam `audit.record`. |
| `backend/app/api/v1/` | Roteadores finos: validam, chamam o serviço e fazem `session.commit()`. `api/deps.py` = autenticação central. |
| `backend/app/cli.py` | `python -m app.cli migrate / bootstrap / init / seed-dev / ensure-partitions` (`init` = admin + marcas + perfis, sem exemplos). |
| `backend/app/api/` | Processo da API REST (porta 8000). `create_app()` é fábrica (`uvicorn --factory`). |
| `backend/app/worker/` | Jobs agendados (APScheduler). `python -m app.worker.main`. |
| `backend/alembic/` | Migrações (a URL vem de `DATABASE_URL`, nunca do `alembic.ini`). |
| `backend/tests/` | pytest contra o PostgreSQL real (`dati_test`). |
| `backend/app/services/{discoveries,counter_lines,supply_replacements,printer_alerts,custom_fields,permissions}.py` | Auditoria do Datacount (PROMPT seção 16): Descobertas, contadores como linhas (`reading_counters`), trocas de suprimento, alertas da `prtAlertTable`, campos personalizados e matriz de permissões por revenda. |
| `backend/app/services/{alert_rules,alert_engine,notifications,notifiers,forecast,retention,alerts_portal}.py` | Alertas e notificações (Fase 6): regras centralizadas, motor de avaliação (job de 1 min), fila e entrega (SMTP, webhook, WhatsApp), previsão de toner, retenção e as telas de alertas, trocas e alertas da impressora. |
| `backend/app/services/reports/` | Relatórios (Fase 7): `base.py` (filtros, registro, escopo), `counters.py` (**regra única de contadores**: leituras válidas, produção por pares, leitura de corte — usada por relatórios, dashboard, API do ERP e conector), `definitions.py` (os 18 relatórios) e `render.py` (CSV/XLSX/PDF com reportlab). |
| `backend/app/services/{erp_api,erp_connector}.py` + `api/erp.py` | API somente leitura do ERP (`/api/erp/v1`, token `dmerp_` com hash) e conector Dataclassic (fila `erp_queue`, transportes arquivo/HTTP/e-mail). Formato em `docs/erp-dataclassic.md`. |
| `backend/app/services/{profiles,customer_import,sites_map,web_access}.py` | Perfis de modelos (versões, publicar/ativar, explorador de walk, gravação de teste), importação de clientes por CSV, mapa dos locais e sessões de acesso à página web da impressora. |
| `backend/app/gateway/devweb.py` + `agent/internal/webproxy/` | Túnel da página web da impressora (4.9): `/devweb/{token}/` no gateway, quadros `web_request`/`web_response`/`web_chunk` no WebSocket, reescrita de links/cookies e isolamento por `CSP sandbox`. |
| `backend/app/services/{assignments,transfers}.py` | Histórico de cliente/local do equipamento (`device_assignments`; quem muda o local chama `assignments.move`) e Descobertas > Transferências (equipamento que apareceu em outro cliente: aprovar ou manter). Relatórios com filtro de cliente usam `reading_window` (só as leituras do período com aquele cliente). |
| `backend/app/services/{park,dashboard,agent_ops,live}.py` | Tela de parque, dashboard, operações de coletor (Reativar, cluster, comandos em massa) e eventos ao vivo (SSE `/api/v1/events` sobre LISTEN/NOTIFY). |
| `frontend/` | Portal React 18 + TS + Vite + Tailwind 4. Testes: Vitest (`src/**/*.test.tsx`) e Playwright (`e2e/`). |
| `frontend/src/api/` | `openapi.json` (gerado da API por `scripts/gen_openapi.py`) e `schema.d.ts` (gerado por `npm run gen:api`). **Não editar à mão.** |
| `frontend/src/components/{paging,pickers}.tsx` + `lib/paging.ts` | Lista por cursor com "Carregar mais" (`useCursorList` + `LoadMore`) e seletores com busca no servidor (`CustomerPicker`, `SitePicker`, `CompanyPicker`). **Use sempre estes**: nenhuma tela baixa lista inteira nem tem limite fixo (regra 13). |
| `frontend/src/lib/` | `api.ts` (cliente openapi-fetch com refresh e CSRF), sessão (`AuthProvider`/`auth-context`), `live.ts` (SSE), `format.ts` (pt-BR/São Paulo), rótulos. |
| `frontend/src/{components,pages}/` | UI (`components/ui` = primitivos Radix; `domain.tsx` = status, níveis, comandos) e telas por menu. Rotas em `src/router.tsx`. |
| `profiles/` | Perfis de leitura YAML. `canon.yaml` e `konica-minolta.yaml` são fornecidos: **não alterar OIDs**. |
| `profiles/recordings/sim/NN-nome/public.snmprec` | Impressoras simuladas (snmpsim), porta UDP `1160+NN`. **Geradas** por `generate.py` (edite o gerador, não o arquivo). Pasta com `sleepy.json` = economia de energia (proxy UDP na porta `1160+NN`, snmpsim em `11100+NN`). |
| `profiles/recordings/real/` | Walks das impressoras reais (Fase 10: C4065 + Fiery, C287, C454e, Kyocera M3550idn), com `<nome>.expected.json`. Rodam no `TestRealRecordings`, `TestStatusOfRealPrinters` e no teste de interfaces do coletor. Detalhes em `docs/validacao-real.md`. |
| `scripts/` | PowerShell do dia a dia + `smtp_catcher.py`. |
| `deploy/` | Dockerfiles, compose e Caddyfile da hospedagem futura (**não usados agora**). |
| `installer/` | Fase 8: `windows/dati-monitor.iss` (Inno Setup; recusa Windows antigo, confere o código sem gastá-lo, instala os 2 serviços) e `linux/` (postinst/prerm/postrm do .deb e o modelo do `install.sh`, servido preenchido em `/api/public/install.sh`). Uso em `installer/README.md`. |
| `backend/app/services/installers.py` + `api/v1/installers.py` | Downloads: instaladores publicados (superadmin) e o link público do instalador, válido só com código de cadastro vigente. |
| `agent/internal/usbprint/` + `collector/usb.go` | Impressoras USB (Fase 9): lista pelo WMI (`Win32_Printer` em portas `USB*`), contador por PJL (`@PJL INFO PAGECOUNT`) quando a impressora responde; roda em todo PC com coletor (não só no MASTER). |
| `backend/app/services/computers.py` + `api/v1/computers.py` | Computadores (PCs com coletor), impressoras USB de cada um e **leitura manual** (`/devices/{id}/manual-readings`, mesma regra de contadores; menor que a última é recusada). |
| `docs/` | Arquitetura, protocolo, `operacao.md` (instalar no cliente, modelo novo com walk, publicar versão, restaurar backup) e `piloto.md` (roteiro de validação com o Datacount). |
| `var/` | Gerado em execução (logs, e-mails capturados, cache do snmpsim). Ignorado pelo git. |

## Comandos (terminal comum, sem administrador)

```powershell
scripts\setup-db.ps1        # cria papel "dati" e bancos dati_dev/dati_test (idempotente)
scripts\init-env.ps1 -PostgresPassword X   # cria .env com segredos aleatórios (instalação nova)
scripts\dev.ps1             # migra, faz seed e sobe API, gateway, worker, portal, smtp_catcher e snmpsim; Ctrl+C encerra
scripts\lan-setup.ps1       # teste em rede local: IP no .env + Firewall "Dati Monitor dev" (Privada); depois dev.ps1 -Lan
scripts\reset-dev-db.ps1    # zera o dati_dev (pede ZERAR), recria o admin e grava DEV_SEED=false (sem clientes de exemplo)
scripts\stop-dev.ps1        # encerra o dev.ps1 (se a janela foi fechada sem Ctrl+C)
scripts\test.ps1            # Go (-race, cobertura >= 80% internal/), pytest (>= 80%), Vitest
scripts\test.ps1 -E2E       # + Playwright
scripts\lint.ps1            # golangci-lint, ruff, mypy --strict, eslint, prettier, tsc
scripts\build-agent.ps1 [-Version x.y.z]   # 3 binários x 7 alvos em dist\
scripts\build-installer.ps1 -Version x.y.z -Server URL [-Sign]   # setup.exe (Inno Setup) em dist\installers\
.venv\Scripts\python scripts\build_linux.py --version x.y.z      # .deb e .tar.gz por arquitetura
scripts\test-installer.ps1 -Server URL -Code XXXXXXXX [-Full]  # -Full instala de verdade (como administrador)
scripts\chaos.ps1 [-OutageMinutes 10]      # teste de caos (seção 13); como administrador reinicia também o PostgreSQL
scripts\soak.ps1 [-Minutes 30]             # resistência: dm-agent real contra o dev.ps1 (no ar); 24 h = -Minutes 1440
scripts\load.ps1                           # carga: banco dati_load próprio, API 8200/gateway 8201, 500 WebSockets, 20.000 equipamentos, parque < 1 s
scripts\backup.ps1 [-OutDir D:\backups] [-Keep 14]          # pg_dump + arquivos + manifesto conferido
scripts\restore.ps1 -Manifest <json> -Database <banco> [-Force] [-RestoreFiles]
scripts\acceptance.ps1 [-Skip soak,load]  # aceitação (seção 15): tudo do zero, OK/FALHOU por critério; ~3 h
scripts\release.ps1 -Version x.y.z     # pacote da versão em dist\release-x.y.z (binários, assinaturas, instaladores, portal, SHA256SUMS)
# Publicar versão: build com -Version x.y.z → dm-tool sign --file <binário> --version x.y.z → colar a saída em Versões (superadmin)
```

Instalação do zero: `py -3.12 -m venv .venv`, `.venv\Scripts\pip install -r backend\requirements-dev.lock`,
`.venv\Scripts\pip install --no-deps -e backend`, `cd frontend; npm ci; npx playwright install chromium`,
copiar `.env.example` para `.env` e rodar `scripts\setup-db.ps1`.

Dependências Python: declare em `backend/pyproject.toml` e regenere os locks com
`.venv\Scripts\pip-compile --strip-extras -o requirements.lock pyproject.toml` e
`... --extra dev -o requirements-dev.lock pyproject.toml` (dentro de `backend/`).

## Convenções

- Código (identificadores, tabelas, colunas) em **inglês**; interface, mensagens, logs voltados ao
  usuário e documentação em **português do Brasil**.
- Horários sempre em UTC (`timestamptz`; bancos com `timezone=UTC`); exibição em `America/Sao_Paulo`.
- **Erros nunca silenciosos**: toda falha aparece na tela (portal/scripts) e no log/`console.error`.
  Nada de `except: pass`, `catch {}` vazio ou `2>$null` sem checar o código de saída.
- Segredos só no `.env` (não versionado) / variáveis de ambiente.
- Proibido stub, `pass` no lugar de lógica, `NotImplementedError`, endpoint com dado fixo, tela com dado de exemplo.
- Nunca inventar OIDs de fabricante (só seção 6.2 do PROMPT e os perfis fornecidos).
- Teste que falha → corrigir o código; nunca pular/afrouxar o teste.
- Scripts `.ps1` em **UTF-8 com BOM** (o PowerShell 5.1 lê sem BOM como ANSI e estraga acentos).
  No PS 5.1 com `ErrorActionPreference=Stop`, stderr redirecionado de programa nativo vira exceção:
  use `Continue` localmente e cheque `$LASTEXITCODE` (veja `setup-db.ps1`).
- Testes que sobem subprocessos: mande a saída para arquivo, nunca `PIPE` não lido (trava no Windows).
- Mudou `product.json`? Rode `go generate ./...` em `agent/` e faça commit do `product_gen.go`.
- Mudou modelo? `cd backend; ..\.venv\Scripts\alembic revision --autogenerate -m "..."`, revise o arquivo
  (FKs circulares, SQL próprio) e rode `alembic check` (um teste falha se modelo e migração divergirem).
- Rotas nunca consultam dados de tenant sem passar pelo serviço (que aplica o escopo). Erros via
  `app.core.errors` (`not_found`, `forbidden`, `conflict`, `bad_request`) com mensagem em português.
- Novas listas: paginação por cursor (`services/pagination.py`) e exportação (`services/export.py`) sem limite fixo;
  no portal, `useCursorList<Tipo>(...)` + `<LoadMore>` e, para escolher cliente/local/empresa, os pickers com busca.
- Permissões: matriz `módulo.read|create|update|delete` + `supplies.monitor` (`core/permissions.py`); a revenda
  ajusta os papéis operacionais (`services/permissions.py`). Use a ação certa (incluir/alterar/excluir), não `write`.
- Equipamento novo nasce `pending` (Descobertas); parque, dashboard, relatórios e alertas filtram
  `discovery_state == 'approved'`. Nos testes, `factory.tenant()` cria o local com ativação automática
  (use `auto_activate=False` para testar Descobertas).
- Mudou uma lista de CHECK (`one_of`) de uma tabela existente? O autogenerate não detecta: recrie a
  constraint na migração à mão.
- Testes de API usam as fixtures `client`, `factory` e `login()` de `tests/conftest.py`; o banco
  `dati_test` é recriado por sessão e esvaziado a cada teste (`clean_db`).
- Primeiro start: a senha temporária do `admin@local` aparece no console do dev.ps1/API.
- Edições complexas por script: grave o script em arquivo (heredocs longos no bash quebram neste ambiente).
- Novo comando remoto: modelo de parâmetros em `schemas/commands.py` (`PARAMS_BY_TYPE`, `COMMAND_LABELS`),
  preparo opcional em `services/commands.py` (`_PREPARERS`) e handler em `agent/internal/agent/commands.go`
  (`commandSpecs`). Todo comando precisa de teste nos dois lados.
- Canais `pg_notify` só em `app/core/notify.py`; o `notify` vai na mesma transação da mudança.
- Alerta novo: abra com `services.alerts.open_alert` (dedup por `dedup_key`); a notificação sai sozinha pela
  fila (`notified_at`). Tipo novo de regra: `ALERT_RULE_TYPES` + migração do CHECK, `PARAMS_BY_TYPE`/`RULE_LABELS`
  em `schemas/alerts.py`, `DEFAULT_RULES` e a condição em `alert_engine.py` (e em `ENGINE_TYPES` se o motor resolve).
- Notificador novo: implemente o protocolo `Notifier` em `services/notifiers.py` e registre em `build_notifier`;
  falha sempre como `NotifyError` com mensagem em português.
- `dev.ps1` sobe cada serviço no próprio console oculto: não volte para `-NoNewWindow` (o reload do
  uvicorn manda CTRL_C para o console inteiro).
- Protocolo do agente: mudou `app/schemas/agent.py`? Atualize `agent/internal/protocol` e rode
  `.venv\Scripts\python scripts\gen_protocol_docs.py` (um teste compara `docs/protocol.md`).
- Mudou a API (rota ou schema)? `.venv\Scripts\python scripts\gen_openapi.py` e `cd frontend; npm run gen:api`;
  o lint e o CI falham se `openapi.json`/`schema.d.ts` estiverem desatualizados. Campo com default numa
  **resposta**: marque o modelo com `json_schema_serialization_defaults_required=True` (senão o TS o vê opcional).
  Checagem de tipos do portal: `npm run typecheck` (`tsc -b`). `npx tsc --noEmit` na raiz do frontend **não
  verifica nada** (o `tsconfig.json` só tem referências).
- Portal: hooks e componentes em arquivos separados (regra `react-refresh`); nada de `setState` síncrono em
  `useEffect` (use `key` para remontar formulários); falhas via `showError` (toast + `console.error`).
- E2E: o `global-setup` roda `scripts/e2e_seed.py` (só dev/CI) com senha aleatória por execução, e
  os testes rodam em série. Leituras de teste precisam de chave de idempotência nova (`agent_id:uuid`).
- Mensagem nova no protocolo (Python + Go): o teste `TestMessagesMatchServerSchemas` confere o Go contra os
  schemas gerados; coleção com padrão no servidor vai `omitempty`, coleção obrigatória nunca vai `null` (D59).
- A chave PRIVADA de versões fica fora do repositório (`%USERPROFILE%\.dati-monitor\release-signing.key`);
  nunca a copie para o projeto, para o servidor ou para logs.
- Contadores (produção, corte, cobrança, ERP): use sempre `services/reports/counters.py`; nunca some contador
  por conta própria (regressão, ajuste manual e troca de placa já estão tratados lá).
- Relatório novo: função em `services/reports/definitions.py` que devolve `ReportData` (colunas tipadas) e
  `register(ReportDef(...))`; a tela, a paginação e as exportações já funcionam.
- Instalador Windows: compile com `scripts\build-installer.ps1` (versão e nomes vêm do `product.json`); o
  instalador de teste (`-TestMode`) roda sem administrador e é usado pelo `test-installer.ps1`. Rodando o
  setup.exe pelo Git Bash, `/VERYSILENT` vira caminho: use `MSYS_NO_PATHCONV=1` ou rode pelo PowerShell.
- Pascal do Inno Setup: `Out` é palavra reservada; a saída dos dm-* é UTF-8 (use `Utf8ToString` do .iss).
- Página web da impressora: a resposta do túnel **sempre** sai com `Content-Security-Policy: sandbox` sem
  `allow-same-origin` (a página da impressora não pode agir como o usuário do portal). Não remova.
- Go: o lint roda também com `GOOS=linux` (arquivos `_windows.go`/`_other.go`); testes que falam SNMP de
  verdade levam `//go:build integration` e usam `internal/simtest`. Rodar `-race` exige o GCC no PATH
  (o `test.ps1` acha o WinLibs sozinho).

## Ambiente desta máquina

Windows 10 Pro **sem virtualização**: nada de Docker/WSL. PostgreSQL 16 nativo (serviço
`postgresql-x64-16`, início automático). Go 1.27, Python 3.12 (`py -3.12`; o `python` padrão é 3.11),
Node 24 LTS, golangci-lint v2 em `%GOPATH%\bin`, GCC (WinLibs, via winget) para `go test -race`.
Portas: API 8000, gateway 8001 (também `/devweb`, que o Vite repassa), portal 5173, SMTP 1025, e-mails 8025, snmpsim 1161–1168
(E2E: 12161–12168, pronto em 12160), página de impressora simulada 8080 (`scripts/printer_web_sim.py`).
A sessão do Claude Code aqui roda com `__COMPAT_LAYER=Win7RTM` herdado pelos processos filhos: o
`RtlGetVersion` responde 6.1. Por isso o agente usa `RtlGetNtVersionNumbers` (D49).

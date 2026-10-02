# PROGRESS — Dati Monitor

Estado das fases da seção 14 do `PROMPT.md`.

| Fase | Situação |
|---|---|
| 0 — Fundação | ✅ concluída (24/09/2026) |
| 1 — Backend núcleo | ✅ concluída (24/09/2026) |
| 2 — Agente núcleo | ✅ concluída (25/09/2026) — falta só instalar o serviço Windows num terminal de administrador (ver abaixo) |
| 3 — Tempo real e comandos | ✅ concluída (25/09/2026) |
| 4 — Portal | ✅ concluída (27/09/2026) |
| 5 — Confiabilidade | ✅ concluída (28/09/2026) — falta só instalar os serviços num terminal de administrador |
| 5.1 — Auditoria do Datacount (ajustes nas fases concluídas) | ✅ concluída (29/09/2026) |
| 6 — Alertas e notificações | ✅ concluída (29/09/2026) |
| 7 — Perfis, relatórios e acesso web | ✅ concluída (02/10/2026) |
| 8 — Instaladores | ✅ concluída (02/10/2026) — falta só o teste `-Full` num terminal de administrador |
| 9 — USB e acabamento | ✅ concluída (02/10/2026) |
| 10 — Rede real | ⏳ próxima (precisa do usuário: faixa de IP, comunidade SNMP e folhas de contadores) |
| 11 — Aceitação final | pendente |

> Modo de trabalho: o usuário pediu para executar todas as fases em sequência, sem parar entre elas
> (decisão D11). O plano de cada fase fica registrado aqui; paradas só onde o PROMPT exige o usuário
> (Fase 10: faixa de IP e folhas de contadores; instalação de serviços Windows: terminal de administrador).

---

## Fase 0 — Fundação ✅

### O que foi feito
- **Ambiente** (seção 0.1): PostgreSQL 16.15 nativo (serviço `postgresql-x64-16`, início automático;
  senha do `postgres` só no `.env`), Node 24.19 LTS (substituiu o 20.20, fora de suporte),
  golangci-lint v2.14.0 (compilado com Go 1.27), GCC WinLibs (para `go test -race`), Chromium do
  Playwright, venv `.venv` com Python 3.12.10.
- **Monorepo** conforme a seção 2 do PROMPT, com `product.json` como fonte única do nome do produto.
- **Agente (Go 1.27)**: `dm-agent`, `dm-watchdog`, `dm-tool` com `version`/ajuda; pacote `product`
  (serviços `DatiMonitorAgent`/`DatiMonitorWatchdog`, pasta de dados Windows/Linux) gerado de
  `product.json`; build para 7 alvos.
- **Backend (FastAPI 0.141 / SQLAlchemy 2.0.54 / asyncpg)**: processos API (8000), gateway (8001) e
  worker (APScheduler 3.11); health com teste real no PostgreSQL (503 + erro quando o banco cai);
  logging JSON; Alembic assíncrono configurado (sem migrações ainda).
- **Portal (React 18.3 + Vite 8 + TS 6 + Tailwind 4)**: página inicial que consulta `/api/health` pelo
  proxy do Vite e mostra o estado; erro aparece na tela e no `console.error`.
- **Scripts**: `setup-db.ps1`, `dev.ps1` (supervisiona todos os processos), `test.ps1`, `lint.ps1`,
  `build-agent.ps1`, `smtp_catcher.py` (SMTP 1025 + lista HTTP 8025).
- **Simulador**: impressora simulada `05-generica` (só Printer-MIB) servida pelo snmpsim na porta 1165.
- **CI** (`.github/workflows/ci.yml`): job Linux (Postgres como *service*, lint + testes + E2E) e job
  Windows (build dos 7 alvos + `go test -race`).
- **Deploy futuro**: `deploy/Dockerfile.backend`, `Dockerfile.web`, `docker-compose.yml`, `Caddyfile`.

### Testes (todos passando)
| Suíte | Resultado |
|---|---|
| Go `go test -race` | 3 pacotes ok; cobertura de `internal/` 86% |
| pytest (PostgreSQL real `dati_test`) | 22 testes; cobertura 93% |
| Vitest | 7 testes |
| Playwright E2E | 1 teste (portal abre, API conectada, sem erros no console) |
| Lint | golangci-lint 0 issues; ruff ok; mypy --strict ok; eslint/prettier/tsc ok |

### Fluxo manual executado (24/09/2026) e resultado
1. `scripts\setup-db.ps1` duas vezes → 1ª cria papel e bancos; 2ª informa "já existe" (idempotente). ✅
2. `scripts\lint.ps1` → "Lint completo sem problemas". ✅
3. `scripts\test.ps1` → "Todos os testes passaram"; `scripts\test.ps1 -E2E` → Playwright ok. ✅
4. `scripts\build-agent.ps1` → 21 binários em `dist\`; `dist\windows-amd64\dm-agent.exe version` →
   `Dati Monitor dm-agent 0.0.0-dev (commit sem-commit, windows/amd64, go1.27.0)`. ✅
5. `scripts\dev.ps1` → todos os processos no ar e:
   - `GET http://127.0.0.1:8000/api/health` → `status=ok`, PostgreSQL 16.15, latência ~2 ms ✅
   - `GET http://127.0.0.1:8001/health` → `status=ok`, `service=gateway` ✅
   - `http://localhost:5173` → HTTP 200; `/api/health` via proxy do Vite → ok ✅
   - e-mail SMTP para `127.0.0.1:1025` → aparece em `http://127.0.0.1:8025/api/messages` ✅
   - SNMP GET `sysObjectID` em `udp 127.0.0.1:1165` → `1.3.6.1.4.1.8072.3.2.10` ✅
   - Playwright contra o ambiente já no ar → ok ✅
   - Matar o `smtp_catcher` → dev.ps1 mostra "smtp terminou inesperadamente", encerra todos os
     processos e libera as portas 8000/8001/5173/8025/1165 ✅

### Como testar
```powershell
scripts\setup-db.ps1
scripts\lint.ps1
scripts\test.ps1 -E2E
scripts\build-agent.ps1
scripts\dev.ps1     # depois abra http://localhost:5173 (deve mostrar "API: conectada") e http://127.0.0.1:8025
```

### O que falta (fica para as próximas fases)
- Tudo das Fases 1–11. O CI está escrito, mas **ainda não rodou**: o repositório não tem remoto no
  GitHub. Quando for publicado, o primeiro push executa os jobs Linux e Windows.

## Fase 1 — Backend núcleo ✅

### Plano executado
Esquema completo da seção 3 em uma migração; partições mensais; autenticação do portal; papéis e
escopo em dependência central; CRUD de revendas/empresas/clientes/locais/usuários; auditoria; seed.

### O que foi feito
- **Modelos (SQLAlchemy 2, tipados)** para todas as tabelas da seção 3 (`app/models/*`), mais as de
  apoio: `refresh_tokens`, `password_reset_tokens`, `reading_idempotency`, `reading_reviews`,
  `reading_discards`, `cluster_events`, `erp_tokens`. `eager_defaults` no modelo base (valores do
  servidor voltam via RETURNING — sem I/O implícito no async).
- **Migração inicial** (`alembic/versions/*_esquema_inicial.py`): 57 tabelas; `readings`,
  `supply_readings` e `agent_heartbeats` particionadas por mês (limites em UTC explícito) com partição
  `default` de segurança; função `dm_ensure_month_partition` (cria a partição e **move** para ela as
  linhas que tinham caído na default — nenhuma leitura se perde); triggers que bloqueiam
  UPDATE/DELETE/TRUNCATE em `readings` e `audit_log` (inclusive em cada partição nova).
  `alembic check` limpo; downgrade testado.
- **Autenticação**: argon2id; JWT de acesso (15 min) com "versão da senha" embutida (trocar a senha
  derruba na hora todos os tokens anteriores); refresh de 7 dias rotativo em cookie httpOnly
  (`SameSite=Strict`, `Path=/api/v1/auth`) com CSRF double-submit (`dm_csrf` + `X-CSRF-Token`); reuso de
  refresh revoga a família (com tolerância de 10 s para duas abas); bloqueio após 10 falhas (15 min);
  rate limit em memória no login; TOTP (segredo cifrado AES-GCM, janela ±1, proteção contra reuso do
  código); TOTP obrigatório para `reseller_admin` quando a revenda configura
  `security.require_totp_for_admins`; troca obrigatória de senha no primeiro acesso (token "limitado");
  "esqueci a senha" por e-mail (sem revelar se o e-mail existe).
- **Autorização**: `Principal` + `reseller_scope`/`customer_scope` (`app/core/principal.py`) aplicados em
  toda consulta de serviço; papéis `superadmin`, `reseller_admin`, `operator`, `technician`,
  `customer_viewer` com permissões por ação (`app/core/permissions.py`, espelhadas nas tabelas
  `roles`/`role_permissions` no bootstrap). Hierarquia: ninguém atribui papel acima do seu; ninguém se
  desativa, rebaixa ou exclui.
- **API** (`/api/v1`): auth (login, refresh, logout, me, preferências, troca/redefinição de senha, TOTP),
  revendas, empresas, clientes (com exportação CSV/XLSX), locais, usuários (com exportação, reset de senha
  temporária ou por e-mail, reset de TOTP, desbloqueio), papéis, auditoria (com exportação). Paginação por
  cursor com ordenação no servidor; erros JSON com código estável e mensagem em português;
  `charset=utf-8` em toda resposta; cabeçalhos de segurança (HSTS em produção).
- **Auditoria**: toda escrita grava `audit_log` na mesma transação, com antes/depois (só os campos
  alterados, nunca segredos) e IP.
- **Bootstrap/seed**: no primeiro start cria a revenda e o superadmin `admin@local` com senha temporária
  exibida no console (troca obrigatória); `python -m app.cli seed-dev` cria 1 empresa, 2 clientes, 2 locais.
- **CLI** `python -m app.cli migrate | bootstrap | seed-dev | ensure-partitions`.
- **Worker**: job diário de partições (mês anterior + 3 à frente; avisa se houver linhas na default).
- **Scripts**: `dev.ps1` roda migração + seed antes de subir e registra os PIDs (`var\dev-pids.json`):
  se a janela for fechada sem Ctrl+C, a próxima execução encerra as sobras sozinha; novo `stop-dev.ps1`;
  novo `init-env.ps1` (gera `.env` com segredos aleatórios); `Invoke-Checked` julga pelo código de saída
  e sempre mostra o stderr.

### Testes (todos passando)
| Suíte | Resultado |
|---|---|
| pytest (PostgreSQL real; esquema recriado com upgrade → downgrade → upgrade) | 69 testes; cobertura 92% (api ≥ 89%, services ≥ 83%) |
| Isolamento multi-revenda | leitura/edição/exclusão/criação cruzada → 404/403; listas, auditoria e exportações filtradas; usuário com escopo de cliente só vê o próprio cliente |
| Go / Vitest / Playwright | inalterados, passando |
| Lint | golangci-lint, ruff, mypy --strict (app + tests), eslint, prettier, tsc: sem problemas |

### Fluxo manual executado (24/09/2026) e resultado
1. `scripts\dev.ps1` → migração + seed; o console mostrou "PRIMEIRO ACESSO … admin@local / senha temporária". ✅
2. Login com a senha temporária → `limited=password_change_required`; `/customers` com esse token → 403. ✅
3. Troca de senha → sessão completa; clientes do seed listados (CLI-0001, CLI-0002). ✅
4. Cliente "Clínica Manual" criado (CNPJ formatado → gravado só com dígitos; acento conferido no banco em UTF-8). ✅
5. Local criado; a auditoria mostrou create/site, create/customer, auth.change_password, auth.login. ✅
6. Refresh via cookie + cabeçalho CSRF → novo token. ✅ Exportação XLSX → 200, arquivo `clientes-AAAAMMDD-HHMM.xlsx`. ✅
7. `dev.ps1` fechado à força (sem Ctrl+C) → a execução seguinte avisou "6 processo(s) de uma execução
   anterior … foram encerrados" e subiu; `stop-dev.ps1` encerrou tudo e liberou as portas. ✅

### Como testar
```powershell
scripts\test.ps1 -E2E
scripts\dev.ps1        # anote a senha temporária do admin@local exibida no console (1º start)
# http://127.0.0.1:8000/docs → POST /api/v1/auth/login, depois /auth/change-password, /customers ...
scripts\stop-dev.ps1   # para encerrar sem Ctrl+C
```

## Fase 2 — Agente núcleo ✅

### Plano executado
Protocolo agente↔servidor v1 (documentado e testado dos dois lados); agente Go com cadastro, credencial
protegida, configuração em cache, descoberta, motor de perfis, leituras independentes, fila SQLite e envio
idempotente; ingestão no backend com as validações da seção 6.5; as 8 impressoras simuladas da seção 13.

### O que foi feito
- **Protocolo v1** (`docs/protocol.md` + `docs/protocol-schemas/*.json`, gerados de
  `app/schemas/agent.py` por `scripts/gen_protocol_docs.py`; um teste falha se a documentação ficar
  desatualizada): `enroll`, `token` (HMAC com proteção de relógio e de nonce), `heartbeat`, `config`,
  `ranges/suggest`, `readings` (lotes gzip de até 500 itens, 16 MB).
- **Backend**:
  - Cadastro de coletores no portal (código de 8 caracteres, 7 dias, uso único; comando e instruções
    em português), revogação e exclusão.
  - Faixas de IP com várias portas SNMP por faixa (`ports`), aprovação de faixas sugeridas pelo agente.
  - Credenciais SNMP v1/v2c/v3 cifradas (AES-GCM, AAD por local); a API nunca devolve o segredo, só
    uma dica (`p…c`).
  - Heartbeat com lease de MASTER por local; `config_version` incrementada a cada mudança de faixa,
    credencial ou local.
  - **Ingestão** (`services/ingest.py`) idempotente por `agent_id:seq`:
    - identificação por serial (`discovered`, `ip_changed`, `moved_site`, `replaced`, `reactivated`);
    - validações 6.5: regressão de contador vira alerta e fica fora do relatório; salto acima de
      50 mil páginas/dia; soma PB+cor fora da tolerância;
    - anti-duplicidade em meio intervalo; `read_at` no futuro é limitado ao horário do servidor;
    - suprimentos, status e eventos.
  - Leituras de equipamentos para o portal (`/api/v1/devices`, leituras, suprimentos, eventos).
- **Agente** (`agent/internal/*`):
  - `snmp` (gosnmp; v1/v2c/v3 SHA/SHA256 + AES/AES256, GET em blocos, BulkWalk).
  - `profile`: motor de perfis com `oid`, `sum`, `first_of`, `expr`, `named_table`,
    `counter_sources` com `detect_oid`, `store_all_rows_in_extra`, `walk_subtree_to_extra`, regras
    mono-only e tolerância de soma; os perfis Canon/Konica fornecidos são usados **sem alteração**.
  - `printer` (identidade, status normalizado 6.3 com bits de erro MSB-first, suprimentos com %).
  - `discovery` (CIDR/intervalo/exclusões/portas, concorrência + limite de pacotes/s, credencial
    conhecida primeiro).
  - `collector`: só o MASTER varre e lê; atributos, contadores, suprimentos e status em intervalos
    independentes; 3 tentativas com 2 min entre elas; `read_failed` 1×/dia; status só quando muda +
    confirmação a cada 1 h; troca de serial no mesmo IP relê a identidade.
  - `store` (SQLite WAL, `synchronous=FULL`): fila com retenção que descarta suprimentos antes de
    contadores, dead-letter para rejeitados.
  - `uploader` (lotes de 500, backoff 1 s → 60 s).
  - `api` (HTTPS obrigatório fora de localhost; proxy manual → variáveis de ambiente → WinHTTP;
    correção de relógio; renovação de token no 401).
  - `health` (`127.0.0.1:47701`, 503 quando um laço para).
  - `svc` (serviço Windows com início automático atrasado e recuperação 5 s/5 s/30 s; unit systemd
    `Type=notify` + `WatchdogSec=60`).
  - `secret` (DPAPI escopo máquina), `osinfo` (recusa Windows 7/8/2012 com mensagem clara;
    impede suspensão quando configurado), logs JSON rotativos em UTC.
- **CLIs**:
  - `dm-agent enroll | run | service | install | uninstall | start | stop | restart | status`;
  - `dm-tool walk` (gera `.snmprec` idêntico ao do simulador).
- **Simulador** (`profiles/recordings/sim/generate.py`, OIDs exatamente os dos perfis fornecidos):
  - `01` Canon cor (tabela por ID), `02` Canon PB (tabela nomeada em hex);
  - `03` Konica cor (217031 = 100150 + 116881), `04` Konica PB;
  - `05` genérica, `06` economia de energia (proxy UDP "sonolento" que só responde na 2ª tentativa);
  - `07` atolamento + porta aberta + toner −3;
  - `08` regressão (`regressed.snmprec.txt` trocado no meio do teste).
  - `dev.ps1` sobe o proxy para as pastas com `sleepy.json`.

### Testes (todos passando)
| Suíte | Resultado |
|---|---|
| Go `go test -race -tags integration` | 21 pacotes ok; cobertura de `internal/` **85,3%**; integração contra o snmpsim real (perfis, status/erros, economia de energia, SNMPv3 SHA256/AES256, descoberta em portas diferentes, regressão, walk ida e volta) |
| pytest (PostgreSQL real) | 111 testes; cobertura 92% (protocolo do agente, ingestão e validações 6.5, credenciais cifradas, isolamento, gravações do simulador, proxy sonolento, documentação do protocolo) |
| Vitest / Playwright | 7 / 1, passando |
| Lint | golangci-lint 0 issues (Windows **e** `GOOS=linux`); ruff; mypy --strict; eslint/prettier/tsc |

### Fluxo manual executado (25/09/2026) e resultado
1. `scripts\dev.ps1` → API, gateway, worker, portal, SMTP, 8 snmpsim (1161–1168; a 1166 atrás do proxy
   sonolento). ✅
2. Pela API: coletor criado no local "Filial" → código `ZG3NH6DP` + comando + instruções em português;
   faixa `127.0.0.1/32` com portas 1161–1168 → `approved`. ✅
3. `dm-agent enroll --server http://127.0.0.1:8000 --code … --data-dir var\agent1` → cadastrado; a
   pasta tem `config.json` e `credential` (DPAPI). Reusar o código → "Código de cadastro inválido, já
   usado ou expirado" (saída 1). ✅
4. `dm-agent run` → MASTER, configuração v2 aplicada. O local (criado na Fase 1) não tinha credencial
   SNMP → log ERROR "nenhuma credencial SNMP configurada para o local" (falha visível, não silenciosa). ✅
5. Credencial `public` adicionada pelo portal → config v3 aplicada, **mas a varredura só voltaria em 6 h**.
   **Defeito encontrado e corrigido**: credencial nova agora dispara varredura imediata (teste
   `TestNewCredentialOrRangeTriggersRescan`). Agente recompilado e reiniciado → 8 impressoras
   encontradas em 2 s. Log com horário em UTC (outra correção: estava em −03:00). ✅
6. `/api/v1/devices` no portal:

   | Porta | Serial | Modelo | Perfil / fonte | Status | Total | PB | Cor |
   |---|---|---|---|---|---|---|---|
   | 1161 | SIMCAN0001 | iR-ADV C5540 | canon / `canon_id_table` | ready | 150000 | 90000 | 60000 |
   | 1162 | SIMCAN0002 | iR 1643i | canon / `canon_named_table` | ready | 45678 | 45678 | 0 |
   | 1163 | A797019500624 | bizhub C287 | konica-minolta / `konica_counters` | ready | **217031** | **100150** | **116881** |
   | 1164 | SIMKM0004 | bizhub 367 | konica-minolta / `konica_counters` | ready | 88000 | 88000 | 0 |
   | 1165 | SIMGEN0005 | Generic 5000 | generic / `standard` | ready | 48213 | — | — |
   | 1166 | SIMSLEEP06 | Generic 6000 | generic / `standard` | **energy_saving** (não offline) | 12000 | — | — |
   | 1167 | SIMERR07 | Generic 7000 | generic / `standard` | error (bits 48) | 77000 | — | — |
   | 1168 | SIMREG08 | Generic 8000 | generic / `standard` | ready | 500000 | — | — |

   Coletor `online`, versão `0.2.0-dev`, config aplicada = config do servidor; eventos `discovered`;
   suprimentos da Konica C287 com os níveis do arquivo (25/55/66/5 %). ✅
7. Segunda credencial adicionada → config v4 aplicada e varredura concluída 2 s depois (`novas=0`, sem
   duplicar). ✅
8. `GET http://127.0.0.1:47711/health` do agente → `ok`, três laços saudáveis, fila 0, sem erros. ✅
9. `dm-tool walk --ip 127.0.0.1 --port 1163` → 62 OIDs, **idêntico** à gravação `03-konica-cor`. ✅

### Como testar
```powershell
scripts\test.ps1 -E2E                      # Go (race + integração snmpsim), pytest, vitest, Playwright
scripts\dev.ps1
# Portal/API: login → POST /api/v1/agents {site_id, name} → anote o código;
#             POST /api/v1/sites/{id}/ip-ranges {"cidr":"127.0.0.1/32","ports":[1161,...,1168]};
#             POST /api/v1/sites/{id}/snmp-credentials {"version":"v2c","community":"public"} (locais antigos)
cd agent; go build -o ..\var\bin\dm-agent.exe .\cmd\dm-agent; cd ..
var\bin\dm-agent.exe enroll --server http://127.0.0.1:8000 --code CODIGO --data-dir var\agent1 --health-addr 127.0.0.1:47711
var\bin\dm-agent.exe run --data-dir var\agent1
# GET /api/v1/devices?site_id=... ; http://127.0.0.1:47711/health
```

### O que falta / pendências
- **Instalação como serviço Windows não foi executada**: exige terminal de administrador. Comandos
  (em PowerShell "Executar como administrador"):
  `var\bin\dm-agent.exe install --data-dir C:\ProgramData\DatiMonitor` → `sc.exe qfailure DatiMonitorAgent`
  (deve mostrar reinício em 5 s/5 s/30 s) → `dm-agent.exe start` → `dm-agent.exe status` →
  `dm-agent.exe uninstall`. O código está pronto e o job Windows do CI fará isso na Fase 11.
- O `systemd` (unit `Type=notify`) está escrito e o `sdnotify` tem teste em Linux, mas ainda não foi
  executado num Linux real (o CI Linux roda os testes; a instalação entra na Fase 8).
- WebSocket, comandos e watchdog: Fases 3 e 5.

## Fase 3 — Tempo real e comandos ✅

### Plano executado
Gateway WebSocket com presença em tabela e LISTEN/NOTIFY (sem Redis), canal de contingência HTTPS,
serviço de comandos com ciclo de vida completo e executor idempotente no agente. Todos os comandos da
tabela 4.7 executados pelo **coletor** foram implementados. Ficam para depois, como manda a seção 14:
- `update` e `rollback`: Fase 5.
- `restart_agent`, `uninstall` e `get_logs` do **watchdog**: Fase 5, junto com o próprio watchdog.
- `web_proxy_open` e `web_proxy_close`: Fase 7 (acesso web, seção 4.9).

### O que foi feito
- **Banco** (migração `fase 3 presenca e pausa`):
  - `agent_presence`: uma linha por conexão viva (gateway, horário, IP, latência);
  - `agents.paused`: a pausa agora é decidida pelo servidor e devolvida em todo heartbeat e configuração;
  - a `config_version` de todos os coletores foi incrementada, porque a configuração ganhou `ws_url`
    e `paused`.
- **Gateway** (`app/gateway/`):
  - `/ws/agent` autenticado pelo token do agente, com uma conexão por coletor (a nova derruba a
    antiga, código 4000);
  - heartbeat pelo WebSocket, gravando `agent_heartbeats` com canal `ws` e latência;
  - `command_update` com confirmação; mensagens inválidas respondidas com `error`; limite de
    mensagens por conexão (código 4429);
  - `LISTEN dm_command` e `dm_agent_revoked` numa conexão dedicada, com reconexão e varredura de
    recuperação, mais varredura periódica a cada 30 s;
  - revogação derruba a conexão na hora (código 4403).
- **Comandos** (`services/commands.py`, `api/v1/commands.py`, `schemas/commands.py`):
  - parâmetros validados por tipo; comandos de diagnóstico só miram IPs da rede local do cliente;
  - `read_now` resolve os equipamentos do local; `wake_host` resolve o MAC de outro coletor do local;
  - `promote_master` troca o lease e registra `cluster_events`; `pause` e `resume` mudam o estado;
  - permissão `agents.command`; auditoria `command.<tipo>` e `command.cancel`;
  - estados `pending → sent → acked → running → succeeded/failed`, mais `expired` (pelo worker,
    padrão 10 min) e `cancelled`; comando não confirmado em 20 s é reentregue; saída limitada a 1 MB;
  - uploads de logs (.zip) e walks (.snmprec.gz) gravados em disco (`STORAGE_DIR`), com listagem e
    download pelo portal.
- **Contingência HTTPS**: `GET /api/agent/commands/pending` e `POST /api/agent/commands/{id}/update`.
- **Worker**, a cada 30 s:
  - `commands`: expira comandos não iniciados e falha os que rodam sem notícia;
  - `presence`: remove presenças órfãs e marca offline quem está sem heartbeat há 3 min.
- **Agente**:
  - `internal/ws`: reconexão com backoff e jitter (1 s → 60 s), zerado quando a sessão funcionou ou o
    servidor só reiniciou (1001/1012); ping a cada 20 s, com reconexão após 2 pongs perdidos; usa o
    proxy do sistema e HTTP/1.1 no handshake.
  - `internal/commands`: executor idempotente por id. Grava cada estado no SQLite antes de enviar e
    reenvia até chegar. Após reinício, finaliza o que ficou pela metade. Aceita cancelamento, tem
    tempo limite por tipo e transforma pânico em falha.
  - `internal/netdiag`: ping ICMP (IcmpSendEcho no Windows, sem administrador; x/net/icmp no Linux),
    teste de portas TCP, Wake-on-LAN, interfaces e disco. Também `logx.ZipRecent` e
    `svc.RestartService`.
  - Coletor:
    - `ScanNow` varre todas as faixas ou uma (uma faixa parcial nunca remove equipamentos);
    - `ReadNow` espera a leitura agendada em curso terminar e lê de novo;
    - `ReadRaw`, `SNMPTest` e `Walk` para os comandos de diagnóstico.
  - Heartbeat pelo WebSocket; com ele caído há mais de 2 min, heartbeat e busca de comandos pelo HTTPS.
- **dev.ps1**: cada serviço roda no próprio console oculto (ver D38).

### Testes (todos passando)
| Suíte | Resultado |
|---|---|
| Go `go test -race -tags integration` | cobertura de `internal/` **84,4%**. Testes novos: canal WebSocket contra gateway falso (hello, heartbeat, comandos, cancelamento, códigos 4401/4403/1012, pongs perdidos); executor (idempotência, reinício, recusa, cancelamento, pânico); comandos pela contingência; **agente real × snmpsim** (`scan_now`, `read_now`, `read_device`, `snmp_test`, `mib_walk`) |
| pytest | **132** testes; cobertura 91,9%. Testes novos: ciclo de vida, validação, permissões e escopo, cancelamento e expiração, reentrega, pausa, promoção, wake, uploads, presença, NOTIFY; **gateway real** (uvicorn + cliente WebSocket): token inválido, heartbeat e presença, comando ao vivo via LISTEN/NOTIFY, entrega na reconexão, substituição de conexão, revogação |
| Vitest / Playwright | 7 / 1 |
| Lint | golangci-lint (Windows e Linux), ruff, mypy --strict, eslint, prettier, tsc: sem problemas |

### Fluxo manual executado (25/09/2026) e resultado
1. `dev.ps1` → migração da Fase 3 aplicada; gateway "escutando comandos no PostgreSQL". ✅
2. Coletor 1 (da Fase 2, com a configuração antiga em cache):
   - o WebSocket no endereço derivado (porta 8000) foi recusado;
   - um comando `diagnostics` criado no portal chegou **pela contingência HTTPS** e foi concluído;
   - no mesmo heartbeat o coletor baixou a configuração v5 com `ws_url` e conectou no WebSocket. ✅
3. Coletor 2 cadastrado no mesmo local: o `ws_url` já veio no cadastro e ele conectou na hora como
   STANDBY. ✅
4. Comandos pelo portal, via WebSocket (0,3 s a 3 s cada):
   - `snmp_test`: a credencial `public` responde na Konica; a `privada-teste` não;
   - `read_device`: total **217031** = PB 100150 + cor 116881;
   - `mib_walk` da subárvore 43: 42 OIDs, baixado pelo portal;
   - `get_logs`: zip baixado pelo portal;
   - `scan_now`: 8 impressoras; `read_now`: 8 de 8;
   - `ping_host`: porta 8000 aberta e porta 9 recusada;
   - `scan_now` no STANDBY: recusado com motivo claro;
   - `pause`: estado `paused` no portal; `resume` e `set_config`: ok;
   - `restart_watchdog`: "sem permissão para controlar serviços do Windows" (terminal comum; o
     watchdog chega na Fase 5);
   - `wake_host` do coletor 2 para o PC do coletor 1: magic packet enviado a 8 destinos;
   - `promote_master` nos dois sentidos: o antigo MASTER vira STANDBY no heartbeat seguinte;
   - `reconnect`: 0,8 s. ✅
5. Revogar o coletor 2 no portal: o gateway derrubou a conexão em ~12 ms, o coletor registrou
   "coletor revogado no portal" e no portal ele aparece offline e sem WebSocket. ✅
6. **Defeitos encontrados e corrigidos no fluxo** (todos com teste):
   - `mib_walk` dava erro 500 com várias impressoras no mesmo IP, porque casava só o IP; agora casa
     IP e porta.
   - `read_now` falhava quando coincidia com a leitura agendada; agora espera e lê.
   - Um reload do uvicorn derrubava o `dev.ps1` inteiro, sem aviso (D38).
   - Depois de um reinício do gateway, o backoff não zerava e o coletor levava 50 s para voltar;
     agora volta em ~1 s (D41).
   - A atualização de comando esperava 15 s de confirmação numa conexão que já tinha caído; agora
     desiste na hora e segue pelo HTTPS.
   - `avg_latency_ms` era gravado, mas não aparecia na API.
   - "Access is denied" virou mensagem em português.

### Como testar
```powershell
scripts\test.ps1 -E2E
scripts\dev.ps1
# cadastre um coletor (Fase 2) e rode: var\bin\dm-agent.exe run --data-dir var\agent1
# POST /api/v1/agents/{id}/commands {"type":"read_device","params":{"ip":"127.0.0.1","port":1163}}
# GET  /api/v1/commands/{id}   (estado e resultado ao vivo);  GET /api/v1/command-types
# POST /api/v1/agents/{id}/revoke   → o gateway derruba a conexão
```

### O que falta
- A tela de comandos ao vivo no portal é da Fase 4; o NOTIFY `dm_command_update` já é emitido a cada
  mudança de estado.
- Comandos do watchdog, atualização e rollback ficam para a Fase 5; o túnel web, para a Fase 7.

---

## Fase 4 — Portal ✅

### Plano executado
Portal completo sobre a API das Fases 1–3, com cliente TypeScript gerado do OpenAPI, eventos ao vivo
por SSE (LISTEN/NOTIFY, sem Redis) e a tela de parque da seção 10.6. O que depende de funcionalidade
que ainda não existe fica para as próximas fases (ver "O que falta").

### O que foi feito
- **Backend para o portal**:
  - `/api/v1/park` com filtros por coluna, pesquisa global, ordenação, cursor e contagens;
  - exportação CSV/XLSX; edição e ações em massa (PAT, setor, mover de local, desativar, ler agora
    no MASTER do local);
  - séries de contadores (dia/mês no fuso de São Paulo), histórico de suprimentos, ajustes manuais
    com motivo (a leitura original nunca é alterada) e exportação de leituras;
  - dashboard: cards, páginas por dia (PB × cor, 30 dias), coletores offline e toners críticos;
  - operações de coletor: histórico de heartbeats, versões, cluster do local, comandos em massa,
    últimas linhas dos logs e o **Reativar** (comandos, failover com promoção + Wake-on-LAN, ou
    diagnóstico com último sinal e sugestões);
  - `/api/v1/events` (SSE) filtrado pelo escopo do usuário, com keep-alive e `resync`;
  - `type_label` nos comandos (nome em português).
- **Portal** (React 18 + Tailwind 4 + Radix, tudo em pt-BR):
  - **Layout**: barra lateral, revenda no cabeçalho, "Você está em", indicador "Ao vivo", sino de
    alertas, menu do usuário e tema claro/escuro/do sistema;
  - **Login**: com TOTP, "esqueci a senha", redefinição por link e sessão limitada (troca de senha
    ou TOTP obrigatórios);
  - **Dashboard**;
  - **Parque** com as 12 colunas do Datacount:
    - filtro por coluna, pesquisa global, Selecionar/Desconectados/Desativados e filtro avançado;
    - colunas salvas por usuário, ações em massa e exportação;
    - virtualização, e **cartões no celular**.
  - **Detalhe do equipamento**:
    - dados editáveis, níveis, contadores dia/mês e leituras com exportação;
    - suprimentos com histórico e previsão, linha do tempo de eventos e ajustes manuais;
    - Ler agora / Testar SNMP / Leitura bruta / Walk.
  - **Coletores**:
    - lista com estado, papel, fila, versão e último sinal;
    - ações por linha e em massa, e **Reativar** com passos ao vivo;
    - "Novo coletor" com código, link e 3 passos.
  - **Detalhe do coletor**:
    - saúde (gráfico de heartbeat, CPU, memória, fila e latência) e comandos com saída ao vivo;
    - logs, cluster, faixas de IP (aprovar sugestões), credenciais SNMP, intervalos/proxy e versões.
  - **Clientes** (locais, coletores, equipamentos, contatos/ERP), **Empresas**, **Revendas**
    (superadmin), **Usuários** (papéis, escopo por cliente, senha temporária, link por e-mail, reset
    do TOTP), **Auditoria** (filtros e exportação) e **Minha conta**.
- **E2E de verdade**:
  - `scripts/e2e_seed.py` cria o usuário E2E (senha aleatória por execução), o cliente e os locais,
    e envia leituras assinadas pelo protocolo do agente;
  - `scripts/e2e_sims.py` sobe as 8 impressoras simuladas em portas próprias;
  - o Playwright compila e roda o **dm-agent real**;
  - `lint.ps1`/CI conferem que `openapi.json` e `schema.d.ts` estão atualizados.
- **Correções encontradas no caminho** (todas com teste):
  - **O agente recusava Windows 10 em modo de compatibilidade**: `RtlGetVersion` é afetado pelo shim
    `Win7RTM`; agora a versão vem de `RtlGetNtVersionNumbers` (D49).
  - Teste do coletor instável: cancelava antes do ciclo terminar.
  - Nome `TokenResponse` duplicado no OpenAPI (D43).
  - O portal chamava o refresh sem sessão e gerava um 403 no console (D50).

### Testes (todos passando)
| Suíte | Resultado |
|---|---|
| Go `go test -race -tags integration` | cobertura de `internal/` **84,3%**; novo teste: versão do Windows correta com `__COMPAT_LAYER=Win7RTM` |
| pytest | **142** testes; cobertura **92%**. Novos: parque (filtros, ordenação, cursor, exportação, edição, massa, desconectados, séries, suprimentos, ajustes imutáveis), dashboard, Reativar (3 desfechos), comandos em massa, SSE com servidor real, `type_label` |
| Vitest | **25**: parser SSE e invalidação, formatação UTC → São Paulo, refresh único para vários 401, `ApiError`, `SupplyBars`, login com TOTP, ApiStatus |
| Playwright | **6**. Com o **dm-agent real** (compilado do código): código gerado no portal → cadastro → online em < 10 s → faixa de IP e "Varrer agora" pelo portal → 8 impressoras no parque, com a Konica em 217.031 = 100.150 + 116.881 e níveis 25/55/66/5% → detalhe com gráfico. Também: login → dashboard → parque → detalhe → recarregar mantém a sessão; parque no celular; rota protegida; senha errada; smoke |
| Lint | golangci-lint (Windows e Linux), ruff, mypy --strict, eslint, prettier, tsc, `openapi.json` e `schema.d.ts` atualizados: sem problemas |

### Fluxo manual executado (27/09/2026) e resultado
1. `dev.ps1` com o coletor 1 (da Fase 2) rodando. Revisão visual de 14 telas (claro, escuro e
   celular 390 px), sem nenhum erro no console. ✅
2. Problemas encontrados na revisão e corrigidos:
   - o parque a 1440 px escondia Medidor, Monitor e Níveis. As colunas agora têm largura mínima
     proporcional e as 12 cabem.
   - no celular a tabela era inutilizável. Virou cartões: serial, modelo, cliente, IP,
     comunicação, status, medidor e níveis.
   - os comandos apareciam pelo código (`reconnect`). A API devolve `type_label` e o portal mostra
     "Reconectar".
   - o eixo do gráfico de heartbeats cortava o minuto ("03:0"). Agora é um eixo de tempo.
   - a auditoria mostrava "sistema/coletor" num login recusado. Agora mostra "não autenticado".
   - a versão do coletor quebrava linha.
   ✅
3. Parque:
   - 9 equipamentos com contadores PB/cor e níveis C/M/Y/K (5% em vermelho);
   - status "Erro" e "Economia de energia" nos simuladores 07 e 06;
   - detalhe da Konica: 217.031, PB/Cor 100.150/116.881, perfil `konica-minolta / konica_counters`.
   ✅
4. **Reativar** com o coletor derrubado: ele aparece offline no portal depois do prazo de 3 min. A
   mensagem é "Nenhum coletor deste local está ligado. Último sinal: 27/09 03:18. Provável PC
   desligado ou sem internet.", com as 3 sugestões. ✅
5. Coletor religado:
   - voltou para **Online sem recarregar a página** (evento ao vivo);
   - Reativar → "Coletor está conectado: comandos enviados", com "Reconectando" e "Lendo todos os
     equipamentos", os dois **Concluído**.
   ✅

### Como testar
```powershell
scripts\test.ps1 -E2E      # inclui o fluxo com o dm-agent real e as 8 impressoras simuladas
scripts\dev.ps1            # portal em http://localhost:5173 (senha temporária do admin@local no console)
# Revisão visual com a credencial E2E descartável (senha só em memória, criada pelo seed):
#   $env:DM_E2E_PASSWORD = '<aleatória>'; .venv\Scripts\python scripts\e2e_seed.py
#   $env:DM_E2E_EMAIL = 'e2e@dati.local'; $env:DM_AGENT_NAME = '<nome do coletor>'
#   node frontend\e2e\manual-screens.mjs <pasta-de-saida>
```

### O que falta
- **Alertas** (aba do cliente e do equipamento, lista no sino), "Toners que acabam em até 7 dias" no
  dashboard e **Relatórios**: Fase 6 (alertas e previsão) e Fase 7 (relatórios). Por enquanto o
  dashboard mostra "Toners críticos (≤ 10%)" (D46).
- **Abrir página web da impressora**: Fase 7 (túnel da seção 4.9).
- **Terminal de logs contínuo**: hoje o portal pede os logs (`get_logs`) e mostra as últimas 500
  linhas. O stream contínuo depende do canal de logs do agente/watchdog e entra na Fase 5 (D45).
- Watchdog vivo, Atualizar e Reiniciar o agente pelo portal: Fase 5.
- E2E da seção 13, partes restantes: derrubar o agente → alerta offline e e-mail (Fase 6); Reativar
  com volta automática (Fase 5); página web pelo túnel (Fase 7).

---

## Fase 5 — Confiabilidade ✅

### Plano executado
Watchdog completo com vigilância mútua, failover do cluster pelo lease no servidor, atualização
assinada com canais, liberação gradual e rollback automático, as 4 etapas do botão Reativar e o teste
de caos da seção 13. O Wake-on-LAN (Fase 3) virou parte do failover do Reativar.

### O que foi feito
- **dm-watchdog** (`agent/internal/watchdog`, `cmd/dm-watchdog`):
  - a cada 15 s confere o coletor:
    - serviço parado → inicia;
    - `/health` falhou 3 vezes seguidas → reinicia;
    - memória acima de 300 MB → reinicia;
    - o motivo de cada reinício vai para o servidor e aparece no portal;
  - canal próprio `POST /api/watchdog/heartbeat` a cada 60 s, com a mesma credencial do coletor e
    independente do WebSocket (D53);
  - executa `restart_agent`, `update`, `rollback`, `get_logs` (do vigia) e `uninstall`:
    - registro local por `command_id`: nunca executa duas vezes;
    - reenvia o resultado que não chegou;
  - roda como serviço `DatiMonitorWatchdog` (LocalSystem, recuperação do SCM) ou, em desenvolvimento e
    testes, em modo processo com `--agent-exe` (D54);
  - `/health` próprio em 127.0.0.1:47702.
- **Vigilância mútua:** o coletor confere o serviço do watchdog a cada 60 s, inicia-o se estiver
  parado e manda o estado no heartbeat. As consultas usam direitos mínimos, que funcionam fora de
  administrador (D55).
- **Atualização assinada (seção 5.2):**
  - `dm-tool keygen / sign / verify`;
  - a chave privada fica fora do repositório e do servidor; a pública vai embutida e também é
    conferida pelo servidor na publicação (D51, D52);
  - fluxo:
    1. baixa e confere sha256 e assinatura;
    2. para e guarda a versão atual como `previous`;
    3. troca e inicia;
    4. espera `/health` saudável e heartbeat aceito pelo servidor por até 2 min;
    5. se não ficar saudável, faz o rollback automático;
  - o coletor atualiza o watchdog (processo inverso).
- **Servidor:**
  - versões: publicar (superadmin), canal, liberação gradual, retirar, e sucesso/falha por versão;
  - atualização automática no worker a cada 5 min, bloqueando versão com falha acima de 5% no canary
    (D56);
  - failover do lease no worker a cada 30 s, com MASTER preferido fixável pelo operador (D57);
  - Reativar etapa 2: `restart_agent` ao vigia e acompanhamento da volta por até 3 min (D58);
  - comandos do watchdog aceitos (D39 cumprida);
  - desinstalar exige papel admin e confirmação dupla.
- **Portal:**
  - tela **Versões**: publicar colando a saída do `dm-tool sign`, com o sha256 conferido no navegador
    antes do envio; canal, liberação e retirada; taxa de falha no canary;
  - cartão do vigia na aba Saúde: estado, reinícios com motivo e versão guardada para voltar;
  - coluna Watchdog na lista de coletores;
  - no menu Comandos: Reiniciar o coletor (pelo watchdog), Logs do watchdog, Atualizar…, Voltar
    versão e Desinstalar…;
  - MASTER preferido na aba Cluster;
  - o Reativar acompanha a volta do coletor.
- **Teste de caos** `scripts\chaos.ps1` (seção 13), descrito abaixo.
- **Defeitos encontrados no caminho** (todos com teste):
  - O Go mandava `null` em 9 campos de lista/mapa vazios e o servidor recusava. O heartbeat do
    watchdog só era aceito quando havia reinício a relatar, e um PC sem IPv4 não conseguiria se
    cadastrar. Foi criado o teste de contrato Go × schemas do servidor (D59).
  - O `dm-tool sign` aceitava assinar outro programa que tivesse a versão certa. Agora exige o nome do
    componente.
  - Consultar serviço do Windows exigia administrador.
  - O script de caos subia a API sem migrar o banco.

### Testes (todos passando)
| Suíte | Resultado |
|---|---|
| Go `go test -race -tags integration` | cobertura de `internal/` **82,5%**. Testes novos: vetor de assinatura gerado pelo servidor (Go e Python assinam a mesma mensagem); verificação (adulterado, tamanho, outro alvo, versão/componente trocados, outra chave); `Installer` (update, rollback em dois sentidos, rollback automático, adulterado não mexe em nada); watchdog (serviço parado, `/health` 3×, memória, comandos, idempotência e reenvio, update quebrado, desinstalar); alvo processo com processo real; serviço inexistente sem administrador; vigilância mútua; coletor atualizando o watchdog; `dm-tool` confere componente e versão; **contrato do protocolo Go × schemas do servidor**; versão do Windows com shim de compatibilidade |
| pytest | **156** testes; cobertura **91%**. Novos: canal do watchdog (estado, reinícios, versão guardada), comandos só no canal do vigia, logs do vigia, rollback, desinstalar (papel e confirmação dupla), Reativar etapa 2 (com vigia vivo e morto), publicação assinada (adulterado, reetiquetado, outra chave, duplicada, só superadmin), download por alvo, update manual e do watchdog, versão retirada, atualização automática (canal, liberação gradual, bloqueio por falha no canary, sem repetir versão que falhou, desligada), failover (lease vencido, prioridade, latência, pausado, ninguém online, MASTER preferido, nome de coletor excluído no histórico) |
| Vitest | **31**: leitura da saída do `dm-tool sign`, sha256 no navegador, estado do vigia, selo do vigia, desinstalar com confirmação dupla |
| Playwright | **6**; o coletor real agora roda sob o **dm-watchdog real**: vigia "ativo" no portal e "Reiniciar o coletor (pelo watchdog)" concluído de ponta a ponta |
| Lint | golangci-lint (Windows e Linux), ruff, mypy --strict, eslint, prettier, tsc, OpenAPI/tipos atualizados: sem problemas |

### Fluxo manual executado (28/09/2026) e resultado
1. **Teste de caos** (`chaos.py --outage-minutes 10`), com dois dm-agent reais vigiados por dm-watchdog
   e as 8 impressoras simuladas: **22 OK, 0 falhou** (`var\chaos\relatorio.json`):
   - ✅ vigias com sinal no portal (heartbeat do watchdog aceito)
   - ✅ cluster inicial: A MASTER, B STANDBY — {'coletorA': 'master', 'coletorB': 'standby'}
   - ✅ varredura inicial encontra as 8 impressoras — encontradas 8
   - ✅ leitura inicial concluída
   - ✅ watchdog reinicia o coletor morto — 7 s
   - ✅ motivo do reinício chega ao portal
   - ✅ coletor A volta a online
   - ✅ leituras ficam na fila local durante a queda — máximo 32 itens
   - ✅ coletor não é reiniciado à toa durante a queda (o /health continua saudável) — 0 reinício(s)
   - ✅ fila local esvazia sozinha quando o servidor volta — 26 s
   - ✅ coletores voltam a online sem intervenção
   - ✅ leituras feitas durante a queda chegaram ao banco (nenhuma perdida) — A797019500624=2, SIMCAN0001=2, SIMCAN0002=2, SIMERR07=2, SIMGEN0005=2, SIMKM0004=2, SIMREG08=2, SIMSLEEP06=2
   - ✅ API se recupera do banco (sem administrador: 10 conexão(ões) do banco derrubadas (pg_terminate_backend))
   - ✅ comando ao vivo depois do banco (LISTEN do gateway reconectou) — succeeded
   - ✅ heartbeats continuam gravando depois do banco
   - ✅ STANDBY assume quando o lease do MASTER expira — 171 s depois da queda
   - ✅ novo MASTER varre e lê as 8 impressoras
   - ✅ antigo MASTER volta como STANDBY (não retoma sozinho) — {'coletorA': 'standby', 'coletorB': 'master'}
   - ✅ nenhum item em dead-letter nos coletores — {'coletorA': 0, 'coletorB': 0}
   - ✅ sem leituras duplicadas entre coletores (anti-duplicidade) — 0 par(es)
   - ✅ tudo online no fim, sem intervenção manual
   - ✅ vigias com sinal no fim
2. **Atualização assinada** com binários reais, a chave privada real e o `dev.ps1`: **13 OK, 0 falhou**.
   - O `dm-tool` recusou assinar um `dm-tool` disfarçado de coletor 0.5.2.
   - O servidor recusou um binário adulterado.
   - 0.5.0 → 0.5.1 pelo watchdog em 12 s, e o portal mostrou 0.5.1 com a 0.5.0 guardada.
   - O rollback voltou para a 0.5.0.
   - A 0.5.2 quebrada não ficou saudável em 60 s e voltou sozinha para a 0.5.0 ("rollback para 0.5.0
     feito"). Ela ficou fora da atualização automática (100% de falha no canary).
   - O worker atualizou sozinho para a 0.5.1 em 80 s, sem oferecer a 0.5.2.
   - Desinstalar pelo portal: o vigia removeu o coletor, relatou e encerrou.
3. **Revisão visual** (Versões, publicar, coletores, cartão do vigia, menu de comandos, Atualizar, Cluster),
   sem nenhum erro no console. Na revisão:
   - o histórico do Cluster mostrava "outro" para coletores excluídos. O servidor agora devolve o nome
     de todo coletor citado no histórico.
   - a tela de Versões mostrou falhas de atualização em coletores do teste de caos. O worker de
     desenvolvimento os atualizava automaticamente, e os dois usavam o mesmo executável, então a troca
     do arquivo falhou com erro claro, sem mexer em nada. O caos agora roda com a atualização
     automática desligada e um executável por coletor, como em PCs diferentes.

### Como testar
```powershell
scripts\test.ps1 -E2E               # inclui o coletor real sob o dm-watchdog e "Reiniciar pelo watchdog"
scripts\chaos.ps1                   # 10 min de queda (≈ 17 min no total); -OutageMinutes 3 para ensaio
# Watchdog em modo processo (sem administrador), depois de cadastrar o coletor em var\agent1:
#   dist\windows-amd64\dm-watchdog.exe run --data-dir var\agent1 --agent-exe dist\windows-amd64\dm-agent.exe
# Serviços (terminal de administrador): dm-agent.exe install / dm-watchdog.exe install; sc.exe qfailure DatiMonitorWatchdog
```

### O que falta / pendências
- **Terminal de administrador** (ação do usuário, como na Fase 2):
  - instalar os serviços `DatiMonitorAgent` e `DatiMonitorWatchdog` e conferir a recuperação do SCM;
  - atualizar o watchdog como serviço pelo coletor;
  - rodar `scripts\chaos.ps1` como administrador para reiniciar de verdade o serviço do PostgreSQL.
  - O código está pronto; o job Windows do CI cobre isso na Fase 11.
- O stream contínuo de logs (D45) segue com `get_logs` + últimas linhas: o canal de logs em tempo real
  ficou fora desta fase por não ser exigido na seção 5 (registrado para a Fase 9, acabamento).

---

## Fase 5.1 — Auditoria do Datacount ✅

Pedido do usuário depois da Fase 5: incorporar ao PROMPT as 15 funcionalidades levantadas na auditoria do
Datacount atual. O que afeta fases concluídas (modelo de dados, agente, ingestão, telas existentes) foi feito
agora, com testes; o resto entrou no PROMPT na fase correspondente.

### Plano executado
- PROMPT.md: regra 13 (defeitos do Datacount que não se copiam), tabelas e campos novos na seção 3,
  descoberta/leitura/atributos nas seções 4.5–4.6, OIDs padrão novos na 6.2, extensões de perfil na 6.4,
  worker (previsão com janela e atolamento recorrente), telas na seção 10, a **Fase 5.1** e o que cada item
  acrescenta às Fases 6 e 7 na seção 14, critérios 20–22 na seção 15 e a nova **seção 16** (um item por
  funcionalidade, dizendo em que fase entra). Inventário de computadores ficou fora do escopo, como pedido.

### Onde cada item entrou
| # | Item | Agora (5.1) | Depois |
|---|---|---|---|
| 1 | Descobertas | modelo, ingestão, agente (`ignored_serials`), API, tela, E2E | — |
| 2 | Contadores como linhas | `reading_counters` particionada e só inserção, mapeamento padrão + `line` do perfil, migração das leituras antigas | relatórios e ERP (Fase 7) |
| 3 | Troca de toner | detecção e `supply_replacements` com rendimento, prematura, serial | tela e relatório de rendimento (Fases 6/7) |
| 4 | Alertas da impressora | `prtAlertTable` completa no agente, `printer_alerts` com classificação e contadores | telas e regra "atolamento recorrente" (Fase 6) |
| 5 | Limiar de toner | cliente (por cor + liga/desliga) e equipamento (desligado/global/individual) no modelo e na API | telas e alertas (Fase 6) |
| 6 | Previsão de toner | colunas (dias mín./máx., páginas restantes, método, confiança) | cálculo e telas (Fase 6) |
| 7 | Cadastro do equipamento | serial alternativo, setor = sysLocation, franquia, excedente, campos personalizados, setor em lote | — |
| 8 | Atributos diários | agente lê firmware, memória, disco, subsistemas, uptime, painel, SSID/peças pelo perfil; histórico só quando muda; aba Atributos | — |
| 9 | Locais | endereço completo, CEP pelo ViaCEP, latitude/longitude | mapa e importação de clientes por CSV (Fase 7) |
| 10 | Coletor | redes conectadas, IP/hostname avulso, importação `.txt`, tentativas 1–5, timeouts, IP público, SO, local de instalação, estatísticas | — |
| 11 | Dataclassic (ErpConnector) | — | Fase 7 (fila `erp_queue`, transporte plugável) |
| 12 | Relatórios | — | Fase 7 |
| 13 | Visão geral | — | Fase 6 (toners em 30 dias) e Fase 7 (produção do mês, comunicando por dia) |
| 14 | Permissões | matriz módulo × ação + Monitorar suprimentos, ajustável por revenda, tela | — |
| 15 | Defeitos a não copiar | regra 13; corrigidos os casos existentes (abaixo) | vale para todas as fases |

### O que foi feito
- **Banco** (migração `11f512d72613`):
  - equipamentos: `discovery_state`, cadastro (16.7), modo e limiares de toner, atributos;
  - clientes: limiares de toner; locais: endereço completo e `auto_activate_devices`;
  - coletores: `monitor_local_networks`, `public_ip`, `install_path`; faixas: `host`;
  - tabelas novas: `reading_counters` (particionada e só inserção), `supply_replacements`, `printer_alerts`,
    `device_attribute_snapshots`, `custom_field_definitions`, `reseller_role_permissions`;
  - previsão e serial do cartucho em `supplies_current`/`supply_readings`;
  - dados que já existiam: equipamentos entram **aprovados**, setor digitado deixa de seguir o sysLocation,
    endereço vai para `street`, e as 288 leituras do banco de desenvolvimento ganharam 1.102 linhas em
    `reading_counters`. A migração desce e sobe sem erro.
- **Ingestão**:
  - equipamento novo nasce `pending`, ou `approved` se o local ativa automaticamente;
  - descartado responde `discarded` e sai da fila do coletor;
  - cada contador vira linha;
  - nível de suprimento que sobe ≥ 20 pontos (configurável) grava a troca com contadores, rendimento
    (contador na troca − contador da troca anterior; preto usa o total, C/M/Y o contador de cor),
    capacidade nominal em páginas, prematura (> 20%) e serial do cartucho;
  - a `prtAlertTable` vira `printer_alerts`: um registro por alerta novo, identificado por índice + código +
    `prtAlertTime`; o alerta é encerrado quando some; classificação em Atolamento / Chamado técnico /
    Consumível / Peças / Outros;
  - atributos: estado atual no equipamento e histórico só quando mudam (o uptime não conta);
  - setor segue o `sysLocation` até alguém digitá-lo; apagar volta a segui-lo.
- **Agente**:
  - perfil com `line`, `supplies.cartridge_serial_oid` e `attributes` (schema único Go/servidor);
  - leitura diária de atributos com OIDs padrão (HOST-RESOURCES e ENTITY-MIB);
  - `prtAlertTable` com todas as colunas; `sysLocation` na identidade;
  - IP/hostname avulso resolvido a cada varredura (nome que não resolve não trava o resto, vai para o log);
  - "monitorar redes conectadas" soma as /24 privadas do PC;
  - timeout próprio das leituras; retentativas limitadas a 4;
  - seriais descartados saem da lista local e não voltam na varredura;
  - local de instalação no heartbeat.
- **API**:
  - `/discoveries` (lista, contagem, decidir em lote);
  - `/custom-fields`; `/permissions/matrix`;
  - `/agents/{id}/stats`; `/sites/{id}/ip-ranges/import`;
  - `GET /devices/{id}` com o cadastro completo;
  - parque, painel e lista de equipamentos só com aprovados.
- **Permissões**: `módulo.read/create/update/delete` + `supplies.monitor`; a revenda ajusta operador, técnico
  e cliente; o papel Cliente só pode receber Consultar; administradores sempre têm tudo; valem na requisição
  seguinte, sem novo login. Limiar de toner exige "Monitorar suprimentos".
- **Portal**:
  - telas Descobertas (pendentes/descartados, ativar/descartar/restaurar individual e em lote), Permissões
    (matriz por papel) e Campos personalizados;
  - diálogo de Local com CEP (ViaCEP), coordenadas e ativação automática;
  - aba Atributos e cadastro completo no equipamento, com selo "Pendente em Descobertas";
  - coletor com IP público, local de instalação, estatísticas de 24 h, redes conectadas, IP/hostname avulso,
    importação `.txt` com relatório de erros por linha, tentativas SNMP e timeouts.
- **Defeitos do Datacount corrigidos no que já existia** (regra 13):
  - várias listas e seletores do portal baixavam no máximo 500 itens, sem avisar. Agora toda lista é
    paginada por cursor com "Carregar mais" (`useCursorList` + `LoadMore`), e cliente/local/empresa são
    escolhidos por seletores com busca no servidor (20 resultados por busca);
  - a exportação do parque tinha teto fixo de 100.000 linhas: saiu, e ela respeita só os filtros.
- **E2E**: o coletor real encontra as 8 impressoras, que chegam **pendentes**; o teste ativa em lote em
  Descobertas e confere parque, contadores, níveis e atributos.

### Testes (todos passando)
| Suíte | Resultado |
|---|---|
| Go `go test -race -tags integration` | cobertura de `internal/` **82,5%**. Novos: atributos do simulador (memória, disco, firmware ENTITY-MIB, subsistemas, uptime), SSID/subsistemas/peças pelo perfil, `prtAlertTable` completa, serial do cartucho, `line` do perfil e schema, IP/hostname avulso (resolvido, sem resolução, IPv6 ignorado), redes do PC, seriais descartados, timeout de leitura e limite de retentativas, contrato com o item `attributes` |
| pytest | **173** testes; cobertura **92%**. Novos: Descobertas (pendente fora do parque/painel, ativar/descartar/restaurar, `ignored_serials`, `discarded` para itens atrasados, ativação automática, permissões, isolamento entre revendas), linhas de contador (padrão, `line`, colisão, só inserção), trocas de suprimento (limiar, rendimento, prematura, capacidade, resíduo ignorado), classificação e ciclo dos alertas da impressora, atributos e setor, matriz de permissões, campos personalizados e cobrança, limiares de toner, endereço do local, IP/hostname avulso e importação `.txt`, estatísticas e opções do coletor |
| Vitest | **34** (novos: ViaCEP, incluindo cada mensagem de falha) |
| Playwright | **6**, com Descobertas no fluxo do coletor real (ver fluxo manual) |
| Lint | golangci-lint (Windows e Linux), ruff, mypy --strict, eslint, prettier, tsc, OpenAPI/tipos: sem problemas |

### Fluxo manual executado (29/09/2026) e resultado
1. **Migração** no banco de desenvolvimento: sobe, desce e sobe de novo. `alembic check` sem diferenças.
   9 equipamentos existentes ficaram aprovados e 288 leituras ganharam 1.102 linhas de contador.
2. **E2E**. A primeira execução completa falhou em Descobertas: a tela mostrou 0 pendentes, e o banco tinha
   as 8 impressoras do local aprovadas, sem evento de aprovação. Não consegui reproduzir. Nas duas execuções
   seguintes (só o coletor real e depois a suíte inteira), as 8 chegaram pendentes, foram ativadas em lote e
   o teste passou (6/6). Acompanhei o banco durante uma execução: `pending` até a ativação pela tela, depois
   `approved` com `discovery_decided_by` do usuário E2E. Registro para observar nas próximas fases.
3. **Revisão visual** das telas novas com o seed real, sem nenhum erro no console:
   - Descobertas, seleção em lote, Permissões, Campos personalizados, Local com CEP, Atributos, cadastro,
     coletor com estatísticas, Faixas de IP e seletor com busca;
   - o CEP 20040-002 foi preenchido pelo **ViaCEP de verdade** (Avenida Rio Branco, Centro, Rio de Janeiro/RJ);
   - corrigido na revisão: CEP cortado no diálogo, "—" antes do modelo quando a marca é vazia, dica do campo
     de faixa desalinhando o formulário e falta do selo "Pendente" no detalhe do equipamento.

### Como testar
```powershell
scripts\test.ps1 -E2E      # inclui Descobertas no fluxo do coletor real
scripts\dev.ps1            # portal: Descobertas, Usuários > Permissões, Campos personalizados,
                           # Clientes > Local (CEP), Coletor > Faixas de IP (avulsos, .txt, redes do PC)
# Local de teste de Descobertas: desligue "Ativar automaticamente" no local e varra de novo.
```

### O que falta / pendências
- Itens 3–6, 11–13 nas Fases 6 e 7, conforme a tabela acima e a seção 16 do PROMPT.
- As pendências de administrador das Fases 2 e 5 continuam (instalar serviços, reinício real do PostgreSQL).

---

## Fase 6 — Alertas e notificações ✅

Começou com a autorização do usuário depois da Fase 5.1.

### Plano executado
Regras centralizadas (seção 16.15) → motor de avaliação → fila de notificações → notificadores → previsão
de toner, resumo diário e retenção → API → telas → E2E do critério 8.

### O que foi feito
- **Regras** (`alert_rules`, `services/alert_rules.py`):
  - uma regra por tipo e por revenda, criada automaticamente com valores padrão;
  - regra por cliente substitui a da revenda naquele cliente; a da revenda não se exclui, se desativa;
  - 12 tipos: coletor sem sinal, equipamento sem leitura, toner abaixo do limiar, toner acaba em N dias,
    erro de hardware, atolamento, porta aberta, **atolamento recorrente** (5 em 3 dias), **alerta da
    impressora** por categoria, e os três da validação de leituras (regressão, salto, PB + cor ≠ total);
  - parâmetros validados por tipo; canais por regra (nenhum marcado = todos os canais ativos).
- **Motor** (`services/alert_engine.py`, job de 1 min):
  - abre um alerta por condição (deduplicado por `dedup_key`) e resolve sozinho quando a condição some;
  - **agrupamento**: local sem nenhum coletor online gera só o alerta do coletor, não um por equipamento;
  - toner baixo pelo limiar do cliente (por cor, liga/desliga) ou do equipamento (desligado/global/individual);
  - "acaba em N dias" só com previsão de confiança mínima;
  - atolamento/porta/hardware só com status confiável (equipamento respondendo);
  - alertas da impressora (`printer_alerts`) por categoria e atolamento recorrente pela janela da regra.
- **Notificações** (`services/notifications.py`, `services/notifiers.py`, job a cada 10 s):
  - fila pelos alertas ainda não notificados (`alerts.notified_at`), inclusive os abertos pela ingestão;
  - e-mail (SMTP do canal ou do servidor), **webhook** com assinatura HMAC e **WhatsApp** (Meta Cloud API e
    provedor HTTP genérico por modelo de URL/corpo, serve para Z-API/Evolution);
  - credenciais cifradas por canal (AES-GCM), mascaradas na API; o segredo não reenviado é mantido;
  - **silêncio** 22h–7h (configurável): crítico sai na hora, os outros saem no fim da janela;
  - alerta resolvido antes do envio: notificação suprimida, com o motivo;
  - retentativa em 1, 2, 5, 15, 30 e 60 min, até 6 tentativas; cada falha vai para o registro e para o log;
  - botão **Testar** em cada canal; registro de envios com reenvio.
- **Resumo diário** às 07:00 (Brasília) nos canais de e-mail (desligável por canal e por revenda).
- **Previsão de toner** (`services/forecast.py`, job de 1 h e na partida do worker):
  - regressão linear do nível nos últimos 30 dias, só desde a última troca;
  - janela otimista/pessimista pelo intervalo de 95% da inclinação;
  - páginas restantes pelo consumo por página; método direto quando a regressão não serve;
  - confiança de 0 a 1; abaixo de 0,5 o portal mostra "estimativa incerta", sem alerta e fora do painel.
- **Retenção** diária: heartbeats (30 dias), leituras de suprimento (400 dias) e registro de notificações
  (180 dias). Leituras de contador nunca são apagadas.
- **API**: `/alerts` (filtros, contagem, reconhecer/resolver em lote), `/alert-rules`,
  `/notification-channels` (+ teste), `/notifications` (+ reenvio), `/notification-settings`,
  `/printer-alerts` (+ contagem por categoria), `/supply-replacements`; dashboard com toners que acabam em
  até 7 dias e previsão de 30 dias por cor; evento ao vivo `alerts` (SSE).
- **Portal**:
  - Alertas (abas Alertas, Regras, Canais, Notificações enviadas, Configurações);
  - Trocas de toner (com rendimento e prematura) e Alertas da impressora (abas por categoria);
  - sino do cabeçalho com a contagem (vermelho com crítico) e link para Alertas;
  - abas Alertas e Suprimentos (limiares) no cliente; aba Alertas, limiar de toner e previsão no equipamento;
  - dashboard com os dois cartões novos.
- **E2E**: depois do "Reiniciar pelo watchdog", o coletor real é derrubado; o worker marca offline, a regra do
  cliente E2E abre o alerta crítico, o portal mostra e o e-mail chega ao `smtp_catcher` (critério 8).

### Testes (todos passando)
| Suíte | Resultado |
|---|---|
| Go `go test -race -tags integration` | cobertura de `internal/` **82,4%** (sem mudanças no agente nesta fase) |
| pytest | **189** testes; cobertura **91%**. Novos: regras (padrões, parâmetros, cliente substitui revenda, permissões, isolamento), motor (coletor sem sinal com agrupamento e resolução, pausado, regra de cliente, limiares de toner em todos os modos, previsão com confiança, erros da impressora, atolamento recorrente, regra desativada), notificações (e-mail real no servidor de teste, silêncio, supressão, regra desligada, webhook com assinatura e retentativa até desistir, WhatsApp Meta e genérico, canais com segredo mascarado e teste, configurações, janela de silêncio, resumo diário), API de alertas (lista, contagem, reconhecer/resolver, outra revenda, usuário de cliente), alertas da impressora e trocas, previsão (matemática e job), painel, retenção, jobs do worker |
| Vitest | **37** (novos: previsão em texto, evento ao vivo de alertas) |
| Playwright | **6**; o fluxo do coletor real termina com alerta de offline no portal e e-mail capturado |
| Lint | golangci-lint, ruff, mypy --strict, eslint, prettier, tsc, OpenAPI/tipos: sem problemas |

### Fluxo manual executado (29/09/2026) e resultado
1. **E2E completo, duas vezes (6/6).** No log do worker, o coletor foi marcado offline às 13:56:01, o
   alerta abriu no mesmo segundo e o e-mail saiu 5 s depois. O critério 8 pede até 10 min; com os tempos
   padrão (offline em 3 min e regra de 5 min) o total fica em cerca de 6 min.
2. **Revisão visual** com o banco de desenvolvimento, sem erros no console:
   - telas: dashboard, Alertas e todas as abas, novo canal de WhatsApp, Trocas de toner, Alertas da
     impressora, suprimentos e limiar do equipamento, suprimentos e alertas do cliente;
   - os alertas reais do banco de desenvolvimento apareceram (coletores do teste de caos offline, toner em
     5% e 8%, atolamento e porta aberta do simulador 07), e os e-mails foram registrados como enviados;
   - corrigido na revisão: mensagem "Coletor Coletor E2E…" (o nome já começa com "Coletor"), seletores de
     gravidade cortando o texto, e a previsão, que só rodava 1 h depois de o worker subir (agora roda
     também na partida).
3. **Previsão no banco de desenvolvimento**: 18 suprimentos avaliados na partida do worker e nenhum com
   previsão, porque os níveis simulados não mudam e o seed tem poucos pontos. O correto é não inventar
   número; a previsão foi validada nos testes com histórico real de consumo.

### Como testar
```powershell
scripts\test.ps1 -E2E      # inclui coletor derrubado → alerta no portal + e-mail no smtp_catcher
scripts\dev.ps1            # Alertas: crie um canal de e-mail, clique em Testar e veja em http://localhost:8025
# Tempos do worker por variável: AGENT_OFFLINE_AFTER_SECONDS, ALERTS_INTERVAL_SECONDS, NOTIFY_INTERVAL_SECONDS
```

### O que falta / pendências
- WhatsApp e SMTP reais dependem das credenciais da Daticopy (provedor, token, servidor de e-mail): basta
  cadastrar o canal e usar o botão Testar.
- Pendências de administrador das Fases 2 e 5 continuam.

---

## Fase 7 — Perfis, relatórios e acesso web ✅

Seguiu a instrução do usuário de 29/09: **nada de revenda** (o sistema é só da Daticopy). Os parâmetros que o
PROMPT descrevia "por revenda" ficaram como parâmetros da empresa; a estrutura multi-revenda que já existia
continua só como detalhe interno.

### Plano executado
Perfis (motor, perfis-base, editor e explorador de walk) → regra única de contadores → relatórios → API do
ERP → conector Dataclassic → importação CSV e mapa → Visão geral → túnel da página web → telas → E2E.

### O que foi feito
- **Perfis de modelos** (6.6):
  - perfis-base de HP, Ricoh, Kyocera, Xerox, Brother, Samsung, Lexmark, Sharp, Epson, OKI e Toshiba
    (casam pelo enterprise ID; OIDs proprietários `PREENCHER_PELO_WALK`; total pela fonte `standard`);
  - versões no banco (`source` arquivo/portal + observação): publicar pela tela vai para todos os coletores,
    ativar uma versão antiga é o rollback, e a versão do portal sobrevive ao reinício enquanto o YAML do
    repositório não mudar;
  - o schema compartilhado passou a exigir `counters` ou `counter_sources` (um perfil sem contadores
    zeraria a leitura da marca);
  - explorador de walk: busca pelo valor da folha de contadores (aceita "217.031"), por texto ou
    sub-árvore, com paginação, texto hex legível e OIDs arrastáveis para o editor;
  - rascunho testado no equipamento sem publicar (`read_device` com perfil);
  - "Salvar como gravação de teste": o walk vai para `profiles/recordings/real/` com os valores da folha
    (`.expected.json`), e o `TestRealRecordings` do Go passa a conferir contador a contador.
- **Regra única de contadores** (`services/reports/counters.py`), usada por relatórios, dashboard, API do ERP
  e conector: só leituras válidas, ajuste manual aplicado campo a campo, regressão fora (a menos que
  classificada válida ou troca de placa), produção pela soma dos aumentos entre leituras consecutivas.
- **Relatórios** (10.9 e 16.12): 18 relatórios em quatro grupos:
  - Produção e cobrança: produção por equipamento, local ou cliente; contador diário com gráfico;
    leitura de corte; cobrança do mês com franquia e excedente;
  - Suprimentos: trocas com rendimento; consumo e rendimento por modelo e cartucho; alertas de suprimento;
  - Parque: visão do parque, status, online, sem conexão, desativados, sem leitura há N horas,
    descobertas, trocas de IP/equipamento, regressões;
  - Coletores: status e disponibilidade.

  Na tela, a lista é paginada no servidor. CSV, XLSX e PDF (reportlab) saem com os mesmos filtros e a
  exportação é auditada. Há tipo de data onde faz sentido.
- **API do ERP** (7): `/api/erp/v1/readings` (cursor), `/cutoff`, `/devices`. O token `dmerp_` aparece uma
  vez, o banco guarda só o hash, a revogação vale na hora, há limite de requisições e o uso é registrado.
  A API está documentada no OpenAPI.
- **Conector Dataclassic** (16.11):
  - parâmetros da empresa (requisição de suprimento, OS, código da empresa, operador, contadores diários);
  - fila `erp_queue` com status por item, tentativas, último erro, conteúdo enviado e reenvio;
  - transportes arquivo (gravação atômica), HTTP (Authorization cifrado e `Idempotency-Key`) e "apenas
    e-mail";
  - formato provisório em `docs/erp-dataclassic.md`.
- **Importação de clientes por CSV** (16.9): `;`, UTF-8 ou Windows-1252, validação linha a linha com as regras
  do formulário, vários locais por cliente, botão Validar antes de Importar, tudo ou nada, modelo para
  baixar.
- **Mapa dos locais**: Leaflet + OpenStreetMap, cor pela situação do local (OK, atenção, coletor offline, sem
  coletor) e lista dos locais sem coordenadas.
- **Visão geral** (16.13): produção do mês (PB, cor, total) e equipamentos comunicando por dia. O gráfico de
  páginas por dia passou a usar a regra única de contadores.
- **Acesso remoto à página web da impressora** (4.9, critério 14):
  - **abertura**: o portal cria a sessão (30 min, só portas 80/443/8000/8080/8443, só técnico em diante, só
    impressoras ativas do local); o servidor manda `web_proxy_open` a um coletor conectado do local
    (MASTER primeiro);
  - **túnel**: o navegador usa `/devweb/{token}/` no gateway; o HTTP vai em quadros `web_request`,
    `web_response` e `web_chunk` dentro do WebSocket;
  - **coletor**: o destino vem sempre da sessão; aceita TLS autoassinado, não segue redirecionamentos e
    tem limite de banda;
  - **reescrita**: links, redirecionamentos, cookies e meta refresh ficam sob o prefixo; um script corrige
    XHR, fetch e window.open;
  - **segurança**:
    - o link vale só para o primeiro navegador que o abre (cookie);
    - a página da impressora roda isolada por `Content-Security-Policy: sandbox` sem `allow-same-origin`;
    - abrir pelo IP só funciona para impressora cadastrada no local; a recusa é auditada;
    - pedidos e bytes são contados por sessão, com teto de dados.
- **Correções encontradas no caminho**:
  - `fmtDate` mostrava o dia anterior para datas sem hora, por causa do fuso; o erro afetava o portal
    inteiro;
  - o link da página web podia ser reaberto em outro navegador depois de usado.

### Testes (todos passando)
| Suíte | Resultado |
|---|---|
| Go `go test -race -tags integration` | cobertura de `internal/` **82,3%**; novos: `webproxy` (HTTP, HTTPS autoassinado, limite de banda, recusas, sessão expirada), quadros do túnel no canal WS, `web_proxy_open`, gravações reais com folha de contadores, perfis-base, rascunho de perfil no `read_device`, contrato das mensagens novas |
| pytest | **216** testes; cobertura **91%**. Novos: perfis (versões, publicação, rollback, sincronia com o arquivo, explorador, gravação de teste, permissões), relatórios (produção sem regressão e com ajuste, corte, cobrança, listas, coletores, escopo, exportações), API do ERP, conector (arquivo, HTTP com backoff até erro e reenvio, e-mail, contadores diários, segredo cifrado), importação CSV, mapa, túnel de ponta a ponta pelo gateway real |
| Vitest | **39** (novos: formatação das células dos relatórios, data sem hora) |
| Playwright | **6**; o fluxo do coletor real agora abre a página da impressora simulada pelo túnel, confere a recusa auditada de um IP não cadastrado, faz o walk da Konica, acha o OID pelo valor da folha e testa o rascunho do perfil no equipamento |
| Lint | golangci-lint (Windows e Linux), ruff, mypy --strict, eslint, prettier, tsc, OpenAPI/tipos: sem problemas |

### Fluxo manual executado (02/10/2026) e resultado
1. **E2E completo (6/6).** A primeira execução falhou no passo de perfis porque o teste procurava o PB da
   Konica (100.150) no walk. Esse valor é a soma de dois OIDs (cópia + impressão), então nenhum OID o guarda
   sozinho. O passo passou a procurar o total da folha (217.031), que existe num OID.
2. **Revisão das telas** com o banco de desenvolvimento (`frontend/e2e/manual-f7.mjs`), sem nenhum erro no
   console:
   - telas: Visão geral, Relatórios (produção, contador diário, cobrança, status dos coletores), Perfis,
     editor da Konica, Integração ERP (parâmetros e fila), mapa, Clientes com Importar CSV;
   - os números da produção batem entre a Visão geral e o relatório;
   - **exportações reais pelos botões**: o PDF e o XLSX da produção foram conferidos. A linha de Total do
     PDF mostrava "—" nas colunas de texto e foi corrigida para ficar em branco.
3. **Mapa**: os mapas do OpenStreetMap carregam desta máquina (8 de 8 respostas 200).

### Como testar
```powershell
scripts\test.ps1 -E2E     # inclui a página da impressora pelo túnel e o editor de perfis com walk real
scripts\dev.ps1           # sobe também a página de impressora simulada em http://127.0.0.1:8080
# Equipamento → "Abrir página web" (porta 8080 nos simuladores); Relatórios; Integração ERP; Perfis de modelos
# API do ERP: crie um token em Integração ERP → Tokens e chame /api/erp/v1/cutoff?date=AAAA-MM-DD
```

### O que falta / pendências
- Layout do Dataclassic: depende da Databit. Até lá vale o JSON documentado; o transporte por banco de
  dados entra quando o layout chegar.
- OIDs proprietários dos perfis-base (`PREENCHER_PELO_WALK`): saem dos walks reais da Fase 10.
- Com mais de uma instância do gateway, o `/devweb` precisa cair na instância do coletor da sessão (hoje
  aparece uma mensagem clara pedindo para tentar de novo).
- Pendências de administrador das Fases 2 e 5 continuam.

---

## Fase 8 — Instaladores ✅

### Plano executado
Conferência do código sem gastá-lo → instalador Windows (Inno Setup) → pacote Linux (.deb, .tar.gz,
install.sh) → publicação e links de download → telas → CI → testes.

### O que foi feito
- **Conferência do código sem gastá-lo**: `POST /api/agent/enroll/check` e `dm-agent enroll --check-only`
  mostram o coletor, o cliente e o local. O instalador confere o código antes de copiar arquivos, então
  cancelar a instalação não gasta o código.
- **Instalador Windows** (`installer/windows/dati-monitor.iss`, `scripts/build-installer.ps1`):
  - um setup.exe com os binários de amd64, arm64 e 386; a arquitetura do PC é escolhida na instalação;
  - recusa Windows 7, 8, 8.1, Server 2008 e 2012 com a mensagem da seção 4.1 (mesma regra e texto do
    agente);
  - tela de servidor e código, conferido na hora;
  - modo silencioso `/VERYSILENT /SERVER= /CODE=` com códigos de saída (0, 1, 3);
  - instala os 2 serviços com início automático com atraso e recuperação do SCM, e inicia os dois;
  - atualização por cima mantém o cadastro;
  - desinstalador limpo: serviços, programa e `ProgramData`;
  - nomes vindos do `product.json`;
  - passo de assinatura pronto (`-Sign` com `signtool` e certificado .pfx ou impressão digital);
  - instalador de teste (`-TestMode`) que roda sem administrador, para testes automáticos.
- **Pacote Linux** (`scripts/build_linux.py`, `installer/linux/`):
  - `.deb` (amd64, i386, arm64, armhf) e `.tar.gz` por arquitetura, montados sem `dpkg-deb` e
    reprodutíveis;
  - `postinst` atualiza e reinicia os serviços quando o PC já está cadastrado; `prerm` remove os
    serviços; `postrm purge` apaga os dados;
  - units do systemd geradas pelo próprio `dm-agent install` (template único);
  - `install.sh` servido já preenchido: detecta a arquitetura, baixa o pacote certo, cadastra, instala e
    inicia.
- **Downloads**:
  - tabela `installers`, publicação pelo superadmin com sha256 e auditoria;
  - a versão mais nova de cada tipo e arquitetura é a oferecida; retirar e devolver;
  - download no portal;
  - link público `/api/public/installer` e `/api/public/install.sh`, válido **só com código de cadastro
    vigente**, com limite de requisições;
  - página Downloads no portal;
  - o diálogo "Novo coletor" mostra o link do instalador, o comando silencioso e a linha única do Linux.
- **CI** (escrito; ainda sem repositório no GitHub para rodar):
  - job Linux: instala o `.deb` pelo `install.sh` com servidor real, confere os serviços no systemd e
    remove com `purge`;
  - job `windows-installer`: PostgreSQL do runner, API, Inno Setup e `test-installer.ps1 -Full`.
- `deploy/Dockerfile.backend` passou a levar `installer/linux` (o `install.sh` é servido pela API).

### Testes (todos passando)
| Suíte | Resultado |
|---|---|
| Go `go test -race -tags integration` | cobertura de `internal/` **82,4%**; novos: `CheckEnrollment`, contrato das mensagens de conferência |
| pytest | **222** testes; cobertura **91%**. Novos: conferência do código (não gasta, usado/inválido recusado), `.deb` (formato ar, control, permissões, scripts preenchidos, reprodutível), CLI de pacotes, `install.sh` executado de verdade com comandos falsos (ordem: baixa → dpkg → cadastra → serviços → inicia), downloads (publicação, validação, oferecida/retirada, download, link público com código válido/expirado/inexistente, install.sh preenchido) |
| Vitest | **40** (novo: tipo/arquitetura/versão pelo nome do instalador) |
| Playwright | **6**; o diálogo "Novo coletor" mostra o link do instalador preso ao código e a linha do Linux |
| Instalador Windows (`scripts/test-installer.ps1`, sem administrador) | recusa Windows 7, 8.1 e Server 2012 com a mensagem do PROMPT; Windows 10 aceito; código inválido recusado em português; código válido conferido e ainda válido depois |
| Lint | golangci-lint (Windows e Linux), ruff, mypy --strict, eslint, prettier, tsc, OpenAPI/tipos: sem problemas |

### Fluxo manual executado (02/10/2026) e resultado
1. Inno Setup 6.7.3 instalado só para o usuário (sem administrador); setup.exe de 24 MB gerado, sem
   assinatura (aviso no build).
2. Pacotes Linux gerados para as 4 arquiteturas com os binários da versão 0.8.0.
3. **Publicação** no banco de desenvolvimento como o superadmin: setup.exe, `.deb` amd64/armhf e
   `.tar.gz` amd64.
4. **Download pelos links públicos** com um código novo: o setup.exe e o `.deb` armhf vieram com o
   sha256 publicado; o `install.sh` veio preenchido com servidor e código; o código inexistente recebeu
   404.
5. **Telas** (`frontend/e2e/manual-f8.mjs`): Downloads (os 4 instaladores, "oferecida", sha256) e o
   diálogo "Novo coletor" com o botão do instalador, o comando silencioso e a linha do Linux; nenhum erro
   no console.
6. **Encontrado e corrigido no caminho**:
   - o Git Bash convertia `/VERYSILENT` em caminho; o teste passou a rodar pelo PowerShell;
   - `Out` é palavra reservada no Pascal do Inno;
   - a versão do `.tar.gz` era lida como "1.2.0-linux";
   - o `load_script` dos testes não registrava o módulo, e as dataclasses do script falhavam.

### Como testar
```powershell
scripts\build-installer.ps1 -Version 1.0.0 -Server http://127.0.0.1:8000
scripts\test-installer.ps1 -Server http://127.0.0.1:8000 -Code <código do portal>          # sem administrador
scripts\test-installer.ps1 -Server http://127.0.0.1:8000 -Code <código do portal> -Full    # COMO ADMINISTRADOR
.venv\Scripts\python scripts\build_linux.py --version 1.0.0
```

### O que falta / pendências (ações do usuário)
- **Terminal como administrador**: `scripts\test-installer.ps1 -Full` instala de verdade nesta máquina e
  confere serviços, recuperação, `/health`, atualização e desinstalação. Isso também resolve as
  pendências de serviço das Fases 2 e 5: o instalador instala os 2 serviços.
- **Certificado de assinatura de código**: quando for comprado, use
  `scripts\build-installer.ps1 -Sign` com `DM_SIGN_PFX` ou `DM_SIGN_THUMBPRINT`.
- **CI**: os jobs Linux (pacote) e `windows-installer` rodam assim que houver repositório no GitHub.

---

## Fase 9 — USB e acabamento ✅

### Plano executado
Backup e restauração → impressoras USB no agente → Computadores e leitura manual → teste de resistência →
teste de carga → tema escuro e responsividade → documentação final → CI → testes.

### O que foi feito
- **Backup e restauração** (`scripts/backup.ps1`, `scripts/restore.ps1`):
  - o backup gera:
    - `pg_dump` formato custom;
    - zip do `STORAGE_DIR` (walks, logs, versões, instaladores);
    - manifesto com sha256, revisão das migrações e linhas por tabela, medidas antes e depois do dump
      (faixa `[min, max]`, porque o sistema continua gravando durante o backup);
  - o dump é conferido com `pg_restore --list`, e `-Keep` apaga os backups antigos;
  - a restauração:
    - recria o banco com o superusuário (dono = papel da aplicação);
    - exige `-Force` se o destino tiver tabelas e recusa se houver conexões abertas;
    - confere no fim a revisão e as linhas de cada tabela;
    - `-RestoreFiles` devolve os arquivos conferindo o sha256 de cada um.
- **Impressoras USB no agente** (`agent/internal/usbprint`, `collector/usb.go`):
  - inventário pelo WMI (`Win32_Printer` nas portas `USB*`, com o dispositivo pai para achar a interface
    USBPRINT);
  - contador por PJL (`@PJL INFO PAGECOUNT`) com timeout e cancelamento de E/S;
  - serial da USB ou, sem ele, `USB-` + hash estável (PC + dispositivo + nome);
  - roda em todo PC com coletor (não só no MASTER), no intervalo dos contadores;
  - status sempre e leitura (`source=usb`, `counter_source=pjl`) quando a impressora responde.
- **Protocolo e ingestão**:
  - `DeviceRef` ganhou `source` (`snmp`/`usb`) e `brand`;
  - a impressora USB fica ligada ao PC (`usb_agent_id`) e não tem IP.
- **Computadores** (tela nova no menu, `/api/v1/computers`):
  - PCs com coletor, estado, último sinal e número de impressoras USB;
  - abrir o PC mostra as impressoras com "automático (PJL)" ou "sem contador disponível".
- **Leitura manual** (`POST /api/v1/devices/{id}/manual-readings`, botão em Computadores e no detalhe do
  equipamento):
  - total, PB e cor da folha de contadores; total vazio = PB + cor;
  - mesma regra de contadores dos relatórios: contador menor que a última leitura válida é recusado com
    o motivo, data no futuro é recusada;
  - grava as linhas de `reading_counters`, atualiza o equipamento e audita (`reading.manual`);
  - permissão `readings.adjust`.
- **Teste de resistência** (`scripts/soak.py` / `soak.ps1`):
  - dm-agent real contra o `dev.ps1` por N minutos, com "Ler agora" a cada minuto;
  - mede pelo `/health` (novo bloco `runtime`): memória privada, memória do runtime Go, working set,
    heap, goroutines, handles abertos e fila;
  - falha se crescer mais de 20% depois de estabilizar, se a fila não esvaziar ou se o agente parar;
  - 24 h = `-Minutes 1440`.
- **Teste de carga** (`scripts/load.py` / `load.ps1`):
  - banco `dati_load` próprio (recriado e apagado), API em 8200 e gateway em 8201;
  - 500 coletores cadastrados pelo caminho do dm-agent, um por local, com os 500 WebSockets abertos e
    heartbeat;
  - 2 horas de leituras de 20.000 equipamentos em lotes assinados;
  - 8 consultas da tela de parque (ordenações, busca, filtro, 2ª página, contagens), com limite de 1 s.
- **Tema escuro e responsividade**:
  - `frontend/e2e/manual-f9.mjs` abre todas as 20 telas do menu a 390 px no tema escuro e mede o
    estouro horizontal: **nenhuma tela estoura**;
  - a tabela USB rola dentro do próprio cartão.
- **Documentação final**: `docs/operacao.md` (instalar no cliente, modelo novo com walk, publicar versão,
  backup/restauração, soak e carga) e `docs/piloto.md` (roteiro de 30 dias em 3 a 5 clientes comparando
  com o Datacount e a folha, com critérios de aprovação).
- **Lint e CI**:
  - `soak.py` e `load.py` no ruff/mypy;
  - o `lint.ps1` confere a sintaxe e o BOM de todos os `.ps1` pelo parser do PowerShell;
  - job de CI `soak-load` (soak de 30 min + carga, relatórios como artefato).
- **Encontrado e corrigido no caminho**:
  - **o portal mostrava "A API respondeu HTTP 400" em vez do motivo do erro**: o `openapi-fetch` já
    lia o corpo da resposta, e o `unwrap` tentava lê-lo de novo. Afetava toda tela que usa
    `unwrap`. Agora o motivo vem do erro já lido, com teste;
  - o modelo aparecia como "HP HP LaserJet" (a marca já vem no modelo pelo driver): `printerName`, com
    teste;
  - o `e2e_seed` trocava a senha do usuário de teste a cada execução e derrubava a sessão de outro teste
    rodando ao mesmo tempo: ganhou `--email` (o soak usa `soak@dati.local` e entra de novo quando o token
    de 15 min vence);
  - o roteiro manual esperava `networkidle`, que nunca chega com o SSE ao vivo aberto.

### Testes (todos passando)
| Suíte | Resultado |
|---|---|
| Go `go test -race -tags integration` | cobertura de `internal/` **82,4%**. Novos: `usbprint` (PJL, ID, serial, interface, WMI real), coletor USB, memória privada e handles do processo |
| pytest | **224** testes; cobertura **91%**. Novos: impressora USB ligada ao PC, Computadores (escopo, contagem, PJL), regras da leitura manual (menor recusado, futuro recusado, total obrigatório, linhas, auditoria, entra na produção, permissão) |
| Vitest | **43** (novos: nome da impressora; erro da API lido do corpo já consumido pelo openapi-fetch) |
| Playwright | **7**; novo: Computadores → impressoras USB → leitura manual registrada → contador menor recusado com o motivo na tela |
| Soak (30 min, dm-agent real) | **OK**: memória privada 51,1 → 55,0 MB (estável em ~54–56 MB), runtime Go 15,9 MB fixo, 17 goroutines, ~215 handles, fila 0, `/health` ok em todas as amostras |
| Carga | **OK**: 500/500 WebSockets conectados em 15 s e abertos até o fim; hora 1 (cria os 20.000 equipamentos) em 7,3 min, hora 2 em 4,6 min; 20.000 equipamentos e 40.000 leituras no banco; parque: lista 133 ms, ordenações 150–270 ms, busca livre 738 ms, filtro 209 ms, 2ª página 64 ms, contagens 40 ms (pior de 5 execuções) |
| Lint | golangci-lint (Windows e Linux), ruff, mypy --strict, eslint, prettier, tsc, OpenAPI/tipos, sintaxe/BOM dos PowerShell: sem problemas |

### Fluxo manual executado (02/10/2026) e resultado
1. **Backup** do `dati_dev` e **restauração** em `dati_restore_teste`: 49 tabelas, revisão e contagens
   conferidas; os arquivos voltaram com o sha256 certo.
2. **USB neste PC**: a consulta WMI real rodou e encontrou 0 impressoras (não há impressora USB aqui).
   As duas impressoras USB do seed chegam pelo mesmo protocolo do agente.
3. **Computadores** (`manual-f9.mjs`): o "Coletor E2E" mostra 2 impressoras USB, uma "automático (PJL)"
   com o total e outra "sem contador disponível". A leitura manual foi registrada e um total menor foi
   recusado com o motivo. Capturas no tema claro, no escuro e a 390 px; nenhum erro no console.
4. **Soak de 30 min**:
   - **1ª execução reprovada**: o working set saltou de 23 para 66 MB e ficou em 34 MB, num momento
     em que o coletor estava ocioso, com heap (1,4–2,9 MB), goroutines e handles estáveis. No Windows,
     o working set inclui páginas compartilhadas e mapeadas e varia com o gerenciamento de memória da
     máquina; o teste passou a medir a memória privada e a do runtime Go (D117).
   - Na mesma execução, o "Ler agora" foi recusado a partir dos 15 min (token vencido, e o roteiro
     manual tinha trocado a senha do usuário compartilhado), corrigido como descrito acima.
   - **2ª execução: OK**, sem nenhum aviso.
5. **Carga completa**: OK, com os números da tabela acima.
6. **Para registro**: o p95 da resposta ao heartbeat chegou a 4 s enquanto 500 coletores enviavam
   leituras ao mesmo tempo (API, gateway e banco nesta mesma máquina). Nenhuma conexão caiu (o coletor
   tolera bem mais); vale reavaliar no servidor de produção.

### Como testar
```powershell
scripts\backup.ps1 ; scripts\restore.ps1 -Manifest var\backups\<arquivo>.json -Database dati_restaurado
scripts\soak.ps1 -Minutes 30        # com o scripts\dev.ps1 no ar
scripts\load.ps1                    # ambiente próprio; leva ~15 min
node frontend\e2e\manual-f9.mjs var\manual-f9    # telas, escuro e 390 px (com o dev.ps1 no ar)
```

### O que falta / pendências
- **Impressora USB real**: não há impressora USB neste PC. A leitura por PJL foi testada com respostas
  gravadas e o WMI com a consulta real. Validar com uma impressora USB no piloto (`docs/piloto.md`).
- **Stream contínuo de logs** (registrado na Fase 5 para cá): segue `get_logs` + últimas linhas. Não é
  exigido pelo PROMPT; fica para depois da v1.0 se a equipe pedir.
- **Banco `dati_restore_teste`**: cópia do teste de restauração, pode ser apagada.
- As pendências de administrador das Fases 2, 5 e 8 continuam valendo.

---

## Decisões

| # | Decisão | Motivo |
|---|---|---|
| D1 | `product.json` na raiz é a fonte única do nome; Go usa `go generate` (arquivo gerado versionado, teste confere) | Troca de nome em um só lugar (PROMPT seção 0) sem depender de caminho relativo em runtime no agente |
| D2 | Locks Python com **pip-tools** (`requirements.lock`, `requirements-dev.lock`) | Venv criado com `py -3.12 -m venv` como pede o PROMPT; pip-tools trabalha sobre ele sem gerenciar Python próprio |
| D3 | Sem Makefile | Opcional pelo PROMPT; `make` não existe nesta máquina e o CI chama os mesmos comandos |
| D4 | Node 24 LTS instalado no lugar do 20.20 | PROMPT pede Node LTS; o 20 saiu de suporte em abr/2026 e o Vite 8 exige versão mais nova |
| D5 | GCC (WinLibs) instalado | `go test -race` (obrigatório pelo PROMPT) exige CGO no Windows; os binários de produção continuam com `CGO_ENABLED=0` |
| D6 | Linter `misspell` desativado no Go | Ele marca palavras em português (ex.: "comando") como erro; mensagens ao usuário são em pt-BR por regra |
| D7 | snmpsim: um processo por impressora, porta `1160+NN`, comunidade `public` | Mapeamento porta→impressora simples e igual ao da seção 0.1; cada impressora pode ser parada/trocada isoladamente (cenário 8 da seção 13) |
| D8 | Impressora simulada `05-generica` criada já na Fase 0, só com OIDs padrão; `sysObjectID` = `1.3.6.1.4.1.8072.3.2.10` (agente Net-SNMP) | Validar o snmpsim no Windows desde já sem inventar OID de fabricante; as outras 7 entram na Fase 2 |
| D9 | Dockerfiles/compose escritos mas **não testados** | Máquina sem virtualização; nenhum fluxo depende de Docker agora (regra 6) |
| D10 | Logs do snmpsim mostram "ERROR Variation module redis/sql load FAILED" | São módulos opcionais que não usamos; o simulador funciona normalmente. Tratar na Fase 2 se atrapalhar |
| D11 | Executar as fases em sequência, sem plan mode a cada fase | Pedido explícito do usuário ("faça todas as fases direto"); o plano de cada fase fica no PROGRESS.md |
| D12 | Todas as tabelas da seção 3 criadas já na Fase 1 (uma migração) | As fases seguintes só acrescentam comportamento; evita migrações encadeadas de criação |
| D13 | Tabelas somente-inserção (`readings`, `supply_readings`, `agent_heartbeats`, `audit_log`, `device_events`) têm só `created_at` | São imutáveis por definição; `updated_at` seria sempre igual |
| D14 | Unicidade de `idempotency_key` numa tabela à parte (`reading_idempotency`) | O PostgreSQL não aceita UNIQUE em tabela particionada sem a chave de partição |
| D15 | Classificação de regressão de contador em `reading_reviews` (não em `readings`) | `readings` é imutável (R6); a exclusão do relatório consulta a classificação |
| D16 | Partição `default` + função que move as linhas ao criar a partição do mês | Leitura com data fora da janela (relógio errado, fila antiga) nunca é rejeitada nem perdida |
| D17 | Bloqueio de alteração por trigger, com "modo manutenção" (`SET LOCAL dati.maintenance`) usado só pela manutenção de partições e pelos testes | Protege contra erro de aplicação; não é barreira contra um DBA (documentado) |
| D18 | Papéis/permissões definidos no código e espelhados nas tabelas no bootstrap | Uma fonte da verdade; a tabela serve para consulta e relatórios |
| D19 | O token de acesso carrega a "versão da senha" (µs de `password_changed_at`) | Comparar só o `iat` (segundos) deixaria válido um token emitido no mesmo segundo da troca |
| D20 | TOTP obrigatório para `reseller_admin` via configuração da revenda `security.require_totp_for_admins` | PROMPT: "obrigatório se configurado"; sem a configuração, é opcional |
| D21 | "Esqueci a senha" responde 202 sempre; falha de SMTP vira ERRO no log (não na resposta) | Responder erro revelaria que o e-mail existe; o reset feito pelo admin mostra o erro na tela |
| D22 | Sem biblioteca de validação de e-mail (regex simples + minúsculas) | O admin inicial é `admin@local` (exigido pelo PROMPT), que validadores estritos recusam |
| D23 | O servidor guarda só `K = SHA-256("dm-agent-auth\n" + S)` (cifrada com a chave mestre); o agente guarda `S` (DPAPI) e assina com `K` | Vazamento do banco não entrega o segredo do agente; teste confere que agente e servidor derivam a mesma chave e assinatura |
| D24 | `ip_ranges.ports` (várias portas SNMP por faixa) e `devices.snmp_port` | O simulador tem 8 impressoras no mesmo IP; também cobre NAT/portas não padrão em cliente |
| D25 | Local novo recebe a credencial `v2c public` (posição 1); locais anteriores à Fase 2 não | É o padrão de fábrica das impressoras; sem credencial o agente registra ERRO visível e o portal permite incluir |
| D26 | Impressora sem serial SNMP ganha identidade `MAC-<mac>`; sem serial e sem MAC não é registrada (erro no log) | Identidade estável é pré-requisito para histórico e anti-duplicidade |
| D27 | Se o perfil não resolve `total`, usa-se `prtMarkerLifeCount` (fonte `standard`) | Nenhuma impressora fica sem contador; a fonte fica registrada na leitura |
| D28 | `v3_context` existe só no agente (simulador); o portal não expõe | Impressoras reais usam contexto vazio; o snmpsim exige o contexto `public` |
| D29 | Regex dos perfis com `regexp2` (compatível com .NET/PCRE) e tempo limite de 200 ms | Os perfis fornecidos usam lookahead, que o RE2 do Go não suporta; o limite evita travamento por regex |
| D30 | AES256 = extensão de chave Blumenthal (a do Net-SNMP) | É a usada pela maioria das impressoras e pelo snmpsim (`AES256BLMT`) |
| D31 | Economia de energia simulada por um proxy UDP (`scripts/sleepy_udp_proxy.py`) na frente do snmpsim | O snmpsim não tem "responder só na 2ª tentativa"; o proxy descarta pacotes enquanto "acorda" |
| D32 | Mudança de faixa **ou de credencial** dispara varredura imediata | Encontrado no fluxo manual: com só a faixa, uma credencial nova esperava o intervalo de descoberta (6 h) |
| D33 | Logs do agente em UTC | Regra "tudo armazenado em UTC"; alinha com os logs do servidor independente do fuso do PC |
| D34 | Presença numa tabela (`agent_presence`) com `gateway_id`; comandos por `pg_notify` na mesma transação | Sem Redis (seção 2); a notificação só é entregue se a transação confirmar, então o gateway nunca vê comando desfeito; a varredura periódica cobre notificações perdidas |
| D35 | Pausa decidida pelo servidor (`agents.paused`) | Sobrevive a reinício do coletor; na Fase 2 a pausa era só o eco do que o agente dizia |
| D36 | Comandos de diagnóstico (`read_device`, `snmp_test`, `mib_walk`, `ping_host`) só aceitam IPs privados, de loopback ou link-local | O coletor não pode virar ferramenta para sondar a internet a partir da rede do cliente |
| D37 | Com o WebSocket caído, heartbeat pelo HTTPS só depois de 2 min (seção 4.3); atualizações de comando vão pelo HTTPS sempre que o WebSocket não confirma | O lease do MASTER dura 3 min, então 2 min sem heartbeat não derruba o papel; o resultado de um comando nunca espera o WebSocket voltar |
| D38 | `dev.ps1` sobe cada serviço no próprio console oculto (`-WindowStyle Hidden`), não mais com `-NoNewWindow` | No Windows o `uvicorn --reload` reinicia o filho com `CTRL_C_EVENT`, que atinge o console inteiro: salvar um arquivo derrubava o supervisor, o worker e os simuladores sem aviso |
| D39 | Comandos executados pelo watchdog (`restart_agent`, `uninstall`, `get_logs` do watchdog) só serão aceitos pela API na Fase 5 | Aceitar um comando que ninguém executa seria um stub (ficaria `pending` até expirar) |
| D40 | Arquivos enviados pelos coletores (logs, walks) ficam em disco (`STORAGE_DIR`, padrão `var/storage`), com o caminho no banco | Seção 3: "armazenar arquivo em disco/objeto e referência aqui"; na hospedagem vira um volume |
| D41 | Reconexão do WebSocket volta ao backoff mínimo quando a sessão durou ≥ 10 s ou o servidor fechou com 1001/1012 | Reinício do gateway (deploy, reload) não pode deixar coletores fora por até 60 s; falhas de conexão continuam com backoff crescente |
| D42 | Cliente TypeScript gerado do OpenAPI (`openapi.json` versionado + `openapi-typescript --default-non-nullable false`); modelos de resposta com campos default marcam `json_schema_serialization_defaults_required` | Tipos do portal sempre iguais aos da API; o lint e o CI falham se ficarem desatualizados |
| D43 | `TokenResponse` do portal renomeado para `SessionResponse` | Dois modelos com o mesmo nome (agente e portal) viravam `app__schemas__...` no OpenAPI |
| D44 | Tempo real do portal por **SSE** (`/api/v1/events`) sobre LISTEN/NOTIFY; o evento só invalida as consultas afetadas | Sem Redis (seção 0); o portal busca os dados de novo pela API, que já aplica o escopo |
| D45 | "Terminal de logs ao vivo" na Fase 4 = `get_logs` + últimas linhas; o stream contínuo fica para a Fase 5 | Um stream de verdade precisa de um canal de logs no agente/watchdog, que é escopo da Fase 5 |
| D46 | O dashboard mostra "Toners críticos (≤ 10%)" até existir a previsão de término (Fase 6) | "Acabam em até 7 dias" sem o cálculo de previsão seria dado inventado |
| D47 | E2E com dados criados pelos fluxos reais (`scripts/e2e_seed.py`, só dev/CI) e senha aleatória por execução, só em memória; testes em série | Nada de fixture falsa nem senha fixa; como o usuário e o banco são compartilhados, rodar em paralelo ficava instável |
| D48 | Simuladores do E2E em portas próprias (12161–12168; a 12160 indica que estão prontos) | O E2E roda mesmo com o `dev.ps1` no ar (1161–1168), sem disputar portas |
| D49 | Versão do Windows lida por `RtlGetNtVersionNumbers`; o `RtlGetVersion` fica só para saber se é Server | O shim de compatibilidade (`__COMPAT_LAYER=Win7RTM`, "modo de compatibilidade" no .exe) faz o `RtlGetVersion` responder 6.1, e o coletor recusaria um Windows 10 |
| D50 | O portal só tenta recuperar a sessão se existir o cookie `dm_csrf` | Sem esse cookie não há sessão; a chamada gerava um 403 no console a cada visita anônima |
| D51 | A assinatura de uma versão cobre componente, versão, SO, arquitetura e sha256 (`release_message`); o `dm-tool sign` só assina se o binário se declarar como o componente e a versão publicados | Um binário assinado antigo não pode se passar por versão nova nem por outro alvo; e um `dm-tool` (ou watchdog) com o número certo não pode ser publicado como coletor |
| D52 | Chave privada de assinatura fora do repositório e do servidor (`%USERPROFILE%\.dati-monitor\release-signing.key`, gerada nesta máquina); a pública fica em `agent/internal/release/public.key`, embutida nos binários e lida pelo servidor (ou `RELEASE_PUBLIC_KEY`) | Seção 5.2: a privada nunca vai ao servidor web. **Guarde cópia da chave privada**; trocar de chave = `dm-tool keygen` + recompilar + atualizar a pública |
| D53 | O watchdog usa a mesma credencial do coletor (DPAPI da máquina) e tem canal próprio `POST /api/watchdog/heartbeat`; comandos têm `target` agent/watchdog | Seção 5.1: canal simples, independente do WebSocket e do código do coletor; nada de segunda credencial para cadastrar |
| D54 | Watchdog com "modo processo" (`--agent-exe`), além do modo serviço | Desenvolvimento, E2E e teste de caos sem terminal de administrador; em produção o watchdog controla o serviço `DatiMonitorAgent` |
| D55 | Consulta a serviços do Windows com direitos mínimos (`SC_MANAGER_CONNECT` + `SERVICE_QUERY_*`); iniciar/parar pede só `SERVICE_START`/`SERVICE_STOP` | O `mgr.Connect` do Go pede acesso total e falha fora de administrador; a vigilância mútua e o `status` precisam funcionar em qualquer conta |
| D56 | Atualização automática: canary recebe canary e estável, estável só estável; liberação gradual por `sha256(coletor:versão) % 100`; nunca versão com falha > 5% no canary; nunca repete no coletor uma versão que falhou nele; uma por vez; só com o executor vivo | Seção 5.2 e rollout previsível (o mesmo coletor sempre cai no mesmo lado da porcentagem) |
| D57 | Failover: prioridade menor = preferido, empate pela menor latência média; coletor pausado nunca assume; o MASTER preferido fixado pelo operador assume assim que fica online | Seção 4.8 ("maior prioridade" = número menor, como no portal); pausa é manutenção no cliente |
| D58 | Reativar etapa 2: o portal acompanha a volta do coletor por até 3 min contra o horário do servidor (`requested_at`) | Relógio errado no PC do técnico não pode dar "voltou"/"não voltou" falso |
| D59 | Teste de contrato Go × schemas do servidor; no Go, coleção com padrão no servidor vai `omitempty` e coleção obrigatória nunca vai `null` | O Go serializa lista vazia (nil) como `null` e o servidor recusava: o heartbeat do watchdog nunca era aceito e um PC sem IPv4 não conseguiria se cadastrar |
| D60 | No teste de caos sem administrador, o passo do banco derruba todas as conexões (`pg_terminate_backend`) em vez de reiniciar o serviço; o relatório diz qual dos dois foi feito | Reiniciar `postgresql-x64-16` exige administrador; a recuperação de API/gateway/worker é a mesma nos dois casos. Rodar `scripts\chaos.ps1` como administrador reinicia o serviço de verdade |
| D61 | Equipamento novo entra `pending` e **lido normalmente**; só o descartado deixa de ser lido (`ignored_serials` + resposta `discarded`) | Nenhuma leitura se perde enquanto ninguém decide; ao ativar, o histórico já existe |
| D62 | `ignored_serials` vale para a revenda inteira | O serial é a identidade do equipamento na revenda (seção 3); o mesmo equipamento pode aparecer em outro local |
| D63 | `reading_counters` sem FK para `readings` | A manutenção de partições move linhas da partição default antes do ATTACH, e a FK barraria a movimentação; as linhas nascem na mesma transação da leitura e são só inserção |
| D64 | Mapeamento padrão dos contadores normalizados no servidor; `color` = `full_color` (na Canon inclui monocor); "pequeno" da Canon e "Total 2" ficam só no `extra` até o perfil mapear | Nada de significado inventado; o perfil corrige com `line` sem mudar OIDs |
| D65 | Troca de suprimento só para suprimento consumível; rendimento usa o total para preto/desconhecido e o contador de cor para C/M/Y; sem troca anterior conhecida o rendimento fica vazio | Reservatório de resíduo enche (o nível subir não é troca); rendimento sem ponto de partida seria inventado |
| D66 | Alerta da impressora identificado por índice + código + grupo + local + `prtAlertTime` + descrição; encerrado quando some da tabela | O `prtAlertTime` muda a cada alerta novo mesmo quando a impressora reaproveita o índice |
| D67 | Classificação: atolamento → falhas (chamado técnico) → consumível → peças → nível de treinamento "fieldService" → outros | Ordem que evita, por exemplo, "vida do fusor quase no fim" virar chamado técnico só pelo nível de treinamento |
| D68 | Setor segue o `sysLocation` até alguém digitá-lo; vazio volta a seguir | Seção 16.7: "padrão = sysLocation, editável" sem a leitura sobrescrever o que o usuário digitou |
| D69 | Matriz de permissões: Clientes inclui empresas e locais; Coletores inclui faixas e credenciais; configuração de coleta do local pede "Clientes: Alterar"; ativar em Descobertas = Equipamentos: Incluir, descartar = Excluir, restaurar = Alterar; o papel Cliente só recebe Consultar; admin e superadmin sempre com tudo | Os seis módulos pedidos cobrem todas as telas; impede a revenda de se trancar fora e o usuário de cliente de alterar dados |
| D70 | O papel Cliente não consulta Empresas, mesmo com "Clientes: Consultar" | Era assim antes da matriz (não tinha `companies.read`); a lista mostraria empresas da revenda a um usuário de cliente |
| D71 | Tentativas SNMP no portal = retentativas + 1 (1 a 5); configuração antiga com 5 retentativas é limitada a 4 na entrega ao coletor | O Datacount fala em tentativas; o protocolo e o gosnmp usam retentativas |
| D72 | Listas do portal por cursor com "Carregar mais" e seletores com busca de 20 resultados; exportações sem teto | Regra 13: nada de lista inteira no navegador nem limite fixo de registros |
| D73 | ViaCEP chamado pelo navegador | O servidor não precisa de saída para a internet para isso; falha do serviço vira mensagem clara e o endereço pode ser digitado à mão |
| D74 | Nos testes do backend o local da fábrica ativa automaticamente; os de Descobertas usam `auto_activate=False` (padrão do produto). No E2E, o seed volta para pendentes as impressoras do local do coletor real | Os testes de leitura/parque continuam testando o que testavam; Descobertas é testada com o padrão real |
| D75 | Uma regra por tipo por revenda, criada automaticamente, e exceções por cliente; a regra da revenda não se exclui (desativa) | Seção 16.15: regras centralizadas; ninguém fica sem regra por engano |
| D76 | Regra sem canais marcados avisa em todos os canais ativos da revenda | O padrão útil para uma revenda com um canal só; marcar canais restringe |
| D77 | Credenciais de notificação cifradas por canal (`notification_channels.config_enc`), não numa chave única de `settings` | Vários canais e provedores por revenda; mesma cifra AES-GCM (associada ao id do canal) |
| D78 | Fila de notificação por `alerts.notified_at`; a migração marca os alertas antigos como notificados | Cobre alertas do motor e da ingestão igual; não dispara e-mails retroativos |
| D79 | Silêncio adia (não descarta) o que não é crítico; alerta resolvido antes do envio é suprimido | Seção 9; ninguém recebe às 7h um alerta que já se resolveu de madrugada |
| D80 | Agrupamento por local: sem nenhum coletor online no local, só o alerta do coletor | Seção 9: "um alerta por coletor, não um por equipamento" |
| D81 | Atolamento, porta aberta e erro de hardware só alertam com o equipamento respondendo | Status de equipamento sem resposta é antigo; evita alertas fantasmas |
| D82 | Alertas da validação de leituras (regressão, salto, soma) nunca se resolvem sozinhos; a regra só controla a notificação | Exigem classificação do operador (seção 6.5) |
| D83 | Previsão: regressão desde a última troca (subida ≥ 10 pontos), janela pelo intervalo de 95% da inclinação, confiança = R² × pontos/10 × dias/7; método direto quando a regressão não serve; confiança < 0,5 = "estimativa incerta", sem alerta e fora do painel | Seção 16.6: previsão incerta não aparece como certa |
| D84 | Retentativa em 1, 2, 5, 15, 30 e 60 min, até 6 tentativas (configurável) | Cobre quedas curtas do provedor sem insistir para sempre |
| D85 | Tempos do worker por variável de ambiente (offline 180 s, avaliação 60 s, envio 10 s); o E2E usa 15/3/2 s | O critério 8 é medido com os padrões; o E2E não pode levar minutos esperando |
| D86 | Resumo diário só em canais de e-mail, desligável por canal e por revenda | WhatsApp não é lugar para relatório longo |
| D87 | O limiar de troca de suprimento (16.3) é editado em Alertas > Configurações e gravado na chave lida pela ingestão | Uma tela só para os parâmetros de alerta da revenda |
| D88 | Nada de funcionalidade de revenda (instrução do usuário em 29/09); parâmetros "por revenda" do PROMPT viram parâmetros da empresa; a estrutura multi-revenda existente fica como detalhe interno até o usuário decidir removê-la | O sistema é de uso exclusivo da Daticopy |
| D89 | Uma regra de contadores só (`services/reports/counters.py`) para relatórios, dashboard, API do ERP e conector; produção = soma dos aumentos entre leituras válidas consecutivas, queda conta zero | Critério 10 (regressão não entra) sem perder páginas depois de troca de placa; todos os números do sistema batem entre si |
| D90 | Leitura de corte inclui o próprio dia (até 23:59 de Brasília) | É o uso na cobrança: a leitura do dia do corte vale |
| D91 | Relatórios calculados no servidor e paginados na tela (até 500 linhas por página); exportações com todas as linhas; PDF limitado a 5.000 linhas com mensagem para usar CSV/XLSX | Regra 13 (nada de lista inteira no navegador) e PDF legível |
| D92 | PDF com reportlab | Licença BSD, rodas para Windows e Linux, fontes padrão com acentos pt-BR |
| D93 | Versão de perfil publicada no portal sobrevive ao reinício; só uma mudança real no YAML do repositório cria versão nova por cima | O ajuste feito na tela com um walk real não pode sumir no próximo deploy |
| D94 | Schema dos perfis exige `counters` ou `counter_sources` | Publicar um perfil vazio pela tela zeraria a leitura de toda a marca |
| D95 | Token da API do ERP com prefixo `dmerp_`, mostrado uma vez, só o SHA-256 no banco, `last_used_at` no máximo uma vez por minuto | Igual a tokens de API comuns; vazamento do banco não entrega o token |
| D96 | Conector só envia alertas abertos depois de ligado (`enabled_since`) | Ligar a integração não pode criar centenas de OS do histórico |
| D97 | Transporte do conector plugável (arquivo e HTTP prontos, e-mail para "apenas e-mail"); banco de dados só com o layout da Databit | Seção 16.11; um transporte "de banco" sem layout seria um stub |
| D98 | Importação CSV tudo ou nada, com botão Validar antes | Arquivo meio importado é pior que nenhum; o relatório de erros diz o que corrigir |
| D99 | Mapa com Leaflet e quadros do OpenStreetMap (com atribuição), carregados pelo navegador | Sem chave de API nem custo; o servidor não precisa de internet |
| D100 | Túnel web por quadros JSON (base64) no WebSocket existente; quadros do túnel fora do limite de mensagens, com limite de banda próprio (1 MiB/s) e teto por sessão (200 MiB) | Seção 4.9; o canal do coletor já existe e atravessa qualquer firewall |
| D101 | Página da impressora servida com `CSP: sandbox` sem `allow-same-origin`, cookies da impressora reescritos para `SameSite=None; Secure` sob o prefixo da sessão; Caddy deixa `X-Frame-Options` como SAMEORIGIN só em `/devweb` | A página da impressora fica na mesma origem do portal: sem o sandbox, um script dela poderia agir como o usuário logado |
| D102 | Link `/devweb/{token}/?dm_key=` vale para o primeiro navegador (cookie HttpOnly no caminho da sessão) | "Token de uso por usuário" da seção 4.9: um link vazado não abre em outro navegador |
| D103 | Abrir por IP digitado só para impressora ativa cadastrada no local; a recusa é auditada (`web_session.denied`) e confirmada antes do erro | Critério 14 |
| D104 | O gateway reconhece os quadros do túnel pelo campo `type` da mensagem, não pelos bytes | A primeira versão dependia da ordem e dos espaços do JSON, e o teste com outro cliente mostrou que isso é frágil |
| D105 | O instalador confere o código no servidor sem gastá-lo (`/api/agent/enroll/check`); o cadastro de verdade só acontece no fim da instalação | Cancelar a instalação depois de digitar o código não pode inutilizar o código de uso único |
| D106 | Um setup.exe com os binários de amd64, arm64 e 386, escolhendo pela arquitetura do PC | O técnico baixa um arquivo só, sem saber a arquitetura do PC do cliente |
| D107 | A recusa de Windows antigo é feita pelo próprio instalador (Pascal), com a mesma regra e texto do agente; `MinVersion=6.1` | Os binários Go não rodam no Windows 7: a mensagem do PROMPT tem de sair antes de qualquer binário |
| D108 | Instalador de teste (`-TestMode`, sem administrador, só verificações, `/SIMULATEOS`) para os testes automáticos; o instalador real não aceita simulação | Testar a recusa de Windows antigo e a conferência do código sem UAC; ninguém consegue burlar a checagem no setup de produção |
| D109 | O pacote Linux não traz units estáticas: o `postinst` e o `install.sh` usam `dm-agent install`/`dm-watchdog install` | Uma fonte só para a unit do systemd (o template já testado no Go) |
| D110 | `.deb` montado em Python (ar + tar.gz), reprodutível | Gera os pacotes nesta máquina Windows sem `dpkg-deb`; mesma entrada, mesmos bytes |
| D111 | Link público do instalador e do `install.sh` vale só com código de cadastro vigente (resposta 404 igual para inexistente, usado ou expirado) e tem limite de requisições | Técnico instala sem login no portal; ninguém de fora baixa o instalador nem descobre códigos |
| D112 | Só o superadmin publica instaladores; qualquer usuário com acesso a Coletores baixa no portal | Mesma regra das versões do coletor: o binário roda em todos os clientes |
| D113 | Desinstalação remove também a pasta de dados (credencial, fila, logs) | "Desinstalador limpo" (Fase 8); quem reinstala recebe um código novo no portal |
| D114 | Assinatura de código pronta no build (`-Sign`, `signtool`, carimbo de tempo, certificado .pfx ou impressão digital), desligada até existir o certificado | PROMPT: deixar o passo pronto; sem certificado, o build avisa em vez de falhar |
| D115 | Backup = `pg_dump -Fc` + zip dos arquivos + manifesto; contagem de linhas como faixa medida antes e depois do dump; a restauração recria o banco e confere revisão e contagens | O sistema continua gravando durante o backup (contagem exata falharia); restaurar sem conferir não prova nada |
| D116 | USB pelo WMI via PowerShell (`Get-CimInstance`) e PJL direto na interface USBPRINT; sem cgo e sem driver próprio | O agente é compilado com `CGO_ENABLED=0`; o PowerShell existe em todo Windows suportado |
| D117 | O coletor USB roda em todo PC (não só no MASTER) e a impressora fica ligada ao PC (`usb_agent_id`), sem IP | A impressora USB só é visível do PC onde está ligada |
| D118 | Leitura manual segue a mesma regra de contadores: menor que a última leitura válida é recusado com o motivo (corrige-se a leitura errada no histórico), total vazio = PB + cor | Uma regra só para relatórios, ERP e conector; a digitação não cria regressão |
| D119 | O soak avalia a memória **privada** do processo (Windows "Private Bytes"; Linux `RssAnon`) e a memória do runtime Go; o working set fica só no relatório | O working set do Windows inclui páginas compartilhadas e mapeadas e oscilou de 23 para 66 MB com o coletor ocioso e o heap estável; a memória privada é o que cresce num vazamento |
| D120 | O teste de carga roda em ambiente próprio (`dati_load`, portas 8200/8201) e respeita o limite de requisições por IP (espera e repete no 429) em vez de afrouxá-lo | Não suja o banco de desenvolvimento; mede o sistema com a configuração real |
| D121 | Limite da carga: uma hora de leituras de 20.000 equipamentos recebida em até 15 min; cada consulta do parque abaixo de 1 s (pior de 5 execuções, medida do cliente) | Folga de 4× sobre o ritmo real; o portal virtualiza a tabela, então o tempo da tela é o tempo da API |
| D122 | `e2e_seed.py --email` e usuário próprio para o soak | Cada execução do seed troca a senha do usuário que recebe; testes simultâneos não podem derrubar a sessão um do outro |
| D123 | O `lint.ps1` confere sintaxe e BOM de todos os `.ps1` | Erro de sintaxe num script de backup ou instalador só apareceria na hora de usar |

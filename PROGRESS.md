# PROGRESS — Dati Monitor

Estado das fases da seção 14 do `PROMPT.md`.

| Fase | Situação |
|---|---|
| 0 — Fundação | ✅ concluída (24/09/2026) |
| 1 — Backend núcleo | ✅ concluída (24/09/2026) |
| 2 — Agente núcleo | ✅ concluída (25/09/2026) — falta só instalar o serviço Windows num terminal de administrador (ver abaixo) |
| 3 — Tempo real e comandos | ⏳ próxima |
| 4 a 11 | pendentes |

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

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
| `backend/app/core/` | Config (`pydantic-settings`, lê `.env` da raiz), banco, segurança (argon2/JWT), cripto AES-GCM, `Principal` + escopos, permissões, erros, e-mail, rate limit. |
| `backend/app/models/` | Todas as tabelas (SQLAlchemy 2 tipado). `PARTITIONED_TABLES`/`APPEND_ONLY_TABLES` em `__init__`. |
| `backend/app/schemas/` | Pydantic de entrada/saída da API. |
| `backend/app/services/` | Regras de negócio. **Toda função recebe o `Principal` e aplica `reseller_scope`/`customer_scope`**; escritas chamam `audit.record`. |
| `backend/app/api/v1/` | Roteadores finos: validam, chamam o serviço e fazem `session.commit()`. `api/deps.py` = autenticação central. |
| `backend/app/cli.py` | `python -m app.cli migrate / bootstrap / seed-dev / ensure-partitions`. |
| `backend/app/api/` | Processo da API REST (porta 8000). `create_app()` é fábrica (`uvicorn --factory`). |
| `backend/app/gateway/` | Processo do gateway WebSocket dos agentes (porta 8001). |
| `backend/app/worker/` | Jobs agendados (APScheduler). `python -m app.worker.main`. |
| `backend/alembic/` | Migrações (a URL vem de `DATABASE_URL`, nunca do `alembic.ini`). |
| `backend/tests/` | pytest contra o PostgreSQL real (`dati_test`). |
| `frontend/` | Portal React 18 + TS + Vite + Tailwind. Testes: Vitest (`src/**/*.test.tsx`) e Playwright (`e2e/`). |
| `profiles/` | Perfis de leitura YAML. `canon.yaml` e `konica-minolta.yaml` são fornecidos: **não alterar OIDs**. |
| `profiles/recordings/sim/NN-nome/public.snmprec` | Impressoras simuladas (snmpsim), porta UDP `1160+NN`. |
| `profiles/recordings/real/` | Walks de impressoras reais (Fase 10). |
| `scripts/` | PowerShell do dia a dia + `smtp_catcher.py`. |
| `deploy/` | Dockerfiles, compose e Caddyfile da hospedagem futura (**não usados agora**). |
| `installer/` | Inno Setup / systemd (Fase 8). |
| `docs/` | Arquitetura, protocolo, operação. |
| `var/` | Gerado em execução (logs, e-mails capturados, cache do snmpsim). Ignorado pelo git. |

## Comandos (terminal comum, sem administrador)

```powershell
scripts\setup-db.ps1        # cria papel "dati" e bancos dati_dev/dati_test (idempotente)
scripts\init-env.ps1 -PostgresPassword X   # cria .env com segredos aleatórios (instalação nova)
scripts\dev.ps1             # migra, faz seed e sobe API, gateway, worker, portal, smtp_catcher e snmpsim; Ctrl+C encerra
scripts\stop-dev.ps1        # encerra o dev.ps1 (se a janela foi fechada sem Ctrl+C)
scripts\test.ps1            # Go (-race, cobertura >= 80% internal/), pytest (>= 80%), Vitest
scripts\test.ps1 -E2E       # + Playwright
scripts\lint.ps1            # golangci-lint, ruff, mypy --strict, eslint, prettier, tsc
scripts\build-agent.ps1 [-Version x.y.z]   # 3 binários x 7 alvos em dist\
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
- Novas listas: paginação por cursor (`services/pagination.py`) e exportação (`services/export.py`).
- Testes de API usam as fixtures `client`, `factory` e `login()` de `tests/conftest.py`; o banco
  `dati_test` é recriado por sessão e esvaziado a cada teste (`clean_db`).
- Primeiro start: a senha temporária do `admin@local` aparece no console do dev.ps1/API.
- Edições complexas por script: grave o script em arquivo (heredocs longos no bash quebram neste ambiente).

## Ambiente desta máquina

Windows 10 Pro **sem virtualização**: nada de Docker/WSL. PostgreSQL 16 nativo (serviço
`postgresql-x64-16`, início automático). Go 1.27, Python 3.12 (`py -3.12`; o `python` padrão é 3.11),
Node 24 LTS, golangci-lint v2 em `%GOPATH%\bin`, GCC (WinLibs, via winget) para `go test -race`.
Portas: API 8000, gateway 8001, portal 5173, SMTP 1025, e-mails 8025, snmpsim 1161–1168.

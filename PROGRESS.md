# PROGRESS — Dati Monitor

Estado das fases da seção 14 do `PROMPT.md`.

| Fase | Situação |
|---|---|
| 0 — Fundação | ✅ concluída (24/09/2026) |
| 1 — Backend núcleo | ⏳ próxima |
| 2 a 11 | pendentes |

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

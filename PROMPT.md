# PROMPT — Sistema de Leitura de Impressoras (substituto do Datacount)

> **Como usar:** crie uma pasta vazia com esta estrutura e abra no VS Code:
> ```
> PROMPT.md                      (este arquivo)
> profiles/canon.yaml            (fornecido)
> profiles/konica-minolta.yaml   (fornecido)
> ```
> Diga ao Claude Code: `Leia PROMPT.md inteiro e execute. Comece pela Fase 0.`
> Em sessões seguintes: `Leia PROMPT.md e PROGRESS.md e continue de onde parou.`
>
> **Decisões já tomadas (não reabrir):** coletor em **Go**; backend **Python/FastAPI + PostgreSQL**; portal **React**; sem hardware dedicado no cliente (o coletor roda em PCs do cliente, com watchdog e cluster); faturamento fica no ERP; hospedagem decidida depois; **sem Docker por enquanto** (a máquina de desenvolvimento é Windows 10 Pro sem virtualização — tudo roda nativo, ver seção 0.1); **sem versão para Windows 7/8/2008/2012 por enquanto**.

---

## 0. PAPEL E REGRAS DE TRABALHO

Você é o engenheiro responsável por construir, do zero, um sistema completo de monitoramento de impressoras para a **Daticopy** (locação de impressoras/multifuncionais, Rio de Janeiro). O sistema substitui o **Datacount** (Printerconnect) e deve ter tudo o que ele tem, com mais confiabilidade. **Bilhetagem por usuário/job NÃO faz parte do escopo.** **Faturamento fica no ERP da empresa** — o sistema só precisa fornecer leituras e exportações confiáveis para ele.

Nome provisório do produto: **Dati Monitor** (use como constante única e configurável — nome dos serviços Windows, instalador, título do portal e pastas derivam dela; o nome definitivo pode ser trocado depois em um só lugar).

Regras obrigatórias:

1. Trabalhe **por fases, na ordem da seção 14**. Não pule fases. Ao terminar cada fase: rode todos os testes, faça commit (`git`) com mensagem clara e atualize `PROGRESS.md` (o que foi feito, o que falta, decisões tomadas, como testar).
2. Antes de cada fase, use plan mode para planejar; depois implemente sem pedir confirmação para cada arquivo.
3. Crie e mantenha um `CLAUDE.md` na raiz com: estrutura do repo, comandos de build/test/run, convenções, e onde fica cada coisa.
4. Código completo e funcional. Nada de `TODO` sem implementação nas partes do escopo da fase. Nada de dados fake no código de produção.
5. **Nunca invente OIDs de fabricante.** Use os OIDs padrão (seção 6.2) e os perfis **já pesquisados e prontos** em `/profiles/canon.yaml` e `/profiles/konica-minolta.yaml` (fornecidos junto com este prompt — copie-os para `/profiles` sem alterar os OIDs). Para os demais fabricantes, deixe os perfis como arquivos preenchíveis a partir de walks reais (seção 6.6).
6. Tudo roda **nativo no Windows, sem Docker** (seção 0.1), com um único comando `scripts\dev.ps1` que sobe backend, worker, portal e simulador. Crie também `Dockerfile`s e `deploy/docker-compose.yml` prontos para a hospedagem futura, mas **nenhum teste ou fluxo pode depender de Docker agora**.
7. Idioma: interface, mensagens e documentação em **português do Brasil**. Código (nomes de variáveis, funções, tabelas) em **inglês**.
8. Horários: armazenar sempre em UTC (`timestamptz`); exibir em `America/Sao_Paulo`.
9. Se algo for ambíguo, tome a decisão mais conservadora para **confiabilidade e integridade das leituras**, registre em `PROGRESS.md` na seção "Decisões" e siga.
10. **"Pronto" significa testado.** Uma fase só termina quando: (a) o código compila sem avisos de lint; (b) todos os testes unitários, de integração e E2E daquela fase passam; (c) você executou de verdade o fluxo manual descrito em `PROGRESS.md` (subindo os serviços locais e o agente) e registrou o resultado. Se um teste falhar, corrija o código — **nunca** desative, pule ou afrouxe um teste para passar.
11. Proibido: stubs, `pass` em lugar de lógica, `NotImplementedError`, endpoints que retornam dados fixos, telas com dados de exemplo no lugar de chamadas reais à API.
12. Use as versões estáveis mais recentes no momento da implementação: Go (≥ 1.25), Python 3.12+, Node LTS, PostgreSQL 16+. **Sem Redis.** Fixe versões em `go.mod`, `pyproject.toml`/lock e `package-lock.json`.

### 0.1 Ambiente de desenvolvimento (Windows 10 Pro, SEM Docker)
Pasta do projeto: `C:\Projetos\dati-monitor`. Já instalados: Git, Node, **Go 1.27**, **Python 3.12** (use `py -3.12`; o `python` padrão da máquina é 3.11 — o venv do projeto deve ser criado com `py -3.12 -m venv .venv`). A virtualização está desativada, então **não use Docker nem WSL**.
- **PostgreSQL 16+ nativo:** se `psql --version` falhar, instale com `winget install -e --id PostgreSQL.PostgreSQL.16` (pedindo aprovação ao usuário), configure o serviço Windows para iniciar automaticamente e guarde a senha do usuário `postgres` só no `.env` (não versionado).
- **Sem Redis** (substituído por LISTEN/NOTIFY + tabelas).
- **Simulador SNMP:** `pip install snmpsim pysmi` no venv.
- **E-mail de teste:** `scripts/smtp_catcher.py` (aiosmtpd) grava os e-mails recebidos em `var/mail/` e expõe uma lista em `http://localhost:8025`.
- **Playwright:** `npx playwright install chromium`.
- Scripts em **PowerShell** (`scripts\*.ps1`). Tudo deve funcionar em um terminal comum, sem administrador — exceto instalar/desinstalar os serviços Windows do agente, que o script avisa que precisa de terminal como administrador e pede ao usuário.
- Portas locais: API 8000, gateway 8001, portal 5173, smtp 1025/8025, snmpsim 1161–1168.

### Aprendizados do projeto anterior da Daticopy (2025, Flask + SQLite + PyInstaller) — não repetir
- Contadores eram sobrescritos no cadastro da impressora (sem histórico) → aqui, leituras são **imutáveis e históricas** (tabela `readings`).
- Envio por HTTP sem TLS e `SECRET_KEY` fixa no código → aqui, só HTTPS/WSS e segredos em variáveis de ambiente.
- `hrPrinterDetectedErrorState` tratado como inteiro → é **OCTET STRING com bit 0 = MSB do 1º byte**.
- Timeout de 0,5 s sem retentativa → impressoras em economia de energia pareciam desligadas. Aqui: timeout 1,5–2 s, retentativas (seção 4.6).
- Consultas paralelas escolhendo "o primeiro que responder" para modelo/serial → aqui, **ordem de prioridade determinística** (seção 6.4 `identity`).
- Serviço sem recuperação automática e agendado só 2×/dia → aqui, watchdog, SCM recovery e intervalos configuráveis.

---

## 1. OBJETIVO E REQUISITOS NÃO NEGOCIÁVEIS

O coletor instalado no cliente **não pode parar**. Hoje, com o Datacount, clientes ficam offline e a equipe precisa acessar a máquina do cliente para reativar. O novo sistema precisa:

- **R1 — Autorrecuperação local:** se o processo do coletor travar, morrer ou vazar memória, ele volta sozinho em segundos, sem intervenção humana.
- **R2 — Controle remoto total pelo portal:** qualquer ação que hoje exige acesso remoto (reativar, reiniciar, reconfigurar, atualizar, ver logs, testar impressora) deve ser feita por um botão no portal.
- **R3 — Redundância (cluster):** um cliente pode ter 2+ coletores (em PCs diferentes). Só um coleta por vez (MASTER); os outros ficam em espera (STANDBY) e assumem automaticamente se o MASTER sumir. Sem leituras duplicadas.
- **R4 — Zero perda de leitura:** se a internet cair, as leituras ficam numa fila local e sobem quando voltar.
- **R5 — Detecção rápida:** coletor sem sinal gera alerta em minutos (portal + e-mail + WhatsApp), antes de o cliente perceber.
- **R6 — Leituras auditáveis:** leitura nunca é editada nem apagada. Correções são registros de ajuste com autor e motivo.
- **R7 — Atualização segura:** autoatualização assinada, em canais (canary → estável), com rollback automático se a nova versão não subir saudável.
- **R8 — Só conexões de saída no cliente:** nenhuma porta de entrada no firewall do cliente. Tudo via HTTPS/WSS 443 iniciado pelo coletor.

---

## 2. ARQUITETURA

```
REDE DO CLIENTE                                         NUVEM / SERVIDOR DATICOPY
┌──────────────────────────────────────┐               ┌──────────────────────────────────────┐
│ Impressoras ◄── SNMP UDP 161 ──┐     │               │  Proxy TLS (na hospedagem futura)     │
│                                │     │   WSS 443     │   ├── /api  → API (FastAPI)           │
│  PC 1: dm-agent (MASTER) ──────┴─────┼──────────────►│   ├── /ws/agent → Gateway WebSocket   │
│        dm-watchdog ──────────────────┼── HTTPS 443 ─►│   └── /     → Portal (React build)    │
│                                      │               │  Worker (alertas, jobs, notificações) │
│  PC 2: dm-agent (STANDBY)            │               │  PostgreSQL 16                        │
│        dm-watchdog                   │               └──────────────────────────────────────┘
└──────────────────────────────────────┘
```

Componentes:

| Componente | Tecnologia | Função |
|---|---|---|
| `agent/` (dm-agent) | Go (versão estável atual, ≥ 1.25), binário único | Descoberta, leitura SNMP, fila local, canal WebSocket, execução de comandos, cluster |
| `agent/` (dm-watchdog) | Go, mesmo repositório, binário separado | Vigia o dm-agent, reinicia, aplica atualizações, faz rollback, tem canal próprio (HTTPS polling) com o servidor |
| `backend/` API | Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic v2 | REST do portal, ingestão de leituras, autenticação |
| `backend/` gateway | FastAPI WebSocket (mesmo código, processo separado) | Mantém conexões persistentes com agentes; recebe/entrega comandos via PostgreSQL `LISTEN/NOTIFY` |
| `backend/` worker | Python, APScheduler | Detecção de offline, alertas, notificações, previsão de toner, retenção, promoção de MASTER |
| `frontend/` | React 18 + TypeScript + Vite + Tailwind + shadcn/ui + TanStack Query + TanStack Table + Recharts | Portal |
| Banco | PostgreSQL 16 | Dados; tabela de leituras particionada por mês |
| Pub/sub e presença | PostgreSQL `LISTEN/NOTIFY` + tabela de presença | Sem Redis: comandos notificados pelo Postgres; presença dos agentes em tabela com `last_seen_at`; rate limit em memória do processo |
| Proxy TLS | Caddy (só na hospedagem futura) | Em desenvolvimento, uvicorn direto em HTTP local |
| Dev | snmpsim via pip (impressoras simuladas), `scripts/smtp_catcher.py` com aiosmtpd (e-mail de teste) | Testes sem impressora real e sem Docker |

Monorepo:

```
/agent            # Go: cmd/dm-agent, cmd/dm-watchdog, cmd/dm-tool, internal/...
/backend          # Python: app/api, app/gateway, app/worker, app/models, app/services, alembic/, tests/
/frontend         # React
/profiles         # perfis de leitura por fabricante/modelo (YAML) + recordings snmprec
/installer        # Inno Setup (Windows), scripts systemd (Linux)
/deploy           # Dockerfiles, docker-compose.yml, Caddyfile (para hospedagem futura; não usados agora)
/scripts          # dev.ps1, test.ps1, acceptance.ps1, chaos.ps1, soak.ps1, setup-db.ps1, smtp_catcher.py
/docs             # arquitetura, protocolo agente↔servidor, manual de operação
CLAUDE.md  PROGRESS.md  PROMPT.md  README.md
```

---

## 3. HIERARQUIA E MODELO DE DADOS (PostgreSQL)

Hierarquia igual ao Datacount: **Revenda → Empresa → Cliente → Local (site) → Coletores + Equipamentos**. A Daticopy é uma revenda; o sistema deve suportar várias (multi-tenant por `reseller_id`, com isolamento obrigatório em toda query).

Tabelas (todas com `id uuid`, `created_at`, `updated_at`; soft delete via `deleted_at` onde fizer sentido):

- `resellers` (revendas): nome, CNPJ, logo, configurações.
- `companies` (empresas): reseller_id, razão social, CNPJ.
- `customers` (clientes): company_id, nome, CNPJ, contato, telefone, e-mail, código no ERP (`erp_code`), ativo.
- `sites` (locais): customer_id, nome, endereço, **cluster** de coletores deste local, fuso.
- `users`: reseller_id, nome, e-mail, hash argon2, papel, TOTP opcional, ativo, último login. Escopo opcional por cliente (usuário de cliente só vê o próprio parque).
- `roles`: `superadmin`, `reseller_admin`, `operator`, `technician`, `customer_viewer`. Permissões por ação (tabela `role_permissions`).
- `agents` (coletores): site_id, nome, tipo (`windows`/`linux`), hostname, SO, IPs locais, MAC do host, versão, canal de atualização (`canary`/`stable`), `cluster_role` (`master`/`standby`), prioridade, estado (`online`/`offline`/`degraded`/`paused`), `last_seen_at`, `last_watchdog_seen_at`, credencial (hash do segredo), `enrolled_at`, `revoked_at`, config atual (JSONB) e `config_version`.
- `agent_enrollment_codes`: código de 8 caracteres, site_id, expira em (padrão 7 dias), uso único.
- `agent_heartbeats`: agent_id, ts, cpu, memória, fila pendente, uptime, versão (reter 30 dias; particionar por mês).
- `ip_ranges`: site_id, CIDR ou início–fim, exclusões, ativo.
- `snmp_credentials`: site_id, ordem, versão (`v1`/`v2c`/`v3`), community (criptografada), usuário v3, auth (SHA/SHA-256), priv (AES/AES-256), senhas criptografadas (AES-GCM com chave mestre em variável de ambiente).
- `brands`, `models`: marca, modelo, `sys_object_id_prefix`, colorida (bool), formato máximo (A4/A3), `profile_id`.
- `read_profiles`: nome, versão, conteúdo YAML validado, ativo. Distribuídos aos agentes.
- `devices` (equipamentos): site_id, customer_id, **serial (identidade principal, único por revenda)**, MAC, IP atual, hostname, marca, modelo, sysObjectID, firmware, `asset_tag` (PAT), setor, observação, `first_seen_at` (Descoberta), `last_read_at` (Comunicação), `last_status`, ativo/desativado, monitorar (bool), `last_agent_id` (DCA).
- `device_events`: device_id, tipo (`discovered`, `ip_changed`, `moved_site`, `replaced`, `counter_regression`, `reactivated`, `deactivated`, `manual_adjust`), dados JSONB, ts, user_id.
- `readings` (**particionada por mês em `read_at`**, somente INSERT; bloquear UPDATE/DELETE com trigger): device_id, agent_id, `read_at` (hora do agente), `received_at` (hora do servidor), `idempotency_key` (único: agent_id + sequência local), `total`, `mono`, `color`, `mono_large`, `color_large`, `copy_mono`, `copy_color`, `print_mono`, `print_color`, `scan`, `fax`, `extra` (JSONB com todos os contadores brutos), `status`, `error_bits`, `source` (`snmp`/`http`/`usb`/`manual`).
- `reading_adjustments`: device_id, reading_id de referência, valores corrigidos, motivo, user_id, ts.
- `supply_readings` (particionada por mês): device_id, read_at, supply_key, descrição, tipo, cor, nível, capacidade, percentual (nullable), `level_state` (`ok`/`unknown`/`some_remaining`).
- `supplies_current`: último estado por device + supply (para a tela de parque ser rápida).
- `alerts`: tipo, severidade, alvo (device/agent/site), estado (`open`/`acknowledged`/`resolved`), aberto em, resolvido em, dados, reconhecido por.
- `alert_rules`: por revenda/cliente: toner abaixo de X%, dias para acabar < N, equipamento sem leitura há H horas, coletor offline há M minutos, contador regrediu, erro de hardware, atolamento, porta aberta.
- `notification_channels`: e-mail (SMTP), WhatsApp (provedor via interface — seção 9), webhook genérico; destinatários por regra.
- `notifications`: log de envio (canal, destino, status, erro).
- `commands`: agent_id (ou watchdog), tipo, parâmetros JSONB, estado (`pending`→`sent`→`acked`→`running`→`succeeded`/`failed`/`expired`/`cancelled`), resultado JSONB, saída (texto, limite 1 MB), criado por, timestamps, `expires_at`.
- `agent_releases`: versão semver, SO/arquitetura, URL do binário, sha256, assinatura ed25519, canal, notas, publicado em, `rollout_percent`.
- `agent_logs`: uploads de log sob demanda (armazenar arquivo compactado em disco/objeto e referência aqui).
- `mib_walks`: device/IP, agent_id, arquivo snmprec, ts, usuário.
- `audit_log`: user_id, ação, entidade, id, antes/depois (JSONB), IP, ts. Registrar TODA ação de escrita do portal.
- `settings`: chave/valor por revenda.

Índices: `devices(reseller, serial)` único; `readings(device_id, read_at desc)`; `alerts(state, type)`; `agents(site_id, cluster_role)`. Crie as partições dos próximos 3 meses automaticamente (job do worker).

---

## 4. COLETOR (dm-agent) — ESPECIFICAÇÃO

### 4.1 Execução e plataforma
- Binário Go único, sem CGO (use `modernc.org/sqlite`). Alvos de build: `windows/amd64`, `windows/386`, `windows/arm64`, `linux/amd64`, `linux/386`, `linux/arm64`, `linux/arm` (GOARM=6, cobre Raspberry Pi antigos e novos).
- **Sistemas suportados:** Windows 10/11 e Windows Server 2016+; Linux com kernel 3.2+; Raspberry Pi OS. O instalador e o próprio agente verificam a versão do Windows na inicialização: em Windows 7/8/8.1/Server 2008/2012, o instalador **recusa com mensagem clara em português** ("Este computador usa Windows 7. Instale o coletor em um PC com Windows 10 ou mais novo na mesma rede."). Build legado para Windows antigo está **fora do escopo** por enquanto.
- Serviço Windows via `github.com/kardianos/service`: nome `DatiMonitorAgent`, início **automático (atraso)**, conta LocalService (ou LocalSystem se necessário para WOL/broadcast — documente a escolha). Configure **ações de recuperação do SCM**: reiniciar após 5 s, 5 s, 30 s; zerar contador após 1 dia.
- Linux: unit systemd com `Restart=always`, `RestartSec=5`, `WatchdogSec=60` (use sd_notify).
- Opcional por config: impedir suspensão do Windows enquanto o serviço roda (`SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED)`), com aviso no portal de que isso mantém o PC acordado.
- Diretório de dados: Windows `C:\ProgramData\DatiMonitor\`, Linux `/var/lib/dati-monitor/`. Contém `agent.db` (SQLite), `config.json`, `logs/` (rotação: 10 arquivos × 10 MB), `credential` (no Windows protegido com DPAPI).
- Endpoint local de saúde somente em `127.0.0.1:47701/health` (usado pelo watchdog): retorna estado dos loops internos, horário do último heartbeat e da última varredura. Se algum loop interno parar de "bater" por mais de 3× o intervalo, `/health` responde não saudável.
- Limites de recursos: memória alvo < 60 MB; CPU baixa. Log estruturado (JSON) com níveis.

### 4.2 Cadastro (enrollment)
1. No portal, o operador cria um coletor para um Local → recebe um **código de 8 caracteres** (validade 7 dias, uso único) e o link do instalador.
2. O instalador (ou `dm-agent enroll --server URL --code XXXX`) troca o código por `agent_id` + segredo de 32 bytes (`POST /api/agent/enroll`).
3. O segredo é guardado localmente (DPAPI no Windows, arquivo 0600 no Linux). No servidor só fica o hash.
4. Autenticação posterior: token de sessão curto (JWT 15 min) obtido com `agent_id` + assinatura HMAC do segredo; renovado automaticamente.
5. O portal pode **revogar** um coletor (a credencial para de funcionar imediatamente e o gateway derruba a conexão).

### 4.3 Canais de comunicação (dois, independentes)
- **Canal principal — WebSocket persistente** `wss://SERVIDOR/ws/agent`:
  - Heartbeat a cada 30 s (CPU, memória, fila pendente, versão, papel no cluster, uptime, IPs locais).
  - Recebe comandos em tempo real; responde ack, progresso e resultado.
  - Reconexão com backoff exponencial com jitter (1 s → máx. 60 s), para sempre. Ping/pong a cada 20 s; se 2 pongs falharem, reconecta.
  - Suporte a proxy HTTP: detectar proxy do sistema (WinHTTP/variáveis de ambiente) + proxy manual na config.
- **Canal de contingência — HTTPS polling:** se o WebSocket estiver caído há mais de 2 min, o agente consulta `GET /api/agent/commands/pending` a cada 60 s e envia heartbeat por `POST /api/agent/heartbeat`. Assim, um comando "Reativar" chega mesmo com o WebSocket quebrado (proxy do cliente que bloqueia WS, por exemplo).
- **Envio de leituras:** sempre por `POST /api/agent/readings` (lotes de até 500 itens, gzip), com `idempotency_key` por item. O servidor responde quais chaves aceitou; o agente só apaga da fila local o que foi confirmado.
- Protocolo documentado em `docs/protocol.md` com JSON Schema de cada mensagem. Mensagens versionadas (`"v": 1`).

### 4.4 Fila local (R4)
- Tabela `outbox` no SQLite: toda leitura é gravada **antes** de tentar enviar.
- Reenvio automático em ordem; guardar até 30 dias ou 200 mil itens (o que vier primeiro; descartar os mais antigos de *suprimentos* primeiro, nunca contadores antes de suprimentos).
- Tamanho da fila vai no heartbeat; o portal mostra "X leituras pendentes de envio".

### 4.5 Descoberta
- Faixas vindas do servidor (`ip_ranges`). Se o Local não tiver faixa, o agente **sugere** as sub-redes das suas interfaces (/24 de cada IP privado) e envia ao portal para aprovação — não varre nada sem faixa aprovada.
- Para cada IP: SNMP GET em `sysObjectID` + `hrDeviceType.1` + `prtGeneralSerialNumber.1`, testando as credenciais do Local em ordem até uma responder.
- É impressora se `hrDeviceType.1 == 1.3.6.1.2.1.25.3.1.5` **ou** se existir `prtGeneralSerialNumber` ou `prtMarkerLifeCount`.
- Concorrência configurável (padrão 64), limite de pacotes/s (padrão 200), timeout 1500 ms, 1 retentativa. Uma /24 deve terminar em menos de 30 s.
- Guardar no agente qual credencial funcionou para cada IP (acelera as próximas leituras).
- Intervalo padrão de descoberta: 6 h (configurável). Também sob demanda (comando).

### 4.6 Leitura
- Intervalos **independentes** e configuráveis por Local: contadores (padrão 60 min), suprimentos (padrão 60 min), status/erros (padrão 10 min), cadastro/atributos (padrão 24 h).
- Equipamento conhecido que não responde: 3 tentativas com intervalo de 2 min antes de registrar falha (impressora em economia de energia costuma responder na 2ª). Registrar falha como evento, não como leitura.
- **Identidade por serial.** Se o mesmo IP passar a responder com outro serial → evento `replaced`. Se um serial conhecido aparecer em outro IP → evento `ip_changed` e atualizar IP.
- Aplicar o **perfil de leitura** correspondente (seção 6.4). Enviar sempre os valores brutos também (`extra`).
- Otimização: para status/erros, só enviar quando mudar (mais 1 envio por hora como confirmação).

### 4.7 Comandos remotos (R2) — todos devem existir e funcionar pelo portal
| Comando | Executado por | O que faz |
|---|---|---|
| `reconnect` | agente | Fecha e reabre o WebSocket, renova token, reenvia fila |
| `restart_agent` | **watchdog** | Para e reinicia o serviço do agente (funciona mesmo com o agente travado) |
| `restart_watchdog` | agente | Reinicia o serviço do watchdog |
| `scan_now` | agente | Descoberta imediata (todas as faixas ou uma específica) |
| `read_now` | agente | Leitura imediata de todos os equipamentos, ou de uma lista de IDs |
| `read_device` | agente | Leitura completa de um IP e devolve o resultado bruto na tela |
| `snmp_test` | agente | Testa um IP com cada credencial e informa qual responde e o tempo |
| `mib_walk` | agente | Walk completo de um IP (ou de uma subárvore), gera arquivo `.snmprec` e envia ao servidor |
| `set_config` | agente | Aplica nova configuração (faixas, credenciais, intervalos, proxy) com `config_version`; confirma aplicação |
| `get_logs` | agente e watchdog | Compacta os logs das últimas N horas e envia |
| `diagnostics` | agente | DNS, conectividade HTTPS/WSS, latência, relógio do sistema vs servidor, proxy detectado, interfaces de rede, uso de disco |
| `update` | watchdog | Atualiza para uma versão específica (seção 5.2) |
| `rollback` | watchdog | Volta para a versão anterior guardada |
| `pause` / `resume` | agente | Suspende/retoma coletas (manutenção no cliente) |
| `promote_master` | servidor → agentes | Força este agente como MASTER do Local |
| `wake_host` | agente STANDBY | Envia Wake-on-LAN (magic packet) para o MAC do PC de outro coletor do mesmo Local |
| `ping_host` | agente | Ping ICMP/TCP para um IP (diagnóstico de rede) |
| `web_proxy_open` / `web_proxy_close` | agente | Abre um túnel temporário (máx. 30 min, renovável) pelo WebSocket para a página web (HTTP/HTTPS) de uma impressora da rede do cliente — ver seção 4.9 |
| `uninstall` | watchdog | Remove os serviços (exige confirmação dupla no portal e papel admin) |

Regras dos comandos: expiram (padrão 10 min, configurável); idempotentes por `command_id`; estado e saída visíveis ao vivo no portal; todo comando gera registro em `audit_log`.

**Botão "Reativar" no portal** (ação composta, a mais importante do sistema):
1. Se o agente está conectado → envia `reconnect` + `read_now`.
2. Se o agente está offline mas o **watchdog** está vivo (polling) → envia `restart_agent` ao watchdog e acompanha até o agente voltar (timeout 3 min).
3. Se ambos estão offline e existe outro coletor no Local → `promote_master` no STANDBY + `wake_host` para o PC caído.
4. Se nada responde → mostra diagnóstico claro: "Nenhum coletor deste local está ligado. Último sinal: DD/MM HH:MM. Provável PC desligado ou sem internet." e sugere as ações manuais.
Mostrar cada etapa em tempo real num painel lateral.

### 4.8 Cluster (R3)
- Todo coletor pertence a um Local. O servidor é a autoridade: mantém um **lease** do MASTER por Local.
- O MASTER renova o lease a cada heartbeat (30 s). Lease expira em 3 min sem heartbeat.
- Lease expirado → o worker promove o STANDBY online de maior prioridade (empate: menor latência média) e envia `promote_master`. O antigo MASTER, ao voltar, recebe papel STANDBY (não retoma sozinho). Operador pode fixar um MASTER preferido.
- **Só o MASTER varre e lê.** STANDBY só mantém heartbeat e canal aberto.
- Anti-duplicidade: além do papel, o servidor descarta leitura de um mesmo device com `read_at` a menos de (intervalo/2) da anterior vinda de outro agente, registrando o descarte.
- Portal mostra o cluster: quem é MASTER, quem está em espera, histórico de trocas.

### 4.9 Acesso remoto à página web da impressora (Device Web Access)
Recurso presente nos coletores mais atuais do mercado (MPS Monitor DCA 4). Permite ao técnico abrir, pelo portal, a página de administração da impressora que está na rede do cliente, sem VPN e sem acesso remoto ao PC.
- O portal chama `POST /api/devices/{id}/web-session` → a API envia `web_proxy_open` ao agente (qualquer agente online do Local, preferindo o MASTER).
- O tráfego HTTP é multiplexado dentro do WebSocket já existente (frames com `stream_id`); o gateway expõe `https://SERVIDOR/devweb/{session_token}/...` e reescreve cabeçalhos/URLs (Location, cookies, links absolutos).
- **Restrições de segurança obrigatórias:** somente IPs de impressoras já cadastradas naquele Local; somente portas 80/443/8000/8080/8443; sessão com token aleatório de uso por usuário, expira em 30 min; papéis `technician`+ ; tudo registrado em `audit_log`; limite de banda por sessão.
- Funciona mesmo com certificados autoassinados das impressoras (o agente aceita o TLS da impressora; o usuário sempre fala HTTPS com o servidor).

---

## 5. WATCHDOG (dm-watchdog) E ATUALIZAÇÃO

### 5.1 Watchdog
- Serviço separado (`DatiMonitorWatchdog`), LocalSystem no Windows (precisa substituir binários em Program Files e controlar serviços), início automático, com ações de recuperação próprias.
- A cada 15 s consulta `127.0.0.1:47701/health`. Se falhar 3 vezes seguidas, ou o processo não existir, ou memória > 300 MB, ou o serviço estiver parado → reinicia o agente e registra o motivo.
- O agente também vigia o watchdog (verifica se o serviço está rodando a cada 60 s e o inicia se não estiver). Um vigia o outro.
- O watchdog tem **canal próprio e simples**: HTTPS polling a cada 60 s (`POST /api/watchdog/heartbeat`, retorna comandos pendentes do watchdog). Não depende do WebSocket nem do código do agente. Isso permite reiniciar/atualizar o agente mesmo quando ele está travado.
- O watchdog deve ser minimalista (pouco código, quase nunca atualizado) — é a peça que não pode falhar.

### 5.2 Atualização (R7)
- Releases publicadas no portal (`agent_releases`): binário + sha256 + **assinatura ed25519** (a chave pública vem embutida no watchdog; a chave privada nunca vai para o servidor web — assinar com `dm-tool sign` localmente/CI).
- Canais: `canary` (poucos clientes) e `stable`. `rollout_percent` para liberação gradual.
- Fluxo no watchdog: baixa → verifica hash e assinatura → para o agente → guarda o binário atual como `previous` → substitui → inicia → espera `/health` saudável e heartbeat no servidor por até 2 min → se falhar, **rollback automático** para `previous` e reporta falha.
- O servidor não oferece atualização automática para uma versão com taxa de falha > 5% no canary.
- O próprio watchdog pode ser atualizado pelo agente (processo inverso), com o mesmo esquema de verificação.

---

## 6. LEITURA SNMP — DETALHES

### 6.1 Biblioteca
`github.com/gosnmp/gosnmp`. Suportar v1, v2c (GETBULK) e v3 (authNoPriv/authPriv; SHA, SHA-256; AES-128, AES-256).

### 6.2 OIDs padrão (usar em todos os equipamentos)
| Dado | OID |
|---|---|
| sysDescr | 1.3.6.1.2.1.1.1.0 |
| sysObjectID (fabricante) | 1.3.6.1.2.1.1.2.0 |
| sysUpTime | 1.3.6.1.2.1.1.3.0 |
| sysName / sysLocation | 1.3.6.1.2.1.1.5.0 / 1.3.6.1.2.1.1.6.0 |
| hrDeviceType | 1.3.6.1.2.1.25.3.2.1.2.1 (impressora = 1.3.6.1.2.1.25.3.1.5) |
| hrDeviceDescr (modelo) | 1.3.6.1.2.1.25.3.2.1.3.1 |
| hrDeviceStatus | 1.3.6.1.2.1.25.3.2.1.5.1 (1 unknown, 2 running, 3 warning, 4 testing, 5 down) |
| hrPrinterStatus | 1.3.6.1.2.1.25.3.5.1.1.1 (1 other, 2 unknown, 3 idle, 4 printing, 5 warmup) |
| hrPrinterDetectedErrorState | 1.3.6.1.2.1.25.3.5.1.2.1 (bitmask, bit 0 = MSB do 1º byte: lowPaper, noPaper, lowToner, noToner, doorOpen, jammed, offline, serviceRequested; 2º byte: inputTrayMissing, outputTrayMissing, markerSupplyMissing, outputNearFull, outputFull, inputTrayEmpty, overduePreventMaint) |
| prtGeneralSerialNumber | 1.3.6.1.2.1.43.5.1.1.17.1 |
| prtMarkerLifeCount (total) | 1.3.6.1.2.1.43.10.2.1.4.1.1 |
| prtMarkerSupplies (tabela) | 1.3.6.1.2.1.43.11.1.1 — .3 ColorantIndex, .4 Class (3 consumido, 4 receptáculo), .5 Type (3 toner, 4 wasteToner, 9 opc, 10 developer, 15 fuser, 20 transferUnit, 21 tonerCartridge…), .6 Description, .7 Unit, .8 MaxCapacity, .9 Level (-2 desconhecido, -3 "tem algum") |
| prtMarkerColorantValue (nome da cor) | 1.3.6.1.2.1.43.12.1.1.4 |
| prtInputTable (bandejas) | 1.3.6.1.2.1.43.8.2.1 |
| prtConsoleDisplayBufferText (texto do painel) | 1.3.6.1.2.1.43.16.5.1.2.1 |
| prtAlertTable | 1.3.6.1.2.1.43.18.1.1 — .2 severity, .7 code, .8 description |
| ifPhysAddress (MAC) | 1.3.6.1.2.1.2.2.1.6 |

Percentual de suprimento = `level / max * 100` quando ambos ≥ 0. Para receptáculo (classe 4, ex.: toner residual), o nível indica quanto está cheio — tratar invertido na exibição. Associar suprimento à cor via ColorantIndex → prtMarkerColorantValue; se vazio, inferir pela descrição (black/preto/cyan/magenta/yellow/amarelo…).

### 6.3 Status normalizado (coluna Status da tela)
`ready` (Pronta), `printing` (Imprimindo), `warmup` (Aquecendo), `energy_saving` (Economia de energia — detectar pelo texto do painel e/ou OIDs do perfil do fabricante), `warning` (Atenção), `error` (Erro — com motivo pelos bits de erro/alertas), `offline` (Sem resposta). Ícones e cores como no Datacount (verde pronta, laranja economia, vermelho erro, cinza offline).

### 6.4 Perfis de leitura (dados, não código)
Arquivos YAML em `/profiles`, carregados no banco (`read_profiles`) e distribuídos aos agentes com versão. Seleção: por prefixo de `sysObjectID` e regex no modelo; o mais específico vence; sempre existe o perfil `generic` (só OIDs padrão).

Formato (validar com JSON Schema; o agente e o backend usam o mesmo schema):
```yaml
id: exemplo-fabricante      # exemplo de formato mínimo; Canon/Konica reais estão em /profiles
version: 1
match:
  sys_object_id_prefix: "1.3.6.1.4.1.XXXX"
  model_regex: "(?i)MODELO"
counters:                     # campo normalizado -> origem
  total:      { oid: "1.3.6.1.2.1.43.10.2.1.4.1.1" }
  mono:       { oid: "PREENCHER_PELO_WALK", note: "confirmar com relatório da máquina" }
  color:      { oid: "PREENCHER_PELO_WALK" }
  # alternativa para tabelas indexadas por ID/nome de contador:
  # mono: { table: "OID_DA_TABELA", match_name_oid: "OID_DO_NOME", match_regex: "(?i)black|preto", value_oid: "OID_DO_VALOR" }
  # campos calculados: color: { expr: "total - mono" }
status:
  energy_saving_text_regex: "(?i)sleep|economia|energy|saving|repouso"
supplies: { use_standard: true }
```
**Canon e Konica Minolta já estão prontos** em `/profiles/canon.yaml` e `/profiles/konica-minolta.yaml`. Eles usam recursos que o motor de perfis DEVE suportar:
- `identity` com listas de OIDs em ordem de preferência (primeiro que responder com valor não vazio).
- `counter_sources`: lista de fontes em ordem; usar a primeira cujo `detect_oid` retornar dados (walk/getnext); a fonte `standard` é o último recurso.
- Por contador: `oid`, `sum: [oids]`, `first_of: [alternativas]`, `expr` (referenciando outros contadores já lidos), e para tabelas com nome: `named_table {names_oid, values_oid}` + `name_regex` / `sum_names` (nomes podem vir como OCTET STRING em hex — decodificar para texto).
- `store_all_rows_in_extra` / `walk_subtree_to_extra`: gravar todos os valores brutos em `readings.extra`.
- `rules.mono_only_models_regex` (equipamento sem cor → `color = 0`) e `rules.validate_sum_tolerance_percent`.
Crie perfis com `match` e estrutura prontos para os demais: HP, Ricoh, Kyocera, Xerox, Brother, Samsung, Lexmark, Sharp, Epson, OKI, Toshiba — mas com os OIDs proprietários marcados `PREENCHER_PELO_WALK` até existir gravação real. Enterprise IDs: Canon 1602, Konica Minolta 18334, HP 11, Ricoh 367, Kyocera 1347, Xerox 253, Brother 2435, Samsung 236, Lexmark 641, Sharp 2385, Epson 1248, OKI 2001, Toshiba 1129.

Suporte a expressões simples (`total - mono`, `a + b`), e a leitor HTTP opcional no perfil (`http: {path, regex}` para modelos que só mostram contador na página web) — implementar a estrutura, desativado por padrão.

### 6.5 Validação de leituras (servidor)
- Contador menor que a leitura anterior → não rejeitar; gravar, marcar `counter_regression`, abrir alerta, e excluir dos relatórios de produção até um operador classificar (troca de placa / erro de leitura).
- Salto absurdo (> 50.000 páginas/dia, configurável) → marcar suspeito + alerta.
- `mono + color` muito diferente de `total` (> 2%) → marcar para revisão do perfil.

### 6.6 Ferramenta de walk e editor de perfis (essencial para Canon/Konica)
- `dm-tool walk --ip X --community public --out arquivo.snmprec` (CLI local) e comando remoto `mib_walk` pelo portal.
- No portal, tela **"Perfis de modelos"**: abre um walk, mostra a árvore com busca por valor (o operador digita o contador que está no relatório impresso da máquina, ex. `100150`, e a tela destaca os OIDs com esse valor), permite arrastar o OID para um campo do perfil, testar o perfil contra um equipamento real (`read_device`) e publicar nova versão.
- Walks gravados viram fixtures de teste automaticamente (pasta `/profiles/recordings`).

---

## 7. BACKEND (FastAPI)

- Estrutura: `app/api/v1/*` (portal), `app/api/agent/*` (agentes), `app/gateway` (WebSocket), `app/worker`, `app/models`, `app/schemas`, `app/services`, `app/core` (config, segurança, db).
- Autenticação do portal: JWT access (15 min) + refresh (7 dias, rotativo, em cookie httpOnly), argon2id, bloqueio após 10 tentativas, TOTP opcional por usuário (obrigatório para `reseller_admin` se configurado).
- Autorização por papel + escopo (revenda/cliente) em **dependência central** — nenhum endpoint consulta dados sem filtro de revenda.
- Rate limit em memória (por IP/agente) em login e endpoints de agente.
- Paginação por cursor nas listas grandes; filtros e ordenação do lado do servidor (a tela de parque precisa aguentar 20.000 equipamentos).
- Exportação CSV e XLSX (openpyxl) de toda lista/relatório, respeitando os filtros aplicados.
- **API para o ERP** (somente leitura, token por integração): `GET /api/erp/v1/readings?customer_erp_code=&from=&to=`, `GET /api/erp/v1/cutoff?date=` (leitura de cada equipamento mais próxima e anterior à data de corte), `GET /api/erp/v1/devices`. Documentada no OpenAPI.
- Gateway WebSocket: mantém mapa `agent_id → conexão`; presença gravada em `agents.last_seen_at` a cada heartbeat; comandos criados pela API são gravados em `commands` e sinalizados com `NOTIFY commands` do PostgreSQL; o gateway que tem a conexão entrega; se nenhum tiver, ficam `pending` para o polling. O desenho deve funcionar com mais de uma instância do gateway (LISTEN/NOTIFY resolve isso).
- Server-Sent Events ou WebSocket do portal para atualizações ao vivo (estado de coletores, progresso de comandos, novos alertas).
- OpenAPI completo; gerar cliente TypeScript para o frontend (`openapi-typescript`).
- Migrações Alembic; seed de desenvolvimento (1 revenda Daticopy, 1 empresa, 2 clientes, 2 locais, usuário admin `admin@local` / senha exibida no primeiro start e trocada no primeiro login).

---

## 8. WORKER (jobs agendados)

| Job | Frequência | Função |
|---|---|---|
| presença de agentes | 30 s | marca `offline` sem heartbeat há 3 min; `degraded` se fila > 1000 ou erros repetidos |
| lease de cluster | 30 s | promove STANDBY quando o lease do MASTER expira |
| equipamentos desconectados | 5 min | sem leitura há H horas (padrão 6 h) → estado desconectado + alerta conforme regra |
| avaliação de alertas | 1 min | aplica `alert_rules`; deduplica; resolve automaticamente quando a condição some |
| notificações | contínuo | envia e-mail/WhatsApp/webhook com retentativa e registro |
| previsão de toner | 1 h | regressão linear do consumo dos últimos 30 dias → "dias até acabar" por suprimento |
| resumo diário | 07:00 | e-mail com coletores offline, equipamentos sem leitura, toners críticos |
| partições | diário | cria partições dos próximos meses |
| retenção | diário | heartbeats > 30 dias, supply_readings > 400 dias (configurável). **Nunca** apagar `readings` |
| expiração de comandos | 1 min | marca `expired` |

---

## 9. NOTIFICAÇÕES

- Interface `Notifier` com implementações: `SmtpNotifier`, `WebhookNotifier`, `WhatsAppNotifier`.
- WhatsApp via provedor configurável (implementar adaptadores para **Meta WhatsApp Cloud API** e um **genérico HTTP** com template de URL/corpo, que serve para Z-API, Evolution API etc.). Credenciais criptografadas em `settings`.
- Silêncio configurável (ex.: não enviar entre 22h e 7h exceto crítico), agrupamento (um alerta por coletor, não um por equipamento quando o coletor inteiro cai).

---

## 10. PORTAL (frontend) — TELAS

Layout igual em espírito ao Datacount: barra lateral à esquerda com menus, cabeçalho com revenda/empresa, breadcrumb "Você está em", ícones de notificação, usuário. Tema claro e escuro. Responsivo (usável no celular pelos técnicos).

Menus e telas:

1. **Login** (+ TOTP, esqueci a senha por e-mail).
2. **Dashboard:** cards (equipamentos monitorados, online, desconectados, coletores online/offline, alertas abertos, toners críticos), gráfico de páginas por dia (30 dias, PB × cor), lista "Coletores offline agora" com botão Reativar, "Toners que acabam em até 7 dias".
3. **Revendas** (superadmin), **Empresas**, **Usuários** (CRUD, papéis, escopo por cliente, reset de senha, TOTP).
4. **Clientes:** lista; detalhe com abas Locais, Coletores, Equipamentos, Alertas, Relatórios, Contatos, Código ERP.
5. **Coletores (Collector):** lista com estado (online/offline/degraded/paused), papel (MASTER/STANDBY), cliente/local, hostname, IP, versão, último sinal, fila pendente, watchdog vivo. Ações por linha e em massa: **Reativar**, Reiniciar, Varrer agora, Ler agora, Logs, Diagnóstico, Atualizar, Pausar. Botão **"Novo coletor"** (gera código + link do instalador + instrução de 3 passos).
   - Detalhe: status ao vivo, gráfico de heartbeat/CPU/memória, cluster do local, faixas de IP (editar, sugerir), credenciais SNMP, intervalos, proxy, histórico de comandos com saída, terminal de logs ao vivo (stream), histórico de versões.
6. **Equipamentos → Parque (tela principal, igual à imagem do Datacount):** colunas **Status** (IP + ícone + texto), **DCA** (coletor), **Descoberta**, **Comunicação** ("Hoje às 08:14"), **PAT**, **Serial**, **Marca**, **Modelo** (+ Setor abaixo), **Cliente**, **Medidor** (total grande; "PB: x CL: y" pequeno), **Monitor**, **Níveis** (barras verticais C/M/Y/K com % e "n/d"). Ordenação e filtro por coluna, **Pesquisa global**, checkboxes **Selecionar / Desconectados / Desativados**, botão limpar filtros, atualizar, filtro avançado (painel lateral), colunas configuráveis e salvas por usuário, ações em massa (ler agora, desativar, mover de cliente/local, editar setor/PAT, exportar). Virtualização da tabela para milhares de linhas.
   - **Detalhe do equipamento:** dados cadastrais editáveis (PAT, setor, observação, cliente, local), status atual e erros, gráfico de contadores (diário/mensal, PB × cor), tabela de leituras com exportação, suprimentos com histórico e previsão de término, eventos (linha do tempo), alertas, ajustes manuais de leitura (com motivo), botões Ler agora, Testar SNMP, Walk, **Abrir página web da impressora** (seção 4.9, abre em nova aba).
7. **Computadores:** PCs com agente (e impressoras USB — seção 11).
8. **Alertas:** lista com filtros, reconhecer, resolver, regras (CRUD de `alert_rules`), canais de notificação.
9. **Relatórios:** produção por período (por cliente, local, equipamento, PB/cor), **leitura de corte** (data de corte → leitura de cada equipamento), consumo de suprimentos, disponibilidade dos coletores (% do tempo online por cliente), equipamentos sem leitura, trocas de IP/equipamento, regressões de contador. Todos com exportação CSV/XLSX/PDF.
10. **Perfis de modelos:** editor e explorador de walk (seção 6.6).
11. **Downloads:** instalador Windows (.exe), pacote Linux, versões publicadas, notas; publicar release (admin), canais e rollout.
12. **Configurações:** notificações (SMTP, WhatsApp), regras padrão, intervalos padrão, limites de validação, integração ERP (tokens), tema/logo da revenda.
13. **Auditoria:** quem fez o quê e quando, com filtro.

Padrões de UX: toda ação com confirmação quando destrutiva; toasts de resultado; estados de carregamento e vazio; datas relativas ("há 5 min") com tooltip da data completa; números com separador de milhar pt-BR.

---

## 11. IMPRESSORAS USB (fase final)

- O mesmo dm-agent, quando instalado num PC, lista impressoras locais via WMI (`Win32_Printer`, filtrar portas USB) e envia como equipamentos `source=usb` vinculados ao PC.
- Tentar obter contador via PJL (`@PJL INFO PAGECOUNT`) pela porta USB quando suportado; se não, registrar como "sem contador disponível" e permitir leitura manual pelo portal.
- Tela Computadores mostra PCs, impressoras USB e suas leituras.

---

## 12. SEGURANÇA

- TLS obrigatório em produção (proxy da hospedagem). Em desenvolvimento local, HTTP em `localhost`; o agente só aceita `http://` quando o servidor é `localhost`/`127.0.0.1` ou com a flag explícita `--insecure-dev`. HSTS e CORS restrito em produção.
- Segredos só por variáveis de ambiente (`.env.example` documentado). Chave mestre para criptografar credenciais SNMP/WhatsApp (AES-256-GCM).
- Assinatura ed25519 de releases (seção 5.2).
- Nenhum dado pessoal coletado das impressoras além do necessário.
- Cabeçalhos de segurança, proteção CSRF para o refresh token em cookie, validação de entrada em tudo (Pydantic).
- Logs sem segredos.

---

## 13. TESTES E QUALIDADE

- **Simulador de impressoras:** `snmpsim` instalado via pip no venv do projeto (com `pysmi`), iniciado por `scripts\dev.ps1` e pelos testes em `127.0.0.1` (portas UDP altas, ex.: 1161–1168, uma por impressora, ou comunidades diferentes), com pelo menos **8 impressoras simuladas** em `/profiles/recordings/sim/`:
  1. Canon colorida com tabela de contadores por ID (1602.1.11.1.3.1.4: 101, 108, 112, 113, 122, 123, 301, 501);
  2. Canon mono (plataforma i-SENSYS) com tabela nomeada (1602.1.11.2.1.1.2/.3, nomes em hex);
  3. Konica bizhub colorida (18334…7.2.x) reproduzindo o caso real: total 217031 = PB 100150 + cor 116881;
  4. Konica mono;
  5. genérica só com Printer-MIB;
  6. em economia de energia (texto do painel "Sleep") e que só responde na 2ª tentativa (latência artificial);
  7. com atolamento + porta aberta nos bits de erro e toner com nível -3;
  8. com contador que regride entre duas leituras (troca de arquivo snmprec no meio do teste).
  Os valores dos arquivos simulados seguem **exatamente** os OIDs dos perfis fornecidos. Quando houver walks reais, eles entram em `/profiles/recordings/real/` e passam a rodar nos mesmos testes.
- **Go:** testes unitários (motor de perfis com `sum`/`first_of`/`expr`/`named_table`, decodificação hex, % de suprimento, bits de erro MSB-first, fila, backoff, eleição de cluster, verificação de assinatura, rollback) + integração contra o snmpsim. Cobertura ≥ 80% em `internal/`. `go test -race` obrigatório.
- **Python:** pytest + pytest-asyncio com PostgreSQL real local (banco `dati_test` criado/zerado pelos testes); cobertura ≥ 80% em `services/` e `api/`; testes de isolamento multi-revenda (usuário de uma revenda nunca vê dados de outra).
- **Frontend:** Vitest nos componentes críticos + **Playwright E2E** com o fluxo completo: login → criar cliente/local → gerar código → agente real (processo local compilado do código) se cadastra → aparece online → descobre as 8 impressoras simuladas → tela de parque mostra contadores PB/cor e níveis corretos → detalhe do equipamento com gráfico → derrubar o agente → alerta de offline e e-mail capturado pelo `smtp_catcher` → botão Reativar → agente volta → abrir página web da impressora pelo túnel.
- **Windows real no CI:** job em `windows-latest` (GitHub Actions) que compila o agente e o watchdog, **instala os dois serviços**, confere as ações de recuperação do SCM, mata o processo do agente e verifica que o watchdog o reinicia, executa uma atualização assinada e um rollback, e por fim desinstala de forma limpa.
- **Teste de caos** (`scripts\chaos.ps1`): mata o processo do agente, simula queda de internet (para a API e o gateway por 10 min, ou aponta o agente para porta fechada), reinicia o serviço do PostgreSQL, derruba o MASTER do cluster — e verifica que nenhuma leitura se perde, não há duplicidade e tudo volta sozinho.
- **Teste de resistência** (`scripts\soak.ps1 -Minutes ...`): agente + simulador rodando continuamente; mede memória, goroutines, arquivos abertos e tamanho da fila. Falha se a memória crescer > 20% após estabilizar. No CI roda 30 min; o comando para rodar 24 h fica documentado.
- **Teste de carga:** backend recebendo leituras de 20.000 equipamentos simulados (a cada hora) e 500 agentes conectados simultaneamente no WebSocket; tela de parque respondendo em < 1 s com 20.000 linhas.
- **`scripts\acceptance.ps1`**: script único que sobe tudo do zero e verifica automaticamente cada item da seção 15, imprimindo um relatório OK/FALHOU por item.
- Lint/format: `golangci-lint`, `ruff` + `mypy --strict` em `app/`, `eslint` + `prettier` + `tsc --noEmit`. CI em GitHub Actions rodando tudo em cada push.

---

## 14. FASES DE IMPLEMENTAÇÃO (siga nesta ordem)

Cada fase termina com: testes passando, commit, `PROGRESS.md` atualizado e instruções de como testar manualmente.

- **Fase 0 — Fundação:** verificar/instalar ambiente (seção 0.1), monorepo, `CLAUDE.md`, `PROGRESS.md`, venv Python 3.12, `scripts\setup-db.ps1` (cria bancos `dati_dev` e `dati_test`), `scripts\dev.ps1` (sobe API, gateway, worker, portal Vite, snmpsim e smtp_catcher), `scripts\test.ps1`, build do agente (`scripts\build-agent.ps1`), CI no GitHub Actions (Postgres como *service* do runner Linux e job Windows), lint, `.env.example`. Makefile opcional só para o CI Linux.
- **Fase 1 — Backend núcleo:** modelos + migrações, partições, auth, papéis/escopo, CRUD de revendas/empresas/clientes/locais/usuários, auditoria, seed.
- **Fase 2 — Agente núcleo:** enrollment, config, SNMP (v1/v2c/v3), descoberta, leitura com perfil `generic`, fila SQLite, envio em lote idempotente, serviço Windows/systemd, `/health`, `dm-tool walk`. Ingestão no backend com validações (6.5).
- **Fase 3 — Tempo real e comandos:** gateway WebSocket, presença em tabela + LISTEN/NOTIFY, polling de contingência, todos os comandos da tabela 4.7 (exceto `update`/`rollback`, que entram na Fase 5), ciclo de vida dos comandos.
- **Fase 4 — Portal:** layout, login, dashboard, clientes, coletores (com Reativar e comandos ao vivo), **tela de parque completa**, detalhe do equipamento, usuários, auditoria.
- **Fase 5 — Confiabilidade:** watchdog, vigilância mútua, cluster com lease e failover, Wake-on-LAN, atualização assinada com canais/rollout/rollback, fluxo completo do botão Reativar (4.7), teste de caos.
- **Fase 6 — Alertas e notificações:** regras, worker, e-mail, WhatsApp, webhook, resumo diário, previsão de toner.
- **Fase 7 — Perfis, relatórios e acesso web:** motor de perfis completo validado com os perfis Canon/Konica fornecidos, editor de perfis com explorador de walk, perfis-base dos demais fabricantes, relatórios completos, exportações, API do ERP, **acesso remoto à página web da impressora (4.9)**.
- **Fase 8 — Instaladores:** Inno Setup (setup.exe com tela de código de cadastro e modo silencioso `/CODE=XXXX /SERVER=URL`, instala os 2 serviços, configura recuperação do SCM, desinstalador limpo), pacote Linux (.deb + script `install.sh`), página de Downloads. Deixar pronto o passo de assinatura de código (certificado a ser comprado depois).
- **Fase 9 — USB e acabamento:** agente USB, Computadores, tema escuro, responsividade, teste de carga e de resistência, documentação final (`docs/operacao.md`: como instalar em cliente, como cadastrar modelo novo com walk, como publicar versão, como restaurar backup; `docs/piloto.md`: roteiro para validar em 3–5 clientes reais comparando com o Datacount) e script de backup/restauração do Postgres.
- **Fase 10 — Teste com impressoras reais da rede local:** o computador onde você (Claude Code) roda está na mesma rede que impressoras reais da Daticopy. Nesta fase:
  1. **Pergunte ao usuário** a faixa de IP da rede (ex.: `192.168.0.0/24`) e a comunidade SNMP (padrão `public`). Não varra nada antes da resposta.
  2. Compile e rode o agente localmente, cadastrado no backend local, com essa faixa. Confirme que ele descobre as impressoras reais.
  3. Para cada modelo encontrado, rode `dm-tool walk` e salve em `/profiles/recordings/real/<marca>_<modelo>.snmprec`.
  4. Monte uma tabela com, por impressora: IP, marca, modelo, serial, total, PB, cor, níveis de toner, e o perfil/fonte usado (ex.: `canon_id_table`, `canon_named_table`, `konica_counters`).
  5. **Peça ao usuário** para imprimir a folha de contadores de cada modelo pelo painel e informar os valores. Compare com a leitura do sistema. Se houver divergência, analise o walk, proponha a correção do perfil YAML e **só altere após confirmação do usuário**.
  6. Adicione os walks reais aos testes automáticos (os perfis passam a ser validados contra dados reais em todo CI).
  7. Registre tudo em `docs/validacao-real.md`.
- **Fase 11 — Aceitação final:** rodar `scripts\acceptance.ps1`, o job Windows, o caos e o soak de 30 min; corrigir tudo que falhar; revisar segurança (dependências com vulnerabilidades, segredos, permissões); gerar `RELEASE_NOTES.md` e o pacote de instalação v1.0.0. **Só declare o projeto concluído quando todos os itens da seção 15 estiverem OK no relatório do `scripts\acceptance.ps1`.**

---

## 15. CRITÉRIOS DE PRONTO (o sistema só está pronto quando TUDO abaixo for verdade)

1. `scripts\dev.ps1` sobe tudo nativo no Windows (sem Docker); o portal abre em `http://localhost:5173`; o admin loga.
2. Um agente cadastrado com código aparece online em < 10 s e descobre as impressoras simuladas.
3. A tela de parque mostra todas as colunas do Datacount com dados reais do simulador.
4. Matando o processo do agente, ele volta sozinho em < 30 s (watchdog).
5. Com o agente travado (não responde), o botão Reativar o recupera pelo watchdog.
6. Com o MASTER desligado, o STANDBY assume em ≤ 3,5 min e as leituras continuam sem duplicar.
7. Com a internet cortada por 1 h, nenhuma leitura se perde.
8. Coletor offline gera e-mail (capturado pelo `smtp_catcher`) e notificação no portal em ≤ 10 min.
9. Atualização com binário quebrado faz rollback automático e o agente continua funcionando.
10. Contador que regride vira alerta e não entra no relatório de produção.
11. Relatório de leitura de corte e API do ERP retornam os valores corretos.
12. As leituras das impressoras simuladas Canon e Konica batem exatamente com os valores dos arquivos snmprec (inclusive total = PB + cor na Konica).
13. Impressora em economia de energia que responde só na 2ª tentativa **não** aparece como desconectada.
14. O túnel abre a página web de uma impressora simulada pelo portal, e uma tentativa de abrir IP não cadastrado é recusada e auditada.
15. No Windows (job do CI): instalação dos 2 serviços, recuperação automática, atualização, rollback e desinstalação funcionam.
16. Instalador recusa Windows 7/8 com mensagem clara.
17. Soak de 30 min sem crescimento de memória; carga de 20.000 equipamentos com tela de parque < 1 s.
18. Todos os testes (unitários, integração, E2E, Windows, caos, soak, carga) passam no CI e `scripts\acceptance.ps1` mostra todos os itens OK.
19. Na rede local real (Fase 10), o sistema descobriu as impressoras, e os contadores PB/cor de cada modelo foram conferidos com a folha de contadores e aprovados pelo usuário.

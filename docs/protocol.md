# Protocolo agente ↔ servidor (v1)

> Arquivo gerado por `scripts/gen_protocol_docs.py` a partir de `backend/app/schemas/agent.py`
> (espelho de `agent/internal/protocol`). Não edite à mão: um teste falha se ficar desatualizado.

Todo tráfego parte do coletor (HTTPS/WSS 443; `http://` só para `localhost` ou com `--insecure-dev`).
Toda mensagem tem `"v": 1`. Horários em UTC (ISO 8601).

## Cadastro e autenticação
1. O portal cria o coletor e gera um código de 8 caracteres (7 dias, uso único).
2. `POST /api/agent/enroll` troca o código por `agent_id` + segredo `S` (32 bytes, base64). O segredo fica
   no PC (DPAPI no Windows, arquivo 0600 no Linux). O servidor guarda só
   `K = SHA-256("dm-agent-auth\n" + S)`, cifrada com a chave mestre.
3. `POST /api/agent/token`: `signature = hex(HMAC-SHA256(K, agent_id + "\n" + ts + "\n" + nonce))`.
   `ts` pode diferir do servidor no máximo 300 s; se diferir, a resposta `401 clock_skew` traz
   `server_time` e o agente passa a usar a diferença (não altera o relógio do PC).
   `nonce` não pode se repetir (10 min). Resposta: JWT de 15 min.
4. Coletor revogado no portal: `401 agent_revoked` em qualquer chamada.

## Heartbeat, cluster e configuração
- A cada 30 s o agente envia `HeartbeatRequest`; a resposta traz o papel no cluster
  (`master`/`standby`), se está pausado e a `config_version`. Quando ela difere da versão aplicada,
  o agente baixa `GET /api/agent/config`.
- O primeiro coletor do local vira MASTER; o MASTER renova o *lease* (3 min) a cada heartbeat. Só o MASTER
  varre e lê.
- Sem faixa de IP aprovada, o agente sugere as /24 privadas das suas interfaces (`/ranges/suggest`) e
  **não varre nada** até a aprovação no portal.

## Leituras (fila local → servidor)
- Tudo é gravado na fila SQLite do agente **antes** de qualquer envio (`outbox`).
- `POST /api/agent/readings`: até 500 itens, gzip. Cada item tem `key = "<agent_id>:<sequência local>"`.
- Resposta: um `ItemResult` por chave — `accepted` (gravado), `duplicate` (já recebido antes),
  `discarded` (anti-duplicidade do cluster; o descarte fica registrado) ou `rejected` (inválido; o agente
  move o item para `dead_letter` e registra o motivo). Só chaves confirmadas saem da fila.
- Tipos: `reading` (contadores), `supplies`, `status` (enviado só quando muda + 1 confirmação por hora),
  `event` (`read_failed` após 3 tentativas com 2 min de intervalo).
- `error_bits`: bit *i* = i-ésima condição de `hrPrinterDetectedErrorState` (bit 0 = MSB do 1º byte:
  lowPaper, noPaper, lowToner, noToner, doorOpen, jammed, offline, serviceRequested, inputTrayMissing,
  outputTrayMissing, markerSupplyMissing, outputNearFull, outputFull, inputTrayEmpty, overduePreventMaint).

## Canal WebSocket `/ws/agent` (principal)
- Handshake com `Authorization: Bearer <token do agente>`; o endereço vem em `ws_url` (cadastro e
  configuração). Toda mensagem é um `WsMessage` `{"v":1,"type":...,"data":{...}}`.
- Coletor → servidor: `hello` (versão e comandos suportados), `heartbeat` (a cada 30 s; resposta
  `heartbeat_ack`), `command_update` (resposta `command_update_ack`).
- Servidor → coletor: `welcome`, `command` (`CommandMessage`), `cancel` (`{"id"}`), `error`.
- Ping WebSocket a cada 20 s; 2 pongs perdidos → reconecta. Reconexão com backoff exponencial e jitter
  (1 s → 60 s), para sempre.
- Fechamentos: `4401` token inválido (o agente renova o token), `4403` coletor revogado, `4000` outra
  conexão do mesmo coletor assumiu, `4429` excesso de mensagens, `1001` gateway reiniciando.
- Presença: o gateway grava a conexão em `agent_presence`; comandos chegam ao gateway por
  `LISTEN/NOTIFY` do PostgreSQL (`dm_command`, `dm_agent_revoked`), com varredura periódica de segurança.

## Canal de contingência (HTTPS)
Com o WebSocket caído há mais de 2 min, o agente envia o heartbeat por `POST /api/agent/heartbeat` e
busca comandos em `GET /api/agent/commands/pending` a cada 60 s. Atualizações de comando podem sempre ir
por `POST /api/agent/commands/{id}/update`.

## Comandos remotos
- Estados: `pending` → `sent` → `acked` → `running` → `succeeded`/`failed`; ou `expired` (padrão 10 min
  sem ser iniciado) e `cancelled` (portal). Estados finais nunca mudam; atualização repetida é aceita e
  ignorada. Comando entregue e não confirmado em 20 s é reentregue — o coletor não executa duas vezes o
  mesmo `id` (registro local na tabela `commands`).
- Tipos executados pelo coletor: `reconnect`, `restart_watchdog`, `scan_now`, `read_now`, `read_device`,
  `snmp_test`, `mib_walk` (envia o arquivo por `/uploads/mib-walk`), `set_config`, `get_logs` (envia por
  `/uploads/logs`), `diagnostics`, `pause`, `resume`, `promote_master`, `wake_host`, `ping_host`.
- Tipos executados pelo **dm-watchdog** (entregues só no heartbeat dele, `POST /api/watchdog/heartbeat`,
  a cada 60 s): `restart_agent`, `update` do coletor, `rollback`, `get_logs` com `source=watchdog` e
  `uninstall`. O coletor executa o `update` do watchdog (processo inverso). O andamento de todos vai por
  `POST /api/agent/commands/{id}/update`, com o mesmo token.
- `update` leva [UpdateParams](protocol-schemas/UpdateParams.json). O executor baixa o binário de `url`,
  confere o sha256 e a assinatura ed25519 da mensagem
  `dati-monitor-release/v1\n<componente>\n<versão>\n<os>\n<arch>\n<sha256>` com a chave pública
  embutida, guarda a versão atual como `previous`, troca, inicia e espera `/health` saudável e heartbeat
  no servidor por até 2 min; se falhar, volta sozinho para `previous` e informa `failed`.
- `output` é texto livre limitado a 1 MB; `result` é JSON; erros vão em `error`.

## Página web da impressora (túnel, seção 4.9)
- O portal cria a sessão (`POST /api/v1/devices/{id}/web-session`) e o servidor manda ao coletor do local
  (o MASTER primeiro, sempre um com WebSocket conectado) o comando `web_proxy_open` com
  [WebProxyOpenParams](protocol-schemas/WebProxyOpenParams.json): um IP, uma porta (80, 443, 8000, 8080 ou
  8443), o protocolo, a validade (30 min) e o limite de banda.
- O navegador abre `/devweb/{token}/...` no gateway, que envia `web_request`
  ([WebRequest](protocol-schemas/WebRequest.json)) pelo WebSocket. O coletor faz o pedido **só ao destino
  da sessão** (o caminho nunca muda o host), aceita certificado autoassinado da impressora, não segue
  redirecionamentos e responde `web_response` ([WebResponseStart](protocol-schemas/WebResponseStart.json)),
  depois `web_chunk` ([WebChunk](protocol-schemas/WebChunk.json), 32 KiB em base64, `end` no último) ou
  `web_error` ([WebError](protocol-schemas/WebError.json)). Esses quadros não contam no limite de
  mensagens do canal; têm o limite de banda da sessão.

## Endpoints

| Método | Caminho | Corpo | Resposta | Autenticação |
|---|---|---|---|---|
| POST | `/api/agent/enroll` | [EnrollRequest](protocol-schemas/EnrollRequest.json) | [EnrollResponse](protocol-schemas/EnrollResponse.json) | sem token (código de 8 caracteres) |
| POST | `/api/agent/enroll/check` | [EnrollCheckRequest](protocol-schemas/EnrollCheckRequest.json) | [EnrollCheckResponse](protocol-schemas/EnrollCheckResponse.json) | sem token; instalador confere o código sem usá-lo |
| POST | `/api/agent/token` | [TokenRequest](protocol-schemas/TokenRequest.json) | [TokenResponse](protocol-schemas/TokenResponse.json) | sem token (assinatura HMAC) |
| POST | `/api/agent/heartbeat` | [HeartbeatRequest](protocol-schemas/HeartbeatRequest.json) | [HeartbeatResponse](protocol-schemas/HeartbeatResponse.json) | Bearer (token do agente) |
| GET | `/api/agent/config` | — | [AgentConfig](protocol-schemas/AgentConfig.json) | Bearer |
| POST | `/api/agent/ranges/suggest` | [SuggestRangesRequest](protocol-schemas/SuggestRangesRequest.json) | — | Bearer |
| POST | `/api/agent/uninstalling` | [UninstallNotice](protocol-schemas/UninstallNotice.json) | — | Bearer; enviado pelo desinstalador |
| POST | `/api/agent/readings` | [ReadingsRequest](protocol-schemas/ReadingsRequest.json) | [ReadingsResponse](protocol-schemas/ReadingsResponse.json) | Bearer; corpo gzip |
| GET | `/api/agent/commands/pending` | — | [PendingCommandsResponse](protocol-schemas/PendingCommandsResponse.json) | Bearer (contingência) |
| POST | `/api/agent/commands/{id}/update` | [CommandUpdate](protocol-schemas/CommandUpdate.json) | [CommandUpdateResponse](protocol-schemas/CommandUpdateResponse.json) | Bearer |
| POST | `/api/agent/uploads/logs?command_id=` | — | [UploadResponse](protocol-schemas/UploadResponse.json) | Bearer; corpo .zip |
| POST | `/api/agent/uploads/mib-walk?command_id=` | — | [UploadResponse](protocol-schemas/UploadResponse.json) | Bearer; .snmprec em gzip |
| GET | `/api/agent/releases/{id}/file` | — | — | Bearer; binário de uma versão (update) |
| POST | `/api/watchdog/heartbeat` | [WatchdogHeartbeatRequest](protocol-schemas/WatchdogHeartbeatRequest.json) | [WatchdogHeartbeatResponse](protocol-schemas/WatchdogHeartbeatResponse.json) | Bearer (mesmo token do coletor) |
| WS | `/ws/agent` | [WsMessage](protocol-schemas/WsMessage.json) | [WsMessage](protocol-schemas/WsMessage.json) | Bearer no handshake |

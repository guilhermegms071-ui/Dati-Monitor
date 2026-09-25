"""Gera docs/protocol.md e docs/protocol-schemas/*.json a partir de backend/app/schemas/agent.py.

Uso:  .venv\\Scripts\\python scripts\\gen_protocol_docs.py          (grava)
      .venv\\Scripts\\python scripts\\gen_protocol_docs.py --check  (falha se estiver desatualizado)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.schemas.agent import PROTOCOL_MESSAGES, PROTOCOL_VERSION  # noqa: E402

DOCS = REPO / "docs"
SCHEMAS = DOCS / "protocol-schemas"

ENDPOINTS = [
    ("POST", "/api/agent/enroll", "EnrollRequest", "EnrollResponse", "sem token (código de 8 caracteres)"),
    ("POST", "/api/agent/token", "TokenRequest", "TokenResponse", "sem token (assinatura HMAC)"),
    ("POST", "/api/agent/heartbeat", "HeartbeatRequest", "HeartbeatResponse", "Bearer (token do agente)"),
    ("GET", "/api/agent/config", "-", "AgentConfig", "Bearer"),
    ("POST", "/api/agent/ranges/suggest", "SuggestRangesRequest", "-", "Bearer"),
    ("POST", "/api/agent/readings", "ReadingsRequest", "ReadingsResponse", "Bearer; corpo gzip"),
]

INTRO = f"""# Protocolo agente ↔ servidor (v{PROTOCOL_VERSION})

> Arquivo gerado por `scripts/gen_protocol_docs.py` a partir de `backend/app/schemas/agent.py`
> (espelho de `agent/internal/protocol`). Não edite à mão: um teste falha se ficar desatualizado.

Todo tráfego parte do coletor (HTTPS/WSS 443; `http://` só para `localhost` ou com `--insecure-dev`).
Toda mensagem tem `"v": {PROTOCOL_VERSION}`. Horários em UTC (ISO 8601).

## Cadastro e autenticação
1. O portal cria o coletor e gera um código de 8 caracteres (7 dias, uso único).
2. `POST /api/agent/enroll` troca o código por `agent_id` + segredo `S` (32 bytes, base64). O segredo fica
   no PC (DPAPI no Windows, arquivo 0600 no Linux). O servidor guarda só
   `K = SHA-256("dm-agent-auth\\n" + S)`, cifrada com a chave mestre.
3. `POST /api/agent/token`: `signature = hex(HMAC-SHA256(K, agent_id + "\\n" + ts + "\\n" + nonce))`.
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

## Endpoints

| Método | Caminho | Corpo | Resposta | Autenticação |
|---|---|---|---|---|
"""


def render() -> dict[Path, str]:
    files: dict[Path, str] = {}
    for name, model in PROTOCOL_MESSAGES.items():
        schema = model.model_json_schema(by_alias=True)
        files[SCHEMAS / f"{name}.json"] = (
            json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        )

    def link(n: str) -> str:
        return "—" if n == "-" else f"[{n}](protocol-schemas/{n}.json)"

    rows = "".join(
        f"| {m} | `{p}` | {link(req)} | {link(resp)} | {auth} |\n" for m, p, req, resp, auth in ENDPOINTS
    )
    files[DOCS / "protocol.md"] = INTRO + rows
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    files = render()
    stale = [p for p, c in files.items() if not p.exists() or p.read_text(encoding="utf-8") != c]
    if args.check:
        for p in stale:
            print(f"desatualizado: {p.relative_to(REPO)}")  # noqa: T201
        return 1 if stale else 0
    SCHEMAS.mkdir(parents=True, exist_ok=True)
    for p, c in files.items():
        p.write_text(c, encoding="utf-8", newline="\n")
    print(f"{len(files)} arquivos gerados em docs/")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())

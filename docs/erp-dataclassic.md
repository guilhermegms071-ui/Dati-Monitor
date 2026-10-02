# Integração com o ERP (Dataclassic / Databit)

Há duas integrações independentes. As duas são configuradas no portal em **Integração ERP**.

1. **API somente leitura** (`/api/erp/v1`): o ERP busca leituras, leitura de corte e equipamentos com um
   token de integração.
2. **Conector Dataclassic**: o Dati Monitor envia ao ERP requisições de suprimento, ordens de serviço e
   os contadores do dia, por uma fila com status por item.

Os contadores seguem sempre as mesmas regras dos relatórios:

- valem só as leituras válidas;
- o ajuste manual é aplicado campo a campo;
- uma leitura com regressão de contador fica de fora, a menos que alguém a classifique como válida ou
  como troca de placa;
- uma queda entre leituras válidas (por exemplo, troca de placa) não gera páginas negativas.

## 1. API somente leitura

Autenticação: `Authorization: Bearer dmerp_...`. O token é criado em Integração ERP → Tokens da API e
aparece **uma única vez**. O banco guarda só o SHA-256 dele, e o token pode ser revogado a qualquer momento.

| Chamada | O que devolve |
|---|---|
| `GET /api/erp/v1/readings?from=AAAA-MM-DD&to=AAAA-MM-DD[&customer_erp_code=][&cursor=][&limit=]` | Leituras válidas do período (dias no horário de Brasília), da mais antiga para a mais recente. Para paginar, use `next_cursor` no parâmetro `cursor`. |
| `GET /api/erp/v1/cutoff?date=AAAA-MM-DD[&customer_erp_code=]` | Para cada equipamento, a leitura válida mais recente até o fim do dia `date`. |
| `GET /api/erp/v1/devices[&customer_erp_code=][&after=][&limit=]` | Equipamentos ativos no parque, com franquia e preços de excedente. Para paginar, use `next_after` no parâmetro `after`. |

O schema completo está no OpenAPI (`/openapi.json`, grupo `erp`).

## 2. Conector Dataclassic

### O que vai para a fila

| Tipo | Quando entra | Chave anti-duplicidade |
|---|---|---|
| `supply_request` (requisição de suprimento) | Alerta `toner_low` aberto depois que o conector foi ligado | `supply:<id do alerta>` |
| `service_order` (OS) | Alerta de um dos tipos marcados: Chamado técnico, Consumíveis, Peças/manutenção e Outros (vindos da prtAlertTable, opcionalmente só dos códigos listados) e Atolamento recorrente | `os:<id do alerta>` |
| `counters` | Uma vez por dia, depois da hora configurada: leitura de corte de cada equipamento ativo no fim do dia anterior | `counters:<AAAA-MM-DD>` |

Os alertas da impressora só existem para as categorias ligadas na regra "Alerta da impressora" do
cliente (Alertas → Regras).

### Estados e reenvio

- `pending` → `sent`: o envio deu certo.
- Em caso de falha, o item continua `pending` com o `last_error` e tenta de novo depois de 1, 2, 5, 15, 30
  e 60 min. Depois da 6ª falha, o item fica `error` até alguém clicar em **Reenviar**.
- Toda falha aparece na tela, com o erro, e no log do worker. Nenhuma falha é descartada em silêncio.

### Transportes

- **Arquivo**: um JSON por item na pasta configurada, que o ERP importa. O arquivo é gravado com nome
  temporário e renomeado no final, então o ERP nunca lê um arquivo pela metade. Nome:
  `<tipo>-<AAAAMMDD-HHMMSS>-<id>.json`.
- **API**: `POST` do mesmo JSON para a URL configurada, com o cabeçalho `Authorization` configurado
  (guardado cifrado) e `Idempotency-Key: <id do item>`, para o ERP descartar repetições. Respostas 2xx
  contam como enviado.
- **Apenas e-mail** (só para a requisição de suprimento): manda um e-mail ao endereço de notificação e
  não cria nada no ERP.
- **Banco de dados**: ainda não implementado, porque depende do layout que a Databit vai informar. Os
  transportes ficam atrás de uma interface (`Transport` em `backend/app/services/erp_connector.py`), então
  um transporte novo entra sem mudar a fila.

### Formato provisório (`layout: "dati-monitor/erp/1"`)

Este é o formato até a Databit informar o layout do Dataclassic. Todo documento tem:

```json
{
  "id": "uuid do item da fila",
  "created_at": "2026-10-02T13:00:00+00:00",
  "layout": "dati-monitor/erp/1",
  "kind": "supply_request | service_order | counters",
  "company_code": "código da empresa",
  "operator": "operador"
}
```

**`supply_request`** acrescenta:

- `request`: operação, tipo desc, status, situação, condição de pagamento, vendedor, tipo de frete e e-mail
  de notificação;
- `alert`: id, tipo, severidade, mensagem, `opened_at` e dados;
- `customer`: `erp_code`, nome e CNPJ;
- `site`: o local;
- `device`: id, serial, PAT, marca, modelo, setor, IP e contadores total, PB e cor.

**`service_order`** acrescenta `order` (código do técnico, motivo, tipo de intervenção, status e
`category`: `service_call`, `consumable`, `parts`, `other` ou `jam_recurrent`) e os mesmos `alert`,
`customer`, `site` e `device`.

**`counters`** acrescenta:

- `date`: o dia, no formato AAAA-MM-DD;
- `readings`: uma entrada por equipamento, com `customer_erp_code`, serial, PAT, `read_at`, total, PB e
  cor.

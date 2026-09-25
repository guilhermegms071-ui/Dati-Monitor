# Impressoras simuladas (snmpsim)

Cada pasta `NN-nome/` é uma impressora simulada. O `scripts\dev.ps1` sobe um processo do
`snmpsim-command-responder` por pasta, em `127.0.0.1:(1160 + NN)`, com a comunidade `public`
(arquivo `public.snmprec`). Os arquivos são gerados por `generate.py` (rode de novo após alterar):

```powershell
.venv\Scripts\python profiles\recordings\sim\generate.py
```

Os OIDs de fabricante são **exatamente** os dos perfis fornecidos (`/profiles/canon.yaml`,
`/profiles/konica-minolta.yaml`); os demais são da Printer-MIB / Host-Resources-MIB.

| Pasta | Porta | Cenário (PROMPT seção 13) |
|---|---|---|
| `01-canon-cor` | 1161 | Canon colorida, tabela de contadores por ID (101, 108, 112, 113, 122, 123, 301, 501) |
| `02-canon-pb` | 1162 | Canon PB (i-SENSYS), tabela nomeada com nomes em OCTET STRING hex |
| `03-konica-cor` | 1163 | Konica bizhub C287: total 217031 = PB 100150 + cor 116881 (caso real) |
| `04-konica-pb` | 1164 | Konica bizhub 367 (monocromática) |
| `05-generica` | 1165 | Genérica, só Printer-MIB |
| `06-economia` | 1166 | Economia de energia (painel "Sleep"); `sleepy.json` põe um proxy na frente que só responde na 2ª tentativa depois de ficar ociosa |
| `07-erros` | 1167 | Atolamento + porta aberta (bits `0x0C`), toner com nível -3, prtAlertTable |
| `08-regressao` | 1168 | Contador que regride: os testes trocam `public.snmprec` por `regressed.snmprec.txt` (total 500000 → 400000) |

Formato `.snmprec`: `OID|tipo|valor`, **ordenado numericamente por OID**. Tipos: `2` Integer,
`4` OctetString, `4x` OctetString em hex, `6` OID, `65` Counter32, `66` Gauge32, `67` TimeTicks.

Walks de impressoras reais (Fase 10) ficam em `../real/` e entram nos mesmos testes.

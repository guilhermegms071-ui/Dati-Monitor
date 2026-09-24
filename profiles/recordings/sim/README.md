# Impressoras simuladas (snmpsim)

Cada pasta `NN-nome/` é uma impressora simulada. O `scripts\dev.ps1` sobe um processo do
`snmpsim-command-responder` por pasta, em `127.0.0.1:(1160 + NN)`, com a comunidade `public`
(arquivo `public.snmprec`).

Formato `.snmprec`: `OID|tipo|valor`, **ordenado numericamente por OID**. Tipos: `2` Integer,
`4` OctetString, `4x` OctetString em hex, `6` OID, `65` Counter32, `66` Gauge32, `67` TimeTicks.

| Pasta | Porta | Cenário (PROMPT seção 13) |
|---|---|---|
| `05-generica` | 1165 | Genérica, só com a Printer-MIB (OIDs padrão da seção 6.2) |

As demais (Canon, Konica, economia de energia, erros, regressão de contador) entram na Fase 2,
com os valores seguindo exatamente os OIDs dos perfis em `/profiles`.

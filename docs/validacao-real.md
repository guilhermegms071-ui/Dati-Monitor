# Validação na rede real (Fase 10)

Teste do Dati Monitor com as impressoras reais do escritório da Daticopy, comparado com a folha de
contadores impressa pelo painel de cada máquina.

## Escopo

| Item | Valor |
|---|---|
| Rede | `10.10.10.0/24` (detectada pela regra "monitorar redes conectadas" na placa "Ethernet 3" do PC 10.10.10.25 e confirmada pelo usuário antes da varredura) |
| SNMP | comunidade `public`, v2c e v1, porta 161 (informada pelo usuário) |
| Coletor | `dm-agent` compilado do código, cadastrado no backend local (local "Escritório 10.10.10", cliente "Daticopy (rede interna)") |
| Data | leitura e walks em 02/10/2026; aprovação dos contadores em 04/10/2026 |

## Resultado da varredura

A varredura de 254 endereços levou 25 s. Responderam 5 IPs, que correspondem a **4 equipamentos**:

| IP | Equipamento | Serial | Perfil / fonte dos contadores |
|---|---|---|---|
| 10.10.10.190 | Konica Minolta AccurioPrint C4065 | ACC2011022817 | `konica-minolta` / `konica_counters` |
| 10.10.10.199 | Controladora Fiery IC-607 da mesma C4065 | ACC2011022817 | **interface secundária: não é lida** (veja abaixo) |
| 10.10.10.191 | Kyocera ECOSYS M3550idn (só PB) | LSM5Y17646 | `kyocera` / `standard` (Printer-MIB) |
| 10.10.10.198 | Konica Minolta bizhub C287 | A797017500038 | `konica-minolta` / `konica_counters` |
| 10.10.10.240 | Konica Minolta bizhub C454e | A5C0011032344 | `konica-minolta` / `konica_counters` |

## Contadores lidos pelo sistema × folha de contadores

Leitura de 02/10/2026 às 17h31 (horário de Brasília):

| Equipamento | Total | PB | Cor | Detalhe |
|---|---|---|---|---|
| AccurioPrint C4065 (.190) | 329.240 | 13.838 | 315.402 | PB = 13.601 impressões + 237 cópias; cor = 315.326 + 76; duplex 56.919 |
| ECOSYS M3550idn (.191) | 784.496 | = total | — | monocromática |
| bizhub C287 (.198) | 66.900 | 10.959 | 55.937 | PB = 9.896 + 1.063; cor = 53.486 + 2.451; scanner 4.852 |
| bizhub C454e (.240) | 485.998 | 2.532 | 483.458 | PB = 2.266 + 266; cor = 483.298 + 160; scanner 179 |

Toner lido: C287 C 85%, M 50%, Y 93%, K 49%; C454e C 90%, M 29%, Y 72%, K 91% (com cilindros,
reveladores, fusor e correia). A C4065 informa só "há toner", sem porcentagem. A Kyocera não informa
nível pela Printer-MIB (descrição vazia, capacidade 0).

**Aprovado pelo usuário em 04/10/2026:** os contadores conferem com as folhas de contadores ("contadores
ok"). Nenhum perfil YAML precisou de correção.

## Achados e correções

1. **Impressora com controladora Fiery (mesmo serial em dois IPs).**
   - A C4065 responde pela placa de rede dela (.190) e pela Fiery IC-607 (.199).
   - A Fiery conta só o que passa por ela: o total dela no walk (328.967) é o da máquina (329.280) menos
     as 313 cópias. Ela também não separa PB e cor.
   - Antes da correção, o sistema alternava o IP do equipamento entre .190 e .199 e gravava leituras das
     duas interfaces.
   - **Correção** (igual ao Datacount/NDD), em `agent/internal/collector/interfaces.go`:
     - IPs com o mesmo serial viram um equipamento só, lido pela interface da própria impressora;
     - controladoras (Fiery/EFI, IC-xxx, Creo, EX-i, "print/color controller") são reconhecidas pela
       descrição SNMP, ficam fora da lista de leitura e são registradas no log;
     - com a impressora desligada, a controladora sozinha também não entra.
   - Teste: `TestSameSerialKeepsPrinterInterfaceNotController`, com os walks reais das duas interfaces.
2. **Status "Erro" para avisos de manutenção.**
   - As três Konica apareciam como "Erro" por `serviceRequested`, que vinha junto com papel baixo e
     manutenção preventiva vencida. As próprias máquinas informam `hrDeviceStatus = warning(3)`.
   - **Correção:** `serviceRequested` passou a "Atenção", como no Datacount. "Erro" fica para parada real:
     sem papel, sem toner, porta aberta, atolamento, offline, bandeja ausente, saída cheia.
   - Teste: `TestStatusOfRealPrinters`. Ao vivo, a C454e passou a "Atenção" em 04/10.
3. **Kyocera só com o total.** O perfil `kyocera` não tem OIDs próprios do fabricante: eles só podem sair de
   um walk conferido com a folha. Para a M3550idn, que é só PB, o total da Printer-MIB é o contador de
   faturamento, e isso foi aprovado.

## Walks nos testes automáticos

Os walks estão em `profiles/recordings/real/`, cada um com um `<nome>.expected.json`:
- `konica_accurioprint_c4065`
- `konica_accurioprint_c4065_fiery_ic607`
- `konica_bizhub_c287`
- `konica_bizhub_c454e`
- `kyocera_ecosys_m3550idn`

Eles rodam em todo build (`TestRealRecordings`, `TestStatusOfRealPrinters`,
`TestSameSerialKeepsPrinterInterfaceNotController`).

Os valores esperados de cada walk são os do momento em que o walk foi feito. Para C287 e Kyocera, eles
coincidem exatamente com a leitura aprovada. A C4065 imprimiu 40 páginas coloridas e a C454e fez 4
cópias PB entre a leitura e o walk; os demais contadores são idênticos.

## Pendente

Confirmar ao vivo, num dia útil com as máquinas ligadas, que a varredura registra a C4065 só pelo .190.
No sábado à noite, só a C454e estava ligada. A regra já está coberta pelo teste com os walks reais.

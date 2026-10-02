# Piloto do Dati Monitor: roteiro de validação em clientes reais

**Objetivo:** antes de desligar o Datacount, rodar o Dati Monitor **em paralelo** em 3 a 5 clientes
reais por **pelo menos 30 dias** e provar que:
- ele lê tudo o que o Datacount lê;
- os contadores batem com a folha da impressora;
- ele não para.

O Datacount continua sendo a referência de faturamento durante todo o piloto.

## 1. Escolha dos clientes

Escolha clientes que, juntos, cubram:

| Critério | Por quê |
|---|---|
| Canon e Konica Minolta coloridas | São os perfis com contadores próprios (PB/cor/A3) e onde um erro custa mais caro. |
| Um cliente com impressora USB | Valida Computadores, o contador por PJL e a leitura manual. |
| Um cliente com rede "difícil" (VLANs, firewall, Wi-Fi) | Valida a descoberta e o túnel da página web. |
| Um cliente que já ficou offline no Datacount | É o problema que o sistema novo precisa resolver. |
| Um local com dois PCs ligados | Valida o MASTER/STANDBY e o failover. |

Para cada cliente, anote: nome, local, número de impressoras no Datacount, PC escolhido para o
coletor e o contato no local.

## 2. Instalação (dia 0)

1. Siga `docs/operacao.md`, seção 1. Instale no **mesmo PC** do Datacount ou em outro PC sempre ligado.
   Os dois convivem: o SNMP só lê.
2. No mesmo dia, confira:
   - [ ] coletor **Online** e vigia saudável na página do coletor;
   - [ ] **número de impressoras** no Parque = número no Datacount. Uma impressora que falta é uma faixa
     de IP ou comunidade errada. Uma que sobra pode ser uma que o Datacount nunca viu: anote;
   - [ ] nenhuma impressora sem modelo/perfil. Se houver, cadastre o modelo (`docs/operacao.md`, seção 2).
3. **Imprima a folha de contadores de cada impressora** e registre os valores na planilha do piloto
   (seção 5).

## 3. Acompanhamento (diário na 1ª semana, depois semanal)

- [ ] **Disponibilidade:** em **Coletores**, nenhum coletor ficou offline sem motivo. Se ficou, o painel
  do vigia diz por quê. Registre e investigue cada caso.
- [ ] **Leituras:** **Relatórios → Equipamentos sem leitura** vazio e **Contador diário** com todos os dias.
- [ ] **Alertas:** os alertas de toner e de erro chegaram por e-mail e batem com o painel da impressora.
- [ ] **Troca de toner:** quando o técnico trocar um toner, o sistema registrou a troca.
- [ ] **Datacount × Dati Monitor:** compare o contador do dia nos dois sistemas, para cada impressora
  (seção 5).

## 4. Testes provocados (uma vez por cliente, com o cliente avisado)

- [ ] **Desligar o PC do coletor** por 1 hora:
  - o portal mostra **Offline** em poucos minutos;
  - ao religar, o coletor volta sozinho;
  - as leituras da hora parada chegam (a fila local guarda tudo).
- [ ] **Matar o processo** `dm-agent` pelo Gerenciador de Tarefas: o vigia o reinicia em menos de
  1 minuto e informa o motivo.
- [ ] **Tirar o cabo de rede** do PC: o coletor guarda as leituras e envia tudo quando a rede volta.
- [ ] **Local com dois PCs:** desligue o MASTER. Em poucos minutos, o STANDBY assume e continua lendo,
  sem leituras duplicadas.
- [ ] **Atualização:** publique uma versão nova no canal canary só para o piloto. O coletor atualiza
  sozinho, sem visita ao cliente.
- [ ] **Página web da impressora:** abra pelo portal (**Abrir página web**) a partir do escritório.

## 5. Comparação de contadores (critério de aprovação)

Planilha por impressora, preenchida no **dia 0**, no **dia 15** e no **dia 30**:

| Cliente | Serial | Modelo | Data | Folha: total / PB / cor | Dati Monitor: total / PB / cor | Datacount: total / PB / cor | Diferença | Observação |
|---|---|---|---|---|---|---|---|---|

**Critérios de aprovação**
- **Contadores:** o Dati Monitor bate **exatamente** com a folha nos três pontos (dia 0, 15 e 30).
- **Diferença com o Datacount:** só é aceita se a folha der razão ao Dati Monitor. Exemplo: o Datacount
  lendo um contador errado de um modelo.
- **Produção do período:** a **Produção** do mês (relatório de produção) bate com o que o faturamento
  calcularia pelo Datacount, considerando as diferenças explicadas acima.
- **Disponibilidade:** nenhum coletor offline por mais de 1 hora sem causa externa (PC desligado, falta
  de energia ou de internet), registrada no painel do vigia.
- **Testes provocados:** todos os itens da seção 4 passaram.

## 6. Fim do piloto

1. Reúna as planilhas, a lista de ocorrências (cada offline, falha ou divergência, com causa e correção)
   e o resultado dos testes provocados.
2. Decisão:
   - **aprovado:** planejar a migração dos demais clientes em ondas (10 a 20 clientes por semana), com o
     Datacount desligado cliente a cliente só depois de **um ciclo de faturamento** batendo;
   - **reprovado:** corrigir os pontos levantados e repetir o piloto nos clientes afetados.

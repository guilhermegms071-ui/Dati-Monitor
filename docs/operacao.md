# Operação do Dati Monitor

Guia da equipe técnica da Daticopy para quatro tarefas do dia a dia: instalar o coletor num cliente,
cadastrar um modelo novo de impressora, publicar uma versão nova do coletor e restaurar um backup.
Os comandos rodam num terminal comum, sem administrador, salvo quando o texto avisar.

---

## 1. Instalar o coletor num cliente

O coletor (`dm-agent`) é instalado num PC do cliente que fica ligado o dia todo. Junto vai o vigia
(`dm-watchdog`), que reinicia o coletor se ele parar. Um PC por local já basta. Um segundo PC no mesmo
local vira reserva (STANDBY) e assume sozinho se o primeiro cair.

**Requisitos do PC**
- Windows 10, 11 ou Server 2016 ou mais novo, de 32 ou 64 bits, ou ARM. O instalador recusa
  Windows 7, 8 e 8.1 com uma mensagem explicando o motivo.
- Linux (Debian, Ubuntu ou Raspberry Pi OS) com systemd.
- Saída HTTPS (porta 443) para o servidor do Dati Monitor. Não precisa abrir nenhuma porta de entrada.
- Acesso SNMP (UDP 161) às impressoras da rede.

**Passo a passo**
1. No portal, cadastre o cliente e o local em **Clientes**, com as faixas de IP e a comunidade SNMP.
   Para importar vários clientes de uma vez, use **Clientes → Importar CSV**.
2. Em **Coletores → Novo coletor**, escolha o local. O portal mostra:
   - o **código de cadastro** (8 caracteres, uso único, vale por tempo limitado);
   - o **link do instalador** (o portal sugere a versão certa pelo navegador);
   - a **linha de instalação silenciosa** do Windows e o **comando do Linux**.
3. No PC do cliente:
   - **Windows, pelo assistente:** baixe o `setup.exe` pelo link e execute. Informe o endereço do
     servidor e o código. O instalador confere o código **antes** de instalar.
   - **Windows, sem interação** (acesso remoto, GPO): copie a linha mostrada no portal, por exemplo
     `setup.exe /VERYSILENT /SERVER=https://monitor.daticopy.com.br /CODE=ABCD1234`.
     Se o cadastro falhar, o código de saída é 3.
   - **Linux:** cole o comando mostrado no portal (`curl … | sudo sh`). Ele baixa o pacote certo
     para a arquitetura, cadastra o coletor e liga os serviços.
4. Em até um minuto, o coletor aparece **Online** em **Coletores**. A primeira varredura encontra as
   impressoras das faixas de IP:
   - se o local tem **ativação automática**, as impressoras entram direto no **Parque**;
   - senão, ficam em **Descobertas** para **Ativar** ou **Descartar**.
5. Impressoras USB ligadas ao PC aparecem em **Computadores**. Quando a impressora não informa o
   contador pela USB (sem PJL), registre a folha de contadores em **Registrar leitura**.

**Se algo der errado**
- O instalador diz que o código é inválido ou vencido: gere outro em **Coletores**. O código anterior
  não foi consumido.
- O coletor fica **Offline**: na página do coletor, veja o painel do vigia. O vigia tem canal próprio e
  informa o motivo da parada, mesmo com o coletor fora.
- Os logs ficam no próprio PC:
  - Windows: `C:\ProgramData\DatiMonitor\logs`;
  - Linux: `journalctl -u DatiMonitorAgent -u DatiMonitorWatchdog`.
  Pelo portal, use **Baixar logs** na página do coletor (o do vigia também).

---

## 2. Cadastrar um modelo novo com walk

Um modelo sem perfil aparece no parque com os contadores genéricos (Printer-MIB). Para ler PB, cor e
os outros contadores do fabricante, o modelo precisa de um perfil. **Nunca invente OIDs:** todo OID sai
de um walk real conferido com a folha de contadores.

1. **Imprima a folha de contadores** pelo painel da impressora. Ela é a referência.
2. **Faça o walk:**
   - **Pelo portal:** abra o equipamento no **Parque** e clique em **Walk**. O coletor do local
     percorre a árvore SNMP e envia o resultado.
   - **No local, sem portal:** rode
     `dm-tool walk --ip 192.168.0.50 --community public --out canon_c3226.snmprec`.
     Também há opções para SNMPv3, com `--version v3 --v3-user …`.
3. **Ache os OIDs:** em **Perfis de modelos**, abra o perfil da marca. No **Explorador do walk**,
   escolha o walk e digite um valor da folha (por exemplo, o total PB). O explorador mostra os OIDs que
   guardam aquele número. Repita para cor, total, A3 e scanner.
4. **Edite o perfil:** arraste o OID do explorador para o **Editor YAML**, ou selecione
   `PREENCHER_PELO_WALK` e clique em **Inserir**. Clique em **Validar**.
5. **Teste sem publicar:** use **Testar rascunho** no IP do equipamento e compare cada valor com a folha.
6. **Publique:** **Publicar nova versão**, com uma observação (modelo, firmware, quem conferiu).
   - Os coletores recebem o perfil novo na próxima configuração; ninguém precisa ir ao cliente.
   - O histórico de versões nunca é apagado.
   - Se algo sair errado, **Ativar** a versão anterior é o rollback.
7. **Guarde o walk como teste:** **Salvar como gravação de teste**, com o nome `marca_modelo` (por
   exemplo, `canon_c3226`) e os valores da folha. A gravação vai para `profiles/recordings/real/`. A
   partir daí, o `TestRealRecordings` do Go confere o perfil contra esse walk a cada build: se uma
   alteração futura quebrar a leitura do modelo, o teste falha.

---

## 3. Publicar uma versão nova do coletor

Os coletores só instalam binários **assinados** com a chave da Daticopy (ed25519). A chave **privada**
fica fora do repositório, em `%USERPROFILE%\.dati-monitor\release-signing.key`, e nunca vai para o
servidor, para o Git ou para os logs. Guarde uma cópia offline dela. Sem a chave, nenhuma versão nova
pode ser publicada.

**Caminho curto:** `scripts\release.ps1 -Version 1.2.0`, com a árvore do git sem alterações. Ele faz os
passos 1, 2 e 5 de uma vez e entrega em `dist\release-1.2.0\`:
- binários dos 7 alvos;
- um JSON de assinatura por binário em `assinaturas\`, pronto para colar em **Versões**;
- instaladores, build do portal e `SHA256SUMS`.

Depois siga os passos 3 e 4. O passo a passo manual é este:

1. **Compile** com o número da versão:
   `scripts\build-agent.ps1 -Version 1.2.0`. Isso gera `dm-agent`, `dm-watchdog` e `dm-tool` para os
   7 alvos em `dist\`.
2. **Assine** cada binário que vai ser publicado:
   ```powershell
   dist\windows-amd64\dm-tool.exe sign --file dist\windows-amd64\dm-agent.exe --version 1.2.0
   dist\windows-amd64\dm-tool.exe sign --file dist\linux-arm64\dm-agent --version 1.2.0 --os linux --arch arm64
   ```
   O `sign` confere se o binário se declara exatamente `dm-agent 1.2.0` e imprime um JSON com sha256,
   tamanho e assinatura.
3. **Publique:** em **Versões** (superadmin), clique em **Publicar versão**:
   - envie o binário e cole o JSON do `sign`;
   - o portal confere o sha256 no navegador antes de enviar.
4. **Libere aos poucos:**
   - comece no canal **canary** ou com **liberação gradual** de 5% a 10%;
   - acompanhe em **Versões** quantos coletores atualizaram e quantos falharam;
   - depois mude para **stable** e 100%.

   Um coletor que não volta saudável depois de atualizar desfaz a troca sozinho (rollback
   automático) e informa o erro.
5. **Instaladores novos** (opcional, para instalações novas já saírem na versão nova):
   - Windows: `scripts\build-installer.ps1 -Version 1.2.0 -Server https://monitor.daticopy.com.br -Sign`;
   - Linux: `.venv\Scripts\python scripts\build_linux.py --version 1.2.0`;
   - publique os arquivos em **Downloads → Publicar instalador**.

---

## 4. Backup e restauração

**Backup** (agende no Agendador de Tarefas do servidor, uma vez por dia):

```powershell
scripts\backup.ps1 -OutDir D:\backups -Keep 30
```

Cada backup gera três arquivos:
- o dump do banco (`.dump`, `pg_dump` formato custom);
- um `.zip` com os arquivos enviados (walks, logs, versões e instaladores);
- um manifesto `.json` com sha256, a revisão das migrações e as linhas por tabela.

O dump é conferido com `pg_restore --list` antes de o backup ser dado como bom. `-Keep` apaga os mais
antigos. Copie a pasta de backups para fora do servidor (outro disco ou a nuvem).

**Restaurar**

1. **Teste antes num banco separado**, sem mexer na produção:
   ```powershell
   scripts\restore.ps1 -Manifest D:\backups\dati-dati_prod-20261002-030000.json -Database dati_restaurado
   ```
   No fim, o script confere a revisão das migrações e se as linhas de cada tabela batem com o manifesto.
2. **Para substituir a produção:**
   - pare os serviços (API, gateway e worker);
   - rode com `-Force` (sobrescreve um banco que já tem tabelas) e `-RestoreFiles` (devolve também os
     arquivos, conferindo o sha256 de cada um):
     ```powershell
     scripts\restore.ps1 -Manifest …json -Database dati_prod -Force -RestoreFiles
     ```
   - suba os serviços de novo.

   O script recusa restaurar se ainda houver conexões abertas no banco.
3. **Depois de restaurar:**
   - os coletores reenviam sozinhos o que ficou na fila local deles desde o horário do backup, e nada se
     perde se a fila não estourou;
   - confira **Coletores** (todos Online);
   - confira o **Parque** (última comunicação recente).

---

## Testes de resistência e de carga

Rode antes de cada versão grande.

- **Resistência:** `scripts\soak.ps1 -Minutes 30`.
  - Precisa do `scripts\dev.ps1` no ar.
  - Para 24 horas: `-Minutes 1440`.
  - Falha se memória, goroutines ou handles do coletor crescerem mais de 20% depois de estabilizar.
- **Carga:** `scripts\load.ps1`.
  - Usa um banco próprio (`dati_load`) e as portas 8200/8201.
  - Simula 500 coletores com WebSocket aberto e leituras de 20.000 equipamentos por hora.
  - Exige a tela de parque abaixo de 1 s.

Os relatórios ficam em `var\soak\` e `var\load\`.

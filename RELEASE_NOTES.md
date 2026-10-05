# Dati Monitor 1.0.0

Primeira versão do sistema de leitura de impressoras da Daticopy, que substitui o Datacount. Ele lê os
contadores e suprimentos das impressoras dos clientes, mostra o parque no portal, gera alertas e entrega
as leituras ao ERP para o faturamento.

## O que vem nesta versão

**Coletor (`dm-agent`) e vigia (`dm-watchdog`)**
- Windows 10, 11 e Server 2016+ (64 bits, 32 bits e ARM) e Linux (Debian, Ubuntu, Raspberry Pi).
- O instalador recusa Windows 7 e 8 com uma mensagem clara.
- Funciona só com saída HTTPS: nenhuma porta de entrada no cliente.
- **Não para:**
  - o vigia reinicia o coletor em segundos e informa o motivo;
  - o botão **Reativar** recupera um coletor travado;
  - as leituras ficam numa fila local enquanto a internet cai e chegam todas quando ela volta;
  - com dois PCs no local, o reserva assume em até 3,5 min se o principal cair.
- **Leitura SNMP v1, v2c e v3:**
  - perfis por fabricante, com tabelas próprias de Canon e Konica Minolta (PB, cor, A3, cópia, impressão,
    scanner, frente e verso);
  - suprimentos com nível em %, status com motivo e alertas da impressora;
  - nova tentativa para impressoras em economia de energia.
- **Impressoras USB:** inventário no PC, contador automático por PJL quando a impressora responde e leitura
  manual (folha de contadores) quando não responde.
- **Um equipamento, um registro:** impressoras com controladora de impressão (Fiery/EFI e similares) são
  lidas pela placa da própria impressora, como no Datacount e no NDD.
- **Atualização automática:** versões assinadas (ed25519), liberação gradual por canal, rollback
  automático se a versão nova não ficar saudável.
- **Página web da impressora pelo portal** (túnel pelo coletor), só para IPs cadastrados e com auditoria.

**Portal**
- Parque com as colunas do Datacount, filtros, ordenação, exportação e ações em massa; responde em menos
  de 1 s com 20.000 equipamentos.
- Descobertas (ativar/descartar), Computadores (USB), Coletores, Clientes e Locais (importação CSV, mapa).
- Alertas por e-mail, webhook e WhatsApp: coletor offline, toner baixo e previsão de término, erros e
  atolamento recorrente, regressão de contador.
- 18 relatórios (produção, leitura de corte, cobrança, trocas de toner etc.) em CSV, XLSX e PDF.
- Tema claro e escuro; usável no celular.
- Usuários e permissões por papel, auditoria de todas as alterações, 2FA.

**Integração com o ERP**
- API somente leitura (`/api/erp/v1`) com token próprio.
- Conector Dataclassic por arquivo, HTTP ou e-mail (`docs/erp-dataclassic.md`).

## Qualidade

- Testes automáticos de Go, Python, portal e ponta a ponta com o coletor real.
- Os 5 walks das impressoras reais do escritório rodam em todo build.
- Testes de caos (queda de 60 min, reinício do banco, queda do coletor principal), resistência de 30 min
  e carga (500 coletores, 20.000 equipamentos).
- Resultado completo: `scripts\acceptance.ps1`, com OK/FALHOU por critério da seção 15.
- Nenhuma vulnerabilidade conhecida nas dependências na data da versão (npm audit, govulncheck,
  pip-audit).

## Validação com impressoras reais

Testado na rede do escritório (10.10.10.0/24) com Konica Minolta AccurioPrint C4065, bizhub C287,
bizhub C454e e Kyocera ECOSYS M3550idn. Os contadores conferiram com as folhas impressas pelo painel e
foram aprovados. Detalhes em `docs/validacao-real.md`.

## Antes de usar em produção

- **Servidor:** hospedar API, gateway, worker e portal com TLS. O HSTS e o CORS restrito são ligados com
  `APP_ENV=production`.
- **Certificado de assinatura de código:** sem ele, o Windows mostra "editor desconhecido" ao instalar.
  Quando for comprado: `scripts\build-installer.ps1 -Sign`.
- **Chave privada de versões** (`%USERPROFILE%\.dati-monitor\release-signing.key`): guarde uma cópia
  offline. Sem ela, nenhuma versão nova pode ser publicada.
- **Piloto:** seguir `docs/piloto.md` (3 a 5 clientes, 30 dias, em paralelo com o Datacount) antes de
  migrar os demais.

## Como instalar e operar

`docs/operacao.md` cobre:
- instalar o coletor num cliente;
- cadastrar um modelo novo com walk;
- publicar uma versão;
- backup e restauração.

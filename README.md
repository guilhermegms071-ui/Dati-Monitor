# Dati Monitor

Monitoramento de impressoras e multifuncionais da Daticopy: coletor instalado no cliente (Go),
backend FastAPI + PostgreSQL e portal React. Substitui o Datacount.

- Especificação: [PROMPT.md](PROMPT.md)
- Andamento, decisões e como testar: [PROGRESS.md](PROGRESS.md)
- Guia do repositório e comandos: [CLAUDE.md](CLAUDE.md)
- Arquitetura: [docs/arquitetura.md](docs/arquitetura.md)

## Começo rápido (Windows, sem Docker)

```powershell
copy .env.example .env               # preencha as senhas
scripts\setup-db.ps1                 # bancos dati_dev e dati_test
scripts\dev.ps1                      # sobe tudo; portal em http://localhost:5173
scripts\test.ps1 -E2E                # todos os testes
```

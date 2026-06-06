# Kiro Gateway

Proxy compatível com **Anthropic** e **OpenAI** que roteia requisições para o **Kiro** usando o `kiro-cli`.

Você cadastra suas **chaves `ksk_`** (API keys do Kiro PRO) no painel admin, gera **API keys próprias** para seus clientes, e eles usam no **Claude Code**, **Claude Desktop** ou qualquer ferramenta compatível — apontando para a URL do seu servidor.

```
Claude Code / Claude Desktop
        │  (sua API key + sua URL base)
        ▼
   Kiro Gateway  ──►  kiro-cli  ──►  Kiro (Claude)
        │
   Painel admin: contas, clientes, gastos
        │
   Tudo persistido em SQLite
```

## Funcionalidades

- **API Anthropic** (`/v1/messages`) e **OpenAI** (`/v1/chat/completions`), com streaming
- **Multi-conta**: cadastre várias chaves `ksk_`, com round-robin e failover automático
- **Painel admin** (`/admin`): adicionar/validar contas, gerar API keys de clientes, dashboard de gastos
- **Senha do admin** definida no primeiro acesso, armazenada (hash) no SQLite
- **Logs de uso** por cliente, conta e modelo

## Como funciona a autenticação

| Quem | Como autentica |
|------|----------------|
| Você (admin) | Senha definida no primeiro acesso ao `/admin` |
| Suas contas Kiro | Chave `ksk_...` (cadastrada no painel) |
| Seus clientes | API key `kgw-...` gerada no painel |

## Deploy na Square Cloud

1. Suba o projeto (upload ou Git Integration)
2. O `squarecloud.app` já está configurado — o `start.sh` baixa o `kiro-cli` Linux automaticamente no primeiro boot
3. Acesse `https://kiro-gateway.squareweb.app/admin`
4. Defina a senha do admin
5. Cadastre suas chaves `ksk_` em **Contas Kiro**
6. Gere API keys em **Clientes**

## Rodar localmente

```bash
pip install -r requirements.txt

# Se já tem o kiro-cli instalado:
KIRO_CLI_PATH=/caminho/para/kiro-cli python3 main.py

# Acesse http://localhost:8000/admin
```

## Configurar no Claude Desktop / Claude Code

```
Base URL:  https://kiro-gateway.squareweb.app
API Key:   (a chave kgw-... gerada no painel)
```

## Estrutura

```
main.py                  Entry point (FastAPI)
start.sh                 Baixa kiro-cli e sobe o servidor (deploy)
kiro/
  database.py            SQLite: contas, clientes, uso, settings
  kirocli_runner.py      Executa o kiro-cli como subprocess
  account_pool.py        Round-robin + failover entre chaves ksk_
  routes_proxy.py        /v1/messages, /v1/chat/completions, /v1/models
  routes_admin.py        Painel admin (API)
  admin_ui.py            Painel admin (HTML)
```

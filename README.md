# ai-agents

Exemplo de orquestrador e especialistas. O caso executável é uma consulta de sustentação: o orquestrador usa o modelo mais capaz, três especialistas usam o modelo mais barato, a ferramenta só passa pelo MCP Gateway e a leitura é sempre na réplica.

O artigo está em [artigo/artigo.md](artigo/artigo.md). Código, pastas e protocolos usam inglês.

## Domínios

Dois domínios, duas aplicações. Cada uma sobe sozinha e lê o próprio banco.

- [apps/payments](apps/payments) — domínio `payments`. Diz se o customer teve processamento na data e se foi sucesso ou falha. Ferramenta `get_processing`. Agente `payments`. Skill `lookup_customer_processing`.
- [apps/reconciliation](apps/reconciliation) — domínio `reconciliation`. Diz se a conciliação está atualizada, pendente ou em erro. Ferramenta `get_reconciliation`. Agente `reconciliation`. Skill `lookup_customer_reconciliation`.

O especialista `anomaly` não é um domínio e não tem banco. Skill `detect_anomaly`. Ele só compara os dois relatórios.

## Em volta dos domínios

- `src/mcp_servers/` — um servidor MCP por domínio. O nome do servidor é o nome do domínio.
- `src/gateway/` — allowlist, rate limit, teto de chamadas, redação e log.
- `src/agents/` — `payments`, `reconciliation` e `anomaly`.
- `src/orchestrator/` — devolve os três customers da carga e monta o parecer.
- `src/contracts/` — o JSON compartilhado.
- `src/carga/` — gera os três customers nas duas réplicas.
- `scripts/run_demo.py` — sobe as duas apps e o restante, e faz a pergunta.
- `tests/` — permissão, réplica, rate limit e redação, sem chamar modelo.

`var/` nasce na execução e não entra no Git. O mesmo vale para `.venv/` e `.env`.

## Rodar

É preciso Python 3.12.

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python \
  "fastapi>=0.115" "uvicorn>=0.32" "httpx>=0.27" "pydantic>=2.10" \
  "mcp>=1.9" "a2a-sdk>=1.2" "pytest>=8.3"
.venv/bin/python -m pytest
CARGA_SEED=7 .venv/bin/python scripts/run_demo.py
```

Sem `OPENAI_API_KEY`, o parecer sai do JSON da réplica. Com a chave, o orquestrador reescreve o texto em `ORCHESTRATOR_MODEL` e os especialistas resumem em `SPECIALIST_MODEL`. Os nomes das variáveis estão em `.env.example`.

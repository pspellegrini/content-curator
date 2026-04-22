# content-curator — Pipeline de Curadoria do Vault

> Contexto universal: `~/.claude/CLAUDE.md`. Hub: `Gethub/CLAUDE.md`.

## Objetivo

Pipeline automático que busca artigos e posts relevantes via RSS e APIs,
analisa a relevância com LLM, e escreve os itens aprovados em `vault/00-inbox/`.

Mantém o vault da Gethub atualizado com conteúdo curado sem esforço manual.

## Fase atual

**Operacional** — pipeline rodando manualmente sob demanda.

## Stack

- Python 3.11+
- `feedparser` — leitura de feeds RSS (Substacks, arxiv)
- `httpx` — requisições HTTP (Hacker News API)
- `google-genai` (gemini-2.0-flash-lite) — análise de relevância
- `PyYAML` — configuração de fontes (`sources.yaml`)
- `python-dotenv` — variáveis de ambiente
- `python-slugify` — nomes de arquivo limpos

## Entradas

- `sources.yaml` — lista de fontes (Substacks RSS, HN tags, arxiv queries)
- `state.json` — URLs já processadas (deduplicação)
- `.env` — `GOOGLE_API_KEY` (não commitado)

## Saída

- `vault/00-inbox/YYYY-MM-DD-slug.md` — item curado em Markdown com frontmatter
- `vault/06-resources/references.md` — atualizado com links adicionados

## Como rodar

```bash
cd projects/content-curator
python curator.py             # roda normalmente
python curator.py --dry-run   # busca e analisa sem escrever nada
python curator.py --once      # força mesmo sem itens novos
```

## Dependências de ambiente

```bash
pip install -r requirements.txt
# Criar .env com:
# GOOGLE_API_KEY=<sua_chave>
```

## Proximos passos

- [ ] Migrar de google-genai para Anthropic SDK (alinhar com stack do hub)
- [ ] Adicionar agendamento automatico (cron ou Task Scheduler)
- [ ] Separar analise de relevancia em prompt configuravel em sources.yaml

## Knowledge references

- Vault: `vault/01-projects/content-curator/_index.md`

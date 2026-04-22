# content-curator

Pipeline automático de curadoria de artigos para vault Obsidian.

Busca novos itens em Substacks (RSS), Hacker News e arxiv, analisa relevância
com LLM e escreve os itens aprovados em `vault/00-inbox/` — prontos para
revisão no Obsidian.

## Como funciona

```
sources.yaml → busca RSS/API → análise LLM → vault/00-inbox/YYYY-MM-DD-slug.md
```

1. Lê as fontes configuradas em `sources.yaml`
2. Filtra itens já processados (`state.json` — deduplicação por URL)
3. Envia título + resumo para o LLM avaliar relevância
4. Itens aprovados são salvos como notas Markdown com frontmatter

## Pré-requisitos

- Python 3.11+
- Conta Google AI Studio com acesso à API (Gemini)
- Vault Obsidian em `../../vault/` (relativo ao projeto)

## Instalação

```bash
git clone https://github.com/pspellegrini/content-curator.git
cd content-curator
pip install -r requirements.txt
cp .env.example .env   # edite com sua chave
```

Crie o arquivo `.env`:
```
GOOGLE_API_KEY=sua_chave_aqui
```

## Uso

```bash
# Roda normalmente — busca e escreve itens novos
python curator.py

# Busca e analisa, mas nao escreve nada (modo seguro para testar)
python curator.py --dry-run

# Forca execucao mesmo sem itens novos
python curator.py --once
```

## Configuracao de fontes

Edite `sources.yaml` para adicionar ou remover fontes:

```yaml
substacks:
  - name: Nome do autor
    url: https://autor.substack.com/feed
    priority: high   # high | medium | low

hacker_news:
  enabled: true
  min_score: 150     # pontuacao minima para considerar
  keywords:
    - python
    - llm
```

## Saida

Cada item aprovado gera um arquivo em `vault/00-inbox/`:

```
vault/00-inbox/
└── 2026-04-22-titulo-do-artigo.md
```

Com frontmatter:
```yaml
---
source: Hacker News
url: https://...
date: 2026-04-22
tags: [inbox, curadoria]
---
```

## Licenca

MIT — veja [LICENSE](LICENSE).

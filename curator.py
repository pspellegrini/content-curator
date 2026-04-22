#!/usr/bin/env python3
"""
Content Curator — pipeline automático de curadoria para o vault da Gethub.

Busca novos itens em Substacks (RSS), Hacker News e arxiv,
analisa relevância com Claude API e escreve itens aprovados em vault/00-inbox/.

Uso:
  python curator.py            # roda normalmente
  python curator.py --dry-run  # busca e analisa sem escrever nada
  python curator.py --once     # força execução mesmo sem itens novos
"""

import argparse
import json
import os
import re
import sys
import textwrap
from datetime import date, datetime, timezone
from pathlib import Path

import feedparser
from google import genai
from google.genai import types as genai_types
import httpx
import yaml
from dotenv import load_dotenv
from slugify import slugify

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
GETHUB_DIR = BASE_DIR.parent.parent
VAULT_DIR = GETHUB_DIR / "vault"
INBOX_DIR = VAULT_DIR / "00-inbox"
REFERENCES_FILE = VAULT_DIR / "06-resources" / "references.md"
SOURCES_FILE = BASE_DIR / "sources.yaml"
STATE_FILE = BASE_DIR / "state.json"
LOG_FILE = BASE_DIR / "curator.log"

# ---------------------------------------------------------------------------
# State (deduplicação de URLs)
# ---------------------------------------------------------------------------


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"seen_urls": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def is_seen(url: str, state: dict) -> bool:
    return url in state["seen_urls"]


def mark_seen(url: str, state: dict) -> None:
    if url not in state["seen_urls"]:
        state["seen_urls"].append(url)


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------


def fetch_substacks(cfg: list[dict], state: dict) -> list[dict]:
    items = []
    for source in cfg:
        try:
            feed = feedparser.parse(source["url"])
            for entry in feed.entries:
                url = entry.get("link", "")
                if not url or is_seen(url, state):
                    continue
                items.append({
                    "source": source["name"],
                    "source_type": "substack",
                    "priority": source.get("priority", "medium"),
                    "title": entry.get("title", ""),
                    "url": url,
                    "summary": entry.get("summary", "")[:2000],
                    "published": entry.get("published", ""),
                })
        except Exception as e:
            log(f"[ERRO] Substack {source['name']}: {e}")
    return items


def fetch_hacker_news(cfg: dict, state: dict) -> list[dict]:
    if not cfg.get("enabled"):
        return []
    items = []
    keywords = cfg.get("keywords", [])
    min_score = cfg.get("min_score", 150)
    try:
        for keyword in keywords[:5]:  # limita chamadas à API
            resp = httpx.get(
                "https://hn.algolia.com/api/v1/search",
                params={"query": keyword, "tags": "story", "numericFilters": f"points>={min_score}"},
                timeout=15,
            )
            resp.raise_for_status()
            for hit in resp.json().get("hits", []):
                url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}"
                if is_seen(url, state):
                    continue
                items.append({
                    "source": "Hacker News",
                    "source_type": "hn",
                    "priority": "medium",
                    "title": hit.get("title", ""),
                    "url": url,
                    "summary": f"Score: {hit.get('points', 0)} | Comments: {hit.get('num_comments', 0)}",
                    "published": hit.get("created_at", ""),
                })
    except Exception as e:
        log(f"[ERRO] Hacker News: {e}")
    return items


def fetch_arxiv(cfg: dict, state: dict) -> list[dict]:
    if not cfg.get("enabled"):
        return []
    items = []
    categories = cfg.get("categories", ["cs.AI"])
    keywords = cfg.get("keywords", [])
    max_results = cfg.get("max_results_per_day", 8)
    cat_query = " OR ".join(f"cat:{c}" for c in categories)
    kw_query = " OR ".join(f'ti:"{k}"' for k in keywords[:4])
    query = f"({cat_query}) AND ({kw_query})" if kw_query else cat_query
    try:
        resp = httpx.get(
            "https://export.arxiv.org/api/query",
            params={"search_query": query, "start": 0, "max_results": max_results, "sortBy": "submittedDate"},
            timeout=20,
        )
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        for entry in feed.entries:
            url = entry.get("link", "")
            if not url or is_seen(url, state):
                continue
            items.append({
                "source": "arxiv",
                "source_type": "paper",
                "priority": "medium",
                "title": entry.get("title", "").replace("\n", " "),
                "url": url,
                "summary": entry.get("summary", "")[:2000],
                "published": entry.get("published", ""),
            })
    except Exception as e:
        log(f"[ERRO] arxiv: {e}")
    return items


# ---------------------------------------------------------------------------
# Análise via Claude API
# ---------------------------------------------------------------------------

VAULT_CONTEXT_TEMPLATE = """
Você é um curador de conhecimento especializado em:
- Claude Code, Claude API, Anthropic SDK
- Vibe coding, Spec-Driven Development (SDD)
- Agentes de IA, multi-agent systems, context engineering
- Python, FastAPI, Streamlit, pandas
- Dados públicos brasileiros, análise de dados

O vault já cobre os seguintes tópicos (não repetir):
{already_covered}

Analise o item abaixo e retorne um JSON com:
{{
  "relevance_score": <1-10>,
  "relevance_reason": "<por que é relevante ou não>",
  "suggested_vault_location": "<pasta/nome-do-arquivo.md ou null>",
  "tags": ["<tag1>", "<tag2>"],
  "summary": "<resumo em português, 2-3 parágrafos>",
  "key_insights": ["<insight 1>", "<insight 2>", "<insight 3>"],
  "suggested_vault_entry": "<rascunho do conteúdo para o vault, em Markdown, ou null se score < 6>"
}}

Pastas do vault: 02-concepts/ (conceitos técnicos), 03-sources/ (fontes de dados), 04-patterns/ (padrões de código), 06-resources/references.md (links)

Item a analisar:
Título: {title}
Fonte: {source}
URL: {url}
Conteúdo: {summary}
"""


def analyze_item(item: dict, client: genai.Client, model: str, max_tokens: int, vault_context: str) -> dict | None:
    prompt = VAULT_CONTEXT_TEMPLATE.format(
        already_covered=vault_context,
        title=item["title"],
        source=item["source"],
        url=item["url"],
        summary=item["summary"],
    )
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(max_output_tokens=max_tokens),
        )
        text = response.text.strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            log(f"[AVISO] Resposta sem JSON para: {item['title'][:60]}")
            return None
        return json.loads(match.group())
    except Exception as e:
        log(f"[ERRO] Análise Gemini para '{item['title'][:60]}': {e}")
        return None


def build_vault_context(max_chars: int = 3000) -> str:
    if not REFERENCES_FILE.exists():
        return "(vault vazio)"
    text = REFERENCES_FILE.read_text(encoding="utf-8")
    return text[:max_chars]


# ---------------------------------------------------------------------------
# Escrita no inbox
# ---------------------------------------------------------------------------


def write_inbox(item: dict, analysis: dict, dry_run: bool) -> Path | None:
    today = date.today().isoformat()
    slug = slugify(item["title"])[:60]
    filename = f"{today}-{slug}.md"
    dest = INBOX_DIR / filename

    tags_str = ", ".join(analysis.get("tags", []))
    key_insights = "\n".join(f"- {i}" for i in analysis.get("key_insights", []))
    vault_entry = analysis.get("suggested_vault_entry") or "_Nenhuma sugestão gerada._"
    location = analysis.get("suggested_vault_location") or "06-resources/references.md"

    content = textwrap.dedent(f"""\
        ---
        source: {item['source']}
        source_type: {item['source_type']}
        url: {item['url']}
        date_captured: {today}
        relevance_score: {analysis['relevance_score']}
        relevance_reason: {analysis['relevance_reason']}
        suggested_vault_location: {location}
        status: pending
        tags: [{tags_str}]
        ---

        # {item['title']}

        ## Resumo
        {analysis.get('summary', '_Sem resumo._')}

        ## Insights principais
        {key_insights}

        ## Sugestão de entrada vault
        {vault_entry}
    """)

    if dry_run:
        log(f"[DRY-RUN] Escreveria: {dest.name} (score={analysis['relevance_score']})")
        return None

    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    log(f"[OK] {dest.name} (score={analysis['relevance_score']})")
    return dest


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def log(msg: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{timestamp}] {msg}"
    print(line)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Curador de conteúdo para o vault da Gethub")
    parser.add_argument("--dry-run", action="store_true", help="Busca e analisa sem escrever arquivos")
    parser.add_argument("--once", action="store_true", help="Força execução mesmo sem itens novos")
    args = parser.parse_args()

    load_dotenv(BASE_DIR / ".env")

    cfg = yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8"))
    state = load_state()
    vault_context = build_vault_context()

    analysis_cfg = cfg.get("analysis", {})
    model = analysis_cfg.get("model", "claude-haiku-4-5-20251001")
    max_tokens = analysis_cfg.get("max_tokens", 1024)
    min_score = analysis_cfg.get("min_relevance_score", 6)

    gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    log("=" * 60)
    log(f"Iniciando curadoria {'[DRY-RUN]' if args.dry_run else ''}")

    # Coleta de itens
    all_items: list[dict] = []
    all_items += fetch_substacks(cfg.get("substacks", []), state)
    all_items += fetch_hacker_news(cfg.get("hacker_news", {}), state)
    all_items += fetch_arxiv(cfg.get("arxiv", {}), state)

    # Remove duplicatas por URL dentro da própria coleta
    seen_this_run: set[str] = set()
    unique_items = []
    for item in all_items:
        if item["url"] not in seen_this_run:
            seen_this_run.add(item["url"])
            unique_items.append(item)

    log(f"Itens novos encontrados: {len(unique_items)}")

    if not unique_items:
        log("Nenhum item novo. Encerrando.")
        return

    saved = 0
    for item in unique_items:
        log(f"Analisando: {item['title'][:70]}")
        analysis = analyze_item(item, gemini_client, model, max_tokens, vault_context)

        mark_seen(item["url"], state)  # marca como visto independente do score

        if not analysis:
            continue

        score = analysis.get("relevance_score", 0)
        if score < min_score:
            log(f"[SKIP] Score {score} < {min_score}: {item['title'][:60]}")
            continue

        result = write_inbox(item, analysis, dry_run=args.dry_run)
        if result:
            saved += 1

    if not args.dry_run:
        save_state(state)
        log(f"State salvo. URLs acumuladas: {len(state['seen_urls'])}")

    log(f"Curadoria concluída. Itens salvos no inbox: {saved}")


if __name__ == "__main__":
    main()

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any

import yaml

from app.config import EMBEDDING_CACHE, KB_PATH

log = logging.getLogger(__name__)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _article_text(article: dict[str, Any]) -> str:
    return yaml.safe_dump(article, allow_unicode=True, sort_keys=False).strip()


@dataclass
class KnowledgeBase:
    raw: dict[str, Any]
    rules_yaml: str
    languages: list[dict[str, str]]
    articles: list[dict[str, Any]]
    article_texts: dict[str, str] = field(default_factory=dict)
    catalog: str = ""
    embeddings: dict[str, list[float]] = field(default_factory=dict)

    def article(self, article_id: str) -> str | None:
        return self.article_texts.get(str(article_id or "").strip())

    def shortlist(self, query_vector: list[float], limit: int = 4) -> list[str]:
        if not query_vector or not self.embeddings:
            return []
        ranked = sorted(
            self.embeddings.items(),
            key=lambda item: _cosine(query_vector, item[1]),
            reverse=True,
        )
        chosen: list[str] = []
        for article_id, _vector in ranked[:limit]:
            text = self.article_texts.get(article_id)
            if text:
                chosen.append(text)
        return chosen


def _validated_policies(policies: dict[str, Any]) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    for key, value in (policies or {}).items():
        if isinstance(value, dict) and str(value.get("status") or "") == "validated":
            kept[key] = value
    return kept


def _language_entries(meta: dict[str, Any]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for item in meta.get("reply_languages") or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        name = str(item.get("name") or "").strip()
        if code:
            entries.append({"code": code, "name": name or code})
    return entries


def load_kb(path=KB_PATH) -> KnowledgeBase:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    meta = dict(raw.get("meta") or {})
    languages = _language_entries(meta)
    meta_for_model = {
        "version": meta.get("version"),
        "source_language": meta.get("source_language"),
        "default_reply_language": meta.get("default_reply_language"),
        "reply_languages": languages,
        "slots": meta.get("slots") or {},
        "trust_tiers": meta.get("trust_tiers") or {},
    }
    facts = raw.get("product_facts") or {}
    rules = {
        "meta": meta_for_model,
        "policies": _validated_policies(raw.get("policies") or {}),
        "product_facts": {
            "validated": facts.get("validated") or {},
            "gaps": facts.get("gaps") or [],
        },
        "global_rules": raw.get("global_rules") or {},
    }
    articles = [
        article
        for article in (raw.get("articles") or [])
        if isinstance(article, dict) and str(article.get("status") or "") == "validated"
    ]
    texts = {
        str(article.get("id")): _article_text(article)
        for article in articles
        if article.get("id")
    }
    catalog_lines = [
        f"{article.get('id')}: {article.get('intent')}"
        for article in articles
        if article.get("id")
    ]
    return KnowledgeBase(
        raw=raw,
        rules_yaml=yaml.safe_dump(rules, allow_unicode=True, sort_keys=False).strip(),
        languages=languages,
        articles=articles,
        article_texts=texts,
        catalog="\n".join(catalog_lines),
    )


def content_hash(kb: KnowledgeBase) -> str:
    blob = "\n".join(f"{key}\n{value}" for key, value in sorted(kb.article_texts.items()))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def attach_embeddings(kb: KnowledgeBase, vectors: dict[str, list[float]]) -> None:
    kb.embeddings = {
        key: value for key, value in vectors.items() if key in kb.article_texts and value
    }


def load_cached_embeddings(digest: str) -> dict[str, list[float]] | None:
    if not EMBEDDING_CACHE.is_file():
        return None
    try:
        payload = json.loads(EMBEDDING_CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("digest") != digest:
        return None
    vectors = payload.get("vectors")
    if not isinstance(vectors, dict):
        return None
    return {str(key): value for key, value in vectors.items() if isinstance(value, list)}


def save_cached_embeddings(digest: str, vectors: dict[str, list[float]]) -> None:
    EMBEDDING_CACHE.parent.mkdir(parents=True, exist_ok=True)
    EMBEDDING_CACHE.write_text(
        json.dumps({"digest": digest, "vectors": vectors}),
        encoding="utf-8",
    )
    log.info("Cached embeddings for %s articles", len(vectors))

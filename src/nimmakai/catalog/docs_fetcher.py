"""Parse NVIDIA build.nvidia.com models.md documentation."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

ENTRY_RE = re.compile(
    r"^- \[([^\]]+)\]\(([^)]+)\)\s*—\s*(.+)$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class DocModel:
    slug: str
    path: str
    description: str
    publisher: str | None = None

    @property
    def api_id_guess(self) -> str | None:
        """Best-effort org/model from publisher + slug, or None."""
        if self.publisher:
            return f"{self.publisher}/{self.slug}"
        return None


async def fetch_models_md(
    base_url: str = "https://build.nvidia.com/models.md",
    *,
    max_pages: int = 3,
    timeout: float = 10.0,
) -> list[DocModel]:
    """Fetch paginated models.md and parse entries."""
    out: list[DocModel] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for page in range(1, max_pages + 1):
            url = base_url if page == 1 else f"{base_url}?page={page}"
            try:
                resp = await client.get(url)
                if resp.status_code >= 400:
                    logger.warning("docs fetch %s → HTTP %s", url, resp.status_code)
                    break
                text = resp.text
            except Exception:
                logger.exception("docs fetch failed for %s", url)
                break

            page_hits = 0
            for m in ENTRY_RE.finditer(text):
                slug, path, desc = m.group(1), m.group(2), m.group(3).strip()
                key = slug.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(DocModel(slug=slug, path=path, description=desc))
                page_hits += 1
            if page_hits == 0:
                break
    logger.info("docs catalog: %s unique model entries", len(out))
    return out


async def enrich_publishers(
    docs: list[DocModel],
    *,
    site: str = "https://build.nvidia.com",
    limit: int = 40,
    timeout: float = 20.0,
) -> list[DocModel]:
    """
    Fetch a limited set of detail pages for publisher frontmatter.
    Budget-limited to avoid hammering NVIDIA docs.
    """
    # Prefer candidates that look like chat/coding families
    priority_kw = (
        "nemotron",
        "qwen",
        "glm",
        "step-",
        "minimax",
        "coding",
        "agent",
        "instruct",
    )
    ranked = sorted(
        docs,
        key=lambda d: (
            0
            if any(k in d.slug.lower() or k in d.description.lower() for k in priority_kw)
            else 1,
            d.slug,
        ),
    )
    enriched: list[DocModel] = []
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for i, doc in enumerate(ranked):
            if i >= limit:
                enriched.extend(ranked[i:])
                break
            path = doc.path if doc.path.startswith("/") else f"/{doc.path}"
            url = f"{site}{path}"
            publisher = None
            try:
                resp = await client.get(url)
                if resp.status_code < 400:
                    pub_m = re.search(
                        r'^publisher:\s*"([^"]+)"', resp.text, re.MULTILINE
                    )
                    if pub_m:
                        publisher = pub_m.group(1)
            except Exception:
                logger.debug("detail fetch failed for %s", url, exc_info=True)
            enriched.append(
                DocModel(
                    slug=doc.slug,
                    path=doc.path,
                    description=doc.description,
                    publisher=publisher,
                )
            )
            await asyncio.sleep(0.05)
    return enriched

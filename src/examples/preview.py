"""The earnings preview: the ex ante information set attached to an event.

From 2026Q3 onward, an ``EARNINGS_RELEASE`` event's ``disclosure.items[]`` may carry a
second item next to the ten earnings-call facts: ``id="earnings-preview"``
(``kind="text"``, ``source="claude_code_web_research"``, ``media_type="text/markdown"``).
Its ``content`` is ONE markdown string — an agent-written research note assembled from
public sources *before* the release, which is why it stands in for the market's ex
ante expectation about the announcement. It is optional: events from before it was
disseminated, and events for which no preview was produced, legitimately have none.

How it is built, at a high level: the competition runs Anthropic's open
``earnings-reviewer`` plugin — specifically its ``earnings-preview`` skill — through
Claude Code, pinned to a fixed commit of :data:`PREVIEW_PLUGIN_REPO`. The agent gets a
one-line request naming the company, ticker, fiscal period and scheduled event time,
and only web search / fetch as tools. No transcript, no consensus feed and no
competition data are injected; whatever the note knows, it found and cited itself.
The skill prescribes the note's shape (consensus estimates, key metrics to watch,
bull/base/bear scenarios, a catalyst checklist and the trading setup), and the ``##``
headings you see in :func:`preview_sections` are that skeleton with per-report
variation.

Timing: a preview disseminated live is assembled before the event's CAR1 measurement
window opens — that is, before its ``knowledge_cutoff``. The 2026Q3 archive is the
exception: those previews were produced ahead of each release and attached to the
archive afterwards, but not always before the knowledge cutoff, so treat them as
illustrative rather than strictly cutoff-clean. No timestamps, model or job
identifiers are disseminated with the item.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

import pandas as pd

from examples.summary import facts_from_disclosure

EARNINGS_PREVIEW_ID = "earnings-preview"
EARNINGS_PREVIEW_SOURCE = "claude_code_web_research"
TEXT_KIND = "text"

# Provenance of the note, for display. The plugin is public; the pipeline pins one
# commit of it so every preview in a period comes from the same skill text.
PREVIEW_PLUGIN = "earnings-reviewer"
PREVIEW_SKILL = "earnings-preview"
PREVIEW_PLUGIN_REPO = "https://github.com/anthropics/financial-services"
PREVIEW_SKILL_PATH = "plugins/agent-plugins/earnings-reviewer/skills/earnings-preview/SKILL.md"

_H1 = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_H2 = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_LEADING_NUMBER = re.compile(r"^\d+[.)]?\s*")

# Columns, in reading order, for the coverage table.
_PREVIEW_COLUMNS = [
    "event_datetime",
    "knowledge_cutoff",
    "quarter",
    "tickers",
    "event_id",
    "n_facts",
    "has_preview",
    "preview_chars",
    "n_sections",
]


def earnings_preview_from_disclosure(record: dict) -> str | None:
    """Pull the agent-written earnings preview out of an event or archive record.

    Looks for the ``disclosure.items[]`` entry with ``id="earnings-preview"`` and
    ``kind="text"`` and returns its markdown ``content``. Returns ``None`` when the
    record carries no such item — the preview is optional, and events from before
    it was disseminated legitimately have none.

    ``record`` is a raw archive line (as yielded by
    :func:`examples.archive.read_jsonl_gz`), an equivalent event payload, or a row
    of :func:`examples.archive.load_archive` turned back into a dict — where a
    missing ``disclosure`` shows up as ``NaN`` rather than ``None``, hence the type
    checks rather than ``or {}``.
    """
    disclosure = record.get("disclosure")
    items = disclosure.get("items") if isinstance(disclosure, dict) else None
    for item in items if isinstance(items, list) else []:
        if item.get("id") == EARNINGS_PREVIEW_ID and item.get("kind") == TEXT_KIND:
            content = item.get("content")
            return content if isinstance(content, str) and content else None
    return None


def preview_title(preview_md: str) -> str | None:
    """The note's ``#`` heading, e.g. ``"NVIDIA (NVDA) — Q2 FY2027 Earnings Preview"``."""
    match = _H1.search(preview_md)
    return match.group(1) if match else None


def preview_sections(preview_md: str) -> list[str]:
    """The note's ``##`` headings in order, with any ``1.`` / ``1)`` numbering stripped.

    Useful for seeing the skill's prescribed skeleton — and how much each report
    departs from it — without reading every note in full.
    """
    return [_LEADING_NUMBER.sub("", h) for h in _H2.findall(preview_md)]


def preview_display_markdown(preview_md: str) -> str:
    """Escape ``$`` so a notebook renders dollar amounts as text, not as math.

    Previews are full of prices and estimates; JupyterLab's markdown renderer would
    otherwise read ``$1.20 vs $1.25`` as an inline MathJax span. Use this only for
    display — the disseminated content is the unescaped string.
    """
    return preview_md.replace("$", r"\$")


def previews_frame(records: Iterable[dict]) -> pd.DataFrame:
    """One row per record: does it carry a preview, and how big is it?

    Accepts raw archive lines or ``load_archive`` rows (``to_dict("records")``).
    ``quarter`` is taken from the record when present (``load_archive`` adds it from
    the filename) so coverage can be grouped by quarter; ``tickers`` is the
    comma-joined focal assets, as in :func:`examples.frames.events_frame`.
    """
    rows = []
    for record in records:
        preview = earnings_preview_from_disclosure(record)
        focal = record.get("focal_assets")
        rows.append(
            {
                "event_datetime": record.get("event_datetime"),
                "knowledge_cutoff": record.get("knowledge_cutoff"),
                "quarter": record.get("quarter"),
                "tickers": ", ".join(
                    a["identifier_value"] for a in (focal if isinstance(focal, list) else [])
                ),
                "event_id": record.get("event_id"),
                "n_facts": len(facts_from_disclosure(record)),
                "has_preview": preview is not None,
                "preview_chars": len(preview) if preview else 0,
                "n_sections": len(preview_sections(preview)) if preview else 0,
            }
        )
    df = pd.DataFrame(rows, columns=_PREVIEW_COLUMNS)
    if not df.empty:
        df["event_datetime"] = pd.to_datetime(df["event_datetime"], utc=True, format="ISO8601")
        df["knowledge_cutoff"] = pd.to_datetime(df["knowledge_cutoff"], utc=True, format="ISO8601")
        df = df.sort_values("event_datetime").reset_index(drop=True)
    return df

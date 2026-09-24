"""The earnings-preview disclosure item and its helpers."""

from __future__ import annotations

from examples.archive import load_archive, read_jsonl_gz
from examples.preview import (
    EARNINGS_PREVIEW_ID,
    EARNINGS_PREVIEW_SOURCE,
    earnings_preview_from_disclosure,
    preview_display_markdown,
    preview_sections,
    preview_title,
    previews_frame,
)
from examples.schemas import Disclosure
from examples.summary import facts_from_disclosure
from tests.conftest import SAMPLE_ARCHIVE_2026Q3

SAMPLE_TICKERS = ["NVDA", "WMT", "HD", "CRM", "AVGO", "ORCL"]

FACTS_ITEM = {
    "id": "earnings-call-facts",
    "kind": "facts",
    "source": "earnings_call",
    "content": ["f"],
}
PREVIEW_ITEM = {
    "id": EARNINGS_PREVIEW_ID,
    "kind": "text",
    "source": EARNINGS_PREVIEW_SOURCE,
    "media_type": "text/markdown",
    "content": "# Preview\n\nConsensus vs. guidance...",
}
PREVIEW_MD = """# Acme (ACME) — Q2 FY2026 Earnings Preview

**Event:** after close.

## 1. Consensus Estimates
| EPS | $1.20 |

## 2) Key Metrics to Watch — Ranked
text

## Scenario Analysis
text
"""


def test_earnings_preview_from_disclosure_reads_text_item_or_none() -> None:
    record = {"disclosure": {"items": [FACTS_ITEM, PREVIEW_ITEM]}}
    preview = earnings_preview_from_disclosure(record)
    assert preview is not None and preview.startswith("# Preview")
    assert facts_from_disclosure(record) == ["f"]

    assert earnings_preview_from_disclosure({"disclosure": {"items": [FACTS_ITEM]}}) is None
    assert earnings_preview_from_disclosure({}) is None
    # Empty content is treated as absent.
    empty = {"disclosure": {"items": [{**PREVIEW_ITEM, "content": ""}]}}
    assert earnings_preview_from_disclosure(empty) is None
    # A load_archive row over mixed files carries NaN, not None, for a missing field.
    assert earnings_preview_from_disclosure({"disclosure": float("nan")}) is None


def test_disclosure_schema_accepts_string_content_items() -> None:
    parsed = Disclosure.model_validate(
        {
            "schema_version": "1.0",
            "items": [{**FACTS_ITEM, "content": ["f1", "f2"]}, PREVIEW_ITEM],
        }
    )
    assert parsed.items[0].content == ["f1", "f2"]
    assert parsed.items[1].content == PREVIEW_ITEM["content"]


def test_preview_title_and_sections() -> None:
    assert preview_title(PREVIEW_MD) == "Acme (ACME) — Q2 FY2026 Earnings Preview"
    assert preview_sections(PREVIEW_MD) == [
        "Consensus Estimates",
        "Key Metrics to Watch — Ranked",
        "Scenario Analysis",
    ]
    assert preview_title("no headings here") is None
    assert preview_sections("no headings here") == []


def test_preview_display_markdown_escapes_dollars() -> None:
    assert preview_display_markdown("EPS $1.20 vs $1.25") == r"EPS \$1.20 vs \$1.25"
    assert preview_display_markdown("no money") == "no money"


def test_previews_frame_marks_presence_and_tolerates_nan() -> None:
    with_preview = {
        "event_id": "e1",
        "event_datetime": "2026-08-26T21:00:00+00:00",
        "knowledge_cutoff": "2026-08-26T20:00:00+00:00",
        "quarter": "2026Q3",
        "focal_assets": [{"identifier_type": "TICKER", "identifier_value": "NVDA"}],
        "disclosure": {"items": [FACTS_ITEM, {**PREVIEW_ITEM, "content": PREVIEW_MD}]},
    }
    without = {
        "event_id": "e0",
        "event_datetime": "2026-07-01T12:00:00+00:00",
        "knowledge_cutoff": float("nan"),
        "quarter": "2026Q3",
        "focal_assets": [{"identifier_type": "TICKER", "identifier_value": "STZ"}],
        "disclosure": float("nan"),
    }
    df = previews_frame([with_preview, without])

    assert list(df.columns) == [
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
    # Sorted by event time, so the July event comes first.
    assert list(df["tickers"]) == ["STZ", "NVDA"]
    assert list(df["has_preview"]) == [False, True]
    assert list(df["preview_chars"]) == [0, len(PREVIEW_MD)]
    assert list(df["n_sections"]) == [0, 3]
    assert list(df["n_facts"]) == [0, 1]
    assert df["event_datetime"].is_monotonic_increasing
    assert df["knowledge_cutoff"].isna().iloc[0]


def test_previews_frame_empty() -> None:
    df = previews_frame([])
    assert df.empty
    assert list(df.columns)[:2] == ["event_datetime", "knowledge_cutoff"]


def test_bundled_2026q3_sample_carries_a_preview_per_event() -> None:
    records = list(read_jsonl_gz(SAMPLE_ARCHIVE_2026Q3))
    assert [r["focal_assets"][0]["identifier_value"] for r in records] == SAMPLE_TICKERS
    for record in records:
        preview = earnings_preview_from_disclosure(record)
        assert preview is not None and preview_title(preview) is not None
        assert len(facts_from_disclosure(record)) == 10
        # Real archive lines: scored, with the realized reaction and the surprise.
        assert record["status"] == "scored"
        assert "car1" in record["event_returns"][record["focal_assets"][0]["identifier_value"]]

    df = load_archive(SAMPLE_ARCHIVE_2026Q3)
    assert set(df["quarter"]) == {"2026Q3"}
    frame = previews_frame(df.to_dict("records"))
    assert frame["has_preview"].all()
    assert (frame["n_sections"] >= 4).all()

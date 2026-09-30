"""The option-implied-stats disclosure item and its helpers."""

from __future__ import annotations

import math

import pytest

from examples.option_stats import (
    OPTION_STATS_ID,
    OPTION_STATS_SOURCE,
    option_implied_stats_from_disclosure,
    option_stats_frame,
    statistic_status,
    statistic_value,
)
from examples.preview import earnings_preview_from_disclosure
from examples.schemas import Disclosure
from examples.summary import facts_from_disclosure
from tests.test_preview import FACTS_ITEM, PREVIEW_ITEM

STATS = {
    "as_of": "2026-10-05T19:36:12Z",
    "methodology": "v1",
    "implied_earnings_volatility": {"value": 0.0847, "status": "ok"},
    "implied_absolute_earnings_move": {"value": 0.0676, "status": "ok"},
    "skew_25_delta": {"value": None, "status": "illiquid_wings"},
}
STATS_ITEM = {
    "id": OPTION_STATS_ID,
    "kind": "stats",
    "source": OPTION_STATS_SOURCE,
    "media_type": "application/json",
    "content": STATS,
}


def record(*items, **fields) -> dict:
    return {"disclosure": {"schema_version": "1.0", "items": list(items)}, **fields}


def test_disclosure_schema_accepts_an_object_content_item() -> None:
    """The regression this module exists for: ``content`` used to be typed
    ``list[str] | str``, so one ``stats`` item failed validation of the whole
    bundle, facts and preview included."""
    parsed = Disclosure.model_validate(
        {"schema_version": "1.0", "items": [FACTS_ITEM, PREVIEW_ITEM, STATS_ITEM]}
    )
    assert [i.id for i in parsed.items] == [
        "earnings-call-facts",
        "earnings-preview",
        OPTION_STATS_ID,
    ]
    assert parsed.items[0].content == ["f"]
    assert parsed.items[1].content == PREVIEW_ITEM["content"]
    assert parsed.items[2].content == STATS and parsed.items[2].kind == "stats"


@pytest.mark.parametrize(
    "content", [{"nested": {"deep": [1, 2]}}, 7, 1.5, True, [1, {"a": 2}], None]
)
def test_disclosure_schema_never_rejects_a_bundle_over_an_unknown_content_type(content) -> None:
    parsed = Disclosure.model_validate(
        {"items": [FACTS_ITEM, {"id": "future", "kind": "something-new", "content": content}]}
    )
    assert parsed.items[0].content == ["f"] and parsed.items[1].content == content


def test_stats_are_read_by_id_and_kind_wherever_they_sit() -> None:
    assert (
        option_implied_stats_from_disclosure(record(FACTS_ITEM, PREVIEW_ITEM, STATS_ITEM)) == STATS
    )
    assert option_implied_stats_from_disclosure(record(STATS_ITEM, FACTS_ITEM)) == STATS
    # The other helpers are untouched by the new item.
    full = record(FACTS_ITEM, PREVIEW_ITEM, STATS_ITEM)
    assert facts_from_disclosure(full) == ["f"]
    assert earnings_preview_from_disclosure(full) == PREVIEW_ITEM["content"]


def test_stats_are_none_when_absent_or_unusable() -> None:
    assert option_implied_stats_from_disclosure(record(FACTS_ITEM, PREVIEW_ITEM)) is None
    assert option_implied_stats_from_disclosure({}) is None
    assert option_implied_stats_from_disclosure({"disclosure": float("nan")}) is None
    assert option_implied_stats_from_disclosure(record({**STATS_ITEM, "content": {}})) is None
    assert option_implied_stats_from_disclosure(record({**STATS_ITEM, "content": "8.2%"})) is None
    assert option_implied_stats_from_disclosure(record({**STATS_ITEM, "kind": "text"})) is None


def test_a_value_counts_only_when_its_status_is_ok() -> None:
    assert statistic_value(STATS, "implied_earnings_volatility") == 0.0847
    assert statistic_value(STATS, "skew_25_delta") is None
    assert statistic_status(STATS, "skew_25_delta") == "illiquid_wings"
    # A status this code has never heard of is unavailable, not an error.
    assert statistic_value({"x": {"value": 0.3, "status": "provisional"}}, "x") is None
    # Defensive against a malformed block.
    for block in (
        {"value": "0.08", "status": "ok"},
        {"value": True, "status": "ok"},
        {"status": "ok"},
        "ok",
        None,
    ):
        assert statistic_value({"x": block}, "x") is None
    assert statistic_value(None, "x") is None and statistic_status(None, "x") is None


def test_the_move_is_reproducible_from_the_volatility() -> None:
    j = statistic_value(STATS, "implied_earnings_volatility")
    phi = 0.5 * (1 + math.erf((j / 2) / math.sqrt(2)))
    assert statistic_value(STATS, "implied_absolute_earnings_move") == pytest.approx(
        4 * phi - 2, abs=1e-4
    )


def test_option_stats_frame() -> None:
    focal = [{"identifier_type": "TICKER", "identifier_value": "NKE"}]
    frame = option_stats_frame(
        [
            record(
                FACTS_ITEM,
                event_id="ea_B",
                event_datetime="2026-10-06T20:05:00+00:00",
                focal_assets=focal,
            ),
            record(
                FACTS_ITEM,
                STATS_ITEM,
                event_id="ea_A",
                event_datetime="2026-10-05T20:05:00+00:00",
                knowledge_cutoff="2026-10-05T20:00:00+00:00",
                focal_assets=focal,
            ),
        ]
    )
    assert list(frame["event_id"]) == ["ea_A", "ea_B"]  # sorted by event time
    assert list(frame["has_option_stats"]) == [True, False]
    with_stats, without = frame.iloc[0], frame.iloc[1]
    assert with_stats["implied_earnings_volatility"] == 0.0847
    assert math.isnan(with_stats["skew_25_delta"]) and with_stats["skew_status"] == "illiquid_wings"
    assert with_stats["as_of"] < with_stats["knowledge_cutoff"]
    assert math.isnan(without["implied_earnings_volatility"]) and without["tickers"] == "NKE"
    assert option_stats_frame([]).empty

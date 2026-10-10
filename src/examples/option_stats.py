"""The option-implied statistics: what the options market expected of a release.

From 2026Q3 onward, an ``EARNINGS_RELEASE`` event's ``disclosure.items[]`` may
carry a third item: ``id="option-implied-stats"`` (``kind="stats"``,
``source="option_market"``, ``media_type="application/json"``). Unlike the facts (a
list of strings) and the preview (one markdown string), its ``content`` is an OBJECT::

    {
      "as_of": "2026-10-05T19:36:12Z",
      "methodology": "v1",
      "implied_earnings_volatility":    {"value": 0.0847, "status": "ok"},
      "implied_absolute_earnings_move": {"value": 0.0676, "status": "ok"},
      "skew_25_delta":                  {"value": null,   "status": "illiquid_wings"}
    }

Every statistic is a ``{value, status}`` pair. ``value`` is a decimal (``0.0847`` is
8.47%) and is ``None`` unless ``status`` is ``"ok"``. Treat any status you do not
recognise as unavailable.

What the three numbers are:

``implied_earnings_volatility``
    The risk-neutral standard deviation of the stock's jump on the release. Option
    prices before a release embed two things, ordinary volatility and the jump, and
    the jump weighs more on a short-dated option than a long-dated one. Comparing
    at-the-money implied volatility across the first two expirations after the
    release separates them (the term-structure estimator of Dubinsky, Johannes,
    Kaeck and Seeger, *Review of Financial Studies*, 2019).

``implied_absolute_earnings_move``
    The risk-neutral expected absolute return from that jump: ``4 * Phi(J / 2) - 2``
    for volatility ``J``, which is about ``0.8 * J``. It is a magnitude. It says
    nothing about direction, and being risk-neutral it is not a forecast of the
    realised move.

``skew_25_delta``
    The 25-delta call implied volatility minus the 25-delta put implied volatility,
    on the nearest expiration after the release, in volatility points as a decimal.
    Negative means downside puts are richer than upside calls. It is an asymmetry in
    option prices, not a predicted return or a probability.

Timing: ``as_of`` is when the market was observed, and it is always strictly before
the event's ``knowledge_cutoff``. ``methodology`` changes whenever the calculation
does.

Coverage: the item is absent when a stock has no listed options or they trade too
thinly to measure, which is most smaller companies. Fewer than half of 2026Q3 events
carry it. The skew can be unavailable on its own when only the out-of-the-money
options are thin. Events before 2026Q3 never carry the item.

Using the move: the competition's score is invariant to any uniform affine remap of
a submission's predictions (see :mod:`examples.scoring`), so a number that is the
same for every event cannot change it. The implied move differs across events, and
:func:`scale_by_implied_move` uses it to stretch or shrink each prediction's
distance from the submission's own centre. Notebook 04 shows the effect.
"""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

OPTION_STATS_ID = "option-implied-stats"
OPTION_STATS_SOURCE = "option_market"
STATS_KIND = "stats"
OK = "ok"

STATISTICS = (
    "implied_earnings_volatility",
    "implied_absolute_earnings_move",
    "skew_25_delta",
)

_COLUMNS = [
    "event_datetime",
    "knowledge_cutoff",
    "quarter",
    "tickers",
    "event_id",
    "has_option_stats",
    "as_of",
    "methodology",
    *STATISTICS,
    "skew_status",
]


def option_implied_stats_from_disclosure(record: dict) -> dict | None:
    """Pull the option-implied statistics item's content out of an event or archive record.

    Returns the item's ``content`` object as-is, or ``None`` when the record carries no
    such item. ``record`` may be a raw archive line, an event payload, or a
    ``load_archive`` row turned back into a dict (where a missing ``disclosure`` is
    ``NaN``, hence the type checks).
    """
    disclosure = record.get("disclosure")
    items = disclosure.get("items") if isinstance(disclosure, dict) else None
    for item in items if isinstance(items, list) else []:
        if (
            isinstance(item, dict)
            and item.get("id") == OPTION_STATS_ID
            and item.get("kind") == STATS_KIND
        ):
            content = item.get("content")
            return content if isinstance(content, dict) and content else None
    return None


def statistic_value(stats: dict | None, name: str) -> float | None:
    """One statistic's value, or ``None`` unless its status is ``"ok"``.

    Reads defensively: a missing block, an unknown status, or a non-numeric value all
    come back as ``None`` rather than raising.
    """
    block = (stats or {}).get(name)
    if not isinstance(block, dict) or block.get("status") != OK:
        return None
    value = block.get("value")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def statistic_status(stats: dict | None, name: str) -> str | None:
    block = (stats or {}).get(name)
    status = block.get("status") if isinstance(block, dict) else None
    return status if isinstance(status, str) else None


def option_stats_frame(records: Iterable[dict]) -> pd.DataFrame:
    """One row per record with the three statistics as plain columns.

    Unavailable statistics are ``NaN``. ``skew_status`` says why the skew is missing
    when the volatility is present.
    """
    rows = []
    for record in records:
        stats = option_implied_stats_from_disclosure(record)
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
                "has_option_stats": stats is not None,
                "as_of": (stats or {}).get("as_of"),
                "methodology": (stats or {}).get("methodology"),
                **{name: statistic_value(stats, name) for name in STATISTICS},
                "skew_status": statistic_status(stats, "skew_25_delta"),
            }
        )
    df = pd.DataFrame(rows, columns=_COLUMNS)
    for name in STATISTICS:
        # None -> NaN, and a float column even when every value is missing.
        df[name] = pd.to_numeric(df[name], errors="coerce").astype("float64")
    if not df.empty:
        for column in ("event_datetime", "knowledge_cutoff", "as_of"):
            df[column] = pd.to_datetime(df[column], utc=True, format="ISO8601")
        df = df.sort_values("event_datetime").reset_index(drop=True)
    return df


def scale_by_implied_move(
    prediction: pd.Series,
    move: pd.Series,
    *,
    pivot: float | None = None,
    power: float = 0.5,
) -> pd.Series:
    """Rescale predicted percentiles event by event with the option-implied move.

    Returns ``pivot + (prediction - pivot) * (move / move.mean()) ** power``, clipped
    to ``[0, 1]`` and indexed like ``prediction``. ``move`` is aligned to
    ``prediction`` by index label first, so pass two columns of the same frame or
    two series sharing an index. An event with no move (``NaN``) keeps its
    prediction unchanged, and if no event has a usable move the input comes back
    as it is.

    Why this shape: the competition scores how well predictions explain the
    *percentile rank* of the one-day abnormal return. A rank cannot grow without
    bound, so the effect of a larger expected magnitude saturates, and a
    submission's own forecasts are rarely centred at 0.5. Hence the two defaults:

    ``pivot=None``
        Pivot at the submission's own mean prediction. Stretching away from 0.5
        instead pushes every low-move event down and every high-move event up
        whatever the model thought, which adds the move as a signed regressor the
        data do not support.
    ``power=0.5``
        A square-root factor, so a move four times the average doubles the
        deviation rather than quadrupling it. On 2026Q3 this parameter-free rule
        captures essentially all of the gain a freely fitted interaction would.

    ``pivot=0.5, power=1.0`` gives the "naive" rescale, ``0.5 + (p - 0.5) * m / mean(m)``,
    which lowers the score for the reference baselines. The reference scale
    ``move.mean()`` matters only through the clip: dividing by any other positive
    constant is an affine remap, which the score ignores.
    """
    move = pd.to_numeric(move.reindex(prediction.index), errors="coerce").astype("float64")
    reference = float(move.mean())
    if not reference > 0:
        return prediction.copy()
    centre = float(prediction.mean()) if pivot is None else pivot
    factor = (move / reference).pow(power).where(move.notna(), 1.0)
    return (centre + (prediction - centre) * factor).clip(0.0, 1.0)

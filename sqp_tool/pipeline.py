"""Convenience: files -> enriched frame + context."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .insights import enrich
from .metrics import Thresholds, compute_metrics
from .parse import load_sqp


def analyse(paths: list[str | Path], t: Thresholds = Thresholds(), own_brand: list[str] | None = None,
            competitors: list[str] | None = None) -> tuple[pd.DataFrame, dict]:
    raw = pd.concat([load_sqp(p) for p in paths], ignore_index=True)
    d = compute_metrics(raw, t)
    return enrich(d, t, own_brand, competitors)

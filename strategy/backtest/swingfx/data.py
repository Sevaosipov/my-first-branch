"""Loading daily OHLC data.

No price history ships with this repo. Point `--data` at a directory of CSVs
named after the pair (`EURUSD.csv`, `USDJPY.csv`, ...) with at least
Date/Open/High/Low/Close columns, in any capitalisation. See
`strategy/backtest/README.md` for one-liners that produce those files from
common free sources.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REQUIRED = ["open", "high", "low", "close"]


def load_csv(path: str | Path) -> pd.DataFrame:
    """Read one OHLC CSV into a date-indexed frame with lowercase columns."""
    df = pd.read_csv(path)
    df.columns = [str(c).strip().lower() for c in df.columns]

    date_col = next((c for c in ("date", "datetime", "time", "timestamp") if c in df), None)
    if date_col is None:
        raise ValueError(f"{path}: no date column found (looked for date/datetime/time/timestamp)")

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing column(s) {missing}; found {list(df.columns)}")

    df[date_col] = pd.to_datetime(df[date_col], utc=False, errors="coerce")
    df = df.dropna(subset=[date_col]).set_index(date_col).sort_index()
    df.index = df.index.normalize()
    df.index.name = "date"

    df = df[~df.index.duplicated(keep="last")]
    df = df[REQUIRED].astype(float)
    df = df.dropna()

    bad = df[(df["high"] < df["low"]) | (df["high"] < df["open"]) | (df["low"] > df["close"])]
    if len(bad):
        raise ValueError(f"{path}: {len(bad)} bars have inconsistent OHLC values")
    return df


def load_directory(directory: str | Path, symbols: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Load every `<SYMBOL>.csv` in `directory`, optionally filtered."""
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(f"data directory not found: {directory}")

    panels: dict[str, pd.DataFrame] = {}
    for path in sorted(directory.glob("*.csv")):
        symbol = path.stem.upper().replace("_", "").replace("-", "")
        if symbols and symbol not in symbols:
            continue
        panels[symbol] = load_csv(path)

    if not panels:
        raise FileNotFoundError(f"no CSV files matched in {directory}")
    return panels


def slice_dates(
    panels: dict[str, pd.DataFrame], start: str | None, end: str | None
) -> dict[str, pd.DataFrame]:
    out = {}
    for symbol, df in panels.items():
        sliced = df.loc[pd.Timestamp(start) if start else None : pd.Timestamp(end) if end else None]
        if not sliced.empty:
            out[symbol] = sliced
    return out

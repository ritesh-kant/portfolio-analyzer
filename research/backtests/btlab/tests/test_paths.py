"""Empty placeholder bar files must not make a year look available."""

import pandas as pd

from btlab import paths


def _write(d, name, rows):
    idx = pd.date_range("2022-01-03 09:15", periods=rows, freq="min", tz="Asia/Kolkata")
    pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx[:rows]).to_parquet(d / name)


def test_empty_years_are_skipped(tmp_path, monkeypatch):
    _write(tmp_path, "AAA_2021.parquet", 0)
    _write(tmp_path, "BBB_2021.parquet", 0)
    _write(tmp_path, "AAA_2022.parquet", 5)
    _write(tmp_path, "BBB_2022.parquet", 0)
    monkeypatch.setattr(paths, "upstox_1m_dir", lambda: tmp_path)
    assert paths.cached_years("NSE") == [2022]
    assert paths.cached_symbols(2021) == []
    assert paths.cached_symbols(2022) == ["AAA"]

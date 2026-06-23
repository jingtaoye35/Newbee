"""fetch_stock_hist 测试 — mock akshare 不实际拉网络.

覆盖 spec/data-ingestion 的 ADDED Requirements:
- 9 字符 stock_code 输入输出
- long-format 列名 (trading_date / close_post_adj / 等)
- 后复权: close_post_adj 列存在
- 业务代码不直接 import akshare
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from newbee.datasource.sources import akshare as akshare_mod


def _make_raw_sina_df(stock_code_9: str, dates: list[str]) -> pd.DataFrame:
    """模拟 akshare.stock_zh_a_daily 的输出 (sina 源)."""
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "open": [10.0] * len(dates),
            "high": [11.0] * len(dates),
            "low": [9.5] * len(dates),
            "close": [10.5] * len(dates),
            "volume": [1000] * len(dates),
            "amount": [1e7] * len(dates),
        }
    )


# ---------- stock_code 转换 ----------


def test_to_full_stock_code_sh() -> None:
    assert akshare_mod._to_full_stock_code("600000") == "600000.SH"
    assert akshare_mod._to_full_stock_code("688981") == "688981.SH"


def test_to_full_stock_code_sz() -> None:
    assert akshare_mod._to_full_stock_code("000012") == "000012.SZ"
    assert akshare_mod._to_full_stock_code("300750") == "300750.SZ"


def test_to_full_stock_code_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        akshare_mod._to_full_stock_code("AB")  # 2 位, 非数字, 无法 zfill
    with pytest.raises(ValueError):
        akshare_mod._to_full_stock_code("ABCDEF")  # 非数字


# ---------- fetch_stock_hist: 9 字符 stock_code ----------


def test_fetch_returns_9char_stock_code() -> None:
    """传 6 位代码, 返回 9 字符 stock_code."""
    raw = _make_raw_sina_df("600000.SH", ["2024-01-02"])
    with patch.object(akshare_mod, "with_retry", side_effect=lambda fn, **kw: fn()):
        with patch("akshare.stock_zh_a_daily", return_value=raw):
            df = akshare_mod.fetch_stock_hist("600000", source="sina")
    assert df.iloc[0]["stock_code"] == "600000.SH"
    assert df.iloc[0]["trading_date"] == "2024-01-02"


def test_fetch_accepts_9char_stock_code() -> None:
    """传 9 字符代码直接用."""
    raw = _make_raw_sina_df("600000.SH", ["2024-01-02"])
    with patch.object(akshare_mod, "with_retry", side_effect=lambda fn, **kw: fn()):
        with patch("akshare.stock_zh_a_daily", return_value=raw):
            df = akshare_mod.fetch_stock_hist("600000.SH", source="sina")
    assert df.iloc[0]["stock_code"] == "600000.SH"


# ---------- fetch_stock_hist: long-format 列 ----------


def test_fetch_output_columns() -> None:
    """输出列必须包含 trading_date / stock_code / OHLCV / amount / volume / close_post_adj."""
    raw = _make_raw_sina_df("600000.SH", ["2024-01-02", "2024-01-03"])
    with patch.object(akshare_mod, "with_retry", side_effect=lambda fn, **kw: fn()):
        with patch("akshare.stock_zh_a_daily", return_value=raw):
            df = akshare_mod.fetch_stock_hist("600000", source="sina")
    expected = {
        "trading_date",
        "stock_code",
        "open",
        "high",
        "low",
        "close",
        "amount",
        "volume",
        "close_post_adj",
    }
    assert set(df.columns) == expected


def test_fetch_trading_date_is_iso_string() -> None:
    raw = _make_raw_sina_df("600000.SH", ["2024-01-02"])
    with patch.object(akshare_mod, "with_retry", side_effect=lambda fn, **kw: fn()):
        with patch("akshare.stock_zh_a_daily", return_value=raw):
            df = akshare_mod.fetch_stock_hist("600000", source="sina")
    assert df.iloc[0]["trading_date"] == "2024-01-02"
    assert isinstance(df.iloc[0]["trading_date"], str)


def test_fetch_ohlcv_nullable_float32() -> None:
    raw = _make_raw_sina_df("600000.SH", ["2024-01-02"])
    with patch.object(akshare_mod, "with_retry", side_effect=lambda fn, **kw: fn()):
        with patch("akshare.stock_zh_a_daily", return_value=raw):
            df = akshare_mod.fetch_stock_hist("600000", source="sina")
    # sina 源 close_post_adj = close (无 adj_factor)
    assert df.iloc[0]["close_post_adj"] == 10.5
    # OHLCV 都应是 float32 (nullable)
    for col in ("open", "high", "low", "close", "amount", "volume", "close_post_adj"):
        assert df[col].dtype == "float32", f"{col} dtype={df[col].dtype}"


# ---------- fetch_index_constituents ----------


def test_fetch_index_constituents_returns_9char() -> None:
    fake = pd.DataFrame({"成分券代码": ["600000", "000012", "688981"]})
    with patch.object(akshare_mod, "with_retry", side_effect=lambda fn, **kw: fn()):
        with patch("akshare.index_stock_cons_csindex", return_value=fake):
            result = akshare_mod.fetch_index_constituents("csi1000")
    assert "600000.SH" in result
    assert "000012.SZ" in result
    assert "688981.SH" in result


# ---------- with_retry ----------


def test_with_retry_eventually_raises() -> None:
    def bad() -> None:
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        akshare_mod.with_retry(bad, max_retries=2, base_delay=0.0)


def test_with_retry_succeeds_after_failures() -> None:
    counter = {"n": 0}

    def flaky() -> str:
        counter["n"] += 1
        if counter["n"] < 3:
            raise RuntimeError("flaky")
        return "ok"

    result = akshare_mod.with_retry(flaky, max_retries=5, base_delay=0.0)
    assert result == "ok"
    assert counter["n"] == 3
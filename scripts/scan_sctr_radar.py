#!/usr/bin/env python3
"""Scan the SCTR leaders for recent bullish MACD histogram turns and volume strength."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf


OBSERVATION_DAYS = 60
SIGNAL_LOOKBACK_DAYS = 10
VOLUME_PERCENTILE = 0.80
DOWNLOAD_PERIOD = "1y"
CHUNK_SIZE = 50


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sctr-json", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def yahoo_symbol(symbol: str) -> str:
    return symbol.replace(".", "-").replace("/", "-")


def remove_incomplete_session(prices: pd.DataFrame) -> pd.DataFrame:
    """Ignore today's Yahoo daily bar until the regular US session is complete."""
    if prices.empty:
        return prices
    now_new_york = datetime.now(ZoneInfo("America/New_York"))
    latest_date = pd.Timestamp(prices.index[-1]).date()
    market_close_buffer = now_new_york.replace(hour=16, minute=15, second=0, microsecond=0)
    if latest_date == now_new_york.date() and now_new_york < market_close_buffer:
        return prices.iloc[:-1]
    return prices


def ticker_frame(download: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    if download.empty:
        return None
    if not isinstance(download.columns, pd.MultiIndex):
        return download.copy()
    if ticker in download.columns.get_level_values(0):
        return download[ticker].copy()
    if ticker in download.columns.get_level_values(1):
        return download.xs(ticker, axis=1, level=1).copy()
    return None


def download_chunk(tickers: list[str]) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(1, 3):
        try:
            return yf.download(
                tickers=tickers,
                period=DOWNLOAD_PERIOD,
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                actions=False,
                threads=True,
                progress=False,
                timeout=30,
            )
        except Exception as error:  # yfinance may raise a batch-level transport error
            last_error = error
            if attempt < 2:
                time.sleep(3)
    raise RuntimeError(f"yfinance batch failed: {last_error}")


def finite_or_none(value: float) -> float | None:
    return round(float(value), 6) if math.isfinite(float(value)) else None


def scan_stock(stock: dict, frame: pd.DataFrame) -> tuple[dict | None, dict]:
    required = {"Close", "Volume"}
    if not required.issubset(frame.columns):
        raise ValueError("missing Close or Volume column")

    prices = frame[["Close", "Volume"]].copy()
    prices["Close"] = pd.to_numeric(prices["Close"], errors="coerce")
    prices["Volume"] = pd.to_numeric(prices["Volume"], errors="coerce")
    prices = prices.dropna(subset=["Close", "Volume"])
    prices = prices[prices["Volume"] >= 0]
    prices = remove_incomplete_session(prices)
    if len(prices) < OBSERVATION_DAYS:
        raise ValueError(f"only {len(prices)} valid daily rows")

    close = prices["Close"]
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal = macd.ewm(span=9, adjust=False).mean()
    histogram = macd - signal

    observed = prices.tail(OBSERVATION_DAYS).copy()
    observed["MACD"] = macd.reindex(observed.index)
    observed["Signal"] = signal.reindex(observed.index)
    observed["Histogram"] = histogram.reindex(observed.index)
    observed["BullishCross"] = (histogram.shift(1) < 0) & (histogram >= 0)

    recent_crosses = observed.tail(SIGNAL_LOOKBACK_DAYS)
    recent_crosses = recent_crosses[recent_crosses["BullishCross"]]
    latest = observed.iloc[-1]
    latest_histogram = float(latest["Histogram"])
    histogram_match = latest_histogram > 0 and not recent_crosses.empty

    volume_mean = float(observed["Volume"].mean())
    volume_std = float(observed["Volume"].std(ddof=0))
    latest_volume = float(latest["Volume"])
    volume_threshold = float(observed["Volume"].quantile(VOLUME_PERCENTILE))
    volume_zscore = (latest_volume - volume_mean) / volume_std if volume_std > 0 else 0.0
    volume_percentile = float((observed["Volume"] <= latest_volume).mean() * 100)
    volume_match = latest_volume > volume_threshold

    cross_date = None
    days_since_cross = None
    if not recent_crosses.empty:
        cross_index = recent_crosses.index[-1]
        cross_date = pd.Timestamp(cross_index).date().isoformat()
        days_since_cross = len(observed) - 1 - observed.index.get_loc(cross_index)

    diagnostics = {
        "histogramMatch": histogram_match,
        "volumeMatch": volume_match,
        "marketDate": pd.Timestamp(observed.index[-1]).date().isoformat(),
    }

    if not (histogram_match and volume_match):
        return None, diagnostics

    result = {
        "sctrRank": int(stock["rank"]),
        "symbol": stock["symbol"],
        "name": stock.get("name", ""),
        "sector": stock.get("sector", ""),
        "industry": stock.get("industry", ""),
        "sctr": float(stock["sctr"]),
        "marketDate": diagnostics["marketDate"],
        "crossDate": cross_date,
        "daysSinceCross": days_since_cross,
        "close": finite_or_none(latest["Close"]),
        "macd": finite_or_none(latest["MACD"]),
        "signal": finite_or_none(latest["Signal"]),
        "histogram": finite_or_none(latest_histogram),
        "volume": int(latest_volume),
        "volumeMean60D": int(round(volume_mean)),
        "volumeStdDev60D": int(round(volume_std)),
        "volumeThreshold": int(round(volume_threshold)),
        "volumePercentile60D": round(volume_percentile, 1),
        "volumeZScore": round(volume_zscore, 3),
    }
    return result, diagnostics


def write_outputs(output_dir: Path, report: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "sctr-radar-scan.json"
    csv_path = output_dir / "sctr-radar-scan.csv"
    markdown_path = output_dir / "sctr-radar-scan.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    fields = [
        "sctrRank",
        "symbol",
        "name",
        "sector",
        "industry",
        "sctr",
        "marketDate",
        "crossDate",
        "daysSinceCross",
        "close",
        "macd",
        "signal",
        "histogram",
        "volume",
        "volumeMean60D",
        "volumeStdDev60D",
        "volumeThreshold",
        "volumePercentile60D",
        "volumeZScore",
    ]
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report["results"])

    lines = [
        "# SCTR Radar Scan",
        "",
        f"- SCTR source date: {report['sctrSourceTime']}",
        f"- Market date: {report['marketDate'] or 'n/a'}",
        f"- SCTR targets: {report['targetCount']}",
        f"- Valid yfinance targets: {report['validTargetCount']}",
        f"- Histogram matches: {report['histogramMatchCount']}",
        f"- Volume matches: {report['volumeMatchCount']}",
        f"- Final matches: {report['resultCount']}",
        "",
        "| SCTR rank | Symbol | SCTR | Cross date | Days | Volume percentile | Close |",
        "| ---: | :--- | ---: | :--- | ---: | ---: | ---: |",
    ]
    lines.extend(
        f"| {row['sctrRank']} | {row['symbol']} | {row['sctr']:.1f} | {row['crossDate']} | "
        f"{row['daysSinceCross']} | {row['volumePercentile60D']:.1f}% | {row['close']:.2f} |"
        for row in report["results"]
    )
    if not report["results"]:
        lines.append("| — | No stocks matched all conditions | — | — | — | — | — |")
    lines.append("")
    markdown = "\n".join(lines)
    markdown_path.write_text(markdown + "\n")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as handle:
            handle.write(markdown + "\n")


def main() -> None:
    args = parse_args()
    sctr = json.loads(args.sctr_json.read_text())
    stocks = sctr.get("stocks", [])
    target_count = int(sctr.get("count", 0))
    if target_count < 1 or len(stocks) != target_count:
        raise ValueError(f"expected {target_count} SCTR targets, received {len(stocks)}")

    frames: dict[str, pd.DataFrame] = {}
    download_errors: list[str] = []
    yahoo_to_stock = {yahoo_symbol(stock["symbol"]): stock for stock in stocks}
    tickers = list(yahoo_to_stock)

    for start in range(0, len(tickers), CHUNK_SIZE):
        chunk = tickers[start : start + CHUNK_SIZE]
        try:
            batch = download_chunk(chunk)
        except Exception as error:
            download_errors.append(f"batch {start // CHUNK_SIZE + 1}: {error}")
            continue
        for ticker in chunk:
            frame = ticker_frame(batch, ticker)
            if frame is not None and not frame.empty:
                frames[ticker] = frame

    results: list[dict] = []
    symbol_errors: list[str] = []
    histogram_match_count = 0
    volume_match_count = 0
    valid_target_count = 0
    market_dates: list[str] = []

    for yahoo, stock in yahoo_to_stock.items():
        frame = frames.get(yahoo)
        if frame is None:
            symbol_errors.append(f"{stock['symbol']}: no yfinance data")
            continue
        try:
            result, diagnostics = scan_stock(stock, frame)
        except Exception as error:
            symbol_errors.append(f"{stock['symbol']}: {error}")
            continue
        valid_target_count += 1
        market_dates.append(diagnostics["marketDate"])
        histogram_match_count += int(diagnostics["histogramMatch"])
        volume_match_count += int(diagnostics["volumeMatch"])
        if result:
            results.append(result)

    results.sort(key=lambda row: (row["sctrRank"], -row["volumeZScore"]))
    required_valid_count = math.ceil(target_count * 0.90)
    if valid_target_count < required_valid_count:
        raise RuntimeError(
            f"only {valid_target_count}/{target_count} symbols returned valid yfinance data; "
            f"required at least {required_valid_count}"
        )

    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sctrSourceTime": sctr.get("sourceTime", ""),
        "marketDate": max(market_dates) if market_dates else None,
        "definition": {
            "universe": f"StockCharts Large-Cap SCTR Top {target_count}",
            "observationTradingDays": OBSERVATION_DAYS,
            "histogramRule": "current MACD histogram > 0 and latest negative-to-positive cross occurred within 10 trading days",
            "volumeRule": "latest daily volume > 80th percentile of the latest 60 complete trading days",
        },
        "targetCount": len(stocks),
        "validTargetCount": valid_target_count,
        "histogramMatchCount": histogram_match_count,
        "volumeMatchCount": volume_match_count,
        "resultCount": len(results),
        "errors": download_errors + symbol_errors,
        "results": results,
    }
    write_outputs(args.output_dir, report)
    print(
        f"Scanned {valid_target_count}/{target_count} symbols; "
        f"histogram={histogram_match_count}, volume={volume_match_count}, final={len(results)}"
    )
    for row in results:
        print(
            f"{row['symbol']:6} rank={row['sctrRank']:3} sctr={row['sctr']:4.1f} "
            f"cross={row['crossDate']} volume_z={row['volumeZScore']:.3f}"
        )


if __name__ == "__main__":
    main()

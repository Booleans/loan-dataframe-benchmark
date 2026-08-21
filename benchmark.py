"""Benchmark Pandas and Polars execution modes on loan CSV files."""

from __future__ import annotations

import argparse
import gc
import math
import os
import platform
import statistics
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from time import perf_counter

import pandas as pd
import polars as pl
import pyarrow as pa


REQUIRED_COLUMNS = ("addr_state", "issue_d", "int_rate")


def find_csvs(data_dir: Path) -> list[Path]:
    paths = sorted(data_dir.glob("*.csv"))
    if not paths:
        raise FileNotFoundError(f"No CSV files found in {data_dir.resolve()}")
    return paths


def _pandas_aggregation(loans: pd.DataFrame) -> pd.DataFrame:
    loans["year"] = pd.to_datetime(
        loans["issue_d"], format="%b-%y", errors="coerce"
    ).dt.year
    loans["interest_rate"] = pd.to_numeric(
        loans["int_rate"].str.strip().str.removesuffix("%"), errors="coerce"
    )
    return (
        loans.dropna(subset=["addr_state", "year", "interest_rate"])
        .groupby(["addr_state", "year"], as_index=False, sort=False)["interest_rate"]
        .mean()
        .rename(columns={"interest_rate": "average_interest_rate"})
        .sort_values(["year", "addr_state"], ignore_index=True)
    )


def calculate_with_pandas(csv_paths: Sequence[Path]) -> pd.DataFrame:
    """Read with Pandas' default C parser and NumPy-backed nullable dtypes."""
    loans = pd.concat(
        [
            pd.read_csv(
                path,
                usecols=list(REQUIRED_COLUMNS),
                dtype={column: "string" for column in REQUIRED_COLUMNS},
            )
            for path in csv_paths
        ],
        ignore_index=True,
    )
    return _pandas_aggregation(loans)


def calculate_with_pandas_pyarrow(csv_paths: Sequence[Path]) -> pd.DataFrame:
    """Read with Pandas' multithreaded PyArrow parser and Arrow-backed dtypes."""
    loans = pd.concat(
        [
            pd.read_csv(
                path,
                usecols=list(REQUIRED_COLUMNS),
                engine="pyarrow",
                dtype_backend="pyarrow",
            )
            for path in csv_paths
        ],
        ignore_index=True,
    )
    return _pandas_aggregation(loans)


def _polars_aggregation(
    loans: pl.DataFrame | pl.LazyFrame,
) -> pl.DataFrame | pl.LazyFrame:
    return (
        loans.with_columns(
            pl.col("issue_d")
            .str.strptime(pl.Date, format="%b-%y", strict=False)
            .dt.year()
            .alias("year"),
            pl.col("int_rate")
            .str.strip_chars()
            .str.strip_suffix("%")
            .cast(pl.Float64, strict=False)
            .alias("interest_rate"),
        )
        .drop_nulls(["addr_state", "year", "interest_rate"])
        .group_by("addr_state", "year")
        .agg(pl.col("interest_rate").mean().alias("average_interest_rate"))
        .sort("year", "addr_state")
    )


def calculate_with_polars(csv_paths: Sequence[Path]) -> pl.DataFrame:
    loans = pl.concat(
        [
            pl.read_csv(
                path,
                columns=REQUIRED_COLUMNS,
                schema_overrides={column: pl.String for column in REQUIRED_COLUMNS},
            )
            for path in csv_paths
        ],
        how="vertical_relaxed",
    )
    result = _polars_aggregation(loans)
    assert isinstance(result, pl.DataFrame)
    return result


def calculate_with_polars_streaming(csv_paths: Sequence[Path]) -> pl.DataFrame:
    loans = pl.concat(
        [
            pl.scan_csv(
                path,
                schema_overrides={column: pl.String for column in REQUIRED_COLUMNS},
            ).select(REQUIRED_COLUMNS)
            for path in csv_paths
        ],
        how="vertical_relaxed",
    )
    result = _polars_aggregation(loans)
    assert isinstance(result, pl.LazyFrame)
    return result.collect(engine="streaming")


def _normalized_result(result: pd.DataFrame | pl.DataFrame) -> dict[tuple[str, int], float]:
    rows = (
        result.iter_rows(named=True)
        if isinstance(result, pl.DataFrame)
        else result.to_dict(orient="records")
    )
    return {
        (str(row["addr_state"]), int(row["year"])): float(
            row["average_interest_rate"]
        )
        for row in rows
    }


def assert_matching_results(results: dict[str, pd.DataFrame | pl.DataFrame]) -> None:
    reference = _normalized_result(results["Pandas"])
    for name, result in results.items():
        actual = _normalized_result(result)
        if actual.keys() != reference.keys() or any(
            not math.isclose(actual[key], reference[key], rel_tol=1e-12, abs_tol=1e-12)
            for key in reference
        ):
            raise AssertionError(f"{name} result does not match Pandas")


def benchmark(
    csv_paths: Sequence[Path], rounds: int
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame | pl.DataFrame]]:
    implementations: list[
        tuple[str, Callable[[Sequence[Path]], pd.DataFrame | pl.DataFrame]]
    ] = [
        ("Pandas", calculate_with_pandas),
        ("Pandas (PyArrow)", calculate_with_pandas_pyarrow),
        ("Polars", calculate_with_polars),
        ("Polars streaming", calculate_with_polars_streaming),
    ]
    timings = {name: [] for name, _ in implementations}
    latest_results: dict[str, pd.DataFrame | pl.DataFrame] = {}

    for round_number in range(rounds):
        offset = round_number % len(implementations)
        ordered = implementations[offset:] + implementations[:offset]
        for name, implementation in ordered:
            gc.collect()
            started = perf_counter()
            latest_results[name] = implementation(csv_paths)
            timings[name].append(perf_counter() - started)

    assert_matching_results(latest_results)
    pandas_median = statistics.median(timings["Pandas"])
    summary = pd.DataFrame(
        [
            {
                "implementation": name,
                "median_seconds": statistics.median(timings[name]),
                "min_seconds": min(timings[name]),
                "max_seconds": max(timings[name]),
                "speedup_vs_pandas": pandas_median / statistics.median(timings[name]),
                "rounds": rounds,
            }
            for name, _ in implementations
        ]
    ).sort_values("median_seconds", ignore_index=True)
    return summary, latest_results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_dir", nargs="?", default=Path("data"), type=Path)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.rounds < 1:
        raise SystemExit("--rounds must be at least 1")

    csv_paths = find_csvs(args.data_dir)
    total_gib = sum(path.stat().st_size for path in csv_paths) / 1024**3
    print(
        f"Python {sys.version.split()[0]} | pandas {pd.__version__} | "
        f"pyarrow {pa.__version__} | polars {pl.__version__}"
    )
    print(
        f"{platform.platform()} | logical CPUs: {os.cpu_count()} | "
        f"Polars threads: {pl.thread_pool_size()} | PyArrow threads: {pa.cpu_count()}"
    )
    print(f"Benchmarking {len(csv_paths)} CSV files ({total_gib:.2f} GiB)...")
    summary, results = benchmark(csv_paths, args.rounds)
    print(summary.to_string(index=False, float_format=lambda value: f"{value:.3f}"))
    print(f"\nVerified: all implementations returned {len(results['Pandas'])} matching groups.")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        summary.to_csv(args.output, index=False)
        print(f"Wrote timing summary to {args.output}")


if __name__ == "__main__":
    main()

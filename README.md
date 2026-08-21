# Loan DataFrame Benchmark

A reproducible comparison of Pandas, eager Polars, and Polars' streaming engine
for aggregating loan interest rates by state and year across multiple CSV files.

## Workload

Each implementation performs equivalent work:

1. Read `addr_state`, `issue_d`, and `int_rate` from every CSV.
2. Parse the issue year and percentage-formatted interest rate.
3. Remove invalid rows.
4. Calculate mean interest rate by state and year.
5. Sort the result.

The benchmark verifies that all implementations return the same groups and
numerically equivalent averages. Timed runs rotate implementation order to
reduce filesystem-cache bias.

## Run

Install [uv](https://docs.astral.sh/uv/), clone this repository, and place loan
CSVs in `data/`. Each CSV must contain the three columns listed above.

```powershell
uv sync
uv run python benchmark.py data --rounds 3
```

Save the timing summary with:

```powershell
uv run python benchmark.py data --rounds 3 --output benchmark_results.csv
```

## Example result

The following result came from 22 CSV files totaling 1.58 GiB. It is an example,
not a claim that every machine or dataset will produce the same result.

| Implementation | Median time | Speedup vs. Pandas |
| --- | ---: | ---: |
| Polars streaming | 0.319 s | 18.98x |
| Polars | 0.529 s | 11.44x |
| Pandas | 6.050 s | 1.00x |

Environment: Python 3.14.7, Pandas 2.3.3, Polars 1.43.2. All three methods
returned the same 655 state/year groups.

## Interpreting the result

This benchmark measures elapsed time for local CSV processing. It does not
measure peak memory, cold starts, network transfer, or cloud-service overhead.
Results should be rerun on the intended production hardware before making an
architecture or cost decision.

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

## Illustrative AWS Lambda cost estimate

AWS Lambda bills for requests and execution duration. Duration charges are
calculated from configured memory in GB multiplied by billed execution time.
For an illustrative comparison, assume:

- x86 Lambda in US East (Ohio), first pricing tier
- 2 GB of configured memory
- $0.0000166667 per GB-second
- $0.20 per million requests
- one invocation processes the complete 22-file dataset
- the local median runtimes above remain unchanged on Lambda
- no AWS Free Tier credits

Using `duration x 2 GB x $0.0000166667`, plus the request charge, gives:

| Implementation | Assumed duration | Cost per invocation | Cost per 1M invocations |
| --- | ---: | ---: | ---: |
| Polars streaming | 0.319 s | $0.00001083 | $10.83 |
| Pandas | 6.050 s | $0.00020187 | $201.87 |

Under these assumptions, Polars streaming is approximately **94.6% less
expensive**, a potential saving of about **$191.03 per million invocations**.
This estimate covers Lambda compute and request charges only. It excludes S3
requests and storage, additional ephemeral storage, data transfer, logging,
cold starts, retries, and orchestration. See the current
[AWS Lambda pricing](https://aws.amazon.com/lambda/pricing/) before using the
formula for a financial decision.

### Production-validation caveat

The cost estimate is a directional guess based on a local Windows benchmark;
it is not a forecast or guarantee of production savings. Lambda CPU capacity
changes with configured memory, and Lambda runs on Amazon Linux rather than the
machine used for the example. Real applications also include package imports,
S3 downloads, result writes, cold starts, concurrency, and production data
distributions that this benchmark does not reproduce.

Before making an architecture or purchasing decision, deploy all three
implementations to the customer's AWS environment and test with representative
production code and data. Compare multiple Lambda memory settings and both
warm and cold invocations, then calculate cost from Lambda's actual billed
duration and maximum-memory metrics.

## Interpreting the result

This benchmark measures elapsed time for local CSV processing. It does not
measure peak memory, cold starts, network transfer, or cloud-service overhead.
Results should be rerun on the intended production hardware before making an
architecture or cost decision.

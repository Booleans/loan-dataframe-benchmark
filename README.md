# Loan DataFrame Benchmark

A reproducible comparison of standard Pandas, Pandas with PyArrow, eager
Python Polars, Python Polars streaming, and native Rust Polars streaming for
aggregating loan interest rates by state and year across multiple CSV files.

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

The Rust binary runs as a persistent worker. Compilation and process startup
are outside the timed rounds; CSV processing and serialization of the 655-row
result back to Python are inside them. This avoids turning a data-engine
benchmark into a process-launch benchmark while preserving end-to-end result
validation in one harness.

## Run

Install [uv](https://docs.astral.sh/uv/) and the stable
[Rust toolchain](https://rustup.rs/), clone this repository, and place loan
CSVs in `data/`. Each CSV must contain the three columns listed above.

Build the Rust worker in release mode, then install the Python environment and
run the benchmark:

```powershell
cargo build --release --manifest-path rust/Cargo.toml
uv sync
uv run python benchmark.py data --rounds 10
```

On Windows, Rust's MSVC target also requires the Visual Studio Build Tools
**Desktop development with C++** workload. Run the Cargo command from a
Developer PowerShell so `link.exe` is available. Linux and macOS require their
platform C/C++ build tools.

Save the timing summary with:

```powershell
uv run python benchmark.py data --rounds 10 --output benchmark_results.csv
```

## Example result

The following result came from 22 CSV files totaling 1.27 GiB. It is an example,
not a claim that every machine or dataset will produce the same result.

| Implementation | Median time | Speedup vs. Pandas |
| --- | ---: | ---: |
| Polars streaming | 0.228 s | 20.49x |
| Polars | 0.447 s | 10.44x |
| Rust Polars streaming | 0.539 s | 8.67x |
| Pandas (PyArrow) | 1.343 s | 3.48x |
| Pandas | 4.670 s | 1.00x |

Environment: Python 3.14.7, Pandas 2.3.3, PyArrow 25.0.1, Python Polars
1.43.2, Rust 1.97.0, and the Rust Polars crate 0.55.2. The Rust binary used
Polars' `performant` feature, thin LTO, and one code-generation unit. Results
are medians from 10 rounds. All five methods returned the same 655 state/year
groups.

The Rust crate and Python package use different version-numbering lines. The
Rust result should therefore be read as a benchmark of this pinned Rust build,
not as a measurement of language overhead around the exact same Polars engine
binary. In this run, native Rust was faster than both Pandas configurations but
slower than both Python Polars configurations; removing Python did not by
itself make the workload faster.

## How CPU cores affected the result

The example machine exposed 8 logical CPUs. Python Polars and PyArrow reported
8-thread worker pools, while the Rust Polars process also defaulted to the
machine's available parallelism. Polars can parallelize CSV parsing,
expressions, and aggregation, so the eager and both streaming Polars variants
benefited from the available cores. See the
[Polars CSV threading documentation](https://docs.pola.rs/api/python/stable/reference/api/polars.read_csv.html)
and [`polars.thread_pool_size`](https://docs.pola.rs/api/python/stable/reference/api/polars.thread_pool_size.html).

The standard Pandas implementation uses its default C CSV parser. The added
`Pandas (PyArrow)` implementation uses Pandas with the multithreaded PyArrow
CSV engine and Arrow-backed dtypes. This gives Pandas access to parallel CSV
parsing and makes the comparison more representative of optimized modern
Pandas. See the
[Pandas CSV parser documentation](https://pandas.pydata.org/pandas-docs/stable/reference/api/pandas.read_csv.html).
PyArrow's multithreaded CSV reader is documented in the
[Apache Arrow CSV guide](https://arrow.apache.org/docs/python/csv.html).

Python streaming Polars was 5.90x faster than PyArrow Pandas in this example.
That is a more balanced parallel comparison, although it still does not isolate
CSV parsing from the Pandas transformations and aggregation that follow it.
The reported results represent each configuration's default end-to-end
throughput on this particular 8-thread machine, not equal single-core
performance.

That is a reasonable comparison for an application allowed to use all
available CPU resources. For a core-scaling study, run separate processes with
both the Polars and Arrow thread pools limited to the same size:

```powershell
foreach ($threads in 1, 2, 4, 8) {
    $env:POLARS_MAX_THREADS = "$threads"
    $env:OMP_NUM_THREADS = "$threads"
    $env:OMP_THREAD_LIMIT = "$threads"
    uv run python benchmark.py data --rounds 10 `
        --output "results-$threads-threads.csv"
}

Remove-Item Env:POLARS_MAX_THREADS
Remove-Item Env:OMP_NUM_THREADS
Remove-Item Env:OMP_THREAD_LIMIT
```

Performance should not be expected to scale linearly with thread count. Disk
throughput, memory bandwidth, CPU architecture, and the difference between
physical cores and logical threads can all become bottlenecks. This is also
important for AWS Lambda: Lambda allocates CPU in proportion to configured
memory, so the desktop speedup should not be assumed at every Lambda memory
setting.

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
| Polars streaming | 0.228 s | $0.00000780 | $7.80 |
| Polars | 0.447 s | $0.00001510 | $15.10 |
| Rust Polars streaming | 0.539 s | $0.00001817 | $18.17 |
| Pandas (PyArrow) | 1.343 s | $0.00004497 | $44.97 |
| Pandas | 4.670 s | $0.00015587 | $155.87 |

Under these assumptions, Python Polars streaming is approximately **95.0% less
expensive than standard Pandas**, a potential saving of about **$148.07 per
million invocations**. Compared with PyArrow Pandas, Python streaming Polars is
approximately **82.7% less expensive**, a potential saving of about **$37.17
per million invocations**. Rust Polars streaming is approximately **88.3% less
expensive than standard Pandas**, a potential saving of about **$137.70 per
million invocations**.
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

Before making an architecture or purchasing decision, deploy all five
implementations to the customer's AWS environment and test with representative
production code and data. Compare multiple Lambda memory settings and both
warm and cold invocations, then calculate cost from Lambda's actual billed
duration and maximum-memory metrics.

## Interpreting the result

This benchmark measures elapsed time for local CSV processing. It does not
measure peak memory, cold starts, network transfer, or cloud-service overhead.
Results should be rerun on the intended production hardware before making an
architecture or cost decision.

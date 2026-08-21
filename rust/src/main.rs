use std::fs;
use std::io::{self, BufRead, Write};
use std::path::{Path, PathBuf};

use anyhow::{Context, Result, bail};
use polars::lazy::dsl::{col, concat};
use polars::prelude::*;
use serde_json::{Value, json};

const REQUIRED_COLUMNS: [&str; 3] = ["addr_state", "issue_d", "int_rate"];

fn find_csvs(data_dir: &Path) -> Result<Vec<PathBuf>> {
    let mut paths = fs::read_dir(data_dir)
        .with_context(|| format!("could not read {}", data_dir.display()))?
        .filter_map(|entry| entry.ok().map(|entry| entry.path()))
        .filter(|path| {
            path.extension()
                .is_some_and(|extension| extension.eq_ignore_ascii_case("csv"))
        })
        .collect::<Vec<_>>();
    paths.sort();
    if paths.is_empty() {
        bail!("no CSV files found in {}", data_dir.display());
    }
    Ok(paths)
}

fn calculate(csv_paths: &[PathBuf]) -> Result<DataFrame> {
    let string_schema = Arc::new(Schema::from_iter(
        REQUIRED_COLUMNS.map(|name| Field::new(name.into(), DataType::String)),
    ));
    let scans = csv_paths
        .iter()
        .map(|path| {
            let path = path
                .to_str()
                .with_context(|| format!("path is not valid UTF-8: {}", path.display()))?;
            Ok(LazyCsvReader::new(PlRefPath::new(path))
                .with_dtype_overwrite(Some(string_schema.clone()))
                .finish()?
                .select(REQUIRED_COLUMNS.map(col)))
        })
        .collect::<Result<Vec<_>>>()?;

    let loans = concat(scans, UnionArgs::default())?;
    let result = loans
        .with_columns([
            col("issue_d")
                .str()
                .to_date(StrptimeOptions {
                    format: Some("%b-%y".into()),
                    strict: false,
                    exact: true,
                    cache: true,
                })
                .dt()
                .year()
                .alias("year"),
            col("int_rate")
                .str()
                .strip_chars(lit(NULL))
                .str()
                .strip_suffix(lit("%"))
                .cast(DataType::Float64)
                .alias("interest_rate"),
        ])
        .filter(
            col("addr_state")
                .is_not_null()
                .and(col("year").is_not_null())
                .and(col("interest_rate").is_not_null()),
        )
        .group_by([col("addr_state"), col("year")])
        .agg([col("interest_rate").mean().alias("average_interest_rate")])
        .sort(["year", "addr_state"], SortMultipleOptions::default())
        .collect_with_engine(Engine::Streaming)?
        .unwrap_single();

    Ok(result)
}

fn rows_as_json(result: &DataFrame) -> Result<Vec<Value>> {
    let states = result.column("addr_state")?.str()?;
    let years = result.column("year")?.i32()?;
    let rates = result.column("average_interest_rate")?.f64()?;
    let mut rows = Vec::with_capacity(result.height());

    for index in 0..result.height() {
        rows.push(json!({
            "addr_state": states.get(index),
            "year": years.get(index),
            "average_interest_rate": rates.get(index),
        }));
    }
    Ok(rows)
}

fn write_json_line(value: &Value, output: &mut impl Write) -> Result<()> {
    serde_json::to_writer(&mut *output, value)?;
    writeln!(output)?;
    output.flush()?;
    Ok(())
}

fn main() -> Result<()> {
    let data_dir = std::env::args_os()
        .nth(1)
        .map(PathBuf::from)
        .context("usage: loan-benchmark-rust <data-directory>")?;
    let csv_paths = find_csvs(&data_dir)?;
    let stdin = io::stdin();
    let mut stdout = io::stdout().lock();

    write_json_line(
        &json!({"status": "ready", "csv_files": csv_paths.len()}),
        &mut stdout,
    )?;

    for line in stdin.lock().lines() {
        match line?.trim() {
            "run" => {
                let result = calculate(&csv_paths)?;
                write_json_line(&json!({"rows": rows_as_json(&result)?}), &mut stdout)?;
            }
            "quit" => break,
            command => bail!("unknown command: {command}"),
        }
    }
    Ok(())
}

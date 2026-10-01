# CSV statistics utility

This offline Python 3.10+ utility implements **median imputation**, **IQR outlier
detection**, and **min-max normalization** for explicitly selected columns.
It uses the Python standard library at runtime. Testing requires pytest 8.3.5.

## Run

```sh
python csv_statistics.py input.csv clean.csv --columns amount quantity --audit audit.json
python -m pip install pytest==8.3.5
python -m pytest -q
```

Both output paths must be absent. Input is a regular local UTF-8 comma-delimited
CSV file with unique nonblank headers and rectangular rows. Quoted fields,
embedded commas/newlines, and Unicode are supported. No network, imports of
customer code, or subprocesses are used by the utility.

## Exact statistical contract

1. Name each numerical column explicitly; there is no automatic type inference.
   Nonselected fields keep their original strings, including leading zeros.
2. Only blank or whitespace-only selected cells count as missing. Text such as
   `NA`, `null`, `NaN`, `Infinity`, currency signs and thousands separators is
   rejected rather than guessed. Finite signed decimal/scientific notation is
   supported within the documented limits.
3. Fit the median and type-7 linearly interpolated quartiles to observed values
   **before** filling missing cells. Fill each missing selected cell with its
   observed median. An all-missing nonempty column is an error.
4. Flag values strictly below Q1 - 1.5*IQR or above Q3 + 1.5*IQR in the JSON audit.
   Never remove or replace outliers. Row indices are one-based data-row indices,
   independent of physical line numbers in CSV with quoted multiline fields.
5. Scale selected values with `(value-minimum)/(maximum-minimum)`. The imputed
   median is within the observed range. A constant column becomes 0. Scale is
   rounded to 12 decimal places, half-even; fitted medians/quartiles use exact
   rational arithmetic and retain their finite decimal values in the audit.
6. Preserve row and column order. Header-only input returns the header with
   zero rows and null fitted statistics. An input without a header is rejected.

The audit includes observed/imputed/outlier counts, median, Q1/Q3, IQR/fences,
minimum/maximum and complete flagged cells with original values. It contains
input-derived data and should receive the same privacy treatment as the input.

## Resource bounds and exclusions

10 MiB input; 100,000 rows; 1,024 columns; 1,000,000 cells; 65,536 characters per
field; 28 significant decimal digits with decimal exponent -50 through +50;
10,000 flagged outlier cells; 32 MiB for each output. Exceeding a bound fails
without a partial returned result. Numerical processing uses exact rationals
before the explicitly rounded normalized output. The CLI validates the full
result before writing; a filesystem failure while writing can still leave an
output file that the operator must remove before retrying.

No date parsing, category inference, fuzzy matching, predictive modeling,
financial recommendations, hosting, arbitrary data/schema inference or
customer-specific analysis is promised. All included tests use synthetic data.

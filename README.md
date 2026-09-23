# Offline data utility

Python 3.10+ and its standard library only. No install, network access or model is
required. Read `contract.json` for the exact operation and parameters. Supply your
own input, update its SHA-256 in the contract, and run:

    python fulfill.py run contract.json --workspace .
    python -m unittest -v test_contract.py

For conversion without constructing a contract:

    python fulfill.py convert json-to-csv input.json output.csv --fields name id
    python fulfill.py convert csv-to-json input.csv output.json --delimiter semicolon --json-format compact

Conversion paths must stay within the current directory, and output must be new.
The run command writes new content-addressed artifact directories and prints a
manifest with hashes and validation results. Existing artifacts are verified on
replay. It never overwrites an existing delivery.

Semantics: UTF-8 (optional BOM); flat JSON arrays only; JSON key order follows
first appearance unless fields specifies exact selected order. Absent selected
fields fail unless missing_fields=blank. CSV-to-JSON preserves all cells as strings
(including leading zeros). JSON output formatting supports pretty (two spaces),
compact array, or newline-delimited objects (--json-format ndjson). Custom nested
output schemas, type coercion and arbitrary template/code evaluation are not
supported. JSON null maps to empty CSV and booleans to true/false.
CSV output uses LF record endings and standard quoting; output columns must be
unique and nonblank. No formula escaping or type inference is silently applied.
Choose an explicit delimiter for ambiguous CSV. Clean CSV changes only explicitly
listed trim_fields; validators never infer missing values. email_basic checks a
limited ASCII syntax, not deliverability. nonnegative_decimal_2dp accepts unsigned
decimal text with zero to two fractional digits. iso_date checks real YYYY-MM-DD
calendar dates. Dedupe supports exact or casefolded composite keys, with explicit
first_seen or first_valid retention; empty key components never reserve a key.
Duplicates and invalid records are separately counted and preserved in rejects.
invalid_rows=error fails the whole job; reject produces an auditable reject file.

Limits: 10 MiB input, 100,000 rows, 1,024 columns, 1,000,000 input/output cells,
65,536 UTF-8 bytes per cell,
32 MiB combined transformed data/rejections. Unsupported keys, malformed input,
duplicate headers/JSON keys, nested JSON, failed acceptance and path escapes fail.
No guessed task interpretation, external commands, payment or submission occurs.
The included examples are conformance tests; passing them is not customer approval.

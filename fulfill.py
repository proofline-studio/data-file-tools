"""Bounded, offline data fulfillment from an exact structured contract.

This module produces artifacts; it never claims jobs, accesses a network, executes
customer code, submits work, or verifies payment. The caller owns those decisions.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

ENGINE_VERSION = "1.0.0"
MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_ROWS = 100000
MAX_COLUMNS = 1024
MAX_CELLS = 1000000
MAX_CELL = 65536
MAX_SPEC_BYTES = 1024 * 1024
OPERATIONS = {"json_to_csv", "csv_to_json", "clean_csv"}
METRICS = {"rows_read", "rows_written", "duplicate_rows", "invalid_rows", "rejected_rows"}
VALIDATORS = {"required", "email_basic", "nonnegative_decimal_2dp", "iso_date"}
LOCAL = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
EMAIL = re.compile(LOCAL + r"(?:\." + LOCAL + r")*@" + LABEL + r"(?:\." + LABEL + r")+")


class FulfillmentError(ValueError):
    """An unsupported, ambiguous, malformed or unfulfilled contract."""


def _require(condition, message):
    if not condition:
        raise FulfillmentError(message)


def _object(value, allowed, label):
    _require(type(value) is dict, label + " must be an object")
    _require(not set(value) - set(allowed), label + " contains unsupported keys")


def _columns(value, label, allow_empty=False):
    _require(type(value) is list and (allow_empty or bool(value)), label + " must be a nonempty array")
    _require(len(value) <= MAX_COLUMNS, label + " exceeds column limit")
    _require(all(type(x) is str and x.strip() and len(x) <= 1024 and "\x00" not in x for x in value),
             label + " contains invalid column names")
    _require(len(set(value)) == len(value), label + " contains duplicate names")
    try:
        for name in value:
            name.encode("utf-8")
    except UnicodeError as error:
        raise FulfillmentError(label + " must contain valid Unicode") from error


def canonical_json(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise FulfillmentError("Contract must be finite, UTF-8 JSON") from error


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def spec_digest(spec):
    validate_spec(spec)
    return sha256(canonical_json(spec))


def _relative(path):
    _require(type(path) is str and path and len(path) <= 512, "Invalid relative file path")
    _require(not any(c in path for c in "\\:\x00") and not path.startswith("/"),
             "Only relative forward-slash paths are supported")
    _require(all(p not in {"", ".", ".."} for p in path.split("/")), "Unsafe path component")
    reserved = {"con", "prn", "aux", "nul"} | {"com" + str(i) for i in range(1, 10)} | {"lpt" + str(i) for i in range(1, 10)}
    _require(all(p.split(".", 1)[0].lower() not in reserved and not p.endswith((" ", ".")) for p in path.split("/")),
             "Reserved file name")
    return path


def _validate_spec(spec):
    _object(spec, {"schema_version", "mode", "operation", "parameters", "input", "acceptance", "examples"}, "spec")
    _require(type(spec.get("schema_version")) is int and spec["schema_version"] == 1, "Unsupported schema_version")
    _require(spec.get("mode") in {"source_package", "processed_data"}, "Unsupported mode")
    _require(spec.get("operation") in OPERATIONS, "Unsupported operation")
    _require(len(canonical_json(spec)) <= MAX_SPEC_BYTES, "Contract exceeds size limit")
    p = spec.get("parameters", {})
    _object(p, {"fields", "missing_fields", "delimiter", "json_format", "trim_fields", "validators", "dedupe", "invalid_rows"}, "parameters")
    if "fields" in p:
        _columns(p["fields"], "fields")
    _require(p.get("missing_fields", "error") in {"error", "blank"}, "Invalid missing_fields policy")
    _require(p.get("delimiter", "auto") in {"auto", ",", ";", "\t", "|"}, "Unsupported delimiter")
    if "json_format" in p:
        _require(spec["operation"] == "csv_to_json" and p["json_format"] in {"pretty", "compact", "ndjson"}, "Unsupported JSON output format")
    if spec["operation"] == "json_to_csv":
        _require(p.get("delimiter", "auto") == "auto", "Input delimiter does not apply to JSON")
    _require(p.get("invalid_rows", "error") in {"error", "reject"}, "Invalid invalid_rows policy")
    if spec["operation"] != "clean_csv":
        _require(not {"trim_fields", "validators", "dedupe", "invalid_rows"} & set(p),
                 "Cleanup rules require clean_csv")
    if "trim_fields" in p:
        _columns(p["trim_fields"], "trim_fields", allow_empty=True)
    if "validators" in p:
        _require(type(p["validators"]) is dict, "validators must be an object")
        _columns(list(p["validators"]), "validator columns", allow_empty=True)
        for rules in p["validators"].values():
            _require(type(rules) is list and rules and all(type(r) is str and r in VALIDATORS for r in rules),
                     "Unsupported validator")
            _require(len(set(rules)) == len(rules), "Duplicate validator")
    if "dedupe" in p:
        d = p["dedupe"]
        _object(d, {"fields", "casefold", "keep"}, "dedupe")
        _columns(d.get("fields"), "dedupe fields")
        _require(type(d.get("casefold", False)) is bool, "casefold must be boolean")
        _require(d.get("keep") in {"first_seen", "first_valid"}, "Explicit dedupe retention policy required")
    a = spec.get("acceptance", {})
    _object(a, METRICS | {"columns", "output_sha256"}, "acceptance")
    for key in METRICS & set(a):
        _require(type(a[key]) is int and 0 <= a[key] <= MAX_ROWS, "Invalid acceptance count")
    if "columns" in a:
        _columns(a["columns"], "acceptance columns", allow_empty=True)
    if "output_sha256" in a:
        _require(type(a["output_sha256"]) is str and re.fullmatch("[a-f0-9]{64}", a["output_sha256"]), "Invalid output digest")
    if spec["mode"] == "processed_data":
        source = spec.get("input")
        _object(source, {"path", "sha256"}, "input")
        _relative(source.get("path"))
        _require(type(source.get("sha256")) is str and re.fullmatch("[a-f0-9]{64}", source["sha256"]), "Input digest is required")
        _require("examples" not in spec, "Processed data does not accept example-only criteria")
    else:
        _require("input" not in spec, "A source package does not process a buyer input file")
        _require(not a, "Use examples for source-package data acceptance")
        examples = spec.get("examples", [])
        _require(type(examples) is list and len(examples) <= 25, "Invalid examples")
        for example in examples:
            _object(example, {"input_text", "expected_output", "expected_metrics"}, "example")
            _require(type(example.get("input_text")) is str and type(example.get("expected_output")) is str,
                     "Examples require input_text and exact expected_output")
            _object(example.get("expected_metrics", {}), METRICS, "example metrics")
            for count in example.get("expected_metrics", {}).values():
                _require(type(count) is int and 0 <= count <= MAX_ROWS, "Invalid example metric")
    return spec


def validate_spec(spec):
    try:
        return _validate_spec(spec)
    except (TypeError, KeyError, OverflowError, RecursionError) as error:
        raise FulfillmentError("Malformed structured contract") from error


def _unique_object(pairs):
    obj = {}
    for k, v in pairs:
        _require(k not in obj, "Duplicate JSON key")
        obj[k] = v
    return obj


def _constant(value):
    raise FulfillmentError("Nonfinite JSON numbers are unsupported")


def _json_load(text):
    try:
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_constant, parse_float=Decimal)
    except (ValueError, RecursionError) as error:
        raise FulfillmentError("Invalid JSON: " + str(error)[:120]) from error


def _scalar(value):
    _require(not isinstance(value, (dict, list)), "JSON rows must contain flat scalar values")
    if value is None:
        result = ""
    elif isinstance(value, bool):
        result = "true" if value else "false"
    else:
        result = str(value)
    try:
        size = len(result.encode("utf-8"))
    except UnicodeError as error:
        raise FulfillmentError("Cell is not valid Unicode") from error
    _require(size <= MAX_CELL and "\x00" not in result, "Cell exceeds limit or contains NUL")
    return result


def _read_rows(operation, raw, delimiter):
    _require(type(raw) is bytes and len(raw) <= MAX_INPUT_BYTES, "Input exceeds 10 MiB or is not bytes")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError as error:
        raise FulfillmentError("Input must be UTF-8") from error
    _require("\x00" not in text, "NUL input is unsupported")
    if operation == "json_to_csv":
        data = _json_load(text)
        _require(type(data) is list and len(data) <= MAX_ROWS, "Expected a bounded array of flat objects")
        columns, rows, cells_read = [], [], 0
        seen = set()
        for i, row in enumerate(data, 1):
            _require(type(row) is dict, "Every JSON row must be an object")
            _columns(list(row), "JSON object keys")
            cells_read += len(row)
            _require(cells_read <= MAX_CELLS, "Too many input cells")
            for name in row:
                if name not in seen:
                    columns.append(name)
                    seen.add(name)
            _require(len(columns) <= MAX_COLUMNS, "Too many columns")
            rows.append((i, None, {k: _scalar(v) for k, v in row.items()}, None))
        return columns, rows
    if delimiter == "auto":
        try:
            delimiter = csv.Sniffer().sniff(text[:65536], delimiters=",;\t|").delimiter
        except csv.Error:
            _require(not any(c in text for c in ",;\t|"), "Ambiguous CSV dialect; supply delimiter")
            delimiter = ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    rows, cells_read = [], 0
    try:
        columns = next(reader, None)
        _columns(columns, "CSV header")
        for i, cells in enumerate(reader, 1):
            _require(i <= MAX_ROWS, "Too many rows")
            _require(len(cells) <= MAX_COLUMNS, "Too many cells")
            cells_read += len(cells)
            _require(cells_read <= MAX_CELLS, "Too many input cells")
            for cell in cells:
                _scalar(cell)
            rows.append((i, reader.line_num, dict(zip(columns, cells)) if len(cells) == len(columns) else None, cells))
    except csv.Error as error:
        raise FulfillmentError("Malformed CSV quoting or oversized field") from error
    return columns, rows


def _valid(value, rule):
    if rule == "required":
        return bool(value.strip())
    if rule == "email_basic":
        return len(value) <= 254 and len(value.split("@", 1)[0]) <= 64 and EMAIL.fullmatch(value) is not None
    if rule == "nonnegative_decimal_2dp":
        return re.fullmatch(r"\d+(?:\.\d{1,2})?", value, flags=re.ASCII) is not None
    if rule == "iso_date":
        try:
            return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value, flags=re.ASCII)) and dt.date.fromisoformat(value) is not None
        except ValueError:
            return False
    raise FulfillmentError("Unknown validator")


def transform(operation, raw, parameters=None, acceptance=None):
    """Pure bounded transformation. Returns output bytes, rejection bytes, metrics.

    Contract defaults: no trimming/validation/deduplication, exact field names,
    missing selected fields fail, CSV strings stay strings, LF CSV records.
    """
    p = {} if parameters is None else parameters
    a = {} if acceptance is None else acceptance
    validate_spec({"schema_version": 1, "mode": "processed_data", "operation": operation,
                   "parameters": p, "acceptance": a, "input": {"path": "input", "sha256": "0" * 64}})
    columns, rows = _read_rows(operation, raw, p.get("delimiter", "auto"))
    chosen = p.get("fields", columns)
    _require(len(rows) * len(chosen) <= MAX_CELLS, "Too many output cells")
    required_columns = set(p.get("trim_fields", [])) | set(p.get("validators", {})) | set(p.get("dedupe", {}).get("fields", []))
    _require(required_columns <= set(columns), "A cleanup rule references a missing column")
    if p.get("missing_fields", "error") == "error":
        _require(set(chosen) <= set(columns) or not rows and operation == "json_to_csv", "Selected field missing from schema")
    kept, rejected, seen = [], [], set()
    counts = dict.fromkeys(sorted(METRICS), 0)
    d = p.get("dedupe")
    for number, end_line, row, original in rows:
        counts["rows_read"] += 1
        errors, duplicate, key = [], False, None
        if row is None:
            errors.append("row_width")
        else:
            row = dict(row)
            for field in p.get("trim_fields", []):
                row[field] = row[field].strip()
            for field, validators in p.get("validators", {}).items():
                errors.extend(field + ":" + rule for rule in validators if not _valid(row[field], rule))
            if d:
                key = tuple(row[f].casefold() if d.get("casefold", False) else row[f] for f in d["fields"])
                # Missing IDs are independently invalid; they never reserve a key.
                if all(value.strip() for value in key):
                    duplicate = key in seen
                    if d["keep"] == "first_seen" or not errors:
                        seen.add(key)
                else:
                    key = None
            if p.get("missing_fields", "error") == "error":
                _require(set(chosen) <= set(row), "Selected field missing from a row")
        if duplicate:
            counts["duplicate_rows"] += 1
            rejected.append({"record_number": number, "physical_end_line": end_line,
                             "reason": "duplicate", "row": original if original is not None else row})
        elif errors:
            _require(operation == "clean_csv" and p.get("invalid_rows", "error") == "reject",
                     "Invalid row " + str(number) + ": " + ", ".join(errors))
            counts["invalid_rows"] += 1
            rejected.append({"record_number": number, "physical_end_line": end_line,
                             "reason": "invalid", "errors": errors, "row": original})
        else:
            kept.append({field: row.get(field, "") for field in chosen})
            counts["rows_written"] += 1
    counts["rejected_rows"] = len(rejected)
    _require(counts["rows_read"] == counts["rows_written"] + counts["duplicate_rows"] + counts["invalid_rows"], "Accounting mismatch")
    if operation == "csv_to_json":
        formatting = p.get("json_format", "pretty")
        if formatting == "ndjson":
            output = b"".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n" for row in kept)
        else:
            output = (json.dumps(kept, ensure_ascii=False, indent=2 if formatting == "pretty" else None,
                                 separators=None if formatting == "pretty" else (",", ":")) + "\n").encode("utf-8")
    else:
        stream = io.StringIO(newline="")
        if chosen:
            writer = csv.DictWriter(stream, fieldnames=chosen, lineterminator="\n")
            writer.writeheader()
            writer.writerows(kept)
        output = stream.getvalue().encode("utf-8")
    rejection_bytes = b"".join(canonical_json(item) + b"\n" for item in rejected)
    _require(len(output) + len(rejection_bytes) <= MAX_OUTPUT_BYTES, "Output exceeds 32 MiB")
    metrics = {**counts, "columns": chosen, "output_sha256": sha256(output)}
    for key, expected in a.items():
        _require(metrics[key] == expected, "Acceptance failed: " + key)
    # Independently parse serialized output and compare content, including quote/newline handling.
    if operation == "csv_to_json":
        parsed = [json.loads(line) for line in output.splitlines()] if p.get("json_format") == "ndjson" else json.loads(output)
        _require(parsed == kept, "JSON output round-trip failed")
    elif chosen:
        reread = list(csv.reader(io.StringIO(output.decode("utf-8"), newline=""), strict=True))
        _require(reread == [chosen] + [[row[c] for c in chosen] for row in kept], "CSV output round-trip failed")
    return output, rejection_bytes, metrics


def _confined(root, relative, must_exist=True):
    relative = _relative(relative)
    current = root
    for part in relative.split("/"):
        current = current / part
        _require(not current.is_symlink(), "Symlinks are not allowed in artifact/input paths")
        _require(current.resolve().is_relative_to(root), "Path escapes job workspace")
    if must_exist:
        _require(current.is_file() and current.stat().st_nlink == 1, "Expected a regular unlinked input file")
    return current


def _read_file(path, limit):
    _require(path.stat().st_size <= limit, "File exceeds size limit")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    _require(len(raw) <= limit, "File exceeds size limit")
    return raw


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"


def self_test():
    """Executable conformance examples bundled with delivered source packages."""
    got, _, counts = transform("json_to_csv", b'[{"id":7,"name":"A, B","skip":true},{"id":8,"name":"C"}]', {"fields": ["name", "id"]})
    _require(got == b'name,id\n"A, B",7\nC,8\n' and counts["rows_written"] == 2, "Field-selection conformance failed")
    got, _, _ = transform("csv_to_json", b'id;note\n001;"a\nb"\n', {"delimiter": ";"})
    _require(json.loads(got) == [{"id": "001", "note": "a\nb"}], "CSV conformance failed")
    params = {"delimiter": ",", "trim_fields": ["email"], "validators": {"email": ["email_basic"]},
              "dedupe": {"fields": ["email"], "casefold": True, "keep": "first_valid"}, "invalid_rows": "reject"}
    got, rejected, counts = transform("clean_csv", b'email,name\n A@example.test ,One\na@EXAMPLE.test,Two\nbad,Three\n', params)
    _require(got == b'email,name\nA@example.test,One\n' and counts["duplicate_rows"] == counts["invalid_rows"] == 1, "Cleanup conformance failed")
    for delimiter in [",", ";", "\t", "|"]:
        csv_raw = ("id" + delimiter + "note\n001" + delimiter + '"a\\b"\n').encode("utf-8")
        for formatting in ["pretty", "compact", "ndjson"]:
            got, _, _ = transform("csv_to_json", csv_raw, {"delimiter": delimiter, "json_format": formatting})
            parsed = [json.loads(line) for line in got.splitlines()] if formatting == "ndjson" else json.loads(got)
            _require(parsed == [{"id": "001", "note": "a\\b"}], "Dialect/format conformance failed")
    for source in [b'[{"x":1,"x":2}]', b'[{"x":NaN}]', b'[{"x":[1]}]']:
        try:
            transform("json_to_csv", source)
        except FulfillmentError:
            pass
        else:
            raise FulfillmentError("Malformed input was accepted")
    return {"passed": True, "examples": 18}


def _check_examples(spec):
    for example in spec.get("examples", []):
        output, _, metrics = transform(spec["operation"], example["input_text"].encode("utf-8"), spec.get("parameters", {}))
        _require(output == example["expected_output"].encode("utf-8"), "Contract example output failed")
        for key, expected in example.get("expected_metrics", {}).items():
            _require(metrics[key] == expected, "Contract example metric failed: " + key)
    return len(spec.get("examples", []))


def _source_files(spec):
    conformance = self_test()
    count = _check_examples(spec)
    source = _read_file(Path(__file__).resolve(), MAX_INPUT_BYTES)
    p = spec.get("parameters", {})
    example_columns = list(dict.fromkeys(p.get("fields", []) + p.get("trim_fields", []) + list(p.get("validators", {}))
                                       + p.get("dedupe", {}).get("fields", []))) or ["name", "id"]
    example_row = {}
    for column in example_columns:
        rules = p.get("validators", {}).get(column, [])
        example_row[column] = "sample@example.test" if "email_basic" in rules else "2026-01-01" if "iso_date" in rules else "12.50" if "nonnegative_decimal_2dp" in rules else "example"
    if spec["operation"] == "json_to_csv":
        example_input = _json_bytes([example_row])
    else:
        example_stream = io.StringIO(newline="")
        example_writer = csv.DictWriter(example_stream, fieldnames=example_columns,
                                        delimiter="," if p.get("delimiter", "auto") == "auto" else p["delimiter"], lineterminator="\n")
        example_writer.writeheader()
        example_writer.writerow(example_row)
        example_input = example_stream.getvalue().encode("utf-8")
    if spec.get("examples"):
        example_input = spec["examples"][0]["input_text"].encode("utf-8")
    transform(spec["operation"], example_input, p)
    # The contract is shipped exactly; buyer input is explicitly supplied at runtime.
    contract = {"schema_version": 1, "mode": "processed_data", "operation": spec["operation"],
                "parameters": spec.get("parameters", {}), "input": {"path": "input.data", "sha256": sha256(example_input)}}
    readme = '''# Offline data utility

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
'''
    tests = '''import json
from pathlib import Path
import unittest
import fulfill

class ContractTests(unittest.TestCase):
    def test_engine_conformance(self):
        self.assertTrue(fulfill.self_test()["passed"])
    def test_bound_examples(self):
        spec = json.loads(Path(__file__).with_name("package-spec.json").read_text(encoding="utf-8"))
        fulfill.validate_spec(spec)
        fulfill._check_examples(spec)
    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaises(fulfill.FulfillmentError):
            fulfill.transform("json_to_csv", b'[{"x":1,"x":2}]')
    def test_invalid_csv_is_rejected(self):
        for raw in [b'a,a\\n1,2\\n', b'a,b\\n1\\n', b'a,b\\n"unterminated\\n']:
            with self.subTest(raw=raw), self.assertRaises(fulfill.FulfillmentError):
                fulfill.transform("csv_to_json", raw, {"delimiter": ","})
    def test_missing_field_policy(self):
        raw = b'[{"id":"001"},{"name":"B"}]'
        with self.assertRaises(fulfill.FulfillmentError):
            fulfill.transform("json_to_csv", raw, {"fields": ["id"]})
        actual, _, _ = fulfill.transform("json_to_csv", raw, {"fields": ["id", "name"], "missing_fields": "blank"})
        self.assertEqual(actual, b'id,name\\n001,\\n,B\\n')

if __name__ == "__main__":
    unittest.main()
'''
    return {"fulfill.py": source, "README.md": readme.encode("utf-8"), "test_contract.py": tests.encode("utf-8"),
            "contract.json": _json_bytes(contract), "package-spec.json": _json_bytes(spec), "input.data": example_input}, count, conformance["examples"]


def verify_manifest(manifest, job_root):
    """Verify an existing local artifact without any claim/payment side effects."""
    root = Path(job_root).resolve(strict=True)
    _require(manifest.get("engine_version") == ENGINE_VERSION and manifest.get("validation", {}).get("passed") is True,
             "Invalid manifest version or validation")
    digest = manifest.get("spec_sha256")
    _require(type(digest) is str and re.fullmatch("[a-f0-9]{64}", digest), "Invalid manifest spec digest")
    prefix = "artifacts/" + digest + "/"
    _require(type(manifest.get("artifacts")) is list and 4 <= len(manifest["artifacts"]) <= 10, "Invalid artifact inventory")
    names = [item.get("path") for item in manifest["artifacts"]]
    _require(all(type(name) is str and name.startswith(prefix) for name in names) and len(names) == len(set(names)),
             "Invalid artifact paths")
    spec = json.loads(_read_file(_confined(root, prefix + "specification.json"), MAX_SPEC_BYTES))
    _require(spec_digest(spec) == digest and spec["operation"] == manifest.get("operation") and spec["mode"] == manifest.get("mode"),
             "Manifest specification mismatch")
    if spec["mode"] == "source_package":
        expected = {"specification.json", "source-package.zip"} | {"package/" + x for x in
                    ["fulfill.py", "README.md", "test_contract.py", "contract.json", "package-spec.json", "input.data"]}
    else:
        output = ("output.jsonl" if spec.get("parameters", {}).get("json_format") == "ndjson" else "output.json") if spec["operation"] == "csv_to_json" else "output.csv"
        expected = {"specification.json", output, "rejected.jsonl", "summary.json"}
    _require(set(names) == {prefix + name for name in expected}, "Incomplete artifact inventory")
    for item in manifest["artifacts"]:
        file = _confined(root, item["path"])
        raw = _read_file(file, MAX_OUTPUT_BYTES)
        _require(len(raw) == item["size_bytes"] and sha256(raw) == item["sha256"], "Artifact integrity mismatch")
    return manifest


def execute_spec(spec, job_root):
    """Validate, execute, verify and commit an artifact set. Idempotent by full spec hash.

    Returns a manifest with relative paths and hashes. The caller must separately
    authorize assignment, bind the task/scope, publish artifacts and confirm payment.
    """
    digest = spec_digest(spec)
    given = Path(job_root)
    _require(not given.is_symlink() and given.is_dir(), "Job workspace must exist and not be a symlink")
    root = given.resolve(strict=True)
    output_root = _confined(root, "artifacts", must_exist=False)
    output_root.mkdir(exist_ok=True)
    final = _confined(root, "artifacts/" + digest, must_exist=False)
    if final.exists():
        manifest = json.loads(_read_file(_confined(root, "artifacts/" + digest + "/manifest.json"), MAX_SPEC_BYTES))
        _require(manifest.get("spec_sha256") == digest, "Existing manifest contract mismatch")
        return verify_manifest(manifest, root)
    metrics, checks = {}, ["strict_spec", "bounded_input", "no_network_or_model"]
    files = {"specification.json": _json_bytes(spec)}
    if spec["mode"] == "processed_data":
        source = _confined(root, spec["input"]["path"])
        raw = _read_file(source, MAX_INPUT_BYTES)
        _require(sha256(raw) == spec["input"]["sha256"], "Input digest mismatch")
        output, rejects, metrics = transform(spec["operation"], raw, spec.get("parameters"), spec.get("acceptance"))
        name = ("output.jsonl" if spec.get("parameters", {}).get("json_format") == "ndjson" else "output.json") if spec["operation"] == "csv_to_json" else "output.csv"
        files.update({name: output, "rejected.jsonl": rejects, "summary.json": _json_bytes(metrics)})
        checks += ["input_sha256", "round_trip_content", "row_accounting", "exact_acceptance"]
    else:
        package, count, conformance_count = _source_files(spec)
        files.update({"package/" + name: raw for name, raw in package.items()})
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, raw in sorted(package.items()):
                item = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
                item.external_attr = 0o100644 << 16
                item.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(item, raw)
        files["source-package.zip"] = stream.getvalue()
        with zipfile.ZipFile(io.BytesIO(files["source-package.zip"])) as archive:
            _require(set(archive.namelist()) == set(package) and all(archive.read(n) == raw for n, raw in package.items()),
                     "Source archive verification failed")
        metrics = {"conformance_examples": conformance_count, "contract_examples": count, "source_files": len(package)}
        checks += ["engine_conformance", "contract_examples", "zip_byte_identity"]
    _require(sum(map(len, files.values())) <= MAX_OUTPUT_BYTES, "Artifact set exceeds 32 MiB")
    artifacts = []
    for name, raw in sorted(files.items()):
        suffix = Path(name).suffix
        media = {".json": "application/json", ".csv": "text/csv", ".jsonl": "application/x-ndjson",
                 ".zip": "application/zip", ".py": "text/x-python", ".md": "text/markdown"}.get(suffix, "application/octet-stream")
        artifacts.append({"path": "artifacts/" + digest + "/" + name, "sha256": sha256(raw), "size_bytes": len(raw), "media_type": media})
    manifest = {"schema_version": 1, "engine_version": ENGINE_VERSION, "spec_sha256": digest,
                "mode": spec["mode"], "operation": spec["operation"], "artifacts": artifacts, "metrics": metrics,
                "validation": {"passed": True, "checks": checks, "limitations": ["No buyer approval, delivery or payment is implied."]}}
    stage = Path(tempfile.mkdtemp(prefix=".pending-", dir=output_root))
    try:
        for name, raw in {**files, "manifest.json": _json_bytes(manifest)}.items():
            destination = stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        stage.rename(final)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return verify_manifest(manifest, root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("spec")
    run.add_argument("--workspace", required=True)
    conv = sub.add_parser("convert")
    conv.add_argument("direction", choices=["json-to-csv", "csv-to-json"])
    conv.add_argument("input")
    conv.add_argument("output")
    conv.add_argument("--fields", nargs="+")
    conv.add_argument("--missing-fields", choices=["error", "blank"], default="error")
    conv.add_argument("--delimiter", choices=["auto", "comma", "semicolon", "tab", "pipe"], default="auto")
    conv.add_argument("--json-format", choices=["pretty", "compact", "ndjson"])
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            root = Path(args.workspace).resolve(strict=True)
            raw = _read_file(_confined(root, args.spec), MAX_SPEC_BYTES)
            spec = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object, parse_constant=_constant)
            print(json.dumps(execute_spec(spec, root), ensure_ascii=False))
        else:
            root = Path.cwd().resolve()
            source = _confined(root, args.input)
            dest = _confined(root, args.output, must_exist=False)
            _require(not dest.exists() and dest.parent.is_dir(), "Output must be new in an existing directory")
            p = {"missing_fields": args.missing_fields, "delimiter": {"auto": "auto", "comma": ",", "semicolon": ";", "tab": "\t", "pipe": "|"}[args.delimiter]}
            if args.fields:
                p["fields"] = args.fields
            if args.json_format:
                p["json_format"] = args.json_format
            result, _, _ = transform(args.direction.replace("-", "_"), _read_file(source, MAX_INPUT_BYTES), p)
            with dest.open("xb") as stream:
                stream.write(result)
        return 0
    except (FulfillmentError, OSError, UnicodeError, ValueError, TypeError, RecursionError) as error:
        parser.exit(2, "Fulfillment failed: " + str(error)[:200] + "\n")


if __name__ == "__main__":
    main()

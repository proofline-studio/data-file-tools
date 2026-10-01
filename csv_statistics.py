"""Bounded offline CSV statistics: median fill, IQR flags and min-max scaling.

Numeric columns are explicit. Other fields retain their original string values.
Only blank numeric cells are missing. Outliers are reported, never removed.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from fractions import Fraction
import io
import json
from pathlib import Path
import re
import threading

MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_ROWS = 100000
MAX_COLUMNS = 1024
MAX_CELLS = 1000000
MAX_FIELD_CHARS = 65536
MAX_OUTLIERS = 10000
NUMBER = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,3})?')
_CSV_LOCK = threading.RLock()


def require(condition, message):
    """Reject inputs outside the exact supported contract."""
    if not condition:
        raise ValueError(message)


def parse_number(text):
    """Return an exact rational for a finite, bounded decimal string."""
    value = text.strip()
    require(len(value) <= 64 and NUMBER.fullmatch(value) is not None,
            'Numeric cell must be a finite decimal; no inferred missing tokens')
    parsed = Decimal(value)
    require(parsed.is_finite() and len(parsed.as_tuple().digits) <= 28
            and -50 <= parsed.as_tuple().exponent <= 50,
            'Numeric precision/exponent limit exceeded')
    return Fraction(parsed)


def quantile(sorted_values, probability):
    """Compute the type-7 linearly interpolated quantile using exact rationals."""
    require(bool(sorted_values), 'Cannot fit statistics to an all-missing column')
    position = (len(sorted_values) - 1) * probability
    lower = position.numerator // position.denominator
    remainder = position - lower
    return (sorted_values[lower] * (1 - remainder)
            + sorted_values[min(lower + 1, len(sorted_values) - 1)] * remainder)


def decimal_text(value, places=None):
    """Serialize exact fitted values, or explicitly round scaled values to 12dp."""
    with localcontext() as context:
        context.prec = 256
        result = Decimal(value.numerator) / Decimal(value.denominator)
        if places is not None:
            result = result.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
        text = format(result, 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return '0' if text in {'-0', ''} else text


def _parse(text):
    require(type(text) is str, 'CSV input must be UTF-8 text')
    require(len(text.encode('utf-8')) <= MAX_INPUT_BYTES, 'CSV input byte limit exceeded')
    require('\0' not in text, 'NUL input is unsupported')
    with _CSV_LOCK:
        previous = csv.field_size_limit()
        try:
            csv.field_size_limit(MAX_FIELD_CHARS)
            reader = csv.reader(io.StringIO(text, newline=''), strict=True)
            header = next(reader, None)
            require(header is not None and 0 < len(header) <= MAX_COLUMNS,
                    'A bounded CSV header is required')
            require(all(name.strip() for name in header) and len(set(header)) == len(header),
                    'CSV headers must be unique and nonblank')
            rows = []
            for row in reader:
                require(len(row) == len(header), 'CSV row width differs from header')
                require(len(rows) < MAX_ROWS and (len(rows) + 1) * len(header) <= MAX_CELLS,
                        'CSV row/cell limit exceeded')
                rows.append(row)
            return header, rows
        except csv.Error as error:
            raise ValueError('Malformed CSV or field-size limit exceeded') from error
        finally:
            csv.field_size_limit(previous)


def serialize_audit(audit):
    """Use the exact bounded representation written by the CLI."""
    return json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'


def clean_csv(text, numeric_columns):
    """Return cleaned CSV plus complete statistics/outlier audit for explicit columns.

    Blank/whitespace-only selected cells use that column's observed median.
    Quartiles are fitted to observed cells before imputation. A value strictly
    outside Q1-1.5*IQR or Q3+1.5*IQR is flagged. No row is dropped or reordered.
    Selected values are min-max scaled; constant columns become 0. Normalized
    values round to 12 decimal places, half-even. All-missing columns are errors.
    Header-only input returns its header with zero rows and no fitted statistics.
    """
    require(type(numeric_columns) is list and 0 < len(numeric_columns) <= MAX_COLUMNS
            and all(type(name) is str and name for name in numeric_columns)
            and len(set(numeric_columns)) == len(numeric_columns),
            'Numeric columns must be a nonempty unique list of exact header names')
    header, rows = _parse(text)
    require(set(numeric_columns) <= set(header), 'Selected numeric column is absent')
    output = [list(row) for row in rows]
    audit = {'schema_version': 1, 'rows_read': len(rows), 'rows_written': len(rows),
             'numeric_columns': list(numeric_columns), 'columns': {}, 'outliers': [],
             'policies': {'missing': 'blank or whitespace-only selected cells',
                          'quantile': 'type-7 linear interpolation over observed cells',
                          'outlier': 'strictly outside 1.5 IQR fences; flag only',
                          'normalization': 'observed min/max after median fill; constant -> 0',
                          'normalized_rounding': '12 decimal places, half-even',
                          'nonselected_fields': 'original strings; row and header order preserved'}}
    for name in numeric_columns:
        index = header.index(name)
        if not rows:
            audit['columns'][name] = {'observed': 0, 'imputed': 0, 'outliers': 0,
                                     'median': None, 'q1': None, 'q3': None, 'iqr': None,
                                     'lower_fence': None, 'upper_fence': None,
                                     'minimum': None, 'maximum': None, 'constant': None}
            continue
        values = [None if not row[index].strip() else parse_number(row[index]) for row in rows]
        observed = sorted(value for value in values if value is not None)
        require(bool(observed), 'Cannot fit statistics to an all-missing column')
        median = quantile(observed, Fraction(1, 2))
        q1, q3 = quantile(observed, Fraction(1, 4)), quantile(observed, Fraction(3, 4))
        iqr = q3 - q1
        low, high = q1 - Fraction(3, 2) * iqr, q3 + Fraction(3, 2) * iqr
        minimum, maximum = observed[0], observed[-1]
        count = 0
        for row_index, value in enumerate(values):
            if value is not None and (value < low or value > high):
                require(len(audit['outliers']) < MAX_OUTLIERS, 'Complete outlier audit limit exceeded')
                audit['outliers'].append({'row': row_index + 1, 'column': name,
                                          'original': rows[row_index][index],
                                          'value': decimal_text(value)})
                count += 1
            filled = median if value is None else value
            normalized = Fraction(0) if maximum == minimum else (filled - minimum) / (maximum - minimum)
            output[row_index][index] = decimal_text(normalized, 12)
        audit['columns'][name] = {'observed': len(observed), 'imputed': len(values) - len(observed),
                                  'outliers': count, 'median': decimal_text(median),
                                  'q1': decimal_text(q1), 'q3': decimal_text(q3),
                                  'iqr': decimal_text(iqr), 'lower_fence': decimal_text(low),
                                  'upper_fence': decimal_text(high), 'minimum': decimal_text(minimum),
                                  'maximum': decimal_text(maximum), 'constant': minimum == maximum}
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(header)
    writer.writerows(output)
    cleaned = stream.getvalue()
    require(len(cleaned.encode('utf-8')) <= MAX_OUTPUT_BYTES, 'Clean CSV output limit exceeded')
    require(len(serialize_audit(audit).encode('utf-8')) <= MAX_OUTPUT_BYTES,
            'Audit output limit exceeded')
    return {'csv': cleaned, 'audit': audit}


def main(argv=None):
    """Run the offline CLI. Refuse to overwrite either output file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--columns', nargs='+', required=True, help='Exact numeric header names')
    parser.add_argument('--audit', type=Path, required=True)
    args = parser.parse_args(argv)
    require(args.input.is_file() and not args.input.is_symlink(), 'Input must be a regular local file')
    require(args.output.resolve() != args.audit.resolve(), 'CSV and audit output paths must differ')
    require(not args.output.exists() and not args.output.is_symlink()
            and not args.audit.exists() and not args.audit.is_symlink(), 'Output already exists')
    with args.input.open('rb') as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    require(len(raw) <= MAX_INPUT_BYTES, 'CSV input byte limit exceeded')
    result = clean_csv(raw.decode('utf-8-sig'), args.columns)
    # Calculate and validate both outputs before creating either file.
    with args.output.open('x', encoding='utf-8', newline='') as stream:
        stream.write(result['csv'])
    with args.audit.open('x', encoding='utf-8', newline='') as stream:
        stream.write(serialize_audit(result['audit']))
    print(json.dumps({'rows_read': result['audit']['rows_read'],
                      'rows_written': result['audit']['rows_written'],
                      'outlier_cells': len(result['audit']['outliers'])}))


if __name__ == '__main__':
    main()

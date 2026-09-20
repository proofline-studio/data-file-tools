"""Python 3.10+ CSV/JSON conversion. Standard library only; no network or model."""
import argparse
import csv
import io
import json
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 100000
MAX_COLUMNS = 1024
MAX_OUTPUT_CHARS = 16 * 1024 * 1024


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key[:80])
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError('Non-finite JSON number: ' + value)


def json_to_csv(text):
    rows = json.loads(text.lstrip('\ufeff'), object_pairs_hook=unique_object,
                      parse_constant=invalid_constant)
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError('Expected an array of at most 100000 flat objects')
    columns = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Each JSON row must be an object')
        for key, value in row.items():
            if not key or isinstance(value, (dict, list)):
                raise ValueError('Only nonempty keys and flat scalar values are supported')
            if isinstance(value, float) and (value != value or abs(value) == float('inf')):
                raise ValueError('Non-finite JSON numbers are unsupported')
            if key not in seen:
                columns.append(key)
                seen.add(key)
                if len(columns) > MAX_COLUMNS:
                    raise ValueError('Input exceeds 1024 columns')
    if rows and not columns:
        raise ValueError('Nonempty input requires at least one column')
    stream = io.StringIO(newline='')
    if not rows:
        return ''
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator='\n')
    writer.writeheader()
    for row in rows:
        writer.writerow({k: '' if v is None else json.dumps(v) if isinstance(v, bool) else v
                         for k, v in row.items()})
        if stream.tell() > MAX_OUTPUT_CHARS:
            raise ValueError('Output exceeds 16 million characters')
    return stream.getvalue()


def csv_to_json(text, delimiter='auto'):
    text = text.lstrip('\ufeff')
    if '\x00' in text or not text.strip():
        raise ValueError('CSV must have a header and cannot contain NUL bytes')
    if delimiter == 'auto':
        try:
            delimiter = csv.Sniffer().sniff(text[:65536], delimiters=',;\t|').delimiter
        except csv.Error:
            # A single column is valid; ambiguous multi-column input needs a flag.
            if any(c in text for c in ',;\t|'):
                raise ValueError('Cannot detect delimiter; supply --delimiter') from None
            delimiter = ','
    if delimiter not in ',;\t|':
        raise ValueError('Unsupported delimiter')
    reader = csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, strict=True)
    headers = next(reader)
    if len(headers) > MAX_COLUMNS:
        raise ValueError('Input exceeds 1024 columns')
    if any(not h.strip() for h in headers) or len(set(headers)) != len(headers):
        raise ValueError('Headers must be nonempty and unique')
    rows = []
    for fields in reader:
        if len(fields) != len(headers):
            raise ValueError('Row width differs from header width')
        if len(rows) >= MAX_ROWS:
            raise ValueError('CSV exceeds 100000 rows')
        rows.append(dict(zip(headers, fields)))
    stream = io.StringIO()
    for chunk in json.JSONEncoder(ensure_ascii=False, indent=2, allow_nan=False).iterencode(rows):
        stream.write(chunk)
        if stream.tell() > MAX_OUTPUT_CHARS:
            raise ValueError('Output exceeds 16 million characters')
    return stream.getvalue() + '\n'


def convert_file(direction, source, destination, delimiter='auto'):
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError('Input and output must differ')
    if source.stat().st_size > MAX_BYTES:
        raise ValueError('Input exceeds 10 MiB')
    with source.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Input exceeds 10 MiB')
    text = raw.decode('utf-8-sig')
    if direction == 'json-to-csv':
        result = json_to_csv(text)
    elif direction == 'csv-to-json':
        result = csv_to_json(text, delimiter)
    else:
        raise ValueError('Unsupported direction')
    if len(result.encode('utf-8')) > 64 * 1024 * 1024:
        raise ValueError('Output exceeds 64 MiB')
    # Exclusive creation avoids overwriting an existing file or symlink.
    with destination.open('x', encoding='utf-8', newline='') as stream:
        stream.write(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('direction', choices=['json-to-csv', 'csv-to-json'])
    parser.add_argument('input')
    parser.add_argument('output')
    parser.add_argument('--delimiter', choices=['auto', 'comma', 'semicolon', 'tab', 'pipe'], default='auto')
    args = parser.parse_args()
    delimiter = {'comma': ',', 'semicolon': ';', 'tab': '\t', 'pipe': '|', 'auto': 'auto'}[args.delimiter]
    try:
        convert_file(args.direction, args.input, args.output, delimiter)
    except (OSError, ValueError, UnicodeError, csv.Error, RecursionError) as error:
        parser.exit(2, 'Conversion failed: ' + str(error) + '\n')


if __name__ == '__main__':
    main()

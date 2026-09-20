# CSV and JSON conversion

Python 3.10 or newer. Conversion uses only the standard library and runs offline.

```sh
python convert.py json-to-csv input.json output.csv
python convert.py csv-to-json input.csv output.json
python convert.py csv-to-json input.tsv output.json --delimiter tab
```

JSON input must be an array of flat objects. Columns use the union of keys in
first-seen order. Missing keys and null values become empty CSV fields. Booleans
use lowercase true/false. CSV quoting preserves commas, quotes and embedded
newlines. Nested objects/arrays, duplicate keys and non-finite numbers are rejected.
An empty JSON array produces an empty file. Nonempty arrays need at least one key.

CSV has a header row and converts to an array of string-valued objects, preserving
leading zeros. UTF-8 and a UTF-8 BOM are supported. Automatic delimiter detection
considers comma, semicolon, tab and pipe, using at most 64 KiB. Specify a delimiter
for ambiguous input. Duplicate/empty headers and inconsistent row widths fail.
Header-only CSV produces an empty array. Input is limited to 10 MiB/100,000 rows/
1,024 columns; output to 16 million characters and 64 MiB. This is a file converter,
not a lossless JSON round-trip:
JSON scalar types/nulls become CSV text and cannot be inferred when converting back.

The output path must not exist. Errors produce a message on stderr and exit status2.
Values are preserved verbatim; treat CSV as data when opening in a spreadsheet.

Tests can run without dependencies:

```sh
python -m unittest discover -s . -p 'test_convert.py' -v
```

They also run with pytest if it is installed:

```sh
python -m pytest -q test_convert.py
```

Tests cover missing keys, quoting, Unicode, supported delimiters, malformed input,
field preservation, duplicate keys/headers, and prevention of output overwrites.

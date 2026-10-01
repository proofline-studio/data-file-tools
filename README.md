# Typed CSV conversion

Python 3.10+ standard-library convert.py with convert_text(text) and a file CLI. CSV headers must be unique/nonempty and include amount; every empty field becomes JSON null; nonempty amount uses finite float conversion; all other nonempty values remain strings. Output contains one object per row, total row_count and sample of the first three records. Reject malformed rows, invalid/nonfinite amounts, duplicate headers and input over 10 MiB/100000 rows/1024 columns/one million cells. No inferred extra field types.

Run `python convert.py input.csv output.json`; the file contains the JSON array, stdout contains row_count and sample. Run `python -m unittest discover -v`. No external dependencies.

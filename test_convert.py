import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from convert import convert_file, csv_to_json, json_to_csv


class ConversionTests(unittest.TestCase):
    def test_union_missing_keys_quotes_and_unicode(self):
        result = list(csv.reader(io.StringIO(json_to_csv('[{"a":"x,y","b":"line\\n界"},{"c":"say \\"hi\\""}]'))))
        self.assertEqual(result, [['a','b','c'], ['x,y','line\n界',''], ['','','say "hi"']])

    def test_scalars_and_null(self):
        self.assertEqual(json_to_csv('[{"n":null,"b":true,"i":2}]'), 'n,b,i\n,true,2\n')

    def test_empty_json(self):
        self.assertEqual(json_to_csv('[]'), '')

    def test_rejects_unsupported_json(self):
        for text in ['{}','[1]','[{"a":[]}]','[{"a":{}}]','[{"x":NaN}]',
                     '[{"x":1,"x":2}]','[{"":1}]','[{}]','[{"x":1e999}]']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                json_to_csv(text)

    def test_csv_strings_special_characters(self):
        text = '\ufeffid,note\n001,"quote ""yes"", \\ and 界\nnext"\n'
        self.assertEqual(json.loads(csv_to_json(text)), [{'id':'001','note':'quote "yes", \\ and 界\nnext'}])

    def test_delimiters(self):
        for delimiter in ',;\t|':
            text = delimiter.join(['a','b'])+'\n'+delimiter.join(['x','y'])+'\n'
            for flag in ['auto', delimiter]:
                self.assertEqual(json.loads(csv_to_json(text, flag)), [{'a':'x','b':'y'}])

    def test_single_column_and_header_only(self):
        self.assertEqual(json.loads(csv_to_json('name\nalice\n')), [{'name':'alice'}])
        self.assertEqual(json.loads(csv_to_json('a,b\n')), [])

    def test_rejects_bad_csv(self):
        for text in ['', 'a,a\n1,2\n', ',b\n1,2\n', 'a,b\n1\n', 'a,b\n1,2,3\n', 'a\n"never closed', 'a\n\x00']:
            with self.subTest(text=text), self.assertRaises((ValueError,csv.Error)):
                csv_to_json(text, ',')

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder)/'input.json', Path(folder)/'output.csv'
            source.write_text('[{"a":"b"}]')
            target.write_text('keep me')
            with self.assertRaises(FileExistsError):
                convert_file('json-to-csv', source, target)
            self.assertEqual(target.read_text(), 'keep me')
            with self.assertRaises(ValueError):
                convert_file('json-to-csv', source, source)

    def test_file_conversion_both_directions(self):
        with tempfile.TemporaryDirectory() as folder:
            source, middle, final = [Path(folder)/x for x in ['in.json','out.csv','out.json']]
            source.write_text('[{"id":"001","note":"界"}]', encoding='utf-8')
            convert_file('json-to-csv', source, middle)
            convert_file('csv-to-json', middle, final)
            self.assertEqual(json.loads(final.read_text(encoding='utf-8')), [{'id':'001','note':'界'}])

    def test_sparse_output_and_column_expansion_are_bounded(self):
        with patch('convert.MAX_OUTPUT_CHARS', 8):
            with self.assertRaises(ValueError):
                json_to_csv('[{"a":"123456789"}]')
            with self.assertRaises(ValueError):
                csv_to_json('a\n123456789\n')
        with patch('convert.MAX_COLUMNS', 1):
            with self.assertRaises(ValueError):
                json_to_csv('[{"a":1,"b":2}]')
            with self.assertRaises(ValueError):
                csv_to_json('a,b\n1,2\n')


if __name__ == '__main__':
    unittest.main()

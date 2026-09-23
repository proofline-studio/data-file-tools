import json
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
        for raw in [b'a,a\n1,2\n', b'a,b\n1\n', b'a,b\n"unterminated\n']:
            with self.subTest(raw=raw), self.assertRaises(fulfill.FulfillmentError):
                fulfill.transform("csv_to_json", raw, {"delimiter": ","})
    def test_missing_field_policy(self):
        raw = b'[{"id":"001"},{"name":"B"}]'
        with self.assertRaises(fulfill.FulfillmentError):
            fulfill.transform("json_to_csv", raw, {"fields": ["id"]})
        actual, _, _ = fulfill.transform("json_to_csv", raw, {"fields": ["id", "name"], "missing_fields": "blank"})
        self.assertEqual(actual, b'id,name\n001,\n,B\n')

if __name__ == "__main__":
    unittest.main()

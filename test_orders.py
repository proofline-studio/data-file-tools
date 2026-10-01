import unittest
from dedupe_orders import convert_text
class KeyOrderTests(unittest.TestCase):
    def test_exact_duplicate(self):
        value=convert_text('order_id,customer_email,amount\n1,a,2\n1,a,2\n')
        self.assertEqual(value['summary'],{'rows_read':2,'duplicates_removed':1,'missing_fields':0,'rows_written':1})
    def test_same_id_different_values_retains_first(self):
        value=convert_text('order_id,customer_email,amount,date\n1,a,2,2026-08-04\n1,b,3,2026-08-05\n')
        self.assertEqual(value['summary']['duplicates_removed'],1)
        self.assertEqual(value['records'],[{'order_id':'1','customer_email':'a','amount':'2','date':'2026-08-04'}])
    def test_first_valid_row_and_each_missing_row(self):
        value=convert_text('order_id,customer_email,amount\n1,,2\n1,a,3\n1,a,\n1,b,4\n')
        self.assertEqual(value['summary'],{'rows_read':4,'duplicates_removed':1,'missing_fields':2,'rows_written':1})
        self.assertEqual(value['records'][0]['amount'],'3');self.assertEqual([r['row_number'] for r in value['rejected']],[1,3])
    def test_case_and_whitespace_sensitive(self):
        value=convert_text('order_id,customer_email,amount\nA,a,2\na,b,2\n A,c,2\n')
        self.assertEqual(value['summary']['rows_written'],3)
    def test_empty(self):
        for text in ['', 'order_id,customer_email,amount\n']:
            self.assertEqual(convert_text(text)['summary'],dict.fromkeys(['rows_read','duplicates_removed','missing_fields','rows_written'],0))
    def test_invalid(self):
        for text in ['order_id,customer_email,amount\n1,a\n','id,amount\n1,2\n']:
            with self.assertRaises(ValueError):convert_text(text)
if __name__=='__main__':unittest.main()

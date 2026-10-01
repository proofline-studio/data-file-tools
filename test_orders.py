import unittest
from dedupe_orders import convert_text
class OrderTests(unittest.TestCase):
    def test_exact_duplicate(self):
        value=convert_text('order_id,customer_email,amount\n1,a@example.com,1.00\n1,a@example.com,1.00\n')
        self.assertEqual(value['summary'],{'rows_read':2,'duplicates_removed':1,'missing_fields':0,'rows_written':1})
    def test_missing_rows_preserved(self):
        value=convert_text('order_id,customer_email,amount\n1,,1.00\n2,b@example.com,\n3,c@example.com,0\n')
        self.assertEqual(value['summary'],{'rows_read':3,'duplicates_removed':0,'missing_fields':2,'rows_written':1})
        self.assertEqual(value['rejected'][0]['row']['amount'],'1.00');self.assertEqual(value['rejected'][1]['row_number'],2)
    def test_empty(self):
        for text in ['', 'order_id,customer_email,amount\n']:
            self.assertEqual(convert_text(text)['summary'],dict.fromkeys(['rows_read','duplicates_removed','missing_fields','rows_written'],0))
    def test_same_id_different_values_preserved(self):
        value=convert_text('order_id,customer_email,amount,date\n1,a,2,2026-08-04\n1,a,2,2026-08-05\n')
        self.assertEqual(value['summary']['rows_written'],2);self.assertEqual(value['summary']['duplicates_removed'],0)
    def test_invalid(self):
        for text in ['order_id,customer_email,amount\n1,a\n','id,amount\n1,2\n']:
            with self.assertRaises(ValueError):convert_text(text)
if __name__=='__main__':unittest.main()

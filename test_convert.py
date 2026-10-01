import unittest
from convert import convert_text
class TypedTests(unittest.TestCase):
    def test_exact_rules(self):
        got=convert_text('id,amount,note\n001,2.50,\n002,,hello\n003,-1.25,"a,b"\n004,0,last\n')
        self.assertEqual(got['row_count'],4);self.assertEqual(len(got['sample']),3)
        self.assertEqual(got['records'][0],{'id':'001','amount':2.5,'note':None})
        self.assertIsNone(got['records'][1]['amount']);self.assertEqual(got['records'][2]['note'],'a,b')
    def test_invalid(self):
        for value in ['id,amount\nx,nan\n','id,amount\nx,inf\n','id,amount\nx,bad\n','id,amount\nx,2,extra\n','id,id,amount\nx,y,1\n','id\nx\n']:
            with self.subTest(value=value),self.assertRaises(ValueError):convert_text(value)
if __name__=='__main__':unittest.main()

import datetime, email.message, io, json, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import dail_board_summary as m

class Response(io.BytesIO):
    def __init__(self,raw,status=200,declared=None,ctype='application/json',url=m.URL):
        super().__init__(raw);self.status=status;self.headers=email.message.Message();self.headers['Content-Type']=ctype;self.url=url
        if declared is not None:self.headers['Content-Length']=declared
    def geturl(self):return self.url
class Opener:
    def __init__(self,response):self.response=response;self.calls=[]
    def open(self,request,timeout):self.calls.append((request,timeout));return self.response
class Tests(unittest.TestCase):
    def raw(self):return json.dumps({'bounties':[{'id':'a','status':'open','reward':25},{'id':'b','status':'claimed','reward':20},{'id':'c','status':'open','reward':4}]}).encode()
    def test_transform_counts_and_totals(self):
        x=m.transform(self.raw());self.assertEqual(x['bounty_count'],3);self.assertEqual(x['by_status']['open'],{'count':2,'posted_reward_dail':29});self.assertEqual(x['by_status']['completed']['count'],0)
    def test_fetch_transform_store_end_to_end(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('PROOFLINE_TEST_ROOT')) as d:
            p=Path(d)/'out.json';o=Opener(Response(self.raw()));x=m.run(p,opener=o,now=datetime.datetime(2026,10,8,tzinfo=datetime.timezone.utc))
            self.assertEqual(json.loads(p.read_text()),x);self.assertEqual(len(o.calls),1);self.assertEqual(o.calls[0][0].full_url,m.URL);self.assertNotIn('Authorization',o.calls[0][0].headers)
    def test_invalid_schemas(self):
        for raw in [b'{}',b'[]',b'{"bounties":[{"id":"a","status":"new","reward":1}]}',b'{"bounties":[{"id":"a","status":"open","reward":true}]}',b'{"bounties":[],"bounties":[]}',b'{"bounties":[NaN]}',b'\xff',b'{']:
            with self.subTest(raw=raw),self.assertRaises(m.BoardError):m.transform(raw)
    def test_duplicate_ids_rejected(self):
        with self.assertRaises(m.BoardError):m.transform(b'{"bounties":[{"id":"a","status":"open","reward":1},{"id":"a","status":"open","reward":1}]}')
    def test_unhashable_status_has_schema_error(self):
        for status in ([],{},None,42):
            with self.subTest(status=status),self.assertRaisesRegex(m.BoardError,'row_schema'):
                m.transform(json.dumps({'bounties':[{'id':'a','status':status,'reward':1}]}).encode())
    def test_overlong_numbers_have_controlled_errors(self):
        with self.assertRaises(m.BoardError):m.transform(('{"bounties":[{"id":"a","status":"open","reward":'+'9'*5000+'}]}').encode())
        with self.assertRaisesRegex(m.BoardError,'content_length'):m.fetch(Opener(Response(b'{}',declared='9'*5000)))
    def test_network_errors_and_bounds(self):
        for r in [Response(self.raw(),status=503),Response(self.raw(),ctype='text/html'),Response(self.raw(),url='https://example.org'),Response(self.raw(),declared='999'),Response(self.raw(),declared='-1'),Response(b'x'*(m.MAX_BYTES+1))]:
            with self.subTest(r=r),self.assertRaises(m.BoardError):m.fetch(Opener(r))
        with patch.object(Opener,'open',side_effect=TimeoutError()),self.assertRaises(m.BoardError):m.fetch(Opener(None))
    def test_redirect_never_followed(self):
        with self.assertRaises(m.BoardError):m.NoRedirect().redirect_request(None,None,302,'',{},'http://127.0.0.1')
    def test_failed_transform_preserves_existing_output(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('PROOFLINE_TEST_ROOT')) as d:
            p=Path(d)/'out.json';p.write_bytes(b'old')
            with self.assertRaises(m.BoardError):m.run(p,opener=Opener(Response(b'bad')))
            self.assertEqual(p.read_bytes(),b'old')
    def test_failed_atomic_replace_preserves_output_and_cleans_temp(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('PROOFLINE_TEST_ROOT')) as d:
            p=Path(d)/'out.json';p.write_bytes(b'old')
            with patch.object(m.os,'replace',side_effect=OSError()),self.assertRaises(OSError):m.store({},p)
            self.assertEqual(p.read_bytes(),b'old');self.assertEqual([x.name for x in Path(d).iterdir()],['out.json'])
if __name__=='__main__':unittest.main()

"""Finite first-valid-row order_id deduplication; exact case-sensitive keys."""
import argparse
import csv
import io
import json
from pathlib import Path
def convert_text(text):
    previous=csv.field_size_limit(10485760)
    try:return _convert_text(text)
    finally:csv.field_size_limit(previous)

def _convert_text(text):
    if type(text) is not str or len(text.encode('utf-8'))>10485760:raise ValueError('input byte limit')
    reader=csv.reader(io.StringIO(text.lstrip('\ufeff')),strict=True);headers=next(reader,None)
    summary={'rows_read':0,'duplicates_removed':0,'missing_fields':0,'rows_written':0}
    if headers is None:return {'records':[],'rejected':[],'summary':summary}
    if not headers or len(headers)>1024 or any(not h for h in headers) or len(set(headers))!=len(headers) or not {'order_id','customer_email','amount'}<=set(headers):raise ValueError('unique required headers missing')
    records=[];rejected=[];seen=set()
    for number,cells in enumerate(reader,1):
        if len(cells)!=len(headers) or number>100000 or number*len(headers)>1000000:raise ValueError('row shape/limit')
        row=dict(zip(headers,cells));summary['rows_read']+=1
        missing=[k for k in ('order_id','customer_email','amount') if not row[k].strip()]
        if missing:
            rejected.append({'row_number':number,'reason':'missing:'+','.join(missing),'row':row});summary['missing_fields']+=1;continue
        key=row['order_id']
        if key in seen:
            summary['duplicates_removed']+=1;continue
        seen.add(key)
        records.append(row)
    summary['rows_written']=len(records)
    return {'records':records,'rejected':rejected,'summary':summary}
def main():
    p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('output');args=p.parse_args()
    raw=Path(args.input).read_bytes()
    if len(raw)>10485760:raise ValueError('input byte limit')
    result=convert_text(raw.decode('utf-8-sig'))
    with Path(args.output).open('x',encoding='utf-8') as out:json.dump(result,out,ensure_ascii=False,indent=2)
    print(json.dumps(result['summary']))
if __name__=='__main__':main()

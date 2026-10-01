"""Bounded stdlib CSV conversion: named amount float, empty string null."""
import argparse
import csv
import io
import json
import math
from pathlib import Path
def convert_text(text):
    previous=csv.field_size_limit(10485760)
    try:return _convert_text(text)
    finally:csv.field_size_limit(previous)

def _convert_text(text):
    if type(text) is not str or len(text.encode('utf-8'))>10485760:raise ValueError('input byte limit')
    reader=csv.reader(io.StringIO(text.lstrip('\ufeff')),strict=True)
    headers=next(reader,None)
    if not headers or len(headers)>1024 or any(not h for h in headers) or len(set(headers))!=len(headers) or 'amount' not in headers:raise ValueError('unique headers including amount required')
    records=[]
    for cells in reader:
        if len(cells)!=len(headers) or len(records)>=100000 or (len(records)+1)*len(headers)>1000000:raise ValueError('row shape/limit')
        row={k:(None if v=='' else v) for k,v in zip(headers,cells)}
        if row['amount'] is not None:
            row['amount']=float(row['amount'])
            if not math.isfinite(row['amount']):raise ValueError('nonfinite amount')
        records.append(row)
    return {'records':records,'row_count':len(records),'sample':records[:3]}
def main():
    p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('output');args=p.parse_args()
    raw=Path(args.input).read_bytes()
    if len(raw)>10485760:raise ValueError('input byte limit')
    result=convert_text(raw.decode('utf-8-sig'))
    with Path(args.output).open('x',encoding='utf-8') as out:json.dump(result['records'],out,ensure_ascii=False,allow_nan=False,indent=2)
    print(json.dumps({'row_count':result['row_count'],'sample':result['sample']},ensure_ascii=False,allow_nan=False))
if __name__=='__main__':main()

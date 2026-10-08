"""One public REST request, strict transformation, atomic local JSON storage."""
from __future__ import annotations
import argparse, collections, datetime, http.client, json, os, socket, tempfile, urllib.error, urllib.request
from pathlib import Path

URL = 'https://dail-3dci.onrender.com/world/bounties'
MAX_BYTES = 1_000_000
STATES = {'open', 'claimed', 'completed', 'cancelled'}

class BoardError(ValueError): pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BoardError('redirect_rejected')

def unique(pairs):
    result = {}
    for k, v in pairs:
        if k in result: raise BoardError('duplicate_json_key')
        result[k] = v
    return result

def transform(raw: bytes):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES: raise BoardError('body_limit')
    try: data = json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(BoardError('nonfinite_json')))
    except (UnicodeError, ValueError, RecursionError) as e: raise BoardError('invalid_json') from e
    if not isinstance(data, dict) or not isinstance(data.get('bounties'), list) or len(data['bounties']) > 10000:
        raise BoardError('board_schema')
    counts = collections.Counter(); totals = collections.Counter(); seen = set()
    for row in data['bounties']:
        if not isinstance(row, dict): raise BoardError('row_schema')
        ident, status, reward = row.get('id'), row.get('status'), row.get('reward')
        if (not isinstance(ident,str) or not 1 <= len(ident) <= 100 or ident in seen
            or not isinstance(status,str) or status not in STATES or type(reward) is not int or not 0 <= reward <= 10**12):
            raise BoardError('row_schema')
        seen.add(ident); counts[status] += 1; totals[status] += reward
    return {'schema_version': 1, 'source_url': URL, 'bounty_count': len(seen),
            'by_status': {s:{'count':counts[s], 'posted_reward_dail':totals[s]} for s in sorted(STATES)},
            'reward_basis': 'Posted gross platform-credit amounts; not verified escrow, settlement or cash.'}

def fetch(opener=None):
    opener = opener or urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(URL, headers={'Accept':'application/json', 'User-Agent':'Proofline-DAiL-Board-Summary/1.0'})
    try:
        with opener.open(request, timeout=15) as response:
            if response.status != 200 or response.geturl() != URL: raise BoardError('http_status_or_redirect')
            if response.headers.get_content_type() != 'application/json': raise BoardError('content_type')
            declared = response.headers.get('Content-Length')
            if declared is not None and (not 1 <= len(declared) <= 7 or not declared.isascii() or not declared.isdecimal() or int(declared) > MAX_BYTES): raise BoardError('content_length')
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES: raise BoardError('body_limit')
            if declared is not None and len(raw) != int(declared): raise BoardError('incomplete_body')
            return raw
    except (urllib.error.URLError, http.client.HTTPException, socket.timeout, OSError) as e:
        raise BoardError('public_request_failed') from e

def store(result, output):
    output = Path(output)
    raw = (json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')
    # Temporary file is in the destination directory so replacement is atomic.
    fd, name = tempfile.mkstemp(prefix='.'+output.name+'-', dir=output.parent)
    try:
        with os.fdopen(fd,'wb') as stream: stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, output)
    finally:
        if os.path.exists(name): os.unlink(name)

def run(output, *, opener=None, now=None):
    result=transform(fetch(opener))
    result['retrieved_at']=(now or datetime.datetime.now(datetime.timezone.utc)).isoformat()
    store(result, output)
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='JSON file in an existing local directory')
    args=parser.parse_args()
    try: result=run(args.output)
    except (BoardError, OSError) as e: parser.exit(2, 'Board summary failed: '+(str(e) if isinstance(e,BoardError) else 'local_storage_failed')+'\n')
    print('Stored '+str(result['bounty_count'])+' bounty observations.')

if __name__=='__main__': main()

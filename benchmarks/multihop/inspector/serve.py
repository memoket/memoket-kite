"""Serve the exported inspector on localhost only; no model calls."""
import argparse
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import shutil
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'artifacts/visualizations/wiki2-source-inspector'
p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8766);args=p.parse_args()
if not (OUT/'data/index.json').exists():raise SystemExit('Run export.py first')
for name in ('index.html','style.css','app.js','indexing.html','indexing.js'):shutil.copyfile(Path(__file__).parent/name,OUT/name)
server=ThreadingHTTPServer(('127.0.0.1',args.port),partial(SimpleHTTPRequestHandler,directory=str(OUT)))
print(f'Inspector: http://127.0.0.1:{args.port}/',flush=True)
server.serve_forever()

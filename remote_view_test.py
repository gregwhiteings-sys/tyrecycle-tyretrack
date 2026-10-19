"""Exercise the read-only HTTP service without camera or AI dependencies."""
import json
from pathlib import Path
import sqlite3
import tempfile
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from remote_view import Viewer
from no_count_replies import SCHEMA
with tempfile.TemporaryDirectory() as folder:
    db = Path(folder)/'test.db'
    with sqlite3.connect(db) as con:
        con.execute(SCHEMA)
        con.execute("INSERT INTO supervisor_no_count(incident_id,started_brisbane,threshold_brisbane,status,reason,supervisor,comments,emailed_utc) VALUES (?,?,?,?,?,?,?,?)", ('NC-1','2026-10-19 05:00','2026-10-19 05:10','COMPLETED','Maintenance','Sam','<script>alert(1)</script>','now'))
    before=db.read_bytes()
    v=Viewer(db,port=0)
    try:
        base='http://127.0.0.1:'+str(v.server.server_port)
        assert v.server.server_address[0]=='127.0.0.1'
        v.publish({'Today':'42','Status':'TEST MODE'},'2026-10-19 12:00',b'jpeg-test')
        with urlopen(base+'/api/status') as r:
            data=json.load(r)
        assert data['cards']['Today']=='42'
        assert data['reports'][0][5]=='Maintenance'
        with urlopen(base+'/camera.jpg') as r: assert r.read()==b'jpeg-test'
        with urlopen(base) as r:
            page=r.read().decode()
            assert 'textContent' in page and 'innerHTML' not in page
            assert 'STALE' in page
        for path in ['/email_config.ini','/../tyretrack.db']:
            try: urlopen(base+path); raise AssertionError('Unexpected access')
            except HTTPError as e: assert e.code==404
        try: urlopen(Request(base+'/api/status', data=b'{}', method='POST')); raise AssertionError('Write allowed')
        except HTTPError as e: assert e.code==501
        assert db.read_bytes()==before, 'Viewer modified the database'
    finally: v.close()
print('PASS: HTTP counters, Gmail report rows, preview, private binding, no write routes, no database changes')

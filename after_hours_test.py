"""After-hours bypass, overnight handover and test DB isolation. No camera needed."""
import ast,sqlite3,tempfile,time
from pathlib import Path
from datetime import datetime,timedelta,timezone
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import Mock
from contextlib import ExitStack
code=ast.parse(Path(__file__).with_name('TyreTrack_Production_Monitor.py').read_text())
names={'oak_session_allowed','log_count','log_oak_test_count','tyre_class_name'}
functions=[n for n in code.body if isinstance(n,ast.FunctionDef) and n.name in names]
main=next(n for n in code.body if isinstance(n,ast.ClassDef) and n.name=='Main')
functions += [n for n in main.body if isinstance(n,ast.FunctionDef) and n.name=='schedule_tick']
zone=timezone(timedelta(hours=10));now=datetime(2026,10,15,22,30,tzinfo=zone)
ns=dict(oak_in_operating_hours=lambda:5<=now.hour<17,brisbane_now=lambda:now,
        time=time,datetime=datetime,OAK_NAME='production',OAK_TEST_NAME='manual_test',
        get_state=lambda k,d='':d)
exec(compile(ast.Module(body=functions,type_ignores=[]),'<helpers>','exec'),ns)
assert not ns['oak_session_allowed'](False)
assert ns['oak_session_allowed'](True)
worker=SimpleNamespace(source_kind='oak',manual_test=True,
                       manual_test_end=now.replace(day=16,hour=5,minute=0),isRunning=lambda:True)
ui=SimpleNamespace(closing=False,auto_schedule=SimpleNamespace(isChecked=lambda:True),worker=worker,
                   auto_retry_at=100,stop=Mock(),start=Mock(),source_combo=SimpleNamespace(setCurrentText=Mock()))
ns['schedule_tick'](ui);ui.stop.assert_not_called()
now=now.replace(day=16,hour=5,minute=0)
ns['schedule_tick'](ui);ui.stop.assert_called_once_with(scheduled=True)
assert ui.auto_retry_at==0
ui.worker=None;ns['schedule_tick'](ui);ui.start.assert_called_once_with(automatic=True)
print('PASS: manual bypass at night, no early stop, next-05:00 handover and automatic production restart')
with tempfile.TemporaryDirectory() as folder, ExitStack() as connections:
 def connect(*a,**kw):
  c=sqlite3.connect(*a,**kw);connections.callback(c.close);return c
 ns.update(DATA=Path(folder),DB=Path(folder)/'production.db',sqlite3=SimpleNamespace(connect=connect))
 with connect(ns['DB']) as con:
  con.execute('CREATE TABLE counts(id INTEGER PRIMARY KEY,ts TEXT,source TEXT,track_id INTEGER,confidence REAL,tyre_type TEXT,class_confidence REAL,metric_utc REAL)')
 ns['log_count']('manual_test',1,.9,'passenger',.8)
 with connect(ns['DB']) as con:assert con.execute('SELECT COUNT(*) FROM counts').fetchone()[0]==0
 with connect(Path(folder)/'oak_manual_tests.db') as con:assert con.execute('SELECT COUNT(*) FROM counts').fetchone()[0]==1
 ns['log_count']('production',2,.9,'passenger',.8)
 with connect(ns['DB']) as con:assert con.execute('SELECT COUNT(*) FROM counts').fetchone()[0]==1
print('PASS: manual counts saved in separate test DB; production writes unchanged')

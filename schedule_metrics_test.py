"""Offline schedule and metric checks. No camera, SMTP, or production data."""
import ast
import sqlite3
import tempfile
from contextlib import ExitStack
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

code = ast.parse(Path(__file__).with_name('TyreTrack_Production_Monitor.py').read_text())
names = {'production_snapshot', 'best_daily_record', 'oak_in_operating_hours'}
functions = [n for n in code.body if isinstance(n, ast.FunctionDef) and n.name in names]
main = next(n for n in code.body if isinstance(n, ast.ClassDef) and n.name == 'Main')
functions += [n for n in main.body if isinstance(n, ast.FunctionDef) and n.name == 'schedule_tick']
from hourly_dashboard import hourly_snapshot
zone = timezone(timedelta(hours=10))
now = datetime(2026, 10, 15, 5, tzinfo=zone)
ns = dict(sqlite3=sqlite3, datetime=datetime, timedelta=timedelta, timezone=timezone,
          defaultdict=defaultdict, hourly_snapshot=hourly_snapshot,
          brisbane_now=lambda: now, OAK_NAME='oak')
exec(compile(ast.Module(body=functions, type_ignores=[]), '<helpers>', 'exec'), ns)
# SQLite's transaction context commits/rolls back but does not close the handle.
# Close every test connection before TemporaryDirectory removes test.db on Windows.
with tempfile.TemporaryDirectory() as folder, ExitStack() as connections:
    def tracked_connect(*args, **kwargs):
        connection = sqlite3.connect(*args, **kwargs)
        connections.callback(connection.close)
        return connection

    ns['sqlite3'] = SimpleNamespace(connect=tracked_connect)
    ns['DB'] = Path(folder)/'test.db'
    with tracked_connect(ns['DB']) as con:
        con.execute('CREATE TABLE counts(id INTEGER PRIMARY KEY, ts TEXT, metric_utc REAL, source TEXT)')
        for day, hour, minute, source in [(14,5,1,'oak'),(14,5,2,'oak'),(14,18,0,'oak'),(15,5,1,'video')]:
            t = now.replace(day=day,hour=hour,minute=minute)
            con.execute('INSERT INTO counts(ts,metric_utc,source) VALUES(?,?,?)',(t.isoformat(),t.timestamp(),source))
    assert ns['best_daily_record']() == ('2026-10-14',2)
    now = now.replace(hour=5,minute=9,second=59)
    assert ns['production_snapshot']()['alert_times'] == []
    now = now.replace(minute=10,second=0)
    assert ns['production_snapshot']()['alert_times'] == [now]
    print('PASS: best daily excludes tests and out-of-shift counts; alert timestamp at 05:10')

state = {}
ns['get_state'] = lambda k,d='':state.get(k,d)
ns['time'] = SimpleNamespace(monotonic=lambda:100)
active = True
ns['oak_in_operating_hours'] = lambda:active
ui = SimpleNamespace(closing=False, auto_schedule=SimpleNamespace(isChecked=lambda:True),
                     auto_retry_at=0, worker=None, source_combo=SimpleNamespace(setCurrentText=Mock()),
                     start=Mock(), stop=Mock())
ns['schedule_tick'](ui)
ui.start.assert_called_once_with(automatic=True)
ns['schedule_tick'](ui)
assert ui.start.call_count == 1
ui.auto_retry_at=0
state['auto_paused_day']=now.date().isoformat()
ns['schedule_tick'](ui)
assert ui.start.call_count == 1
state.clear()
ui.worker=SimpleNamespace(isRunning=lambda:True,source_kind='video')
ns['schedule_tick'](ui)
assert ui.start.call_count == 1
active=False
ui.worker.source_kind='oak'
ns['schedule_tick'](ui)
ui.stop.assert_called_once_with(scheduled=True)
print('PASS: auto start, retry throttle, manual pause, video isolation, scheduled stop')

# Exercise the actual shift boundary helper using a controlled clock.
class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return now.astimezone(tz) if tz else now
ns['datetime']=Clock
boundary=next(n for n in functions if n.name=='oak_in_operating_hours')
exec(compile(ast.Module(body=[boundary], type_ignores=[]),'<boundary>','exec'),ns)
for hour, minute, second, expected in [(4,59,59,False),(5,0,0,True),(17,4,59,True),(17,5,0,False)]:
    now=now.replace(hour=hour,minute=minute,second=second)
    assert ns['oak_in_operating_hours']() is expected
print('PASS: 04:59:59 / 05:00 / 17:04:59 / 17:05 Brisbane boundaries')

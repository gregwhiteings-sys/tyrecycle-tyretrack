"""Startup migration and real Brisbane boundary/retry/stop behavior; no camera."""
import os,time
os.environ['QT_QPA_PLATFORM']='offscreen'
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import Mock
from PyQt5.QtWidgets import QApplication
import TyreTrack_Production_Monitor as m
zone=timezone(timedelta(hours=10))
def at(h,minute=0,second=0):return datetime(2026,10,19,h,minute,second,tzinfo=zone)
for stamp,expected in [(at(4,59,59),False),(at(5),True),(at(17,4,59),True),(at(17,5),False),(at(23),False)]:
 assert m.oak_in_operating_hours(stamp)==expected,(stamp,expected)
qt=QApplication.instance() or QApplication([])
m.Main.tick=lambda self:None
m.set_state('auto_schedule','0');m.set_state('auto_paused_day','2026-10-17')
w=m.Main();w.clock.stop()
assert w.auto_schedule.isChecked() and m.get_state('auto_schedule')=='1'
assert m.get_state('auto_paused_day','')==''
w.start=Mock();w.stop=Mock()
m.brisbane_now=lambda:at(4,59,59)
w.schedule_tick();w.start.assert_not_called()
m.brisbane_now=lambda:at(5)
w.schedule_tick();w.start.assert_called_once_with(automatic=True)
assert w.source_combo.currentText()==m.OAK_NAME
w.schedule_tick();assert w.start.call_count==1,'Retry throttled for 60 seconds'
w.auto_retry_at=time.monotonic()-1;w.schedule_tick();assert w.start.call_count==2
w.worker=SimpleNamespace(isRunning=lambda:True,source_kind='oak',manual_test=False)
m.brisbane_now=lambda:at(17,4,59);w.schedule_tick();w.stop.assert_not_called()
m.brisbane_now=lambda:at(17,5);w.schedule_tick();w.stop.assert_called_once_with(scheduled=True)
w.worker=None;w.start.reset_mock();w.auto_retry_at=0
m.brisbane_now=lambda:at(23);w.schedule_tick();w.start.assert_not_called()
m.brisbane_now=lambda:at(8);w.schedule_tick();w.start.assert_called_once_with(automatic=True)
w.worker=None;w._confirm_admin_pin=lambda action:True;w.close()
print('PASS: startup overrides saved Auto-off/pause; 05:00 inclusive and 17:05 exclusive Brisbane; no early/late starts; immediate in-shift start; 60-second retry; automatic stop')

assert not m.oak_in_operating_hours(datetime(2026,10,17,10,tzinfo=zone))
assert not m.oak_in_operating_hours(datetime(2026,10,18,10,tzinfo=zone))
print('PASS: Saturday and Sunday are excluded')

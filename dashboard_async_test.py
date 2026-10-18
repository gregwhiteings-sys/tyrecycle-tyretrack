"""Verify real dashboard collection stays off the GUI thread and applies the result."""
import os,sys,types,time,tempfile,threading,sqlite3
os.environ['QT_QPA_PLATFORM']='offscreen'
stub=types.ModuleType('ultralytics');stub.YOLO=object;sys.modules['ultralytics']=stub
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer
import TyreTrack_Production_Monitor as m
m.Main._confirm_admin_pin=lambda self,action:True
qt=QApplication([]);m.Main.tick=lambda self:None
entered=threading.Event();release=threading.Event();thread_ids=[]
original=m.production_snapshot
window=None
with tempfile.TemporaryDirectory() as temp:
 from pathlib import Path
 m.DB=Path(temp)/'dashboard.db';m.init_db()
 def slow_snapshot(*a,**k):
  thread_ids.append(threading.get_ident());entered.set()
  assert release.wait(3)
  return original(*a,**k)
 m.production_snapshot=slow_snapshot
 try:
  window=m.Main();window.clock.stop();assert entered.wait(2)
  fired=[];QTimer.singleShot(0,lambda:fired.append(True));qt.processEvents()
  assert fired and window._dashboard_cache is None, 'GUI must remain responsive during blocked query'
  release.set();deadline=time.monotonic()+3
  while window._dashboard_cache is None and time.monotonic()<deadline:qt.processEvents();time.sleep(.005)
  assert window._dashboard_cache is not None
  assert window.hour_lbl.text()==str(window._dashboard_cache['metrics']['current_hour'])
  assert thread_ids and all(i!=threading.get_ident() for i in thread_ids)
  window.close();qt.processEvents()
 finally:
  release.set()
  if window and hasattr(window,'_dashboard_loader'):window._dashboard_loader.wait(3)
  m.production_snapshot=original
 # sqlite context managers commit but don't close; force finalizers before Windows temp cleanup.
 import gc;gc.collect()
print('PASS: background database snapshot, responsive GUI during slow read, snapshot applied to counters and clean close')

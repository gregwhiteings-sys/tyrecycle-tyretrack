"""Exercise real counter worker + foreign alarm and authenticated GUI reset with fake packets."""
import os,time,tempfile
os.environ['QT_QPA_PLATFORM']='offscreen'
import numpy as np
from types import SimpleNamespace
from pathlib import Path
from PyQt5.QtWidgets import QApplication
import TyreTrack_Production_Monitor as m
m.Main._confirm_admin_pin=lambda self,action:True
qt=QApplication.instance() or QApplication([])
m.log_count=lambda *args:None;m.oak_session_allowed=lambda manual:True
m.YOLO=lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('OAK path must not instantiate CPU YOLO'))
def raw(kind,cl=0):
 if kind=='tyre':return np.zeros(18144,np.float32)
 out={f'output{i}_yolov6r2':np.zeros((1,7,s,s),np.float32) for i,s in enumerate((52,26,13),1)}
 out['output1_yolov6r2'][0,:,26,26]=[5,5,5,5,.99,.99 if cl==0 else .01,.99 if cl==1 else .01]
 return out
for class_id in (0,1):
 class Source:
  def __init__(self,ip,foreign_enabled=False):self.index=0;self.last_read_frame_at=time.monotonic();assert foreign_enabled
  def read(self):
   if self.index==6:worker.running=False;return False,None,None
   self.index+=1;self.last_read_neural_output=raw('tyre');self.last_read_frame_at=time.monotonic()
   return True,np.zeros((270,480,3),np.uint8),self.index
  def read_foreign(self):return self.index,np.zeros((270,480,3),np.uint8),raw('foreign',class_id),time.monotonic()
  def release(self):pass
 m.OakSource=Source
 worker=m.CounterWorker('oak','','Bottom to Top',.35,.5);worker.foreign_enabled=True;worker.foreign_every=20
 events=[];statuses=[];worker.foreign_event.connect(events.append);worker.status.connect(statuses.append)
 worker.run()
 assert len(events)==1 and events[0]['class_id']==class_id,(events,statuses)
 assert worker.foreign_latched and not worker.foreign_failed
 assert not any(s.startswith('ERROR') for s in statuses),statuses
print('PASS: real OAK counter worker uses compiled foreign outputs without CPU YOLO; each class triggers once; OAK ignores PC-only frame interval')
# Reset must remain authenticated and persistent; no actual alarm sound in this test.
m.Main.tick=lambda self:None
with tempfile.TemporaryDirectory() as tmp:
 window=m.Main();window.clock.stop();window.alarm_timer.stop() if hasattr(window,'alarm_timer') else None
 window.alarm_state_path=Path(tmp)/'state.json';window.admin_path=Path(tmp)/'admin.json'
 window.alarm_state={'latched':True};window.worker=None
 window.ask_admin=lambda:False
 m.set_password(window.admin_path,'testpass')
 m.write_json(window.alarm_state_path,window.alarm_state)
 window.reset_foreign_alarm();assert window.alarm_state['latched']
 window.ask_admin=lambda:True;window.stop_alarm_sound=lambda:None
 window.reset_foreign_alarm();assert not window.alarm_state['latched']
 assert not m.read_json(window.alarm_state_path,{})['latched']
 window.close()
print('PASS: actual GUI reset denies unauthenticated reset; authenticated reset persists cleared latch')

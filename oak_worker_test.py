"""Run the real OAK counting worker with synthetic device packets, without camera or DB writes."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import numpy as np,time
from PyQt5.QtCore import QCoreApplication
import TyreTrack_Production_Monitor as m
qt=QCoreApplication.instance() or QCoreApplication([])
logged=[];statuses=[]
m.log_count=lambda *args:logged.append(args)
m.oak_session_allowed=lambda manual:True
m.YOLO=lambda *a,**k:(_ for _ in ()).throw(AssertionError('OAK must not load CPU tyre YOLO'))
ys=list(range(250,95,-5))+list(range(95,251,5))
class SimulatedOak:
 def __init__(self,ip,foreign_enabled=False):self.index=0;self.last_read_frame_at=time.monotonic()
 def read(self):
  if self.index==len(ys):worker.running=False;return False,None,None
  raw=np.zeros((6,3024),dtype=np.float32);raw[:,0]=[192,ys[self.index],80,60,.95,.01]
  self.last_read_neural_output=raw.ravel();self.index+=1;self.last_read_frame_at=time.monotonic()
  return True,np.zeros((270,480,3),dtype=np.uint8),self.index
 def release(self):pass
m.OakSource=SimulatedOak
worker=m.CounterWorker('oak','','Bottom to Top',.35,.5)
counted_frames=[]
worker.counted.connect(lambda *args:counted_frames.append(worker.processing_frame))
worker.status.connect(statuses.append)
worker.run()
assert not any(s.startswith('ERROR') for s in statuses),statuses
assert worker.session_count==1 and len(logged)==1,(worker.session_count,logged,statuses)
assert worker.session_type_counts['passenger']==1
assert len(counted_frames)==1
y=ys[counted_frames[0]-1]
assert (y-84)*1.25 < .59*269 < (ys[counted_frames[0]-2]-84)*1.25, (counted_frames,y)
print('PASS: actual ByteTrack worker counts on red-dot centre crossing, not box bottom')
print('PASS: actual OAK worker bypasses CPU YOLO, tracks packets, counts single crossing once and reconciles Passenger total')

"""Exercise actual capture threads with independent camera and AI packet sequences."""
import os,time,threading
os.environ['QT_QPA_PLATFORM']='offscreen'
import numpy as np
import TyreTrack_Production_Monitor as m
logs=[];m.log=logs.append;m.oak_frame_received=lambda:None;m.oak_stream_stopped=lambda:None
m.RECORD_OAK_VIDEO=False
class Packet:
 def __init__(self,seq,nn=False):self.seq=seq;self.nn=nn
 def getSequenceNum(self):return self.seq
 def getTimestamp(self):return m.dai.Clock.now()
 def getCvFrame(self):
  shape=(270,480,3) if self.seq==23 else ((416,416,3) if self.seq in (17,18) else (384,384,3))
  return np.full(shape,self.seq,dtype=np.uint8)
 def getLayerFp16(self,name):
  if name=='output0':return np.full(18144,.123,dtype=np.float32)
  size={1:52,2:26,3:13}[int(name[6])]
  return np.full(7*size*size,.123,dtype=np.float32)
class Q:
 def __init__(self,items):self.items=list(items)
 def tryGet(self):return self.items.pop(0) if self.items else None
class Device:
 def __init__(self,*args):
  self.queues={'recording':Q([]),'live':Q([Packet(23)]),'rgb':Q([Packet(9),Packet(10)]),
               'nn':Q([Packet(10,True),Packet(99,True)])}
 def getOutputQueue(self,name,**kw):return self.queues[name]
 def close(self):pass
original=m.dai.Device;m.dai.Device=Device
source=None
try:
 source=m.OakSource('')
 ok,frame,seq=source.read();assert ok and frame.shape==(270,480,3)
 assert np.all(frame==10), 'Counting must use matched AI passthrough, never newer preview'
 assert np.allclose(source.last_read_neural_output,.123)
 deadline=time.monotonic()+1
 while source.preview_snapshot()[0] is None and time.monotonic()<deadline:time.sleep(.005)
 live,live_seq,_=source.preview_snapshot();assert live_seq==23 and np.all(live==23)
 time.sleep(.03)
 assert source.sequence==seq, 'Unmatched NN packet must not publish'
 before=len(logs);source._stream_diagnostics(source.capture_diagnostic_since+10.01,time.monotonic())
 assert len(logs)==before+1 and 'OAK stream summary:' in logs[-1]
 assert 'max_preview_loop_pause_ms=' in logs[-1]
 assert not any('recording WARNING' in x for x in logs)
finally:
 if source:source.release()
 m.dai.Device=original
assert not source.reader.is_alive() and not source.preview_reader.is_alive()
print('PASS: independent preview, exact AI sequence match, no unmatched publication, periodic timing summary, clean thread shutdown')
# Exercise the real source recording thread with camera-like encoded packets.
import tempfile,av
from pathlib import Path
with av.open(str(Path(__file__).parent/'test_data/oak_h264_sample.h264'),format='h264') as fixture:
 encoded=[bytes(p) for p in fixture.demux(video=0) if p.size]
class EncodedPacket:
 def __init__(self,index,data):self.index=index;self.data=data
 def getSequenceNum(self):return self.index
 def getData(self):return self.data
class RecordingDevice(Device):
 def __init__(self,*args):
  super().__init__(*args)
  self.queues['recording']=Q([EncodedPacket(i,p) for i,p in enumerate(encoded)])
original_folder=m.OAK_RECORDINGS
try:
 with tempfile.TemporaryDirectory() as folder:
  m.OAK_RECORDINGS=Path(folder);m.RECORD_OAK_VIDEO=True;m.dai.Device=RecordingDevice
  source=m.OakSource('')
  deadline=time.monotonic()+2
  while source.encoded_queue.items and time.monotonic()<deadline:time.sleep(.005)
  source.release()
  assert not source.recorder.is_alive(), 'Recording thread did not finalise'
  files=list(Path(folder).rglob('*.mp4'));assert len(files)==1,logs
  with av.open(str(files[0])) as recorded:
   assert sum(1 for _ in recorded.decode(video=0))==45
finally:
 if source:source.release()
 m.dai.Device=original;m.OAK_RECORDINGS=original_folder;m.RECORD_OAK_VIDEO=False
print('PASS: actual OakSource recording thread writes and finalises playable MP4 on shutdown')
# Both neural branches must pair with THEIR OWN passthrough frame sequence.
class ForeignDevice(Device):
 def __init__(self,*args):
  super().__init__(*args)
  self.queues['foreign_rgb']=Q([Packet(17),Packet(18)])
  self.queues['foreign_nn']=Q([Packet(18,True),Packet(199,True)])
try:
 m.dai.Device=ForeignDevice
 source=m.OakSource('',foreign_enabled=True)
 ok,frame,seq=source.read();assert ok and np.all(frame==10)
 deadline=time.monotonic()+1
 item=None
 while item is None and time.monotonic()<deadline:
  item=source.read_foreign()
  if item is None:time.sleep(.005)
 assert item is not None and item[0]==18 and np.all(item[1]==18)
 assert source.read_foreign() is None,'Same inference must not increment confirmation twice'
 source.foreign_received_at=time.monotonic()-7
 try:source.read_foreign();raise AssertionError('Stale foreign stream accepted')
 except RuntimeError:pass
finally:
 if source:source.release()
 m.dai.Device=original
print('PASS: independent foreign/tyre sequence pairing, no replay confirmation, foreign stream stall detection')

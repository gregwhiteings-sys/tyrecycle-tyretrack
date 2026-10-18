"""Detection-only blob/pipeline/geometry/decoder/tracker and alarm tests."""
from pathlib import Path
import numpy as np,depthai as dai
from oak_rvc2 import validate_models,build_pipeline,decode,unletterbox,OakTracker,read_layers
ROOT=Path(__file__).parent/'rvc2_models'
assert validate_models(dai,ROOT)
for foreign in (False,True):
 for recording in (False,True):
  p=build_pipeline(dai,ROOT,recording,foreign)
  camera=next(n for n in p.getAllNodes() if isinstance(n,dai.node.ColorCamera))
  assert camera.getIspSize()==camera.getVideoSize()==(640,360)
  assert camera.getPreviewSize()==(480,270)
  networks=[n for n in p.getAllNodes() if isinstance(n,dai.node.NeuralNetwork)]
  assert len(networks)==(2 if foreign else 1)
def foreign_raw(c=0,x=26,y=26):
 out={f'output{i}_yolov6r2':np.zeros((1,7,s,s),np.float32) for i,s in enumerate((52,26,13),1)}
 out['output1_yolov6r2'][0,:,y,x]=[5,5,5,5,.95,.95 if c==0 else .01,.95 if c==1 else .01]
 return out
raw=foreign_raw();boxes,masks=decode(raw)
np.testing.assert_allclose(boxes[0,:4],[172*480/416,(172-91)*270/234,252*480/416,(252-91)*270/234],rtol=1e-5)
assert masks is None
raw['output1_yolov6r2'][0,:,26,27]=[5,5,5,5,.8,.8,.01]
assert len(decode(raw)[0])==1
raw['output1_yolov6r2'][0,5:7,26,27]=[.01,.8]
assert len(decode(raw)[0])==2 and len(decode(raw,agnostic=True)[0])==1
assert len(decode(foreign_raw(y=1))[0])==0
bad=foreign_raw();bad['output1_yolov6r2']=np.zeros(3)
try:decode(bad);raise AssertionError('Bad head accepted')
except ValueError:pass
# Distinct channel colors check RGB tyre and BGR foreign preprocessing.
a=np.zeros((384,384,3),np.uint8);a[84:300,:,0]=255
frame=unletterbox(a);assert frame.shape==(270,480,3) and np.all(frame[:,:,2]==255) and not frame[:,:,0].any()
b=np.zeros((416,416,3),np.uint8);b[91:325,:,0]=255
f=unletterbox(b,'foreign');assert np.all(f[:,:,0]==255) and not f[:,:,2].any()
tracker=OakTracker();ids=[]
for y in range(250,110,-10):
 r=np.zeros((6,3024),np.float32);r[:,0]=[192,y,80,60,.95,.01]
 result=tracker.track_oak(frame,r.ravel(),.35,.5,False)
 assert result.boxes.id is not None and result.masks is None
 ids.append(int(result.boxes.id[0]))
assert len(set(ids))==1
from foreign_alarm import CLASSES,Confirmation
assert CLASSES==('wheel_rim','person')
for cl in (0,1):
 c=Confirmation()
 for t in (0,.2,.4):
  rows,_=decode(foreign_raw(cl),.45)
  event=c.update([dict(class_id=int(r[5]),confidence=float(r[4]),box=r[:4].tolist()) for r in rows],t)
 assert event['class_id']==cl
print('PASS: detection-only actual compiled contracts; four pipeline combinations; 384 RGB tyre/416 BGR foreign; full view; NMS; no masks; ByteTrack continuity; both foreign alarm classes')

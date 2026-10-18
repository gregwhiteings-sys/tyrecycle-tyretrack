"""Offline checks for real decoder, geometry and ByteTrack adapter."""
import numpy as np,depthai
from pathlib import Path
from oak_ai import decode,OakTracker,build_pipeline
raw=np.zeros((6,3024),dtype=np.float32)
raw[:,0]=[192,192,80,80,.95,.02]
raw[:,1]=[193,192,80,80,.8,.01]
b=decode(raw,.1,.5)
np.testing.assert_allclose(b[0,:4],[190,85,290,185]);assert len(b)==1
raw[:,2]=[192,20,50,20,.99,.01]
assert len(decode(raw,.1,.5))==1
assert decode(np.zeros(18144)).shape==(0,6)
try:decode(np.zeros(12));raise AssertionError('Wrong shape accepted')
except ValueError:pass
tracker=OakTracker();frame=np.zeros((270,480,3),dtype=np.uint8);ids=[]
for y in range(250,110,-10):
 r=np.zeros((6,3024),dtype=np.float32);r[:,0]=[192,y,80,60,.95,.02]
 result=tracker.track_oak(frame,r,.35,.5,False)
 assert result.boxes.id is not None
 ids.append(int(result.boxes.id[0]))
assert len(set(ids))==1
tracker.track_oak(frame,np.zeros(18144),.35,.5,False)
r[:,0]=[192,110,80,60,.95,.02]
result=tracker.track_oak(frame,r,.35,.5,False)
assert int(result.boxes.id[0])==ids[0]
build_pipeline(depthai,Path(__file__).parent/'tyre_detector_oak.blob',recording=True)
print('PASS: decode/NMS, letterbox geometry, padding rejection, malformed/empty output, ID continuity, missed frame recovery, blob hash and pipeline construction')

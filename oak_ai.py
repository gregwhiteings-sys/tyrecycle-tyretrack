"""OAK RVC2 inference adapter. CPU handles decoding and ByteTrack, never tyre inference."""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import numpy as np
import cv2

BLOB_SHA256 = '758570aa248f73b16ef0801c636f7449b1a465dccdcbafdab6d2036d5ab15e2e'
SIZE=384
PAD=84
CONTENT_HEIGHT=216

def build_pipeline(dai, blob, recording=False):
    if hashlib.sha256(Path(blob).read_bytes()).hexdigest()!=BLOB_SHA256:
        raise ValueError('OAK blob is missing or differs from the verified embedded-normalization trial')
    network=dai.OpenVINO.Blob(str(blob))
    assert [(str(v.dataType),list(v.dims)) for v in network.networkInputs.values()]==[('DataType.U8F',[384,384,3,1])]
    assert [list(v.dims) for v in network.networkOutputs.values()]==[[3024,6,1]]
    p=dai.Pipeline();camera=p.create(dai.node.ColorCamera)
    camera.setBoardSocket(dai.CameraBoardSocket.CAM_A)
    camera.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1080_P)
    camera.setPreviewSize(480,270);camera.setPreviewKeepAspectRatio(True)
    camera.setInterleaved(False);camera.setColorOrder(dai.ColorCameraProperties.ColorOrder.RGB)
    camera.setFps(15)
    if recording:
        # NV12 camera.video feeds the hardware encoder; AI/preview remain unchanged.
        # video is a crop of ISP output, not a resize. Scale the entire 1080p
        # image first, so video and its derived preview keep the full 16:9 FOV.
        camera.setIspScale(1,3)
        camera.setVideoSize(640,360)
        encoder=p.create(dai.node.VideoEncoder)
        encoder.setDefaultProfilePreset(15,dai.VideoEncoderProperties.Profile.H264_MAIN)
        encoder.setBitrateKbps(1500)
        encoder.setKeyframeFrequency(15)
        encoder.setNumBFrames(0)
        encoder.input.setBlocking(False);encoder.input.setQueueSize(4)
        camera.video.link(encoder.input)
        encoded=p.create(dai.node.XLinkOut);encoded.setStreamName('recording')
        encoded.input.setBlocking(False);encoded.input.setQueueSize(30)
        encoder.bitstream.link(encoded.input)
    manip=p.create(dai.node.ImageManip)
    manip.initialConfig.setResizeThumbnail(SIZE,SIZE)
    manip.initialConfig.setFrameType(dai.ImgFrame.Type.RGB888p)
    manip.setMaxOutputFrameSize(SIZE*SIZE*3)
    # Keep the live branch independent of inference throughput.
    manip.inputImage.setBlocking(False);manip.inputImage.setQueueSize(1)
    live=p.create(dai.node.XLinkOut);live.setStreamName('live')
    live.input.setBlocking(False);live.input.setQueueSize(1)
    camera.preview.link(live.input)
    camera.preview.link(manip.inputImage)
    nn=p.create(dai.node.NeuralNetwork);nn.setBlobPath(str(blob));nn.setNumInferenceThreads(1)
    nn.input.setBlocking(False);nn.input.setQueueSize(1)
    manip.out.link(nn.input)
    for name,out in [('rgb',nn.passthrough),('nn',nn.out)]:
        x=p.create(dai.node.XLinkOut);x.setStreamName(name);out.link(x.input)
    return p

def decode(values, confidence=.1, iou=.5, agnostic=False):
    a=np.asarray(values,dtype=np.float32)
    if a.size!=6*3024:raise ValueError(f'Unexpected OAK output length {a.size}')
    a=a.reshape(6,3024)
    cls=a[4:].argmax(0);scores=a[4:].max(0)
    valid=np.isfinite(a).all(0)&(scores>=confidence)&(scores<=1.001)&(a[2]>0)&(a[3]>0)
    rows=[]
    for i in np.flatnonzero(valid):
        x,y,w,h=a[:4,i]
        x1=max(0,float(x-w/2));x2=min(384,float(x+w/2))
        y1=max(PAD,float(y-h/2));y2=min(PAD+CONTENT_HEIGHT,float(y+h/2))
        if x2<=x1 or y2<=y1:continue
        rows.append([x1*1.25,(y1-PAD)*1.25,x2*1.25,(y2-PAD)*1.25,float(scores[i]),int(cls[i])])
    if not rows:return np.empty((0,6),dtype=np.float32)
    rows=np.array(rows,dtype=np.float32);kept=[]
    for group in ([None] if agnostic else [0,1]):
        ids=np.arange(len(rows)) if group is None else np.flatnonzero(rows[:,5]==group)
        b=rows[ids,:4].copy();b[:,2:]-=b[:,:2]
        chosen=cv2.dnn.NMSBoxes(b.tolist(),rows[ids,4].tolist(),confidence,iou)
        kept.extend(ids[np.asarray(chosen,dtype=int).reshape(-1)].tolist())
    return rows[kept]

class OakTracker:
    names={0:'passenger',1:'4x4'}
    def __init__(self):
        from ultralytics.trackers.byte_tracker import BYTETracker
        self.tracker=BYTETracker(SimpleNamespace(track_high_thresh=.25,track_low_thresh=.1,
            new_track_thresh=.25,track_buffer=30,match_thresh=.8,fuse_score=True))
        self.predictor=SimpleNamespace(trackers=[self.tracker])
    def track_oak(self,frame,raw,confidence,iou,agnostic):
        from ultralytics.engine.results import Results,Boxes
        boxes=decode(raw,min(confidence,.1),iou,agnostic)
        tracks=self.tracker.update(Boxes(boxes,frame.shape[:2]),frame)
        tracked=tracks[:,:7] if len(tracks) else np.empty((0,7),dtype=np.float32)
        import torch
        return Results(frame,path='OAK',names=self.names,boxes=torch.from_numpy(tracked.copy()))

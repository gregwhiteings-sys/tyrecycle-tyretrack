"""Detection-only dual OAK RVC2 adapter. No mask tensors or reconstruction.
Tyres: supplied verified 384 RGB detection blob. Foreign: 416 BGR detector.
Host performs detection decoding and ByteTrack; device performs inference/encoding.
"""
from pathlib import Path
import hashlib,json
import numpy as np
import cv2
from oak_ai import BLOB_SHA256,OakTracker,decode as decode_tyres
FRAME_SIZE=(480,270)

def validate_models(dai,root):
    root=Path(root)
    tyre=root.parent/'tyre_detector_oak.blob'
    if hashlib.sha256(tyre.read_bytes()).hexdigest()!=BLOB_SHA256:raise ValueError('Tyre detection blob integrity mismatch')
    blob=dai.OpenVINO.Blob(str(tyre))
    ins=list(blob.networkInputs.values());outs=list(blob.networkOutputs.values())
    if len(ins)!=1 or list(ins[0].dims)!=[384,384,3,1] or 'U8' not in str(ins[0].dataType):raise ValueError('Unexpected tyre input')
    if len(outs)!=1 or list(outs[0].dims)!=[3024,6,1]:raise ValueError('Tyre model must be detection only')
    hashes=json.loads((root/'sha256.json').read_text())
    for relative,digest in hashes.items():
        data=(root/relative).read_bytes()
        # Git on Windows may check text out with CRLF. JSON content must still
        # match the original export; compiled binary bytes stay strictly exact.
        if Path(relative).suffix.lower()=='.json':
            data=data.replace(b'\r\n',b'\n')
        if hashlib.sha256(data).hexdigest()!=digest:
            raise ValueError('Foreign compiled integrity mismatch: '+relative)
    cfg=json.loads((root/'foreign/config.json').read_text())['model']
    blob=dai.OpenVINO.Blob(str(root/'foreign'/cfg['metadata']['path']))
    ins=list(blob.networkInputs.values())
    if len(ins)!=1 or list(ins[0].dims)!=[416,416,3,1] or 'U8' not in str(ins[0].dataType):raise ValueError('Unexpected foreign input')
    expected={o['name']:list(reversed(o['shape'])) for o in cfg['outputs']}
    if set(blob.networkOutputs)!=set(expected):raise ValueError('Foreign output mismatch')
    for name,dims in expected.items():
        if list(blob.networkOutputs[name].dims)!=dims:raise ValueError('Foreign tensor shape mismatch')
    return True

def build_pipeline(dai,root,recording=False,foreign_enabled=True):
    root=Path(root);validate_models(dai,root)
    p=dai.Pipeline();camera=p.create(dai.node.ColorCamera)
    camera.setBoardSocket(dai.CameraBoardSocket.CAM_A)
    camera.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1080_P)
    camera.setPreviewSize(*FRAME_SIZE);camera.setPreviewKeepAspectRatio(True)
    camera.setInterleaved(False);camera.setColorOrder(dai.ColorCameraProperties.ColorOrder.BGR)
    camera.setFps(15);camera.setIspScale(1,3);camera.setVideoSize(640,360)
    def stream(name,out,size=4):
        node=p.create(dai.node.XLinkOut);node.setStreamName(name)
        node.input.setBlocking(False);node.input.setQueueSize(size);out.link(node.input)
    stream('live',camera.preview,1)
    if recording:
        enc=p.create(dai.node.VideoEncoder)
        enc.setDefaultProfilePreset(15,dai.VideoEncoderProperties.Profile.H264_MAIN)
        enc.setBitrateKbps(1500);enc.setKeyframeFrequency(15);enc.setNumBFrames(0)
        enc.input.setBlocking(False);enc.input.setQueueSize(4)
        camera.video.link(enc.input);stream('recording',enc.bitstream,30)
    for kind in ('tyre','foreign') if foreign_enabled else ('tyre',):
        tyre=kind=='tyre';size=384 if tyre else 416
        manip=p.create(dai.node.ImageManip)
        manip.initialConfig.setResizeThumbnail(size,size)
        manip.initialConfig.setFrameType(dai.ImgFrame.Type.RGB888p if tyre else dai.ImgFrame.Type.BGR888p)
        manip.setMaxOutputFrameSize(size*size*3)
        manip.inputImage.setBlocking(False);manip.inputImage.setQueueSize(1)
        camera.preview.link(manip.inputImage)
        nn=p.create(dai.node.NeuralNetwork)
        nn.setBlobPath(str(root.parent/'tyre_detector_oak.blob' if tyre else root/'foreign/foreign_object.blob'))
        nn.setNumInferenceThreads(1);nn.input.setBlocking(False);nn.input.setQueueSize(1)
        manip.out.link(nn.input)
        stream('nn' if tyre else 'foreign_nn',nn.out,8)
        stream('rgb' if tyre else 'foreign_rgb',nn.passthrough,4)
    return p

def read_layers(packet,kind):
    if kind=='tyre':
        out=np.asarray(packet.getLayerFp16('output0'),dtype=np.float32)
        if out.size!=18144:raise ValueError('Malformed tyre detection tensor')
        return out
    out={}
    for i,s in enumerate((52,26,13),1):
        name=f'output{i}_yolov6r2'
        a=np.asarray(packet.getLayerFp16(name),dtype=np.float32)
        if a.size!=7*s*s:raise ValueError('Malformed foreign detection head '+name)
        out[name]=a.reshape(1,7,s,s)
    return out

def unletterbox(image,kind='tyre'):
    if kind=='tyre':
        if image.shape[:2]!=(384,384):raise ValueError('Unexpected tyre thumbnail')
        return cv2.cvtColor(cv2.resize(image[84:300],FRAME_SIZE),cv2.COLOR_RGB2BGR)
    if image.shape[:2]!=(416,416):raise ValueError('Unexpected foreign thumbnail')
    return cv2.resize(image[91:325],FRAME_SIZE)

def decode(raw,confidence=.1,iou=.5,agnostic=False):
    """Return detection rows and None; no segmentation tensors are consumed."""
    if not isinstance(raw,dict):return decode_tyres(raw,confidence,iou,agnostic),None
    rows=[]
    for i,stride in enumerate((8,16,32),1):
        s=416//stride;a=np.asarray(raw[f'output{i}_yolov6r2'],np.float32)
        if a.shape!=(1,7,s,s):raise ValueError('Unexpected foreign head shape')
        a=a[0];scores=a[5:7].max(0);classes=a[5:7].argmax(0)
        valid=np.isfinite(a).all(0)&(a[4]>confidence)&(scores>=confidence)&(scores<=1.001)
        for y,x in zip(*np.nonzero(valid)):
            l,t,r,b=a[:4,y,x]
            if min(l,t,r,b)<0:continue
            xy=np.array([(x+.5-l)*stride,(y+.5-t)*stride,(x+.5+r)*stride,(y+.5+b)*stride],np.float32)
            xy[[0,2]]=np.clip(xy[[0,2]],0,416);xy[[1,3]]=np.clip(xy[[1,3]],91,325)
            if xy[2]<=xy[0] or xy[3]<=xy[1]:continue
            rows.append([*xy,float(scores[y,x]),int(classes[y,x])])
    if not rows:return np.empty((0,6),np.float32),None
    rows=np.asarray(rows,np.float32);kept=[]
    for cl in ([None] if agnostic else (0,1)):
        ids=np.arange(len(rows)) if cl is None else np.flatnonzero(rows[:,5]==cl)
        if not len(ids):continue
        boxes=rows[ids,:4].copy();boxes[:,2:]-=boxes[:,:2]
        sel=np.asarray(cv2.dnn.NMSBoxes(boxes.tolist(),rows[ids,4].tolist(),confidence,iou),int).ravel()
        kept.extend(ids[sel].tolist())
    kept=sorted(kept,key=lambda i:-rows[i,4])[:300];chosen=rows[kept].copy()
    chosen[:,[0,2]]*=480/416;chosen[:,[1,3]]=(chosen[:,[1,3]]-91)*270/234
    return chosen,None

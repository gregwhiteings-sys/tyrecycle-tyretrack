"""Check actual DepthAI camera geometry; no attached camera required."""
from pathlib import Path
import depthai as dai
from oak_ai import build_pipeline
blob=Path(__file__).parent/'tyre_detector_oak.blob'
for recording in (False,True):
    pipeline=build_pipeline(dai,blob,recording=recording)
    camera=next(node for node in pipeline.getAllNodes() if isinstance(node,dai.node.ColorCamera))
    assert camera.getResolutionSize()==(1920,1080)
    assert camera.getPreviewSize()==(480,270)
    isp=camera.getIspSize();video=camera.getVideoSize()
    assert isp==video, f'Video would crop ISP: ISP={isp}, video={video}'
    assert isp[0]*1080==isp[1]*1920, 'Preserve sensor aspect ratio'
    if recording: assert isp==(640,360)
    print('PASS: recording',recording,'sensor 1920x1080; ISP/video',isp,'preview 480x270; no ISP-to-video crop')

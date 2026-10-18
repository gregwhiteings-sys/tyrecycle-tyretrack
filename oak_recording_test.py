"""Decode stream-copy output to verify playable MP4, keyframe rotation and recovery."""
from pathlib import Path
from datetime import datetime,timedelta
import tempfile
import av
from oak_recording import EncodedRecorder

fixture=Path(__file__).parent/'test_data/oak_h264_sample.h264'
with av.open(str(fixture),format='h264') as src:
    packets=[bytes(p) for p in src.demux(video=0) if p.size]
assert len(packets)==45

def inspect(folder, expected):
    files=sorted(Path(folder).rglob('*.mp4'))
    counts=[]
    for path in files:
        with av.open(str(path)) as source:
            stream=source.streams.video[0]
            assert stream.codec_context.name=='h264'
            frames=list(source.decode(stream))
            assert all(f.width==640 and f.height==360 for f in frames)
            assert all(a.pts<b.pts for a,b in zip(frames,frames[1:]))
            counts.append(len(frames))
    assert counts==expected,(counts,expected)
    assert not list(Path(folder).rglob('*.h264'))

now=datetime(2026,10,16,5)
with tempfile.TemporaryDirectory() as folder:
    recorder=EncodedRecorder(folder,print,segment_seconds=1)
    for i,p in enumerate(packets): recorder.feed(p,now+timedelta(seconds=i/15),i)
    recorder.close(); inspect(folder,[15,15,15])
print('PASS: keyframe-aligned segments, 45 decoded frames, correct dimensions and increasing timestamps')
with tempfile.TemporaryDirectory() as folder:
    recorder=EncodedRecorder(folder,print)
    for i,p in enumerate(packets):
        if i==7: continue
        recorder.feed(p,now+timedelta(seconds=i/15),i)
    recorder.close(); inspect(folder,[7,30]); assert recorder.gaps==1
print('PASS: dropped packet ends segment; recording resumes at next decodable keyframe')
with tempfile.TemporaryDirectory() as folder:
    recorder=EncodedRecorder(folder,print)
    for i,p in enumerate(packets): recorder.feed(p,now+timedelta(seconds=i/15),i,enabled=(i>=16))
    recorder.close(); inspect(folder,[15])
print('PASS: enabling midstream waits for a keyframe')
with tempfile.TemporaryDirectory() as folder:
    recorder=EncodedRecorder(folder,print)
    for i,p in enumerate(packets): recorder.feed(p,now+timedelta(seconds=i/15),i,enabled=False)
    assert not list(Path(folder).rglob('*'))
print('PASS: recording disabled creates no files')
with tempfile.TemporaryDirectory() as folder:
    recorder=EncodedRecorder(folder,lambda message:None)
    for i,p in enumerate(packets[:15]): recorder.feed(p,now+timedelta(seconds=i/15),i)
    original=av.open
    try:
        def fail(*a,**k): raise OSError('simulated container error')
        av.open=fail; recorder.close()
    finally: av.open=original
    assert len(list(Path(folder).rglob('*.h264')))==1
    assert not list(Path(folder).rglob('*.partial.mp4'))
print('PASS: failed MP4 finalisation preserves original H264 for recovery')

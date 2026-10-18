"""Save OAK H.264 access units; remux to MP4 without decoding or encoding."""
from pathlib import Path
from fractions import Fraction
import re
import av


def nals(data):
    return [p for p in re.split(b'\x00\x00\x00\x01|\x00\x00\x01', data) if p]


class EncodedRecorder:
    def __init__(self, folder, log, fps=15, segment_seconds=900):
        self.folder=Path(folder); self.log=log; self.fps=Fraction(str(fps))
        if self.fps <= 0: raise ValueError('Recording FPS must be positive')
        self.segment_seconds=segment_seconds
        self.writer=None; self.started=None; self.path=None; self.frames=0
        self.previous_sequence=None; self.headers={}; self.gaps=0

    def feed(self, data, now, sequence, enabled=True):
        data=bytes(data)
        units=nals(data)
        types={u[0]&31 for u in units}
        for unit in units:
            if unit[0]&31 in (7,8): self.headers[unit[0]&31]=unit
        if self.previous_sequence is not None and sequence!=self.previous_sequence+1:
            self.gaps+=1
            self.log('OAK H264 recording packet gap; closing segment and waiting for keyframe')
            self.close()
        self.previous_sequence=sequence
        if not enabled:
            self.close(); return
        if self.writer is not None and (now-self.started).total_seconds()>=self.segment_seconds and 5 in types:
            self.close()
        if self.writer is None:
            if 5 not in types or not all(k in self.headers for k in (7,8)): return
            folder=self.folder/now.strftime('%Y-%m-%d'); folder.mkdir(parents=True,exist_ok=True)
            self.path=folder/f'OAK_RGB_{now:%Y%m%d_%H%M%S_%f}.h264'
            self.writer=self.path.open('wb'); self.started=now; self.frames=0
            for kind in (7,8):
                if kind not in types: self.writer.write(b'\x00\x00\x00\x01'+self.headers[kind])
            self.log(f'OAK recording started: {self.path}; H264 encoded on OAK, MP4 finalised at segment close')
        self.writer.write(data)
        self.frames+=int(bool(types & {1,5}))

    def close(self):
        if self.writer is None: return
        self.writer.close(); self.writer=None
        raw=self.path; target=raw.with_suffix('.mp4'); partial=raw.with_suffix('.partial.mp4')
        try:
            with av.open(str(raw),format='h264',options={'framerate':str(self.fps)}) as source:
                video=source.streams.video[0]
                with av.open(str(partial),'w',format='mp4',options={'movflags':'+faststart'}) as output:
                    stream=output.add_stream_from_template(video)
                    index=0
                    for packet in source.demux(video):
                        if not packet.size: continue
                        packet.pts=packet.dts=index
                        packet.duration=1; packet.time_base=Fraction(1,self.fps)
                        packet.stream=stream; output.mux(packet); index+=1
            partial.replace(target)
            raw.unlink()
            self.log(f'OAK recording closed: {target}; frames={index}; stream-copy only, no PC encoding')
        except Exception as exc:
            partial.unlink(missing_ok=True)
            self.log(f'OAK MP4 finalisation failed: {exc}; recoverable H264 retained: {raw}')
        finally:
            self.path=None; self.frames=0

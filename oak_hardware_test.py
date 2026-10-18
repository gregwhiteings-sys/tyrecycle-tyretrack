"""Physical OAK-D S2 probe. No production DB, email, counts or alarm state writes."""
import argparse,json,time,traceback
from pathlib import Path

def main(argv=None,root=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--ip',default='')
    parser.add_argument('--seconds',type=float,default=60)
    parser.add_argument('--tyre-only',action='store_true')
    parser.add_argument('--output',default='oak_hardware_report.json')
    args=parser.parse_args(argv)
    if args.seconds<5:parser.error('--seconds must be at least 5')
    root=Path(root) if root else Path(__file__).resolve().parent
    report={'status':'running','physical_camera':True,'foreign_enabled':not args.tyre_only,
            'production_state_modified':False,'alarm_audio_tested':False,
            'detection_accuracy_verified':False,'seconds_requested':args.seconds}
    counts={'tyre':0,'foreign':0};detections={'tyre':0,'foreign':0};latencies={'tyre':[],'foreign':[]}
    preview=recording=0
    try:
        import depthai as dai
        from oak_rvc2 import build_pipeline,read_layers,decode,unletterbox
        from oak_recording import EncodedRecorder
        from datetime import datetime
        pipeline=build_pipeline(dai,root/'rvc2_models',recording=True,foreign_enabled=not args.tyre_only)
        info=dai.DeviceInfo(args.ip) if args.ip else None
        device=dai.Device(pipeline,info) if info else dai.Device(pipeline)
        with device:
            kinds=['tyre'] if args.tyre_only else ['tyre','foreign']
            queues={kind:device.getOutputQueue('nn' if kind=='tyre' else 'foreign_nn',8,False) for kind in kinds}
            fqs={kind:device.getOutputQueue('rgb' if kind=='tyre' else 'foreign_rgb',4,False) for kind in kinds}
            pending={kind:({}, {}) for kind in kinds}
            live=device.getOutputQueue('live',1,False)
            encoded=device.getOutputQueue('recording',120,False)
            recorder=EncodedRecorder(Path(args.output).resolve().parent/'oak_probe_recording',lambda s:None,fps=15)
            last_seen={k:time.monotonic() for k in kinds};start=time.monotonic();sequence={}
            try:
                while time.monotonic()-start<args.seconds:
                    packet=live.tryGet()
                    if packet is not None:
                        assert packet.getCvFrame().shape[:2]==(270,480)
                        preview+=1
                    for _ in range(120):
                        packet=encoded.tryGet()
                        if packet is None:break
                        recorder.feed(packet.getData(),datetime.now(),int(packet.getSequenceNum()))
                        recording+=1
                    for kind in kinds:
                        frames,outputs=pending[kind]
                        for q,buf in [(fqs[kind],frames),(queues[kind],outputs)]:
                            for _ in range(32):
                                packet=q.tryGet()
                                if packet is None:break
                                buf[int(packet.getSequenceNum())]=packet
                                while len(buf)>16:buf.pop(min(buf))
                        common=frames.keys() & outputs.keys()
                        for seq in sorted(common):
                            p=outputs[seq];frame=unletterbox(frames[seq].getCvFrame(),kind)
                            raw=read_layers(p,kind)
                            boxes,_=decode(raw,.45,.5)
                            counts[kind]+=1;detections[kind]+=len(boxes);last_seen[kind]=time.monotonic()
                            latencies[kind].append(max(0,(dai.Clock.now()-p.getTimestamp()).total_seconds()*1000))
                            sequence[kind]=seq
                            for buf in (frames,outputs):
                                for old in list(buf):
                                    if old<=seq:del buf[old]
                    if any(time.monotonic()-t>10 for t in last_seen.values()):raise RuntimeError('A model produced no matched output for 10 seconds')
                    time.sleep(.002)
            finally:recorder.close()
            elapsed=time.monotonic()-start
            if not preview or not recording:raise RuntimeError('Missing preview or encoder output')
            if any(counts[k]==0 for k in kinds):raise RuntimeError('Missing neural stream')
            import numpy as np
            report.update(status='passed_stream_test',elapsed_seconds=elapsed,
                          matched_results=counts,matched_fps={k:counts[k]/elapsed for k in kinds},
                          frame_level_detections=detections,preview_frames=preview,h264_packets=recording,
                          latency_ms={k:{'median':float(np.median(latencies[k])),'p95':float(np.percentile(latencies[k],95))} for k in kinds},
                          recording_packet_gaps=recorder.gaps)
    except Exception:
        report.update(status='failed',error=traceback.format_exc())
    Path(args.output).write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    return 0 if report['status']=='passed_stream_test' else 1

if __name__=='__main__':raise SystemExit(main())

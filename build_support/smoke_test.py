"""Called with the actual bundled executable; no network/camera/SMTP required."""
import json,os,sys,traceback
from pathlib import Path

def run(app_module, output):
    # CI build smoke: verify the packaged runtime without running CPU-intensive
    # YOLO inference or initializing an OAK camera. Full regression tests run
    # separately in run_ci_tests.py and hardware is tested on site.
    if os.environ.get("TYRETRACK_SMOKE_LEVEL") == "quick":
        try:
            print("SMOKE: checking bundled resources", flush=True)
            resources = ["tyre_detector.pt", "tyre_detector_oak.blob",
                         "foreign_model/foreign_object.pt", "rvc2_models/sha256.json"]
            for name in resources:
                path = Path(app_module.resource_path(name))
                if not path.is_file() or path.stat().st_size == 0:
                    raise RuntimeError(f"Missing or empty required resource: {name} ({path})")
                print(f"SMOKE: OK {name}", flush=True)
            print("SMOKE: initializing Qt offscreen", flush=True)
            from PyQt5.QtWidgets import QApplication
            qt = QApplication.instance() or QApplication([])
            qt.processEvents()
            payload = {"status": "passed", "level": "quick", "checks":
                       ["application imports", "model and OAK resources", "Qt initialization"],
                       "note": "AI inference, camera operation and full GUI layout are not verified by this quick smoke test"}
            Path(output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print("SMOKE: PASSED", flush=True)
            return 0
        except Exception:
            error = traceback.format_exc()
            print(error, flush=True)
            Path(output).write_text(json.dumps({"status":"failed", "level":"quick", "traceback":error}, indent=2), encoding="utf-8")
            return 1
    stage="dependency imports"
    try:
        import torch,torchvision,cv2,depthai,lap,optree,av,numpy as np
        from oak_recording import EncodedRecorder
        from scipy.linalg import cho_factor
        from ultralytics import YOLO
        from ultralytics.trackers.byte_tracker import BYTETracker
        from PyQt5.QtWidgets import QApplication
        stage='pinned versions and CPU runtime'
        assert not torch.cuda.is_available(), 'Expected CPU-only build'
        assert torch.__version__.startswith('2.5.1')
        assert torchvision.__version__.startswith('0.20.1')
        assert np.__version__=='1.26.4'
        assert depthai.__version__.startswith('2.')
        stage="SciPy and torchvision native operators"
        cho_factor(np.eye(4))
        boxes=torch.tensor([[0.,0.,10.,10.],[1.,1.,9.,9.]])
        assert torchvision.ops.nms(boxes,torch.tensor([.9,.8]),.5).numel()==1
        stage='H264 native libraries and stream-copy MP4 recording'
        import tempfile
        from datetime import datetime,timedelta
        with tempfile.TemporaryDirectory() as folder:
            recorder=EncodedRecorder(folder,lambda message:None)
            with av.open(str(app_module.resource_path('test_data/oak_h264_sample.h264')),format='h264') as source:
                packets=[bytes(p) for p in source.demux(video=0) if p.size]
            for index,data in enumerate(packets):
                recorder.feed(data,datetime(2026,10,16,5)+timedelta(seconds=index/15),index)
            recorder.close()
            files=list(Path(folder).rglob('*.mp4'))
            assert len(files)==1, 'MP4 remux failed'
            with av.open(str(files[0])) as recorded:
                assert sum(1 for _ in recorded.decode(video=0))==45
        stage='tyre model load and ByteTrack'
        model=YOLO(str(app_module.resource_path('tyre_detector.pt')))
        assert model.task=='detect', f'Expected detection-only model, got {model.task}'
        model.track(np.zeros((360,640,3),dtype=np.uint8),imgsz=480,device='cpu',
                    persist=True,tracker='bytetrack.yaml',verbose=False)
        stage='OAK blob, pipeline and CPU tracker adapter'
        from oak_ai import build_pipeline, OakTracker
        oak_pipeline=build_pipeline(depthai, app_module.resource_path('tyre_detector_oak.blob'),recording=True)
        camera=next(node for node in oak_pipeline.getAllNodes() if isinstance(node,depthai.node.ColorCamera))
        assert camera.getIspSize()==camera.getVideoSize()==(640,360), 'Camera recording crops the full sensor image'
        assert camera.getPreviewSize()==(480,270)
        raw=np.zeros((6,3024),dtype=np.float32)
        raw[:,0]=[192,192,80,80,.95,.02]
        adapter=OakTracker()
        tracked=adapter.track_oak(np.zeros((270,480,3),dtype=np.uint8),raw.ravel(),.35,.5,False)
        assert tracked.boxes.id is not None and len(tracked.boxes)==1
        stage='new dual RVC2 model integrity and pipeline'
        import oak_rvc2
        oak_rvc2.build_pipeline(depthai,app_module.resource_path('rvc2_models'),recording=True,foreign_enabled=True)
        stage='foreign model load and inference' 
        foreign=YOLO(str(app_module.resource_path('foreign_model/foreign_object.pt')))
        from foreign_alarm import CLASSES
        assert tuple(foreign.names[i] for i in range(len(foreign.names)))==CLASSES
        foreign.predict(np.zeros((360,640,3),dtype=np.uint8),imgsz=480,device='cpu',verbose=False)
        stage="Qt window and controls layout"
        qt=QApplication.instance() or QApplication([])
        # Main builds every control but never runs camera/scheduler during this check.
        original_tick=app_module.Main.tick
        app_module.Main.tick=lambda self:None
        try:
            window=app_module.Main();window.clock.stop();window.resize(1366,768)
            window.show();qt.processEvents()
            red=np.zeros((270,480,3),dtype=np.uint8);red[:,:,2]=255
            window.video.set_frame(red);qt.processEvents()
            assert window.video._pix.width()==480, 'Keep native frame size'
            assert window.video._pix.toImage().pixelColor(240,135).red()>245, 'BGR rendering colour error'

            for index in range(window.controls_tabs.count()):
                window.controls_tabs.setCurrentIndex(index)
                # Allow layout/scrollbar resize events to settle, then check real geometry.
                for _ in range(4):qt.processEvents()
                scroll=window.controls_tabs.widget(index)
                name=window.controls_tabs.tabText(index)
                assert scroll.widget().width()<=scroll.viewport().width(), (
                    f"Clipped tab {name}: page={scroll.widget().width()} viewport={scroll.viewport().width()}")
                from PyQt5.QtWidgets import QCheckBox,QComboBox,QPushButton,QSlider
                for cls in (QCheckBox,QComboBox,QPushButton,QSlider):
                    for control in scroll.widget().findChildren(cls):
                        x=control.mapTo(scroll.widget(),control.rect().topLeft()).x()
                        assert x>=0 and x+control.width()<=scroll.widget().width(), (
                            f"Clipped control in {name}: {type(control).__name__} x={x} width={control.width()}")
            assert window.best_daily_card.geometry().top()==window.best_hour_card.geometry().top()
            window.close();qt.processEvents()
        finally:app_module.Main.tick=original_tick
        payload={'status':'passed','torch':torch.__version__,'torchvision':torchvision.__version__,
                 'numpy':np.__version__,'depthai':depthai.__version__,
                 'checks':['imports','H264-native-remux-and-MP4-playback','scipy','torchvision-nms','bundled-detection-model',
                           'CPU-inference-and-ByteTrack','OAK-blob-pipeline-and-tracker','foreign-model-CPU-inference','Qt-controls-layout']}
        Path(output).write_text(json.dumps(payload,indent=2));return 0
    except Exception:
        error=traceback.format_exc()
        print(error,flush=True)
        Path(output).write_text(json.dumps({'status':'failed','stage':stage,'traceback':error},indent=2));return 1

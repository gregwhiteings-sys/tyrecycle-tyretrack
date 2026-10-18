"""Build and validate a Windows x64 portable folder. Run on Windows only."""
from pathlib import Path
import hashlib,json,os,platform,shutil,subprocess,sys
ROOT=Path(__file__).resolve().parent
LOGS=ROOT/'build_logs';BUNDLE=ROOT/'dist'/'TyreTrack';RELEASE=ROOT/'release'/'TyreTrack'

def run(args):
    subprocess.run([str(a) for a in args],cwd=ROOT,check=True)

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def smoke(command,result,env,cwd,timeout):
    print(f"SMOKE: launching {command[0]} (timeout {timeout}s)", flush=True)
    try:
        completed=subprocess.run([str(x) for x in command],cwd=cwd,env=env,timeout=timeout,
                                 stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,
                                 encoding="utf-8",errors="replace")
        output=completed.stdout or ""
    except subprocess.TimeoutExpired as exc:
        output=exc.stdout or b""
        if isinstance(output,bytes): output=output.decode("utf-8","replace")
        result.with_suffix('.stdout.txt').write_text(output+f"\nTIMEOUT after {timeout} seconds\n",encoding="utf-8")
        print(output[-16000:],flush=True)
        raise RuntimeError(f"Smoke test exceeded {timeout}s; see build_logs/{result.stem}.stdout.txt") from exc
    result.with_suffix('.stdout.txt').write_text(output,encoding="utf-8")
    print(output[-16000:],flush=True)
    if result.exists():
        print(result.read_text(encoding="utf-8"),flush=True)
    else:
        print("Smoke process returned no JSON; inspect native DLL/startup output",flush=True)
    if completed.returncode:
        raise RuntimeError(f"Smoke test exited {completed.returncode}; diagnostic printed above")
    if not result.exists() or json.loads(result.read_text())["status"]!="passed":
        raise RuntimeError("Smoke test did not report success")

def main():
    if sys.platform!='win32' or sys.version_info[:2]!=(3,12) or platform.architecture()[0]!='64bit':
        raise RuntimeError('Windows x64 Python 3.12 required')
    LOGS.mkdir(exist_ok=True)
    # Source probe gives a useful diagnostic before spending time freezing.
    env=os.environ.copy();env['QT_QPA_PLATFORM']='offscreen';env['OMP_NUM_THREADS']='2';env['TYRETRACK_SMOKE_LEVEL']='quick';env['PYTHONUNBUFFERED']='1'
    source_result=LOGS/'source-smoke.json'
    run([sys.executable,'-m','compileall','-q','TyreTrack_Production_Monitor.py','count_line.py','hourly_dashboard.py','foreign_alarm.py','oak_rvc2.py','oak_hardware_test.py','oak_ai.py','oak_recording.py','background_export.py','build_support'])
    smoke([sys.executable,ROOT/'TyreTrack_Production_Monitor.py','--build-smoke-test',source_result],source_result,env,ROOT,180)
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed',
             '--name','TyreTrack','--paths',ROOT,'--noupx',
             '--hidden-import','oak_recording','--collect-all','av','--hidden-import','background_export','--hidden-import','oak_rvc2','--hidden-import','oak_hardware_test','--hidden-import','oak_ai','--hidden-import','count_line','--hidden-import','hourly_dashboard',
             '--hidden-import','build_support.smoke_test','--hidden-import','foreign_alarm','--hidden-import','no_count_replies',
             '--hidden-import','ultralytics.nn.tasks',
             '--hidden-import','ultralytics.trackers.byte_tracker',
             '--hidden-import','ultralytics.trackers.bot_sort',
             '--hidden-import','scipy.linalg',
             '--collect-data','ultralytics','--collect-all','torchvision',
             '--collect-all','depthai','--collect-all','lap','--collect-all','optree',
             '--copy-metadata','torch','--copy-metadata','ultralytics',
             '--exclude-module','tensorflow','--exclude-module','keras','--exclude-module','tkinter',
             '--add-data',str(ROOT/'tyre_detector.pt')+';.',
             '--add-data',str(ROOT/'tyre_detector_oak.blob')+';.',
             '--add-data',str(ROOT/'oak_model_metadata.json')+';.',
             '--add-data',str(ROOT/'rvc2_models')+';rvc2_models',
             '--add-data',str(ROOT/'test_data')+';test_data',
             '--add-data',str(ROOT/'foreign_model')+';foreign_model']
    if (ROOT/'tyrecycle_logo.png').exists():command+=['--add-data',str(ROOT/'tyrecycle_logo.png')+';.']
    command += [ROOT/'TyreTrack_Production_Monitor.py'];run(command)
    internal=BUNDLE/'_internal'
    # Match the previous duplicate-Qt-runtime workaround. Never remove torch/lib.
    for qtbin in [internal/'PyQt5'/'Qt5'/'bin',internal/'PyQt5'/'Qt'/'bin']:
        for name in ['MSVCP140.dll','VCRUNTIME140.dll','VCRUNTIME140_1.dll']:
            dll=qtbin/name
            if dll.exists() and (internal/name).exists():
                backup=LOGS/'qt_duplicate_dll_backup'/qtbin.relative_to(internal)/name
                backup.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(dll),str(backup))
    models=list(BUNDLE.rglob('tyre_detector.pt'))
    assert len(models)==1 and digest(models[0])==digest(ROOT/'tyre_detector.pt'), 'Model missing or changed'
    blobs=list(BUNDLE.rglob('tyre_detector_oak.blob'))
    assert len(blobs)==1 and digest(blobs[0])==digest(ROOT/'tyre_detector_oak.blob')
    foreign=list(BUNDLE.rglob('foreign_object.pt'))
    assert len(foreign)==1 and digest(foreign[0])==digest(ROOT/'foreign_model/foreign_object.pt')
    assert not list(BUNDLE.rglob('email_config.ini')), 'Private configuration in bundle'
    result=LOGS/'packaged-smoke.json'
    # Test from an unrelated working directory to catch resource-path assumptions.
    unrelated=LOGS/'unrelated_cwd';unrelated.mkdir(exist_ok=True)
    smoke([BUNDLE/'TyreTrack.exe','--build-smoke-test',result],result,env,unrelated,180)
    assert json.loads(result.read_text())['status']=='passed'
    # Smoke-test DB and auto-generated email template must never ship as user data.
    for name in ['data','reports']:
        shutil.rmtree(BUNDLE/name,ignore_errors=True);(BUNDLE/name).mkdir()
    shutil.copy2(ROOT/'OAK_DUAL_AI_START_HERE.txt',BUNDLE/'OAK_DUAL_AI_START_HERE.txt')
    shutil.copy2(ROOT/'RUN_OAK_HARDWARE_TEST.bat',BUNDLE/'RUN_OAK_HARDWARE_TEST.bat')
    shutil.copy2(ROOT/'OAK_COMPRESSION_START_HERE.txt',BUNDLE/'OAK_COMPRESSION_START_HERE.txt')
    shutil.copy2(ROOT/'GUI_RENDER_START_HERE.txt',BUNDLE/'GUI_RENDER_START_HERE.txt')
    shutil.copy2(ROOT/'SMOOTH_PREVIEW_START_HERE.txt',BUNDLE/'SMOOTH_PREVIEW_START_HERE.txt')
    shutil.copy2(ROOT/'OAK_ONBOARD_START_HERE.txt',BUNDLE/'OAK_ONBOARD_START_HERE.txt')
    shutil.copy2(ROOT/'START_WINDOWS_BUILD.txt',BUNDLE/'START_HERE.txt')
    shutil.copy2(ROOT/'FOREIGN_DETECTOR_START_HERE.txt',BUNDLE/'FOREIGN_DETECTOR_START_HERE.txt')
    shutil.copy2(ROOT/'OAK_MANUAL_TEST_START_HERE.txt',BUNDLE/'OAK_MANUAL_TEST_START_HERE.txt')
    shutil.copy2(ROOT/'DETECTION_ONLY_START_HERE.txt',BUNDLE/'DETECTION_ONLY_START_HERE.txt')
    shutil.copy2(LOGS/'installed-versions.txt',BUNDLE/'BUILD_VERSIONS.txt')
    prerequisites=BUNDLE/'prerequisites';prerequisites.mkdir(exist_ok=True)
    shutil.copy2(ROOT/'vc_redist.x64.exe',prerequisites/'vc_redist.x64.exe')
    if RELEASE.exists():shutil.rmtree(RELEASE)
    RELEASE.parent.mkdir(exist_ok=True);shutil.copytree(BUNDLE,RELEASE)
    shutil.make_archive(str(ROOT/'release'/'TyreTrack-Windows-x64-Portable'),'zip',RELEASE.parent,RELEASE.name)
    print('SUCCESS: release/TyreTrack-Windows-x64-Portable.zip')

if __name__=='__main__':main()

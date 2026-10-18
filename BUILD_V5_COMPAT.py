"""V5 compatibility builder based on older TyreTrack PyInstaller packaging."""
from pathlib import Path
from datetime import datetime
import hashlib, os, shutil, subprocess, sys, time

ROOT=Path(__file__).resolve().parent
STAMP=datetime.now().strftime('%Y%m%d_%H%M%S')
STAGE=ROOT/'build'/('v5_compat_'+STAMP)
BUNDLE=STAGE/'dist'/'TyreTrack'
RELEASE=ROOT/'releases'/('TyreTrack_V5_Compat_'+STAMP)

def run(*args):
    cmd=[str(x) for x in args]
    print('RUN:',subprocess.list2cmdline(cmd),flush=True)
    subprocess.run(cmd,cwd=ROOT,check=True)

def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def check_exe(exe,cwd,label,seconds=12):
    log=STAGE/(label+'_output.txt')
    print('STARTUP TEST:',label,exe,flush=True)
    with log.open('w',encoding='utf-8') as f:
        proc=subprocess.Popen([str(exe)],cwd=cwd,stdout=f,stderr=subprocess.STDOUT)
        try:
            time.sleep(seconds)
            code=proc.poll()
            if code is not None:
                raise RuntimeError(f'{label} exited with {code}. Read {log}')
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
    print(label,'passed',flush=True)

def main():
    if sys.platform!='win32' or sys.version_info[:2]!=(3,12):
        raise RuntimeError('Requires Windows x64 Python 3.12')
    files=['TyreTrack_Production_Monitor.py','count_line.py','hourly_dashboard.py','tyre_detector.pt']
    for name in files:
        if not (ROOT/name).is_file():raise RuntimeError('Missing '+name)
    run(sys.executable,'-m','py_compile',*(ROOT/n for n in files if n.endswith('.py')))
    import torch,torchvision,depthai
    from ultralytics import YOLO
    print('torch',torch.__version__,'torchvision',torchvision.__version__,'depthai',depthai.__version__,flush=True)
    print('torch tensor',torch.zeros(1),flush=True)
    model=YOLO(str(ROOT/'tyre_detector.pt'))
    if model.task!='detect':raise RuntimeError('V5 requires detection model, got '+repr(model.task))
    print('Verified detection model',flush=True)
    STAGE.mkdir(parents=True,exist_ok=False)

    # First prove that torch/c10.dll works in a tiny packaged program.
    probe=STAGE/'torch_probe.py'
    probe.write_text('import torch\nprint("PACKAGED_TORCH_OK",torch.__version__,torch.zeros(1),flush=True)\n',encoding='utf-8')
    run(sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--console',
        '--name','torch_probe','--distpath',STAGE/'probe_dist','--workpath',STAGE/'probe_work',
        '--specpath',STAGE/'probe_spec',probe)
    probe_exe=STAGE/'probe_dist'/'torch_probe'/'torch_probe.exe'
    with (STAGE/'torch_probe_output.txt').open('w',encoding='utf-8') as log:
        result=subprocess.run([str(probe_exe)],cwd=probe_exe.parent,stdout=log,stderr=subprocess.STDOUT,timeout=60)
    if result.returncode!=0:raise RuntimeError('Packaged torch probe failed; see torch_probe_output.txt')
    print((STAGE/'torch_probe_output.txt').read_text(encoding='utf-8',errors='replace'),flush=True)

    # Mirror earlier successful builder: collect-all torchvision and depthai,
    # keep simple onedir packaging and avoid speculative torch/optree edits.
    run(sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed',
        '--name','TyreTrack','--distpath',STAGE/'dist','--workpath',STAGE/'work',
        '--specpath',STAGE/'spec','--paths',ROOT,
        '--hidden-import','lap','--hidden-import','count_line',
        '--hidden-import','hourly_dashboard','--collect-all','torchvision',
        '--collect-all','depthai','--add-data',str(ROOT/'tyre_detector.pt')+';.',
        ROOT/'TyreTrack_Production_Monitor.py')
    exe=BUNDLE/'TyreTrack.exe'
    internal=BUNDLE/'_internal'
    if not exe.is_file():raise RuntimeError('EXE missing')
    # Same duplicate Qt runtime treatment as old working build, conditional.
    for qtbin in [internal/'PyQt5'/'Qt5'/'bin',internal/'PyQt5'/'Qt'/'bin']:
        for name in ('MSVCP140.dll','VCRUNTIME140.dll','VCRUNTIME140_1.dll'):
            local=qtbin/name
            if local.is_file() and (internal/name).is_file():
                backup=STAGE/'qt_duplicate_dll_backup'/qtbin.relative_to(internal)/name
                backup.parent.mkdir(parents=True,exist_ok=True)
                shutil.move(str(local),str(backup))
                print('Moved Qt duplicate',local,flush=True)
    models=list(BUNDLE.rglob('tyre_detector.pt'))
    if len(models)!=1 or sha256(models[0])!=sha256(ROOT/'tyre_detector.pt'):
        raise RuntimeError('Detector hash mismatch or duplicate')
    if list(BUNDLE.rglob('email_config.ini')):raise RuntimeError('Private email config found')
    for name in ('data','reports'):(BUNDLE/name).mkdir(exist_ok=True)
    check_exe(exe,BUNDLE,'startup',12)
    RELEASE.parent.mkdir(exist_ok=True)
    shutil.move(str(BUNDLE),str(RELEASE))
    print('SUCCESS RELEASE:',RELEASE,flush=True)
    print('Camera and email require real-machine tests.',flush=True)

if __name__=='__main__':
    main()

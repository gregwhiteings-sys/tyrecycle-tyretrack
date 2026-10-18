"""Remove only the YOLO11 Segment mask branches, preserving trained box weights."""
from pathlib import Path
from copy import deepcopy
import json,time,torch
from ultralytics import YOLO
from ultralytics.nn.modules.head import Detect,Segment
from ultralytics.nn.tasks import DetectionModel

def prediction_tensor(value):
    while isinstance(value,(list,tuple)):value=value[0]
    if not isinstance(value,torch.Tensor):raise TypeError(f'Unexpected prediction output {type(value)}')
    return value

def convert(source,output):
    torch.set_num_threads(2)
    wrapper=YOLO(str(source));original=wrapper.model.cpu().float().eval()
    head=original.model[-1]
    if type(head) is not Segment:raise ValueError(f'Expected standard Segment head, got {type(head)}')
    nc=head.nc
    converted=deepcopy(original)
    newhead=converted.model[-1]
    newhead.__class__=Detect
    removed=[]
    for name in ['proto','cv4','one2one_cv4','nm','npr']:
        if hasattr(newhead,name):delattr(newhead,name);removed.append(name)
    newhead.type='ultralytics.nn.modules.head.Detect'
    newhead.np=sum(p.numel() for p in newhead.parameters())
    converted.__class__=DetectionModel
    converted.task='detect';converted.args=dict(converted.args,task='detect')
    converted.yaml=deepcopy(converted.yaml)
    converted.yaml['head'][-1][2]='Detect';converted.yaml['head'][-1][3]=[nc]
    if 'yaml_file' in converted.yaml:converted.yaml['yaml_file']=str(converted.yaml['yaml_file']).replace('-seg','')
    # Verify every surviving trained tensor, before and after surgery.
    original_state=original.state_dict()
    for key,value in converted.state_dict().items():
        assert torch.equal(value,original_state[key]), f'Changed trained tensor: {key}'
    torch.manual_seed(42);checks=[]
    with torch.inference_mode():
        for size in [320,480]:
            x=torch.rand(1,3,size,size)
            a=prediction_tensor(original(x))[:,:4+nc,:]
            b=prediction_tensor(converted(x))
            assert a.shape==b.shape,(a.shape,b.shape)
            diff=(a-b).abs().max().item()
            torch.testing.assert_close(a,b,rtol=1e-5,atol=1e-4)
            checks.append(dict(size=size,max_box_score_difference=diff,shape=list(b.shape)))
    # This checkpoint's trained tensors originated in FP16. Verify a half-precision
    # save round-trip is lossless for every surviving tensor before using it.
    saved_model=deepcopy(converted).half()
    for key,value in converted.state_dict().items():
        assert torch.equal(saved_model.state_dict()[key].float(),value.float()), f'FP16 round-trip changed {key}'
    checkpoint={'model':saved_model,'ema':None,'optimizer':None,'epoch':-1,
                'train_args':dict(wrapper.ckpt.get('train_args',{}),task='detect'),
                'version':__import__('ultralytics').__version__,
                'conversion_note':'YOLO11 Segment mask branches removed; trained box/class tensors unchanged. Not retrained.'}
    torch.save(checkpoint,str(output))
    loaded=YOLO(str(output));assert loaded.task=='detect'
    assert loaded.names==wrapper.names
    with torch.inference_mode():
        x=torch.rand(1,3,480,480)
        torch.testing.assert_close(prediction_tensor(loaded.model.float().eval()(x)),
                                   prediction_tensor(converted(x)),rtol=1e-5,atol=1e-4)
    import numpy as np
    r=loaded.predict(np.zeros((360,640,3),dtype=np.uint8),imgsz=480,device='cpu',verbose=False)[0]
    assert r.masks is None
    report={'source':str(source),'output':str(output),'task':'detect','classes':loaded.names,
            'removed':removed,'saved_precision':'FP16; surviving tensor round-trip checked','original_parameters':sum(p.numel() for p in original.parameters()),
            'detection_parameters':sum(p.numel() for p in converted.parameters()),
            'box_equivalence_checks':checks,'reload_and_predict':'passed','field_accuracy':'not tested',
            'warning':'Counting anchor changes from mask bottom to bounding-box bottom; revalidate line counts.'}
    Path(output).with_suffix('.verification.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    return report

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('source');parser.add_argument('output');args=parser.parse_args()
    convert(Path(args.source),Path(args.output))

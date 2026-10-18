"""Foreign-object confirmation, persistent latch and local admin credentials."""
import hashlib,hmac,json,os,math,wave
from array import array
from pathlib import Path

CLASSES=('wheel_rim','person')

def overlap(a,b):
    x0,y0=max(a[0],b[0]),max(a[1],b[1]);x1,y1=min(a[2],b[2]),min(a[3],b[3])
    area=max(0,x1-x0)*max(0,y1-y0)
    return area/max(1,(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-area)

def in_region(box,width,height,top,bottom,left,right):
    # Trapezoid: narrower at the top, wider near the camera.
    x=(box[0]+box[2])/2/width;y=(box[1]+box[3])/2/height
    if not top<=y<=bottom:return False
    t=(y-top)/max(.001,bottom-top)
    l=.5-(.5-left)*(.45+.55*t);r=.5+(right-.5)*(.45+.55*t)
    return l<=x<=r

class Confirmation:
    def __init__(self,hits=3,max_gap=5):self.hits=hits;self.max_gap=max_gap;self.tracks=[]
    def clear(self):self.tracks=[]
    def update(self,detections,now):
        old=[t for t in self.tracks if now-t['time']<=self.max_gap];new=[];used=set();event=None
        for d in detections:
            matches=[(overlap(d['box'],t['box']),i,t) for i,t in enumerate(old) if i not in used and t['class_id']==d['class_id']]
            best=max(matches,default=(0,None,None),key=lambda v:v[0])
            hits=1
            if best[0]>=.2:used.add(best[1]);hits=best[2]['hits']+1
            t=dict(d,hits=hits,time=now);new.append(t)
            if hits>=self.hits and (event is None or d['confidence']>event['confidence']):event=t
        self.tracks=new
        return event

def read_json(path,default):
    p=Path(path)
    if not p.exists():return default
    return json.loads(p.read_text())

def write_json(path,data):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,indent=2));tmp.replace(p)

def set_password(path,password):
    if len(password)<6:raise ValueError('Use at least 6 characters')
    salt=os.urandom(16);digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,200000)
    write_json(path,{'salt':salt.hex(),'digest':digest.hex()})

def verify_password(path,password):
    data=read_json(path,{})
    if not data:return False
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(data['salt']),200000)
    return hmac.compare_digest(digest.hex(),data['digest'])

def make_tone(path):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    if p.exists():return p
    samples=array('h',(int(10000*math.sin(2*math.pi*(880 if i<11025 else 660)*i/22050)) for i in range(22050)))
    with wave.open(str(p),'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(22050);f.writeframes(samples.tobytes())
    return p

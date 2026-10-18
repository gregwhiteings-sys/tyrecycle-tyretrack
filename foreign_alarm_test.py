"""Offline confirmation/region/auth tests; no model, camera, audio or SMTP."""
import ast,json,tempfile
from pathlib import Path
from foreign_alarm import Confirmation,in_region,set_password,verify_password,write_json,read_json

def detection(box=(10,10,30,30),cls=0):return dict(box=box,class_id=cls,confidence=.9)
c=Confirmation()
assert c.update([detection()],0) is None
assert c.update([detection()],1) is None
assert c.update([detection()],2)['class_id']==0
c.clear()
assert c.update([detection()],3) is None
assert c.update([detection((100,100,130,130))],4) is None
assert c.update([],5) is None
assert c.update([detection()],6) is None
c.clear();c.update([detection()],0)
assert c.update([detection()],10) is None
assert in_region((280,160,360,220),640,360,.03,.98,.18,.82)
assert not in_region((0,100,30,140),640,360,.03,.98,.18,.82)
assert not in_region((280,0,320,5),640,360,.03,.98,.18,.82)
with tempfile.TemporaryDirectory() as folder:
 p=Path(folder)/'admin.json';set_password(p,'testpass')
 assert verify_password(p,'testpass') and not verify_password(p,'wrongpass')
 assert 'testpass' not in p.read_text()
 state=Path(folder)/'alarm.json';write_json(state,{'latched':True})
 assert read_json(state,{})['latched']
 write_json(state,{'latched':False});assert not read_json(state,{})['latched']
print('PASS: confirmation, spatial matching, gaps, ROI exclusion, password hash and persistent latch')

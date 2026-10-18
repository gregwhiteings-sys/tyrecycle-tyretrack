"""Exercise the application's actual crossing block with synthetic tracks."""
import ast
import math
from pathlib import Path
from types import SimpleNamespace
from count_line import segment_bounds, crosses_segment
source=Path(__file__).with_name('TyreTrack_Production_Monitor.py').read_text()
tree=ast.parse(source)
worker=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='CounterWorker')
run=next(n for n in worker.body if isinstance(n,ast.FunctionDef) and n.name=='run')
# The crossing statements are contained directly in the detection loop.
loop=next(n for n in ast.walk(run) if isinstance(n,ast.For) and any(isinstance(x,ast.Assign) and any(isinstance(t,ast.Tuple) and [getattr(v,'id','') for v in t.elts]==['ll','rr','ly','ls'] for t in x.targets) for x in n.body))
start=next(i for i,n in enumerate(loop.body) if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Tuple) and getattr(n.targets[0].elts[0],'id','')=='ll')
# Wrap in a one-item loop so the source's cutoff continue remains legal.
block=ast.Module(body=[ast.For(target=ast.Name(id='_once',ctx=ast.Store()),iter=ast.List(elts=[ast.Constant(1)],ctx=ast.Load()),body=loop.body[start:],orelse=[])],type_ignores=[])
compiled=compile(ast.fix_missing_locations(block),'<actual crossing block>','exec')
logged=[]
def make(direction='Top to Bottom'):
 return SimpleNamespace(line_ratio=.59,line_angle=0,line_length=1.,previous_side={},previous_x={},previous_observation_frame={},processing_frame=0,confirmed_ids={1},counted_ids=set(),direction=direction,source_kind='video',running=True,class_votes={},session_type_counts={'passenger':0},session_count=0,counted=SimpleNamespace(emit=lambda *a:None))
def observe(w,y,x=50):
 w.processing_frame+=1
 ns=dict(self=w,width=101,height=101,counting_y=y,cx=x,centre_x=50,track_id=1,confidence=.9,source_name='test',segment_bounds=segment_bounds,crosses_segment=crosses_segment,math=math,V48_HYSTERESIS_PX=5,V48_MAX_TRACK_GAP=20,tyre_class_vote=lambda _:('passenger',1),log_count=lambda *a:logged.append(a))
 exec(compiled,ns)
w=make()
for y in [10,40]: observe(w,y)
assert w.session_count==0,'Crossing removed line at 30% must not count'
for y in [55,59,65,40,70]: observe(w,y)
assert w.session_count==1,'Count once at remaining 59% line'
w=make('Bottom to Top')
for y in [80,40]:observe(w,y)
assert w.session_count==1
w=make();w.line_length=.2
for y in [40,80]:observe(w,y,x=0)
assert w.session_count==0,'Crossing outside segment must not count'
w=make()
observe(w,40);w.processing_frame+=21;observe(w,80)
assert w.session_count==0,'Stale track must not count'
assert all(token not in source for token in ['end_line_pos','end_line_ratio','self.line_ids','line_progress'])
print('PASS: removed-line crossing ignored; single crossing counts once; reverse direction; segment bounds; stale track; second-line controls removed')

# Count the first observed centre just beyond the line, without a far-side delay.
w=make()
for y in [40,55,58,59]:observe(w,y)
assert w.session_count==0
observe(w,60);assert w.session_count==1
for y in [58,60,40,80]:observe(w,y)
assert w.session_count==1, 'Same ID must not count twice after jitter/reversal'
w=make('Bottom to Top')
for y in [80,65,60,59]:observe(w,y)
assert w.session_count==0
observe(w,58);assert w.session_count==1
# A new ID seen only inside the approach deadband must not arm from tiny jitter.
w=make()
for y in [58,60,58,60]:observe(w,y)
assert w.session_count==0
from count_line import count_point
assert count_point((20,20,80,80),101,101)==(50,50)
assert count_point((-20,-20,80,80),101,101)==(40,40)
print('PASS: red-dot centre geometry; first frame beyond line; exact-line touch ignored; once-per-ID; approach jitter rejected')

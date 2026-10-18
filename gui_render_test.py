"""Test real Qt rendering methods without detector dependencies."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import ast,time,threading
from pathlib import Path
from types import SimpleNamespace
import numpy as np,cv2
from PyQt5.QtCore import Qt,pyqtSignal
from PyQt5.QtGui import QImage,QPixmap,QPainter,QPen,QColor
from PyQt5.QtWidgets import QLabel,QApplication
from count_line import segment_bounds
from background_export import CoalescedExport
source=Path(__file__).with_name('TyreTrack_Production_Monitor.py').read_text();tree=ast.parse(source)
node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='VideoLabel')
exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),'<actual VideoLabel>','exec'))
qt=QApplication.instance() or QApplication([])
frame=np.zeros((270,480,3),dtype=np.uint8);frame[:]=(0,0,255)
v=VideoLabel();v.editable=False;v.resize(960,600);v.show();v.set_frame(frame);qt.processEvents()
assert v._pix.width()==480 and v.pixmap() is None
assert v.image_geometry()[:4]==(0,30,960,540)
colour=v.grab().toImage().pixelColor(480,300);assert colour.red()>245 and colour.blue()<10
v.resize(600,600);qt.processEvents();assert abs(v.image_geometry()[1]-131.25)<.01
main=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Main')
method=next(n for n in main.body if isinstance(n,ast.FunctionDef) and n.name=='display_frame')
exec(compile(ast.fix_missing_locations(ast.Module(body=[method],type_ignores=[])),'<actual display_frame>','exec'))
class CountingVideo(VideoLabel):
 conversions=0
 @staticmethod
 def prepare_frame(frame):
  CountingVideo.conversions+=1
  return VideoLabel.prepare_frame(frame)
normal=CountingVideo();full=VideoLabel();full.editable=False;full.resize(1280,720);full.show()
host=SimpleNamespace(video=normal,fullscreen_video=full,gui_timing=lambda *a:None)
display_frame(host,frame)
assert CountingVideo.conversions==1 and normal._pix.cacheKey()==full._pix.cacheKey()
qt.processEvents();assert full.grab().toImage().pixelColor(640,360).red()>245
entered=threading.Event();release=threading.Event();calls=[];errors=[]
def job():
 calls.append(threading.get_ident())
 if len(calls)==1:entered.set();assert release.wait(2)
exporter=CoalescedExport(job,errors.append);start=time.monotonic();exporter.request()
assert time.monotonic()-start<.2 and entered.wait(2)
for _ in range(20):exporter.request()
release.set();assert exporter.wait(2)
assert len(calls)==2 and not errors and all(i!=threading.get_ident() for i in calls)
failed=[]
def bad():raise ValueError('test')
f=CoalescedExport(bad,failed.append);f.request();assert f.wait(2);f.request();assert f.wait(2);assert len(failed)==2
v.close();full.close();normal.close()
print('PASS: BGR colours, native paint, aspect ratio, one fullscreen conversion/shared frame, nonblocking coalesced exports and error recovery')

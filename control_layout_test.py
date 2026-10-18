"""Render real app controls without model/camera dependencies, at several sizes."""
import os,sys,types,tempfile,shutil
from pathlib import Path
os.environ['QT_QPA_PLATFORM']='offscreen'
# Isolated process: exercise Qt layout only; model inference belongs to smoke_test.
for name in ['numpy','cv2','ultralytics']:
    sys.modules[name]=types.ModuleType(name)
sys.modules['ultralytics'].YOLO=object
from PyQt5.QtWidgets import QApplication,QCheckBox,QComboBox,QPushButton,QSlider
source=Path(__file__).resolve().parent
folder=Path(tempfile.mkdtemp(prefix='tyretrack_layout_'))
for name in ['TyreTrack_Production_Monitor.py','count_line.py','hourly_dashboard.py','foreign_alarm.py']:
    shutil.copy2(source/name,folder/name)
sys.path.insert(0,str(folder))
import TyreTrack_Production_Monitor as m
m.Main._confirm_admin_pin=lambda self,action:True
m.Main.tick=lambda self:None
app=QApplication([]);window=m.Main();window.show()
for width,height in [(1366,768),(1280,720),(1100,760)]:
    window.resize(width,height)
    for index in range(window.controls_tabs.count()):
        window.controls_tabs.setCurrentIndex(index)
        for _ in range(5):app.processEvents()
        scroll=window.controls_tabs.widget(index);host=scroll.widget()
        name=window.controls_tabs.tabText(index)
        assert host.width()==scroll.viewport().width(),(name,host.width(),scroll.viewport().width())
        for cls in (QCheckBox,QComboBox,QPushButton,QSlider):
            for control in host.findChildren(cls):
                x=control.mapTo(host,control.rect().topLeft()).x()
                assert x>=0 and x+control.width()<=host.width(),(name,cls.__name__,x,control.width(),host.width())
        print('PASS',width,height,name,'page/viewport',host.width())
    history=window.production_scroll
    for label in window.production_slots:
        assert label.height()>=label.sizeHint().height(), ('Clipped hour label',label.height(),label.sizeHint().height())
    assert history.viewport().height()>=max(label.sizeHint().height() for label in window.production_slots)
    print('PASS',width,height,'two-line hour labels clear of scrollbar')
window.set_flow_rate(4.9)
assert window.flow_flash_timer.isActive() and '#FF5C68' in window.flow_lbl.styleSheet()
window.flash_low_flow()
assert window.flow_lbl.styleSheet()==''
window.flash_low_flow()
assert '#FF5C68' in window.flow_lbl.styleSheet()
for rate in (5.0,8.0):
    window.set_flow_rate(rate)
    assert not window.flow_flash_timer.isActive() and window.flow_lbl.styleSheet()==''
window.set_flow_rate(0.0)
assert window.flow_flash_timer.isActive()
print('PASS: low flow flashes red below 5; 5 and above restore normal colour')
window.close()
# SQLite finalizers may retain temp files on Windows; never confuse layout results
# with best-effort scratch cleanup in this isolated process.
shutil.rmtree(folder,ignore_errors=True)

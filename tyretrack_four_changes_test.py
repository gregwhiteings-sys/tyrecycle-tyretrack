"""Fast, hardware-independent regression checks for the four operator changes."""
import ast
from pathlib import Path
from datetime import datetime, timezone, timedelta
src=Path(__file__).with_name('TyreTrack_Production_Monitor.py').read_text(encoding='utf-8')
mod=ast.parse(src)
hours=next(n for n in mod.body if isinstance(n,ast.FunctionDef) and n.name=='oak_in_operating_hours')
ns={'brisbane_now':lambda:None}
exec(compile(ast.Module(body=[hours],type_ignores=[]),'<hours>','exec'),ns)
zone=timezone(timedelta(hours=10))
for day,h,m,expected in [(19,5,0,True),(19,17,4,True),(19,17,5,False),(17,10,0,False),(18,10,0,False)]:
 assert ns['oak_in_operating_hours'](datetime(2026,10,day,h,m,tzinfo=zone)) is expected
assert 'self._confirm_admin_pin("stop counting")' in src
assert 'self._confirm_admin_pin("close TyreTrack")' in src
assert 'if not scheduled and self.worker is not None and self.worker.isRunning()' in src
assert 'self.oak_view_combo.addItems(["Smooth", "Show Boxes"])' in src
assert 'matched_view = self.oak_view_combo.currentText() == "Show Boxes"' in src
assert 'day_start = now.replace(hour=5, minute=0' in src
assert 'active_shift = now.weekday() < 5 and day_start <= now < cutoff' in src
print('PASS: weekday shift hours, PIN guards, correct display modes, daily No Count shift boundaries')

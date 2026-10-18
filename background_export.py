"""Coalesce export requests; all requested work is serviced by one worker at a time."""
import threading

class CoalescedExport:
    def __init__(self, job, on_error):
        self.job=job;self.on_error=on_error
        self.lock=threading.Lock();self.pending=False;self.running=False;self.thread=None
    def request(self):
        with self.lock:
            self.pending=True
            if self.running:return
            self.running=True
            self.thread=threading.Thread(target=self._run,name='TyreTrack CSV export',daemon=True)
            self.thread.start()
    def _run(self):
        while True:
            with self.lock:
                if not self.pending:
                    self.running=False
                    return
                self.pending=False
            try:self.job()
            except Exception as exc:self.on_error(exc)
    def wait(self, timeout=3):
        with self.lock:thread=self.thread
        if thread:thread.join(timeout)
        return thread is None or not thread.is_alive()

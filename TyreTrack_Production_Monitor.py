# TYRETRACK_YELLOW_LINE_SMALL_MP4_NO_YARD_V2
# TYRECYCLE | TYRETRACK
# Windows 11 conveyor tyre counter
# OAK stream/count update 2026-10-08: independent MP4 writer; matched-frame
# AI view; confidence-gated track continuation; stable-side crossings.
# Reduced OAK stream: 640x360 at 20 FPS; MP4 and preview match 20 FPS.
# Requires on-site validation; does not change detector weights.

import configparser
import csv
import math
import numpy as np
import os
import shutil
import smtplib
import no_count_replies
import sqlite3
import sys
import threading
import queue
import time
import traceback
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
_PORTABLE_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
os.environ["YOLO_CONFIG_DIR"] = str(_PORTABLE_ROOT / "data" / "detector-settings")
(_PORTABLE_ROOT / "data" / "detector-settings").mkdir(parents=True, exist_ok=True)



# Keep DLL search-directory handles alive for the process lifetime.
_DLL_DIR_HANDLES = []
if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
    _site_libraries = Path(sys.executable).resolve().parent / "Lib" / "site-packages"
    if _site_libraries.is_dir():
        _DLL_DIR_HANDLES.append(os.add_dll_directory(str(_site_libraries)))


def bootstrap_frozen_dlls():
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return

    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    exe_dir = Path(sys.executable).parent
    candidates = (
        base,
        base / "torch" / "lib",
        base / "cv2",
        exe_dir / "_internal",
        exe_dir / "_internal" / "torch" / "lib",
    )

    seen = set()
    for directory in candidates:
        try:
            directory = directory.resolve()
            key = str(directory).lower()
            if directory.exists() and key not in seen:
                seen.add(key)
                os.environ["PATH"] = (
                    str(directory)
                    + os.pathsep
                    + os.environ.get("PATH", "")
                )
                if hasattr(os, "add_dll_directory"):
                    _DLL_DIR_HANDLES.append(
                        os.add_dll_directory(str(directory))
                    )
        except Exception:
            pass


bootstrap_frozen_dlls()

import cv2
from ultralytics import YOLO
from count_line import segment_bounds, crosses_segment, count_point
from hourly_dashboard import hourly_snapshot, brisbane_now
from foreign_alarm import CLASSES, Confirmation, in_region, read_json, write_json, set_password, verify_password, make_tone

from PyQt5.QtCore import Qt, QThread, QTimer, QEvent, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QInputDialog,
    QLineEdit,
    QLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QVBoxLayout,
    QWidget,
)

try:
    import depthai as dai
    DEPTHAI_OK = True
except Exception:
    dai = None
    DEPTHAI_OK = False


APP_NAME = "TYRECYCLE | TYRETRACK"
MODEL_NAME = "tyre_detector.pt"
OAK_NAME = "OAK-D S2 PoE / CAT5"
OAK_TEST_NAME = "OAK-D S2 / MANUAL TEST"

IOU_DEFAULT = 0.50
INFERENCE_SIZE = 480

# V4.8 counting configuration
V48_HYSTERESIS_PX = 5
V48_MAX_TRACK_GAP = 20
V48_AGNOSTIC_NMS = True


# Target up to 20 live-preview updates/second, independently of inference.
OAK_PREVIEW_INTERVAL_MS = 50

# Draw the detector geometry faithfully; cosmetic shrinking does not improve detection.
VISUAL_BOX_INSET = 0.0

# Existing conservative conveyor-sized false-positive guard.
MAX_BOX_WIDTH_RATIO = 0.68
MAX_BOX_HEIGHT_RATIO = 0.70
MAX_BOX_AREA_RATIO = 0.32
MAX_BOX_ASPECT_RATIO = 3.60

RECORD_OAK_VIDEO = False
_EMAIL_LOCK = threading.Lock()
_LAST_REPLY_CHECK = 0.0
_OAK_IDLE_LOCK = threading.Lock()
_OAK_IDLE_SECONDS = 10 * 60
_OAK_IDLE = {
    "last_frame": None,
    "since": None,
    "last_count_id": None,
    "sent": False,
    "retry_after": 0.0,
}


def app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(name):
    bundled = Path(getattr(sys, "_MEIPASS", app_dir())) / name
    return bundled if bundled.exists() else app_dir() / name


DATA = app_dir() / "data"
REPORTS = app_dir() / "reports"
OAK_RECORDINGS = DATA / "oak_recordings"
PRIVATE_CONFIG_DIR = DATA / "private_config"

for directory in (
    DATA,
    REPORTS,
    OAK_RECORDINGS,
    PRIVATE_CONFIG_DIR,
):
    directory.mkdir(parents=True, exist_ok=True)

DB = DATA / "tyretrack.db"
LOG = DATA / "tyretrack.log"
EMAIL_INI = PRIVATE_CONFIG_DIR / "email_config.ini"


def log(message):
    try:
        with LOG.open("a", encoding="utf-8") as file:
            file.write(
                f"{datetime.now().isoformat(timespec='seconds')} "
                f"{message}\n"
            )
    except Exception:
        pass


def init_db():
    with sqlite3.connect(DB, timeout=15) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS counts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                source TEXT,
                track_id INTEGER,
                confidence REAL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )

        columns = {
            row[1] for row in con.execute("PRAGMA table_info(counts)")
        }
        if "metric_utc" not in columns:
            con.execute("ALTER TABLE counts ADD COLUMN metric_utc REAL")
        if "tyre_type" not in columns:
            con.execute(
                "ALTER TABLE counts "
                "ADD COLUMN tyre_type TEXT DEFAULT 'unknown'"
            )
        if "class_confidence" not in columns:
            con.execute(
                "ALTER TABLE counts "
                "ADD COLUMN class_confidence REAL DEFAULT 0"
            )


def get_state(key, default=""):
    with sqlite3.connect(DB, timeout=15) as con:
        row = con.execute(
            "SELECT value FROM state WHERE key=?",
            (key,),
        ).fetchone()
    return row[0] if row else default


def set_state(key, value):
    with sqlite3.connect(DB, timeout=15) as con:
        con.execute(
            """
            INSERT INTO state(key,value) VALUES(?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, str(value)),
        )


def db_count_since(start):
    with sqlite3.connect(DB, timeout=15) as con:
        return con.execute(
            "SELECT COUNT(*) FROM counts WHERE ts>=?",
            (start.isoformat(),),
        ).fetchone()[0]


def db_count_between(start, end):
    """Count persisted events in the clock hour, without clearing history."""
    with sqlite3.connect(DB, timeout=15) as con:
        return con.execute(
            "SELECT COUNT(*) FROM counts WHERE ts>=? AND ts<?",
            (start.isoformat(), end.isoformat()),
        ).fetchone()[0]


def db_total():
    with sqlite3.connect(DB, timeout=15) as con:
        return con.execute(
            "SELECT COUNT(*) FROM counts"
        ).fetchone()[0]



from collections import defaultdict

def tyre_class_name(name):
    name = str(name).strip().lower()

    if name == "passenger":
        return "passenger"

    if name in ("4x4", "suv", "suv/4x4", "suv / 4x4"):
        return "4x4"

    return "unknown"


def tyre_class_vote(votes):
    if not votes:
        return "unknown", 0.0

    passenger = float(votes.get("passenger", 0))
    suv = float(votes.get("4x4", 0))

    total = passenger + suv

    if total <= 0:
        return "unknown", 0.0

    if passenger >= suv:
        return "passenger", passenger / total

    return "4x4", suv / total


def classification_counts(start=None, end=None, source=None):
    conditions = []
    params = []

    if start is not None:
        conditions.append("ts >= ?")
        params.append(start.isoformat())

    if end is not None:
        conditions.append("ts < ?")
        params.append(end.isoformat())

    if source is not None:
        conditions.append("source = ?")
        params.append(source)

    where = (
        " WHERE " + " AND ".join(conditions)
        if conditions else ""
    )

    with sqlite3.connect(DB, timeout=15) as con:
        rows = con.execute(
            "SELECT tyre_type, COUNT(*) FROM counts"
            + where
            + " GROUP BY tyre_type",
            params
        ).fetchall()

    result = {
        "passenger": 0,
        "4x4": 0,
        "unknown": 0
    }

    for name, count in rows:
        result[tyre_class_name(name)] += count

    return result


def log_oak_test_count(source, track_id, confidence, tyre_type, class_confidence):
    # Use an independent DB, so legacy CSV exports cannot absorb manual tests.
    with sqlite3.connect(DATA / "oak_manual_tests.db", timeout=15) as con:
        con.execute("CREATE TABLE IF NOT EXISTS counts (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, source TEXT, track_id INTEGER, confidence REAL, tyre_type TEXT, class_confidence REAL, metric_utc REAL)")
        con.execute("INSERT INTO counts(ts,source,track_id,confidence,tyre_type,class_confidence,metric_utc) VALUES(?,?,?,?,?,?,?)",
                    (brisbane_now().isoformat(),source,int(track_id),float(confidence),
                     tyre_class_name(tyre_type),float(class_confidence),time.time()))


def log_count(
    source,
    track_id,
    confidence,
    tyre_type="unknown",
    class_confidence=0.0
):
    if source == OAK_TEST_NAME:
        log_oak_test_count(source,track_id,confidence,tyre_type,class_confidence)
        return
    tyre_type = tyre_class_name(tyre_type)

    with sqlite3.connect(DB, timeout=15) as con:
        con.execute(
            """
            INSERT INTO counts(
                ts,source,track_id,confidence,
                tyre_type,class_confidence,metric_utc
            )
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                datetime.now().isoformat(),
                source,
                int(track_id),
                float(confidence),
                tyre_type,
                float(class_confidence),
                time.time()
            )
        )



def export_classification_csv(day=None):
    """Export hourly Passenger / SUV / Unknown totals."""

    if day is None:
        day = datetime.now().date()

    start = datetime.combine(
        day,
        datetime.min.time()
    )

    end = start + timedelta(days=1)

    output = REPORTS / (
        f"TyreTrack_Classification_{day:%Y%m%d}.csv"
    )

    output.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with sqlite3.connect(DB, timeout=15) as con:
        rows = con.execute(
            """
            SELECT
                substr(ts, 12, 2) AS hour,
                tyre_type,
                COUNT(*)
            FROM counts
            WHERE ts >= ? AND ts < ?
            GROUP BY hour, tyre_type
            ORDER BY hour, tyre_type
            """,
            (
                start.isoformat(),
                end.isoformat()
            )
        ).fetchall()

    hours = {}

    for hour, tyre_type, count in rows:
        if hour not in hours:
            hours[hour] = {
                "passenger": 0,
                "4x4": 0,
                "unknown": 0
            }

        category = tyre_class_name(tyre_type)
        hours[hour][category] += count

    with open(
        output,
        "w",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.writer(file)

        writer.writerow([
            "Date",
            "Hour",
            "Passenger",
            "SUV / 4x4",
            "Unknown",
            "Total"
        ])

        for hour, totals in sorted(hours.items()):
            passenger = totals["passenger"]
            suv = totals["4x4"]
            unknown = totals["unknown"]

            writer.writerow([
                str(day),
                hour,
                passenger,
                suv,
                unknown,
                passenger + suv + unknown
            ])

    return output


def export_daily_csv(day=None):
    """Total-only daily report; retain all stored count records."""
    if day is None:
        day = datetime.now().date()
    elif isinstance(day, str):
        day = datetime.fromisoformat(day).date()

    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)

    with sqlite3.connect(DB, timeout=15) as con:
        rows = con.execute(
            "SELECT ts FROM counts WHERE ts>=? AND ts<?",
            (start.isoformat(), end.isoformat()),
        ).fetchall()

    hourly = [0] * 24
    for (timestamp,) in rows:
        try:
            hourly[datetime.fromisoformat(timestamp).hour] += 1
        except ValueError:
            log(f"Invalid count timestamp: {timestamp!r}")

    path = REPORTS / f"TyreTrack_{day.isoformat()}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["Date", "Hour", "Total"])
        for hour, count in enumerate(hourly):
            if count:
                writer.writerow(
                    [
                        day.strftime("%d/%m/%Y"),
                        f"{hour:02d}:00-{hour:02d}:59",
                        count,
                    ]
                )
        writer.writerow([])
        writer.writerow(["TOTAL", "", sum(hourly)])

    return path


def ensure_email_config():
    if not EMAIL_INI.exists():
        EMAIL_INI.write_text(
            "[EMAIL]\n"
            "sender_email = your_gmail@gmail.com\n"
            "sender_app_password = your_app_password\n"
            "recipient_email = recipient@example.com\n"
            "supervisor_email = supervisor@example.com\n",
            encoding="utf-8",
        )


def email_settings():
    ensure_email_config()
    config = configparser.ConfigParser(interpolation=None)
    config.read(EMAIL_INI, encoding="utf-8")

    sender = config.get(
        "EMAIL", "sender_email", fallback=""
    ).strip()
    password = config.get(
        "EMAIL", "sender_app_password", fallback=""
    ).replace(" ", "")
    recipient = config.get(
        "EMAIL", "recipient_email", fallback=""
    ).strip()

    if (
        not sender
        or "your_" in sender
        or not password
        or "your_" in password
        or not recipient
        or "example.com" in recipient
    ):
        return None

    return sender, password, recipient


def supervisor_recipient():
    """Separate supervisor address; falls back to existing report recipient."""
    config = configparser.ConfigParser(interpolation=None)
    config.read(EMAIL_INI, encoding="utf-8")
    address = config.get("EMAIL", "supervisor_email", fallback="").strip()
    if not address or "example.com" in address:
        settings = email_settings()
        return settings[2] if settings else ""
    return address


def send_message(subject, body, snapshot=None, recipient_override=None):
    settings = email_settings()
    if settings is None:
        log(f"Email waiting for configuration: {EMAIL_INI}")
        return False

    sender, password, recipient = settings
    recipient = recipient_override or recipient
    message = MIMEMultipart() if snapshot and Path(snapshot).is_file() else MIMEText(body)
    if message.is_multipart():
        message.attach(MIMEText(body))
        with open(snapshot, "rb") as picture:
            attachment = MIMEImage(picture.read(), _subtype="png")
        attachment.add_header("Content-Disposition", "attachment", filename="TyreTrack_Dashboard.png")
        message.attach(attachment)
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient

    try:
        with smtplib.SMTP_SSL(
            "smtp.gmail.com", 465, timeout=20
        ) as smtp:
            smtp.login(sender, password)
            smtp.send_message(message)
        log(f"Gmail accepted: {subject}")
        return True
    except Exception as exc:
        log(
            f"Gmail failed: {type(exc).__name__}: {exc}"
        )
        return False


DASHBOARD_SNAPSHOT = DATA / "dashboard_snapshot.png"

def production_snapshot(now=None):
    """Single Brisbane-time source for dashboard and reporting. OAK events only."""
    now = (now or brisbane_now()).astimezone(brisbane_now().tzinfo)
    day_start = now.replace(hour=5, minute=0, second=0, microsecond=0)
    cutoff = day_start.replace(hour=17, minute=5)
    effective = min(max(now, day_start), cutoff)
    with sqlite3.connect(DB, timeout=15) as con:
        slots, best, _ = hourly_snapshot(con, source=OAK_NAME, now=effective)
        rows = con.execute("SELECT ts, metric_utc FROM counts WHERE source=? ORDER BY id", (OAK_NAME,)).fetchall()
        all_time = con.execute("SELECT COUNT(*) FROM counts WHERE source=?", (OAK_NAME,)).fetchone()[0]
    events = []
    for ts, epoch in rows:
        try:
            stamp = (datetime.fromtimestamp(epoch, now.tzinfo) if epoch is not None
                     else datetime.fromisoformat(ts).replace(tzinfo=now.tzinfo))
            if day_start <= stamp < cutoff and stamp <= effective:
                events.append(stamp)
        except (ValueError, TypeError, OverflowError):
            continue
    events.sort()
    # The No Count incident window is scoped to this Brisbane shift only.
    # Weekends and pre-shift hours must not accrue new incidents.
    active_shift = now.weekday() < 5 and day_start <= now < cutoff
    incidents = 0
    alert_times = []
    no_flow = 0.0
    if events:
        for previous, following in zip([day_start] + events[:-1], events):
            gap = (following - previous).total_seconds()
            if gap >= 600:
                incidents += 1
                alert_times.append(previous + timedelta(minutes=10))
                no_flow += gap
        trailing = max(0, (effective - events[-1]).total_seconds())
        if trailing >= 600:
            incidents += 1
            alert_times.append(events[-1] + timedelta(minutes=10))
            no_flow += trailing
        status = "WEEKEND" if now.weekday() >= 5 else "CLOSED" if now >= cutoff else "NOT OPEN" if now < day_start else "NO COUNT" if trailing >= 600 else "RUNNING"
    else:
        trailing = max(0, (effective - day_start).total_seconds())
        if active_shift and trailing >= 600:
            incidents = 1
            alert_times.append(day_start + timedelta(minutes=10))
            no_flow = trailing
        status = "WEEKEND" if now.weekday() >= 5 else "CLOSED" if now >= cutoff else "NOT OPEN" if now < day_start else "NO COUNT" if trailing >= 600 else "WAITING"
    current = next((slot for slot in slots if slot['start'] and slot['start'] <= effective < slot['end']), None)
    recent = sum(1 for event in events if effective - timedelta(minutes=5) <= event <= effective)
    return dict(day_start=day_start, day_end=cutoff, daily=len(events), all_time=all_time,
                current_hour=current['count'] if current else 0,
                best_count=best['count'] if best else 0,
                no_count_incidents=incidents, no_flow_minutes=no_flow / 60,
                no_count_status=status, alert_times=alert_times, flow_per_minute=recent / 5)


def best_daily_record(now=None):
    now = now or brisbane_now()
    totals = defaultdict(int)
    with sqlite3.connect(DB, timeout=15) as con:
        rows = con.execute("SELECT ts, metric_utc FROM counts WHERE source=?", (OAK_NAME,)).fetchall()
    for ts, epoch in rows:
        try:
            stamp = (datetime.fromtimestamp(epoch, now.tzinfo) if epoch is not None
                     else datetime.fromisoformat(ts))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=now.tzinfo)
            stamp = stamp.astimezone(now.tzinfo)
            begin = stamp.replace(hour=5, minute=0, second=0, microsecond=0)
            if begin <= stamp < begin.replace(hour=17, minute=5) and stamp <= now:
                totals[stamp.date().isoformat()] += 1
        except (ValueError, TypeError, OverflowError):
            continue
    return max(totals.items(), key=lambda item: (item[1], item[0])) if totals else (None, 0)


def production_report_counts(start, end):
    """One OAK-only database query for hourly, daily and classification counts."""
    brisbane = timezone(timedelta(hours=10))
    def as_brisbane(value):
        return value.replace(tzinfo=brisbane) if value.tzinfo is None else value.astimezone(brisbane)
    start, end = as_brisbane(start), as_brisbane(end)
    day_start = start.replace(hour=5, minute=0, second=0, microsecond=0)
    with sqlite3.connect(DB, timeout=15) as con:
        rows = con.execute(
            "SELECT metric_utc, tyre_type FROM counts "
            "WHERE source=? AND metric_utc>=? AND metric_utc<? ORDER BY metric_utc",
            (OAK_NAME, day_start.timestamp(), end.timestamp())
        ).fetchall()
        all_time = con.execute("SELECT COUNT(*) FROM counts WHERE source=?", (OAK_NAME,)).fetchone()[0]
    hourly_types = {"passenger": 0, "4x4": 0, "unknown": 0}
    daily_types = {"passenger": 0, "4x4": 0, "unknown": 0}
    hourly = 0
    for epoch, tyre_type in rows:
        kind = tyre_class_name(tyre_type)
        daily_types[kind] += 1
        if start.timestamp() <= epoch < end.timestamp():
            hourly += 1
            hourly_types[kind] += 1
    return dict(hourly=hourly, daily=len(rows), all_time=all_time,
                hourly_types=hourly_types, daily_types=daily_types,
                start=start, end=end)


def build_hourly_report(start, end):
    """Pure report formatter; used by live SMTP and the offline validation suite."""
    r = production_report_counts(start, end)
    start, end = r['start'], r['end']
    h, d = r['hourly_types'], r['daily_types']
    period_minutes = (end-start).total_seconds()/60
    flow = r['hourly'] / period_minutes if period_minutes > 0 else 0.0
    body = (
        "TYRECYCLE | TYRETRACK\n\n"
        f"Period: {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} (Brisbane time)\n\n"
        f"OAK HOURLY TOTAL: {r['hourly']}\n"
        f"Passenger: {h['passenger']}\n"
        f"SUV / 4x4: {h['4x4']}\n"
        f"Unknown: {h['unknown']}\n"
        f"AVERAGE FLOW: {flow:.2f} tyres/min\n\n"
        f"OAK DAILY TOTAL: {r['daily']}\n"
        f"Passenger: {d['passenger']}\n"
        f"SUV / 4x4: {d['4x4']}\n"
        f"Unknown: {d['unknown']}\n\n"
        f"ALL-TIME TOTAL: {r['all_time']}\n"
        "Video-file and webcam tests are excluded.\n"
        "A zero count does not prove that the camera was online.\n"
    )
    subject = f"TYRETRACK OAK Hourly Count - {start:%Y-%m-%d %H:%M} - {r['hourly']} tyres"
    return subject, body


def send_hourly_email(start, end):
    subject, body = build_hourly_report(start, end)
    try:
        body += "\n" + no_count_replies.summary(DB, start.strftime("%Y-%m-%d"))
    except Exception as exc:
        log(f"Supervisor report summary unavailable: {exc}")
    return send_message(subject, body, DASHBOARD_SNAPSHOT)


def oak_in_operating_hours(now=None):
    now = now or brisbane_now()
    return now.weekday() < 5 and 5 <= now.hour and (now.hour < 17 or (now.hour == 17 and now.minute < 5))


def oak_session_allowed(manual_test=False):
    return bool(manual_test) or oak_in_operating_hours()


def oak_stream_stopped():
    with _OAK_IDLE_LOCK:
        _OAK_IDLE.update(
            last_frame=None,
            since=None,
            last_count_id=None,
            sent=False,
            retry_after=0.0,
        )


def oak_frame_received():
    now = time.monotonic()

    if not oak_in_operating_hours():
        oak_stream_stopped()
        return

    with _OAK_IDLE_LOCK:
        if (
            _OAK_IDLE["last_frame"] is None
            or now - _OAK_IDLE["last_frame"] > 30
        ):
            with sqlite3.connect(DB, timeout=15) as con:
                newest_id = con.execute(
                    "SELECT COALESCE(MAX(id),0) FROM counts "
                    "WHERE source=?",
                    (OAK_NAME,),
                ).fetchone()[0]

            _OAK_IDLE["last_count_id"] = newest_id
            _OAK_IDLE["since"] = now  # Begin monitoring with first live frame, before first tyre.
            _OAK_IDLE["sent"] = False
            _OAK_IDLE["retry_after"] = 0.0

        _OAK_IDLE["last_frame"] = now


def oak_no_count_alert_tick():
    if not oak_in_operating_hours():
        return

    now = time.monotonic()

    with _OAK_IDLE_LOCK:
        last_frame = _OAK_IDLE["last_frame"]
        since = _OAK_IDLE["since"]
        if (
            last_frame is None
            or since is None
            or now - last_frame > 30
        ):
            return

        with sqlite3.connect(DB, timeout=15) as con:
            newest_id = con.execute(
                "SELECT COALESCE(MAX(id),0) FROM counts "
                "WHERE source=?",
                (OAK_NAME,),
            ).fetchone()[0]

        if newest_id != _OAK_IDLE["last_count_id"]:
            # Close the previous No Count event when counting resumes.
            try:
                no_count_replies.close_incident(DB, brisbane_now().strftime("%Y-%m-%d %H:%M:%S"))
            except Exception as exc:
                log(f"No Count incident close failed: {exc}")
            _OAK_IDLE["last_count_id"] = newest_id
            _OAK_IDLE["since"] = now  # Reset after a genuine tyre count.
            _OAK_IDLE["sent"] = False
            _OAK_IDLE["retry_after"] = 0.0
            return

        if (
            _OAK_IDLE["sent"]
            or now - since < _OAK_IDLE_SECONDS
            or now < _OAK_IDLE["retry_after"]
        ):
            return

        _OAK_IDLE["retry_after"] = now + 300

    threshold_at = brisbane_now()
    incident_start = threshold_at - timedelta(seconds=now-since)
    incident_id = f"NC-{incident_start:%Y%m%d-%H%M%S}"
    body = (
        "TYRECYCLE | TYRETRACK - SUPERVISOR NO COUNT REPORT\n\n"
        f"Incident ID: {incident_id}\n"
        f"No counts since: {incident_start:%Y-%m-%d %H:%M:%S} Brisbane\n"
        f"Alert raised: {threshold_at:%Y-%m-%d %H:%M:%S} Brisbane\n"
        "No OAK tyre counts for at least 10 minutes while live frames were received.\n"
        "This does not prove that a tyre passed undetected.\n\n"
        "SUPERVISOR: Please REPLY to this email. Complete these three lines "
        "at the TOP of your reply (keep the incident ID in the subject):\n\n"
        "Reason: [Waiting for tyres / Scheduled break / Conveyor stopped / Maintenance / "
        "Conveyor blockage / Camera issue / Other]\n"
        "Supervisor: [Your name]\n"
        "Comments: [Optional details]\n\n"
        "Please do not remove the incident ID from the subject.\n"
    )
    sent = send_message(f"TYRETRACK NO COUNT {incident_id} - Supervisor response required", body,
                        recipient_override=supervisor_recipient())
    if sent:
        try:
            no_count_replies.create_incident(DB, incident_id,
                incident_start.strftime("%Y-%m-%d %H:%M:%S"),
                threshold_at.strftime("%Y-%m-%d %H:%M:%S"))
        except Exception as exc:
            log(f"No Count incident persistence failed: {exc}")

    if sent:
        with _OAK_IDLE_LOCK:
            if newest_id == _OAK_IDLE["last_count_id"]:
                _OAK_IDLE["sent"] = True


def send_daily_production_email(now):
    """Send one Brisbane shift report; caller records success for retry safety."""
    metrics = production_snapshot(now)
    with sqlite3.connect(DB, timeout=15) as con:
        slots, best, _ = hourly_snapshot(con, source=OAK_NAME, now=now)
    completed = [slot for slot in slots if slot['completed']]
    average = sum(slot['count'] for slot in completed) / len(completed) if completed else 0
    peak = max((slot['count'] / max((slot['end']-slot['start']).total_seconds()/60, 1/60)
                for slot in completed), default=0)
    best_period = f"{best['start']:%H:%M}-{best['end']:%H:%M}" if best else "None"
    body = (f"TYRECYCLE | TYRETRACK DAILY PRODUCTION REPORT\n"
            f"Brisbane shift: {metrics['day_start']:%Y-%m-%d 05:00}–17:05\n\n"
            f"Total tyres: {metrics['daily']:,}\n"
            f"Best completed hour: {metrics['best_count']:,} ({best_period})\n"
            f"Average hourly production (completed slots incl. zero): {average:.1f}\n"
            f"Best completed-slot flow average: {peak:.2f} tyres/min\n"
            f"No-count incidents (10+ minutes): {metrics['no_count_incidents']}\n"
            f"No-flow time in incidents: {metrics['no_flow_minutes']:.1f} minutes\n"
            "Camera availability and missed detections are not verified by counts alone.\n")
    try:
        body += "\n" + no_count_replies.summary(DB, now.strftime("%Y-%m-%d"))
    except Exception as exc:
        log(f"Supervisor report summary unavailable: {exc}")
    return send_message(f"TYRETRACK Daily Production - {now:%Y-%m-%d}", body, DASHBOARD_SNAPSHOT)


def process_scheduled_emails(now):
    """Send 05:00-17:00 hourly reports plus final 17:00-17:05 report.

    `now` is expected in Brisbane local time. Never send outside production
    reporting boundaries; failed sends remain queued for retry.
    """
    if not _EMAIL_LOCK.acquire(blocking=False):
        return
    try:
        global _LAST_REPLY_CHECK
        # Inbox failures must never stop outgoing production reports.
        if time.monotonic() - _LAST_REPLY_CHECK >= 120:
            _LAST_REPLY_CHECK = time.monotonic()
            settings = email_settings()
            if settings is not None:
                sender, password, _ = settings
                try:
                    no_count_replies.check_gmail(DB, sender, password, supervisor_recipient(), log)
                except Exception as exc:
                    log(f"Supervisor Gmail reply check failed (outgoing reports continue): {exc}")
        if now.tzinfo is not None:
            now = now.astimezone(timezone(timedelta(hours=10))).replace(tzinfo=None)
        oak_no_count_alert_tick()
        today_open = now.replace(hour=5, minute=0, second=0, microsecond=0)
        today_close = today_open.replace(hour=17, minute=5)
        if now < today_open:
            return
        stored = get_state("hourly_next_start", "")
        try:
            cursor = datetime.fromisoformat(stored) if stored else today_open
        except ValueError:
            cursor = today_open
        if cursor < today_open or cursor > today_close:
            cursor = today_open
        for _ in range(13):
            if cursor >= today_close:
                break
            end = min(cursor + timedelta(hours=1), today_close)
            if now < end:
                break
            if not send_hourly_email(cursor, end):
                return
            cursor = end
            set_state("hourly_next_start", cursor.isoformat())
        if now >= today_close and cursor >= today_close:
            daily_key = f"daily_sent_{today_open:%Y%m%d}"
            if get_state(daily_key, "") != "1":
                if send_daily_production_email(today_close.replace(tzinfo=timezone(timedelta(hours=10)))):
                    set_state(daily_key, "1")
    except Exception:
        log("Hourly scheduler error:\n" + traceback.format_exc())
    finally:
        _EMAIL_LOCK.release()


def set_oak_recording(enabled):
    global RECORD_OAK_VIDEO
    RECORD_OAK_VIDEO = bool(enabled)
    log(
        "OAK recording "
        + ("enabled" if enabled else "disabled")
    )


def acceptable_box(box, width, height):
    x1, y1, x2, y2 = box
    box_width = max(1.0, float(x2 - x1))
    box_height = max(1.0, float(y2 - y1))
    aspect = max(
        box_width / box_height,
        box_height / box_width,
    )

    return not (
        box_width / width > MAX_BOX_WIDTH_RATIO
        or box_height / height > MAX_BOX_HEIGHT_RATIO
        or (box_width * box_height) / (width * height)
        > MAX_BOX_AREA_RATIO
        or aspect > MAX_BOX_ASPECT_RATIO
    )


def draw_detections(frame, detections):
    height, width = frame.shape[:2]

    for x1, y1, x2, y2, track_id, confidence in detections:
        x1 = max(0, min(width - 1, x1))
        x2 = max(0, min(width - 1, x2))
        y1 = max(0, min(height - 1, y1))
        y2 = max(0, min(height - 1, y2))

        if x2 <= x1 or y2 <= y1:
            continue

        centre_x, centre_y = count_point((x1,y1,x2,y2),width,height)

        inset_x = round((x2 - x1) * VISUAL_BOX_INSET)
        inset_y = round((y2 - y1) * VISUAL_BOX_INSET)

        bx1, by1 = x1 + inset_x, y1 + inset_y
        bx2, by2 = x2 - inset_x, y2 - inset_y

        cv2.rectangle(
            frame,
            (bx1, by1),
            (bx2, by2),
            (20, 255, 57),
            1,
        )
        cv2.circle(
            frame,
            (centre_x, centre_y),
            5,
            (0, 0, 255),
            -1,
        )
        cv2.putText(
            frame,
            f"TYRE ID {track_id}  D:{confidence:.2f}",
            (bx1, max(24, by1 - 7)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            (20, 255, 57),
            2,
        )


def draw_line_and_total(
    frame, line_ratio, line_angle, line_length, total
):
    height, width = frame.shape[:2]
    left, right, line_y, slope = segment_bounds(
        width,
        height,
        line_ratio,
        line_angle,
        line_length,
    )
    centre_x = (width - 1) / 2
    y_left = round(line_y + slope * (left - centre_x))
    y_right = round(line_y + slope * (right - centre_x))

    # Yellow in OpenCV's BGR colour order.
    yellow = (0, 255, 255)

    cv2.line(
        frame,
        (round(left), y_left),
        (round(right), y_right),
        yellow,
        4,
    )
    cv2.putText(
        frame,
        "COUNT LINE",
        (18, max(32, round(line_y) - 12)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        yellow,
        2,
    )
    # Small, content-sized video overlay; counting logic is unchanged.
    total_text = f"TOTAL {total}"
    font = cv2.FONT_HERSHEY_SIMPLEX
    total_width = cv2.getTextSize(total_text, font, 0.55, 1)[0][0]
    box_right = min(width - 8, 18 + total_width)
    box_bottom = 35

    cv2.rectangle(
        frame, (8, 8), (box_right, box_bottom), (0, 0, 0), -1
    )
    cv2.putText(
        frame, total_text, (13, 27), font, 0.55,
        (255, 255, 255), 1, cv2.LINE_AA
    )



class OakSource:
    """
    Capture the newest OAK frame independently of inference.

    If recording is enabled, save on-device H264 as unannotated
    640x360 MP4 segments, rotated at a keyframe after 15 minutes.
    """

    def __init__(self, ip="", foreign_enabled=False):
        if not DEPTHAI_OK:
            raise RuntimeError(
                "DepthAI is not installed. Run INSTALL_DEPTHAI.bat."
            )

        self.device = None
        self.queue = None
        self.reader = None
        self.preview_reader = None
        self.recorder = None
        self.record_drops = 0
        self.last_read_frame_at = None

        self.stop_event = threading.Event()
        self.condition = threading.Condition()
        self.latest_frame = None
        self.latest_frame_at = None
        self.sequence = 0
        self.last_read_sequence = 0
        self.reader_error = None

        self.capture_fps = 20.0
        self.previous_camera_sequence = None
        self.live_frame = None
        self.live_frame_at = None
        self.live_sequence = -1
        self.live_error = None
        self.preview_received = 0
        self.preview_max_age_ms = 0.0
        self.preview_max_loop_gap_ms = 0.0
        self.preview_last_received = time.monotonic()
        self.capture_diagnostic_since = time.monotonic()
        self.sequence_gaps = 0
        self.largest_sequence_gap = 0
        self.nn_received = 0
        self.pair_received = 0
        self.max_nn_age_ms = 0.0
        self.max_capture_loop_gap_ms = 0.0
        self.worker_last_read_at = time.monotonic()
        self.worker_max_read_gap_ms = 0.0

        from oak_rvc2 import build_pipeline
        pipeline = build_pipeline(dai, resource_path("rvc2_models"), recording=True, foreign_enabled=foreign_enabled)
        self.capture_fps = 15.0
        self.latest_neural_output = None
        self.last_read_neural_output = None
        self.pending_frames = {}
        self.pending_outputs = {}
        self.foreign_enabled = foreign_enabled
        self.pending_foreign_frames = {}
        self.pending_foreign_outputs = {}
        self.latest_foreign = None
        self.last_foreign_sequence = -1
        self.foreign_started_at = time.monotonic()
        self.foreign_received_at = self.foreign_started_at
        self.foreign_pair_count = 0

        try:
            if ip.strip():
                self.device = dai.Device(
                    pipeline, dai.DeviceInfo(ip.strip())
                )
            else:
                self.device = dai.Device(pipeline)

            self.queue = self.device.getOutputQueue(
                "rgb",
                maxSize=4,
                blocking=False,
            )

            self.nn_queue = self.device.getOutputQueue("nn", maxSize=8, blocking=False)
            self.foreign_frame_queue = self.device.getOutputQueue("foreign_rgb", maxSize=4, blocking=False) if foreign_enabled else None
            self.foreign_nn_queue = self.device.getOutputQueue("foreign_nn", maxSize=8, blocking=False) if foreign_enabled else None
            self.live_queue = self.device.getOutputQueue("live", maxSize=1, blocking=False)
            self.encoded_queue = self.device.getOutputQueue("recording", maxSize=120, blocking=False)
            self.preview_reader = threading.Thread(target=self._preview_loop,
                name="OAK independent live preview", daemon=True)
            self.preview_reader.start()
            self.reader = threading.Thread(
                target=self._capture_loop,
                name="OAK RGB capture and recorder",
                daemon=True,
            )
            self.recorder = threading.Thread(target=self._record_loop,
                                             name="OAK H264 stream-copy recorder", daemon=True)
            self.recorder.start()
            self.reader.start()

        except Exception:
            self.release()
            raise

    def _packet_age_ms(self, packet):
        try:
            return max(0.0, (dai.Clock.now()-packet.getTimestamp()).total_seconds()*1000)
        except Exception:
            return -1.0

    def _preview_loop(self):
        previous_loop = time.monotonic()
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                gap_ms = (now-previous_loop)*1000
                previous_loop = now
                packet = self.live_queue.tryGet()
                with self.condition:
                    self.preview_max_loop_gap_ms = max(self.preview_max_loop_gap_ms, gap_ms)
                if packet is None:
                    time.sleep(.003)
                    continue
                frame = packet.getCvFrame()
                received = time.monotonic()
                age = self._packet_age_ms(packet)
                with self.condition:
                    self.live_frame = frame
                    self.live_sequence = int(packet.getSequenceNum())
                    self.live_frame_at = received
                    self.preview_last_received = received
                    self.preview_received += 1
                    self.preview_max_age_ms = max(self.preview_max_age_ms, age)
        except Exception as exc:
            self.live_error = str(exc)
            log("OAK live preview FAULT; counting stream remains independent: "+str(exc))

    def _stream_diagnostics(self, now, last_pair):
        elapsed = now-self.capture_diagnostic_since
        if elapsed < 10:
            return
        with self.condition:
            live_silence = now-self.preview_last_received
            message = ("OAK stream summary: "
                f"camera_rx_fps={self.preview_received/elapsed:.1f}, "
                f"nn_rx_fps={self.nn_received/elapsed:.1f}, matched_fps={self.pair_received/elapsed:.1f}, "
                f"sequence_gaps={self.sequence_gaps}, largest_sequence_gap={self.largest_sequence_gap}; "
                "gaps include inference skips and host latest-frame selection, not proven network loss; "
                f"live_silence_ms={live_silence*1000:.0f}, ai_silence_ms={(now-last_pair)*1000:.0f}, "
                f"max_camera_age_ms={self.preview_max_age_ms:.0f}, max_nn_age_ms={self.max_nn_age_ms:.0f}, "
                f"max_capture_loop_pause_ms={self.max_capture_loop_gap_ms:.0f}, "
                f"max_preview_loop_pause_ms={self.preview_max_loop_gap_ms:.0f}, "
                f"max_worker_read_gap_ms={self.worker_max_read_gap_ms:.0f}, "
                f"recording=on-device-H264, recording_packet_gaps={self.record_drops}")
            self.preview_received = 0
            self.preview_max_age_ms = 0.0
            self.preview_max_loop_gap_ms = 0.0
            self.worker_max_read_gap_ms = 0.0
        log(message)
        self.nn_received = self.pair_received = self.sequence_gaps = self.largest_sequence_gap = 0
        self.max_nn_age_ms = self.max_capture_loop_gap_ms = 0.0
        self.capture_diagnostic_since = now

    def _capture_loop(self):
        last_packet_at = time.monotonic()
        previous_loop = last_packet_at
        stall_reported = False

        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                self.max_capture_loop_gap_ms = max(self.max_capture_loop_gap_ms, (now-previous_loop)*1000)
                previous_loop = now
                self._stream_diagnostics(now, last_packet_at)
                if now-last_packet_at > 1.0 and not stall_reported:
                    with self.condition:
                        preview_silence = now-self.preview_last_received
                    log(f"OAK AI STALL: no matched result for {now-last_packet_at:.2f}s; "
                        f"preview_silence={preview_silence:.2f}s; pending_frames={len(self.pending_frames)}, "
                        f"pending_outputs={len(self.pending_outputs)}; inspect stream summary for timing")
                    stall_reported = True
                # Foreign frames pair independently: never apply a stale hazard to a new tyre frame.
                if self.foreign_enabled:
                    from oak_rvc2 import read_layers, unletterbox
                    for q, pending in [(self.foreign_frame_queue,self.pending_foreign_frames),
                                       (self.foreign_nn_queue,self.pending_foreign_outputs)]:
                        for _ in range(32):
                            incoming=q.tryGet()
                            if incoming is None:break
                            pending[int(incoming.getSequenceNum())]=incoming
                            while len(pending)>16:pending.pop(min(pending))
                    common=self.pending_foreign_frames.keys() & self.pending_foreign_outputs.keys()
                    if common:
                        seq=max(common)
                        fr=unletterbox(self.pending_foreign_frames[seq].getCvFrame(), 'foreign')
                        raw=read_layers(self.pending_foreign_outputs[seq], 'foreign')
                        with self.condition:
                            self.foreign_received_at=time.monotonic()
                            self.latest_foreign=(seq,fr,raw,self.foreign_received_at)
                            self.foreign_pair_count+=1
                        for pending in (self.pending_foreign_frames,self.pending_foreign_outputs):
                            for old in list(pending):
                                if old<=seq:del pending[old]
                # Match NN output and passthrough by camera sequence before publishing.
                for queue_source, pending in [(self.queue, self.pending_frames),
                                              (self.nn_queue, self.pending_outputs)]:
                    for _drain in range(32):
                        incoming = queue_source.tryGet()
                        if incoming is None: break
                        if queue_source is self.nn_queue:
                            self.nn_received += 1
                            self.max_nn_age_ms = max(self.max_nn_age_ms, self._packet_age_ms(incoming))
                        pending[int(incoming.getSequenceNum())] = incoming
                        while len(pending) > 16: pending.pop(min(pending))
                matched = sorted(self.pending_frames.keys() & self.pending_outputs.keys())
                packet = None
                if matched:
                    matched_sequence = matched[-1]
                    packet = self.pending_frames[matched_sequence]
                    neural_packet = self.pending_outputs[matched_sequence]
                    from oak_rvc2 import read_layers
                    neural_values = read_layers(neural_packet,"tyre")
                    for pending in (self.pending_frames, self.pending_outputs):
                        for old in list(pending):
                            if old <= matched_sequence: del pending[old]

                if packet is None:
                    if time.monotonic() - last_packet_at > 6:
                        raise RuntimeError(
                            "No matched OAK AI frames for 6 seconds (see stream timing diagnostics)"
                        )
                    time.sleep(0.005)
                    continue

                if stall_reported:
                    log(f"OAK AI RECOVERED: matched result gap {time.monotonic()-last_packet_at:.2f}s")
                    stall_reported = False
                last_packet_at = time.monotonic()
                self.pair_received += 1
                received_at = datetime.now()
                # Remove inference letterbox and return the original 16:9 scene.
                # Compiled exports accept planar BGR; getCvFrame returns BGR for Qt/OpenCV.
                rgb = packet.getCvFrame()
                from oak_rvc2 import unletterbox
                frame = unletterbox(rgb)

                oak_frame_received()

                try:
                    camera_sequence = int(packet.getSequenceNum())
                    previous = self.previous_camera_sequence

                    if (
                        previous is not None
                        and camera_sequence > previous + 1
                    ):
                        gap = camera_sequence-previous-1
                        self.sequence_gaps += gap
                        self.largest_sequence_gap = max(self.largest_sequence_gap, gap)

                    self.previous_camera_sequence = camera_sequence
                except Exception:
                    pass

                with self.condition:
                    self.latest_neural_output = neural_values
                    self.latest_frame = frame
                    self.latest_frame_at = time.monotonic()
                    self.sequence += 1
                    self.condition.notify_all()


        except Exception as exc:
            self.reader_error = exc
            log(
                "OAK capture/recording failed:\n"
                + traceback.format_exc()
            )
            with self.condition:
                self.condition.notify_all()

        finally:
            oak_stream_stopped()
            self.stop_event.set()

    def _record_loop(self):
        from oak_recording import EncodedRecorder
        recorder = EncodedRecorder(OAK_RECORDINGS, log, fps=self.capture_fps)
        drained = 0
        try:
            while not self.stop_event.is_set() or drained < 120:
                if self.stop_event.is_set(): drained += 1
                packet = self.encoded_queue.tryGet()
                if packet is None:
                    if self.stop_event.is_set(): break
                    if not RECORD_OAK_VIDEO: recorder.close()
                    self.stop_event.wait(.005)
                    continue
                recorder.feed(packet.getData(), datetime.now(), int(packet.getSequenceNum()),
                              enabled=RECORD_OAK_VIDEO)
                self.record_drops = recorder.gaps
        except Exception:
            log("OAK H264 recording failed; live counting continues:\n" + traceback.format_exc())
        finally:
            recorder.close()

    def read(self):
        """For inference: return the newest frame and its sequence."""
        with self.condition:
            self.condition.wait_for(
                lambda: (
                    self.sequence != self.last_read_sequence
                    or self.reader_error is not None
                    or self.stop_event.is_set()
                ),
                timeout=1.0,
            )

            if self.reader_error is not None:
                raise RuntimeError(
                    "OAK capture/recording stopped: "
                    f"{self.reader_error}"
                ) from self.reader_error

            if self.sequence == self.last_read_sequence:
                return False, None, None

            self.last_read_sequence = self.sequence
            now = time.monotonic()
            self.worker_max_read_gap_ms = max(self.worker_max_read_gap_ms, (now-self.worker_last_read_at)*1000)
            self.worker_last_read_at = now
            self.last_read_neural_output = self.latest_neural_output
            self.last_read_frame_at = self.latest_frame_at
            return (
                True,
                self.latest_frame.copy(),
                self.sequence,
            )

    def read_foreign(self):
        with self.condition:
            if time.monotonic()-self.foreign_received_at > 6:
                raise RuntimeError("No matched foreign OAK result for 6 seconds")
            item=self.latest_foreign
            if item is None or item[0]==self.last_foreign_sequence:return None
            self.last_foreign_sequence=item[0]
            return item

    def preview_snapshot(self):
        """Independent camera image; never changes the matched counting observation."""
        with self.condition:
            if self.live_frame is None:
                return None, self.live_sequence, None
            return self.live_frame.copy(), self.live_sequence, self.live_frame_at

    def release(self):
        self.stop_event.set()
        with self.condition:
            self.condition.notify_all()

        if self.preview_reader is not None and self.preview_reader.is_alive():
            self.preview_reader.join(timeout=2)
        if self.reader is not None and self.reader.is_alive():
            self.reader.join(timeout=5)

        if self.recorder is not None and self.recorder.is_alive():
            self.recorder.join(timeout=5)
            if self.recorder.is_alive():
                log("WARNING: OAK MP4 writer still finishing queued frames")

        if self.device is not None:
            try:
                self.device.close()
            except Exception:
                pass

        if self.reader is not None and self.reader.is_alive():
            self.reader.join(timeout=2)

        if self.reader is not None and self.reader.is_alive():
            log(
                "WARNING: OAK capture thread did not stop cleanly"
            )









class CounterWorker(QThread):
    frame_ready = pyqtSignal(object)
    counted = pyqtSignal(int, float)
    foreign_event = pyqtSignal(object)
    foreign_status = pyqtSignal(str)
    status = pyqtSignal(str)
    finished_clean = pyqtSignal()

    def __init__(
        self,
        source_kind,
        source,
        direction,
        confidence,
        iou,
        line_ratio=0.59,
        line_angle=0,
        line_length=1.0,
    ):
        super().__init__()

        self.manual_test = False
        self.manual_test_end = None
        self.session_started_monotonic = time.monotonic()
        self.source_kind = source_kind
        self.source = source
        self.direction = direction
        self.confidence = confidence
        self.iou = iou

        self.line_ratio = max(
            0.10, min(0.90, float(line_ratio))
        )
        self.line_angle = max(
            -30, min(30, int(line_angle))
        )
        self.line_length = max(
            0.10, min(1.0, float(line_length))
        )

        self.running = True
        self.session_count = 0
        self.counted_ids = set()
        self.show_tyre_labels = False
        self.class_votes = defaultdict(
            lambda: defaultdict(float)
        )
        self.session_type_counts = {
            "passenger": 0,
            "4x4": 0,
            "unknown": 0
        }
        self.previous_side = {}
        self.previous_x = {}
        self.previous_observation_frame = {}
        self.confirmed_ids = set()
        self.last_seen_frame = {}
        self.processing_frame = 0
        self.video_fps = 20.0
        self.tracker_recoveries = 0
        self.oak_source = None
        self.foreign_enabled = False
        self.foreign_latched = False
        self.foreign_reset_pending = False
        self.foreign_confidence = .45
        self.foreign_every = 5
        self.foreign_region = (.03,.98,.18,.82)
        self.foreign_confirm = Confirmation()
        self.foreign_model = None
        self.foreign_boxes = []
        self.foreign_failed = False
        self.overlay_mode = "None"

        # V4.8.1 recorded-video review export.
        self.review_writer = None
        self.review_path = None


        self.overlay_lock = threading.Lock()
        self.oak_overlay = None

    def stop(self):
        self.running = False

    def latest_oak_overlay(self):
        with self.overlay_lock:
            return self.oak_overlay

    def foreign_check(self, frame):
        self.foreign_boxes = []
        if self.foreign_reset_pending:
            self.foreign_confirm.clear()
            self.foreign_reset_pending = False
        if not self.foreign_enabled or self.foreign_failed:
            return
        if self.source_kind != "oak" and self.processing_frame % self.foreign_every:
            return
        try:
            if self.source_kind == "oak":
                from oak_rvc2 import decode
                item=self.oak_source.read_foreign()
                if item is None:return
                seq, frame, raw, received=item
                if time.monotonic()-received>2:return
                rows,_=decode(raw,self.foreign_confidence,.5)
                candidates=[(row[:4].tolist(),int(row[5]),float(row[4])) for row in rows]
            else:
                result=self.foreign_model.predict(frame,conf=self.foreign_confidence,
                                                   imgsz=416,device="cpu",verbose=False)[0]
                candidates=[] if result.boxes is None else list(zip(result.boxes.xyxy.cpu().tolist(),
                    result.boxes.cls.int().cpu().tolist(),result.boxes.conf.cpu().tolist()))
            height,width=frame.shape[:2]
            detections=[]
            for box,c,confidence in candidates:
                if c not in range(len(CLASSES)) or not in_region(box,width,height,*self.foreign_region):continue
                detections.append(dict(class_id=c,box=box,confidence=float(confidence)))
            self.foreign_boxes=detections
            event=self.foreign_confirm.update(detections,time.monotonic())
            if event and not self.foreign_latched:
                self.foreign_latched=True
                self.foreign_event.emit(dict(event,frame=frame.copy(),source_kind="oak_test" if self.manual_test else self.source_kind,
                                             source=str(self.source),timestamp=brisbane_now().isoformat()))
        except Exception as exc:
            self.foreign_failed=True
            log("Foreign detector failed:\n"+traceback.format_exc())
            self.foreign_status.emit(f"FAULT: {exc} — tyre counting continues")

    def draw_foreign_region(self,frame):
        if not self.foreign_enabled:return
        h,w=frame.shape[:2];top,bottom,left,right=self.foreign_region
        points=np.array([[int((.5-(.5-left)*.45)*w),int(top*h)],
                         [int((.5+(right-.5)*.45)*w),int(top*h)],
                         [int(right*w),int(bottom*h)],[int(left*w),int(bottom*h)]],dtype=np.int32)
        cv2.polylines(frame,[points],True,(0,130,255),1)
        for d in self.foreign_boxes:
            x1,y1,x2,y2=map(int,d['box'])
            cv2.rectangle(frame,(x1,y1),(x2,y2),(0,0,255),2)
            cv2.putText(frame,CLASSES[d['class_id']],(x1,max(15,y1-5)),cv2.FONT_HERSHEY_SIMPLEX,.45,(0,0,255),1)

    def run(self):
        source = None
        perf_since = time.monotonic()
        perf_frames = 0
        perf_track_seconds = 0.0

        try:
            model_path = resource_path(MODEL_NAME)
            if not model_path.exists():
                raise FileNotFoundError(
                    f"{MODEL_NAME} not found beside the app."
                )

            if self.source_kind == "oak":
                from oak_rvc2 import OakTracker
                model = OakTracker()
                log("Tyre detection: OAK RVC2 384 RGB; CPU decoding and ByteTrack; detection only; full 16:9 view")
            else:
                self.status.emit("Loading tyre detector...")
                model = YOLO(str(model_path))
                if model.task != "detect":
                    raise ValueError("This build requires the included detection-only tyre model")

            if self.foreign_enabled and self.source_kind != "oak":
                try:
                    self.foreign_model = YOLO(str(resource_path("foreign_model/foreign_object.pt")))
                    actual=tuple(self.foreign_model.names[i] for i in range(len(self.foreign_model.names)))
                    if actual!=CLASSES:raise ValueError(f"Foreign class mapping mismatch: {actual}")
                    self.foreign_status.emit("Synthetic starter · PC inference · 3 confirmations required")
                except Exception as exc:
                    self.foreign_failed=True
                    self.foreign_status.emit(f"FAULT loading foreign model: {exc}")
                    log("Foreign model load failed: "+str(exc))
            if self.source_kind == "oak":
                source = OakSource(str(self.source or ""), foreign_enabled=self.foreign_enabled)
                if self.foreign_enabled:self.foreign_status.emit("Synthetic starter · OAK inference · 3 confirmations required")
                self.oak_source = source
                source_name = OAK_TEST_NAME if self.manual_test else OAK_NAME
            else:
                source = cv2.VideoCapture(self.source)
                if not source.isOpened():
                    raise RuntimeError(
                        "Could not open video source."
                    )
                source_name = (
                    Path(self.source).name
                    if self.source_kind == "video"
                    else f"Camera {self.source}"
                )

            self.status.emit("COUNTING")

            if self.source_kind == "video":
                fps = source.get(cv2.CAP_PROP_FPS)

                if not fps or fps <= 0:
                    fps = 20.0
                self.video_fps = float(fps)

                review_folder = REPORTS / "video_reviews"
                review_folder.mkdir(
                    parents=True,
                    exist_ok=True
                )

                timestamp = datetime.now().strftime(
                    "%Y%m%d_%H%M%S"
                )

                self.review_path = (
                    review_folder
                    / f"TyreTrack_Review_{timestamp}.mp4"
                )

                frame_width = int(
                    source.get(cv2.CAP_PROP_FRAME_WIDTH)
                )

                frame_height = int(
                    source.get(cv2.CAP_PROP_FRAME_HEIGHT)
                )

                self.review_writer = cv2.VideoWriter(
                    str(self.review_path),
                    cv2.VideoWriter_fourcc(*"mp4v"),
                    fps,
                    (frame_width, frame_height)
                )

                if not self.review_writer.isOpened():
                    raise RuntimeError(
                        "Could not create review MP4. "
                        "Check codec support and disk space."
                    )

            while self.running:
                if self.source_kind == "oak" and not oak_session_allowed(self.manual_test):
                    break
                if self.source_kind == "oak":
                    ok, frame, frame_sequence = source.read()
                else:
                    ok, frame = source.read()
                    frame_sequence = None

                if not ok:
                    if self.source_kind == "oak":
                        continue
                    break

                self.processing_frame += 1
                height, width = frame.shape[:2]
                self.foreign_check(frame)
                left, right, line_y, slope = segment_bounds(
                    width,
                    height,
                    self.line_ratio,
                    self.line_angle,
                    self.line_length,
                )
                centre_x = (width - 1) / 2

                started = time.monotonic()
                try:
                    if self.source_kind == "oak":
                        result = model.track_oak(frame, source.last_read_neural_output,
                                                 self.confidence, self.iou, V48_AGNOSTIC_NMS)
                    else:
                        result = model.track(
                            frame,
                            persist=True,
                            tracker="bytetrack.yaml",
                            # ByteTrack can associate weak detections with established tracks.
                            # Only tracks observed at the user's threshold are allowed to count.
                            conf=min(self.confidence, 0.10),
                            iou=self.iou,
                            imgsz=INFERENCE_SIZE,
                            agnostic_nms=V48_AGNOSTIC_NMS,
                            verbose=False,
                        )[0]
                except np.linalg.LinAlgError as exc:
                    # A corrupt ByteTrack Kalman covariance can poison subsequent frames.
                    # Reset the tracker; never reset completed counts or session totals.
                    self.tracker_recoveries += 1
                    log(f"Tracker numerical failure at frame {self.processing_frame}: {exc}; "
                        f"recovering (attempt {self.tracker_recoveries})")
                    if self.tracker_recoveries > 20:
                        raise RuntimeError("Too many tracker numerical failures; stopping safely") from exc
                    predictor = getattr(model, "predictor", None)
                    trackers = getattr(predictor, "trackers", None)
                    if trackers:
                        for tracker in trackers:
                            tracker.reset()
                    # Track IDs can restart at 1. Namespace them by recovery generation.
                    # Discard old positions/votes to avoid false line crossings.
                    self.previous_side.clear()
                    self.previous_x.clear()
                    self.previous_observation_frame.clear()
                    self.confirmed_ids.clear()
                    self.last_seen_frame.clear()
                    self.class_votes.clear()
                    # Completed line counts remain; new track IDs use a new namespace.
                    # Continue reading frames, including recording this one.
                    if self.review_writer is not None:
                        self.review_writer.write(frame)
                    continue
                perf_track_seconds += (
                    time.monotonic() - started
                )
                perf_frames += 1

                detections = []

                if (
                    result.boxes is not None
                    and result.boxes.id is not None
                ):
                    boxes = result.boxes.xyxy.cpu().numpy()
                    ids = [int(value) + self.tracker_recoveries * 1000000
                           for value in result.boxes.id.int().cpu().tolist()]
                    confidences = (
                        result.boxes.conf.cpu().tolist()
                    )
                    class_ids = (
                        result.boxes.cls.int().cpu().tolist()
                    )

                    for detection_index, (
                        box, track_id, confidence, class_id
                    ) in enumerate(
                        zip(boxes, ids, confidences, class_ids)
                    ):
                        if not acceptable_box(
                            box, width, height
                        ):
                            continue

                        class_name = tyre_class_name(
                            model.names.get(
                                int(class_id), "unknown"
                            )
                        )

                        if (
                            class_name != "unknown"
                            and track_id not in self.counted_ids
                            and confidence >= self.confidence
                        ):
                            self.class_votes[track_id][class_name] += (
                                float(confidence)
                            )

                        x1, y1, x2, y2 = (
                            int(value) for value in box
                        )
                        cx, cy = count_point((x1,y1,x2,y2),width,height)
                        counting_y = float(cy)

                        self.last_seen_frame[track_id] = self.processing_frame
                        if confidence >= self.confidence:
                            self.confirmed_ids.add(track_id)
                        detections.append(
                            (
                                x1, y1, x2, y2,
                                int(track_id),
                                float(confidence),
                            )
                        )

                        # Classification label for this tracked tyre.
                        tyre_type, class_score = tyre_class_vote(
                            self.class_votes.get(track_id, {})
                        )

                        if tyre_type == "passenger":
                            display_type = "PASSENGER"
                            display_colour = (255, 180, 40)
                        elif tyre_type == "4x4":
                            display_type = "SUV / 4X4"
                            display_colour = (80, 220, 100)
                        else:
                            display_type = "UNCLASSIFIED"
                            display_colour = (180, 180, 180)

                        label = (
                            f"{display_type} "
                            f"ID:{int(track_id)} "
                            f"vote:{class_score:.0%}"
                        )

                        if self.show_tyre_labels:
                            cv2.putText(
                                frame,
                                label,
                                (max(0, x1), max(20, y1 - 8)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.52,
                                display_colour,
                                2,
                                cv2.LINE_AA
                            )



                        ll,rr,ly,ls=segment_bounds(width,height,self.line_ratio,self.line_angle,self.line_length)
                        side=counting_y-(ly+ls*(cx-centre_x))
                        key=(0,track_id)
                        previous=self.previous_side.get(key)
                        margin=V48_HYSTERESIS_PX*math.sqrt(1+ls*ls)
                        stable=abs(side)>=margin
                        crossed=False
                        # Arm on a stable approach; count at the red-dot centre crossing.
                        # Keep a stable reference to reject approach-side jitter.
                        # Require the same ID to have been seen recently; stale
                        # re-associations must not create phantom crossings.
                        gap=self.processing_frame-self.previous_observation_frame.get(key,-10000)
                        if (previous is not None and gap<=V48_MAX_TRACK_GAP
                                and track_id in self.confirmed_ids
                                and track_id not in self.counted_ids):
                            if self.direction == "Bottom to Top":
                                crossed=previous>=margin and side<0
                            elif self.direction == "Top to Bottom":
                                crossed=previous<=-margin and side>0
                            else:
                                crossed=(previous<=-margin and side>0) or (previous>=margin and side<0)
                        if crossed:
                            crossed=crosses_segment(previous,side,self.previous_x.get(key,cx),cx,ll,rr)
                        if crossed:
                            if self.source_kind == "oak" and (not self.running or not oak_session_allowed(self.manual_test)):
                                continue
                            if track_id not in self.counted_ids:
                                # Persist this single-line crossing once before updating totals.
                                tyre_type, class_conf = tyre_class_vote(
                                    self.class_votes.get(track_id, {})
                                )

                                log_count(
                                    source_name,
                                    track_id,
                                    confidence,
                                    tyre_type,
                                    class_conf
                                )

                                self.session_type_counts[tyre_type] += 1
                                self.counted_ids.add(track_id)
                                self.session_count += 1
                                self.counted.emit(track_id,float(confidence))
                        if stable:
                            self.previous_side[key]=side
                            self.previous_x[key]=cx
                            self.previous_observation_frame[key]=self.processing_frame


                # Retire stale positions after 90 processed frames, not wall time.
                # Keep counted IDs for the entire session to prevent repeat counts.
                for track_id, seen in list(self.last_seen_frame.items()):
                    if self.processing_frame-seen > 90:
                        for index in (0,):
                            self.previous_side.pop((index,track_id), None)
                            self.previous_x.pop((index,track_id), None)
                            self.previous_observation_frame.pop((index,track_id), None)
                        self.confirmed_ids.discard(track_id)
                        self.last_seen_frame.pop(track_id, None)

                self.draw_foreign_region(frame)
                if self.source_kind == "oak":
                    # One immutable snapshot, including the frame
                    # sequence used for inference. An empty result
                    # clears boxes from earlier detections.
                    with self.overlay_lock:
                        self.oak_overlay = (
                            source.last_read_frame_at,
                            frame_sequence,
                            (width, height),
                            tuple(detections),
                            frame.copy(),
                            result,
                        )
                else:
                    if self.overlay_mode == "Bounding Boxes":
                        draw_detections(frame, detections)
                    draw_line_and_total(
                        frame,
                        self.line_ratio,
                        self.line_angle,
                        self.line_length,
                        self.session_count,
                    )
                    if self.review_writer is not None:
                        self.review_writer.write(frame)

                    self.frame_ready.emit(
                        (frame.copy(), time.monotonic())
                    )

                if self.source_kind == "oak":
                    elapsed = time.monotonic() - perf_since
                    if elapsed >= 10:
                        log(
                            "PERF OAK processing: "
                            f"fps={perf_frames / elapsed:.1f}, "
                            "track_avg_ms="
                            f"{1000 * perf_track_seconds / max(1, perf_frames):.0f}"
                        )
                        perf_since = time.monotonic()
                        perf_frames = 0
                        perf_track_seconds = 0.0

            self.status.emit("STOPPED")

        except Exception as exc:
            log(
                "Counter worker failed:\n"
                + traceback.format_exc()
            )
            self.status.emit(f"ERROR: {exc}")

        finally:
            if self.review_writer is not None:
                try:
                    self.review_writer.release()

                    if self.review_path is not None:
                        log(
                            "Review video saved: "
                            + str(self.review_path)
                        )

                except Exception:
                    log(
                        "Review video close failed:\\n"
                        + traceback.format_exc()
                    )

                self.review_writer = None

            if source is not None:
                try:
                    source.release()
                except Exception:
                    log(
                        "Source release failed:\n"
                        + traceback.format_exc()
                    )
            self.finished_clean.emit()


class VideoLabel(QLabel):
    line_dragged = pyqtSignal(float, int, float)
    fullscreen_requested = pyqtSignal()

    def __init__(self):
        super().__init__("NO VIDEO SOURCE")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(320, 240)
        self.setStyleSheet(
            "background:#05080d;color:#718096;"
            "border:1px solid #263241;"
            "border-radius:12px;font-size:18px;"
        )

        self._pix = None
        self.line_ratio = 0.59
        self.line_angle = 0
        self.line_length = 1.0
        self.editable = True
        self.drag_mode = None
        self.setCursor(Qt.CrossCursor)

    @staticmethod
    def prepare_frame(frame):
        # Qt supports BGR888 in the pinned Qt 5.15 runtime: no OpenCV RGB conversion.
        height, width = frame.shape[:2]
        image = QImage(frame.data, width, height, frame.strides[0], QImage.Format_BGR888).copy()
        return QPixmap.fromImage(image)

    def set_frame(self, frame):
        self.set_prepared_frame(self.prepare_frame(frame))

    def set_prepared_frame(self, pixmap, redraw=True):
        # QPixmap uses implicit sharing; both views reference the same native-size frame.
        self._pix = pixmap
        if self.text(): self.setText("")
        if redraw: self._redraw()

    def resizeEvent(self, event):
        self._redraw()
        super().resizeEvent(event)

    def _redraw(self):
        # Paint scaling does not change QLabel's pixmap/size hint or trigger layout work.
        self.update()

    def image_geometry(self):
        if self._pix is None:
            return None

        scale = min(
            self.width() / self._pix.width(),
            self.height() / self._pix.height(),
        )
        width = self._pix.width() * scale
        height = self._pix.height() * scale

        return (
            (self.width() - width) / 2,
            (self.height() - height) / 2,
            width,
            height,
            scale,
        )

    def line_points(self):
        geometry = self.image_geometry()
        if geometry is None:
            return None

        ox, oy, _, _, scale = geometry
        left, right, line_y, slope = segment_bounds(
            self._pix.width(),
            self._pix.height(),
            self.line_ratio,
            self.line_angle,
            self.line_length,
        )
        centre_x = (self._pix.width() - 1) / 2

        return (
            ox + left * scale,
            oy + (
                line_y + slope * (left - centre_x)
            ) * scale,
            ox + right * scale,
            oy + (
                line_y + slope * (right - centre_x)
            ) * scale,
        )

    def paintEvent(self, event):
        started = time.monotonic()
        super().paintEvent(event)
        if self._pix is not None:
            ox, oy, width, height, _ = self.image_geometry()
            image_painter = QPainter(self)
            image_painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            image_painter.drawPixmap(round(ox), round(oy), round(width), round(height), self._pix)
            image_painter.end()
        self._last_image_paint_ms = (time.monotonic()-started)*1000
        self._max_image_paint_ms = max(getattr(self, '_max_image_paint_ms', 0), self._last_image_paint_ms)

        if not self.editable:
            return

        points = self.line_points()
        if points is None:
            return

        x1, y1, x2, y2 = points
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#ffd400"), 3))
        painter.drawLine(
            round(x1),
            round(y1),
            round(x2),
            round(y2),
        )

        for x, y in (
            (x1, y1),
            (x2, y2),
            ((x1 + x2) / 2, (y1 + y2) / 2),
        ):
            painter.drawEllipse(
                round(x) - 7,
                round(y) - 7,
                14,
                14,
            )
        painter.end()

    def mousePressEvent(self, event):
        if (
            not self.editable
            or event.button() != Qt.LeftButton
        ):
            return

        points = self.line_points()
        if points is None:
            return

        x1, y1, x2, y2 = points
        x, y = event.x(), event.y()

        if not x1 <= x <= x2:
            return

        expected = (
            y1 + (y2 - y1)
            * (x - x1)
            / max(1, x2 - x1)
        )
        if abs(y - expected) > 18:
            return

        self.drag_mode = (
            "resize"
            if abs(x - x1) < 22
            or abs(x - x2) < 22
            else "move"
        )

    def mouseMoveEvent(self, event):
        if not self.drag_mode or not self.editable:
            return

        geometry = self.image_geometry()
        if geometry is None:
            return

        ox, oy, width, height, _ = geometry

        if self.drag_mode == "move":
            ratio = (
                event.y()
                - oy
                - math.tan(
                    math.radians(self.line_angle)
                ) * (event.x() - ox - width / 2)
            ) / height
            self.line_ratio = max(
                0.10, min(0.90, ratio)
            )
        else:
            dx = event.x() - ox - width / 2
            if abs(dx) < 15:
                return

            angle = math.degrees(
                math.atan(
                    (
                        event.y()
                        - oy
                        - height * self.line_ratio
                    ) / dx
                )
            )
            self.line_angle = max(
                -30, min(30, round(angle))
            )
            self.line_length = max(
                0.10,
                min(1.0, 2 * abs(dx) / width),
            )

        self.line_dragged.emit(
            self.line_ratio,
            self.line_angle,
            self.line_length,
        )
        self.update()

    def mouseDoubleClickEvent(self, event):
        self.drag_mode = None
        if event.button() == Qt.LeftButton:
            self.fullscreen_requested.emit()

    def mouseReleaseEvent(self, event):
        self.drag_mode = None


class ControlScrollArea(QScrollArea):
    """Keep wrapped pages at viewport width, including Windows scrollbar changes."""
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.viewport().installEventFilter(self)

    def fit_width(self):
        host=self.widget()
        if host is not None and self.viewport().width()>0:
            width=self.viewport().width()
            if host.width()!=width or host.minimumWidth()!=width:
                host.setFixedWidth(width)
                host.updateGeometry()

    def eventFilter(self, watched, event):
        if watched is self.viewport() and event.type()==QEvent.Resize:
            self.fit_width()
        return super().eventFilter(watched,event)

    def showEvent(self,event):
        super().showEvent(event)
        self.fit_width()
        QTimer.singleShot(0,self.fit_width)


class Main(QMainWindow):
    dashboard_ready = pyqtSignal(object)
    def __init__(self):
        super().__init__()

        init_db()
        ensure_email_config()

        if not get_state("hourly_next_start"):
            boundary = brisbane_now().replace(
                minute=0, second=0, microsecond=0
            )
            set_state(
                "hourly_next_start",
                boundary.replace(tzinfo=None).isoformat(),
            )

        self.auto_retry_at = 0.0
        self.auto_attempt = False
        self.closing = False
        self.alarm_state_path = PRIVATE_CONFIG_DIR / "foreign_alarm_state.json"
        self.admin_path = PRIVATE_CONFIG_DIR / "alarm_admin.json"
        try:
            self.alarm_state = read_json(self.alarm_state_path,{"latched":False})
        except Exception:
            self.alarm_state = {"latched":True,"object":"State file unreadable — admin check required","timestamp":brisbane_now().isoformat()}
        self.alarm_sound_active = False
        self.foreign_fault = ""
        self.foreign_monitor_status = "Disabled · enable in FOREIGN tab"
        self.email_thread = None
        self.email_retry_at = 0.0
        self.worker = None
        self.video_path = ""
        self.fullscreen_dialog = None
        self.fullscreen_video = None
        self.session = 0
        self.test_events = []  # Video playback seconds at confirmed crossings.

        self.setWindowTitle(
            APP_NAME + " - TOTAL TYRE COUNT [Controls 2026-10-12]"
        )
        self.resize(1450, 850)
        self.dashboard_ready.connect(self.dashboard_data_ready)
        self._dashboard_cache = None
        self.build_ui()
        self.flow_flash_timer = QTimer(self)
        self.flow_flash_timer.setInterval(500)
        self.flow_flash_timer.timeout.connect(self.flash_low_flow)
        self._flow_flash_red = False
        self.refresh_counts()

        self.oak_preview_timer = QTimer(self)
        self.oak_preview_timer.setInterval(
            OAK_PREVIEW_INTERVAL_MS
        )
        self.oak_preview_timer.timeout.connect(
            self.display_oak_preview
        )
        self._oak_preview_sequence = 0
        self._oak_preview_since = time.monotonic()
        self._oak_preview_frames = 0

        self.clock = QTimer(self)
        self.clock.timeout.connect(self.tick)
        self.clock.start(1000)
        self.tick()
        self.start_remote_viewer()

    def start_remote_viewer(self):
        try:
            from remote_view import Viewer
            self.remote_viewer = Viewer(DB)
            self.remote_timer = QTimer(self)
            self.remote_timer.timeout.connect(self.publish_remote_view)
            self.remote_timer.start(2000)
            log("Read-only viewer ready on 127.0.0.1:8765 (Tailscale Serve)")
        except Exception as exc:
            log(f"Remote viewer unavailable: {exc}")

    def publish_remote_view(self):
        if self.closing: return
        try:
            from PyQt5.QtCore import QBuffer, QIODevice
            # Only the camera widget is captured; settings and passwords are excluded.
            image = self.video.grab()
            buffer = QBuffer()
            buffer.open(QIODevice.WriteOnly)
            image.save(buffer, "JPG", 70)
            cards = {"Status":self.production_status_lbl.text(),
                "Flow rate":self.flow_lbl.text(), "Best hour":self.session_lbl.text(),
                "Best daily":self.best_daily_lbl.text(),
                "This hour":self.hour_lbl.text(), "Today":self.day_lbl.text(),
                "All time":self.total_lbl.text(), "Passenger":self.passenger_lbl.text(),
                "4×4/SUV":self.suv_lbl.text(), "No-count alerts":self.no_count_lbl.text(),
                "No-flow time":self.gap_lbl.text(),
                "Foreign object alarm":"ACTIVE" if self.alarm_state.get("latched") else "Clear"}
            self.remote_viewer.publish(cards, brisbane_now().strftime("%Y-%m-%d %H:%M:%S"), bytes(buffer.data()))
        except Exception as exc:
            log(f"Remote snapshot failed: {exc}")

    def card(self, heading):
        frame = QFrame()
        frame.setObjectName("card")

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)

        title = QLabel(heading)
        title.setObjectName("cardTitle")
        layout.addWidget(title)

        return frame, layout

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 8, 12, 10)
        outer.setSpacing(7)

        header = QHBoxLayout()
        title_column = QVBoxLayout()
        title_column.setSpacing(1)

        brand = QLabel("TYRECYCLE  |  TYRETRACK")
        brand.setObjectName("brand")
        subtitle = QLabel("AI CONVEYOR MONITORING  /  PRODUCTION CONTROL")
        subtitle.setObjectName("subtitle")

        title_column.addWidget(brand)
        title_column.addWidget(subtitle)

        self.health = QLabel("● READY")
        self.health.setObjectName("health")

        # Optional official logo: place tyrecycle_logo.png beside this Python file.
        # This also works in a packaged executable via resource_path().
        logo_path = resource_path("tyrecycle_logo.png")
        if logo_path.exists():
            logo_pixmap = QPixmap(str(logo_path))
            if not logo_pixmap.isNull():
                logo_label = QLabel()
                logo_label.setObjectName("tyrecycleLogo")
                logo_label.setPixmap(logo_pixmap.scaled(
                    136, 52, Qt.KeepAspectRatio, Qt.SmoothTransformation
                ))
                logo_label.setFixedSize(140, 54)
                logo_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                header.addWidget(logo_label)
        header.addLayout(title_column)
        header.addStretch()

        self.counters_btn = QPushButton("Hide counters")
        self.counters_btn.setToolTip("Show or hide production counters to enlarge the camera")
        self.counters_btn.clicked.connect(self.toggle_counters)

        self.controls_btn = QPushButton("Hide controls")
        self.controls_btn.clicked.connect(
            self.toggle_controls
        )
        fullscreen_btn = QPushButton("Fullscreen video")
        fullscreen_btn.clicked.connect(
            self.open_fullscreen
        )

        header.addWidget(self.counters_btn)
        header.addWidget(self.controls_btn)
        header.addWidget(fullscreen_btn)
        header.addWidget(self.health)
        outer.addLayout(header)

        # Compact totals: existing live classification QLabel objects are reused.
        self.passenger_lbl = QLabel("0")
        self.suv_lbl = QLabel("0")
        self.passenger_lbl.setToolTip("Passenger tyres in the current session; resets with SESSION.")
        self.suv_lbl.setToolTip("SUV / 4x4 tyres in the current session; resets with SESSION.")
        self.truck_lbl = QLabel("0")  # Reserved for future truck classification.
        self.session_lbl = QLabel("0")
        self.no_count_lbl = QLabel("0")
        self.flow_lbl = QLabel("0.0 tyres per min")
        self.gap_lbl = QLabel("0 min")
        self.production_status_lbl = QLabel("WAITING")
        self.hour_lbl = QLabel("0")
        self.day_lbl = QLabel("0")
        self.month_lbl = QLabel("0")
        self.total_lbl = QLabel("0")

        def metric_card(name, label, accent=None):
            frame = QFrame()
            frame.setObjectName("metric")
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(10, 3, 10, 3)
            layout.setSpacing(0)
            title = QLabel(name)
            title.setObjectName("metricName")
            label.setObjectName("metricValue")
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            if accent:
                label.setStyleSheet("color:" + accent + ";font-size:22px;font-weight:900;")
            layout.addWidget(title)
            layout.addWidget(label)
            return frame

        self.counters_panel = QWidget()
        counters_layout = QVBoxLayout(self.counters_panel)
        counters_layout.setContentsMargins(0, 0, 0, 0)
        counters_layout.setSpacing(6)

        summary = QHBoxLayout()
        summary.setSpacing(8)
        for name, label in (
            ("THIS HOUR", self.hour_lbl),
            ("DAILY TOTAL", self.day_lbl),
            ("ALL-TIME TOTAL", self.total_lbl),
        ):
            summary.addWidget(metric_card(name, label), 1)
        self.best_hour_card = metric_card("BEST HOUR", self.session_lbl, "#38D996")
        self.best_hour_card.setMinimumHeight(100)
        self.session_lbl.setStyleSheet("color:#38D996;font-size:38px;font-weight:900;")
        self.best_hour_card.setToolTip("Highest completed production hour (Brisbane time)")
        summary.addWidget(self.best_hour_card, 1)
        self.best_daily_lbl = QLabel("0 tyres")
        self.best_daily_card = metric_card("BEST DAILY · OAK", self.best_daily_lbl, "#38D996")
        self.best_daily_lbl.setWordWrap(True)
        self.best_daily_lbl.setStyleSheet("color:#38D996;font-size:22px;font-weight:900;")
        summary.addWidget(self.best_daily_card, 1)
        counters_layout.addLayout(summary)

        monitor = QHBoxLayout()
        monitor.setSpacing(8)
        for title, label in (("FLOW RATE", self.flow_lbl),
                             ("NO COUNT ALERTS", self.no_count_lbl),
                             ("NO-FLOW TIME", self.gap_lbl),
                             ("PRODUCTION STATUS", self.production_status_lbl)):
            monitor.addWidget(metric_card(title, label, "#FF5C68" if title in ("NO COUNT ALERTS", "NO-FLOW TIME") else None), 1)
        counters_layout.addLayout(monitor)

        classes = QHBoxLayout()
        classes.setSpacing(8)
        classes.addWidget(metric_card("PASSENGER", self.passenger_lbl, "#42B7FF"), 1)
        classes.addWidget(metric_card("SUV / 4X4", self.suv_lbl, "#38D996"), 1)

        counters_layout.addLayout(classes)

        # Permanent production metrics live outside the hideable controls panel.
        production = QHBoxLayout()
        production.setSpacing(8)
        self.production_average = QLabel("0")
        self.production_flow_best = QLabel("0.0 tyres per min")
        for heading, label, colour in (
            ("AVERAGE HOURLY PRODUCTION", self.production_average, "#38D996"),
            ("BEST FLOW AVERAGE TODAY", self.production_flow_best, "#42B7FF"),
        ):
            tile = metric_card(heading, label, colour)
            label.setStyleSheet("color:"+colour+";font-size:26px;font-weight:900;")
            production.addWidget(tile, 1)
        history_frame = QFrame()
        history_frame.setObjectName("metric")
        history_layout = QVBoxLayout(history_frame)
        history_layout.setContentsMargins(10, 3, 10, 3)
        self.history_heading = QLabel("HOURLY HISTORY  ·  starts 05:00 Brisbane")
        self.history_heading.setObjectName("metricName")
        history_layout.addWidget(self.history_heading)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Reserve separate space for the two text lines and horizontal scrollbar.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        content = QWidget()
        row = QHBoxLayout(content)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(7)
        self.production_slots = []
        for index in range(24):
            label = QLabel(f"H{index+1} –\n0")
            label.setStyleSheet("color:#84929E;font-size:13px;font-weight:700;padding:2px;")
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumWidth(76)
            row.addWidget(label)
            self.production_slots.append(label)
        scroll.setWidget(content)
        scroll.setFixedHeight(max(label.sizeHint().height() for label in self.production_slots)
                              + 4 + scroll.horizontalScrollBar().sizeHint().height()
                              + 2 * scroll.frameWidth() + 4)
        self.production_scroll = scroll
        history_layout.addWidget(scroll)
        production.addWidget(history_frame, 3)
        counters_layout.addLayout(production)
        outer.addWidget(self.counters_panel)
        self.alarm_banner=QLabel()
        self.alarm_banner.setWordWrap(True)
        outer.addWidget(self.alarm_banner)
        self.update_alarm_banner()

        body = QHBoxLayout()
        outer.addLayout(body, 1)

        left = QVBoxLayout()
        left.setSpacing(6)
        body.addLayout(left, 3)

        self.video = VideoLabel()
        self.video.fullscreen_requested.connect(
            self.open_fullscreen
        )
        self.video.line_dragged.connect(
            self.mouse_line_changed
        )
        left.addWidget(self.video, 1)

        run_row = QHBoxLayout()

        self.start_btn = QPushButton(
            "▶  START COUNTING"
        )
        self.start_btn.setObjectName("start")
        self.start_btn.clicked.connect(self.start)

        self.stop_btn = QPushButton(
            "■  STOP COUNTING"
        )
        self.stop_btn.setObjectName("stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop)

        run_row.addWidget(self.start_btn)
        run_row.addWidget(self.stop_btn)
        left.addLayout(run_row)

        self.status_lbl = QLabel(
            "Ready  •  Choose a source, then start counting"
        )
        self.status_lbl.setObjectName("status")
        left.addWidget(self.status_lbl)

        footer = QHBoxLayout()

        self.all_time_lbl = QLabel("ALL TIME  0")
        self.all_time_lbl.setObjectName("footer")

        self.source_status = QLabel(
            "Source: none selected"
        )
        self.source_status.setObjectName("footer")

        footer.addWidget(self.all_time_lbl)
        footer.addStretch()
        footer.addWidget(self.source_status)
        left.addLayout(footer)

        tabs = QTabWidget()
        self.controls_tabs = tabs
        tabs.setMinimumWidth(360)
        tabs.setMaximumWidth(410)
        tabs.setUsesScrollButtons(True)
        body.addWidget(tabs, 1)

        def tab_page(name):
            scroll = ControlScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarAlwaysOff
            )

            host = QWidget()
            layout = QVBoxLayout(host)
            layout.setSizeConstraint(QLayout.SetNoConstraint)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(10)

            scroll.setWidget(host)
            tabs.addTab(scroll, name)
            return layout

        source_tab = tab_page("CAMERA")
        foreign_tab = tab_page("FOREIGN")
        line_tab = tab_page("COUNTING")
        reports_tab = tab_page("REPORTS")
        hours_tab = tab_page("PERFORMANCE")

        source_card, source_layout = self.card(
            "01  CAMERA & VIDEO SOURCE"
        )
        self.source_combo = QComboBox()
        self.source_combo.addItems(
            [
                "Video File",
                "Camera 0",
                "Camera 1",
                OAK_NAME,
            ]
        )
        self.source_combo.currentTextChanged.connect(
            self.update_source_status
        )
        source_layout.addWidget(self.source_combo)
        self.auto_schedule = QCheckBox("Auto connect and count")
        schedule_note = QLabel("05:00–17:05 Brisbane time. Connection retries every 60 seconds.")
        schedule_note.setObjectName("helper")
        schedule_note.setWordWrap(True)
        # Every launch arms production scheduling, including upgraded installations
        # that previously saved Auto=off or a manual-stop pause for today.
        set_state("auto_schedule", "1")
        set_state("auto_paused_day", "")
        self.auto_schedule.setChecked(True)
        self.auto_schedule.toggled.connect(self.auto_schedule_changed)
        source_layout.addWidget(self.auto_schedule)
        source_layout.addWidget(schedule_note)
        self.manual_oak_test = QCheckBox("Manual OAK test · any time")
        self.manual_oak_test.setChecked(False)
        source_layout.addWidget(self.manual_oak_test)
        test_note=QLabel("Check this and press Start to test OAK outside production hours. Test counts stay separate; no production emails. With Auto enabled, an overnight test hands over at the next 05:00 Brisbane start.")
        test_note.setWordWrap(True)
        source_layout.addWidget(test_note)

        self.record_oak_checkbox = QCheckBox(
            "Record OAK video (MP4)"
        )
        self.record_oak_checkbox.setToolTip("H264 compressed on OAK; MP4 finalises every 15 minutes or when recording stops. Uses disk space.")
        self.record_oak_checkbox.setChecked(False)
        self.record_oak_checkbox.toggled.connect(
            set_oak_recording
        )
        source_layout.addWidget(
            self.record_oak_checkbox
        )
        self.overlay_mode_combo = QComboBox()
        self.overlay_mode_combo.addItems(["None", "Bounding Boxes"])
        self.overlay_mode_combo.setCurrentText("Bounding Boxes")
        self.overlay_mode_combo.currentTextChanged.connect(
            self.update_overlay_mode)
        self.oak_view_combo = QComboBox()
        self.oak_view_combo.addItems(["Smooth", "Show Boxes"])
        self.oak_view_combo.setToolTip("Smooth: clean live stream. Show Boxes: matched analysed frames with detection boxes. Counting is unaffected.")
        self.oak_view_combo.currentTextChanged.connect(lambda _: self.update_overlay_mode(self.overlay_mode_combo.currentText()))
        source_layout.addWidget(QLabel("OAK viewing mode"))
        source_layout.addWidget(self.oak_view_combo)
        source_layout.addWidget(QLabel("AI detection display (matched view)"))
        source_layout.addWidget(self.overlay_mode_combo)

        self.show_tyre_labels = QCheckBox("Show Tyre Labels")
        self.show_tyre_labels.setChecked(False)
        self.show_tyre_labels.setToolTip(
            "Display tyre classification labels and IDs."
        )
        source_layout.addWidget(self.show_tyre_labels)

        self.show_tyre_labels.toggled.connect(
            self.update_tyre_label_visibility
        )

        self.browse_btn = QPushButton(
            "Choose video file…"
        )
        self.browse_btn.clicked.connect(self.browse)
        source_layout.addWidget(self.browse_btn)

        self.file_lbl = QLabel(
            "No video selected"
        )
        self.file_lbl.setObjectName("helper")
        self.file_lbl.setWordWrap(True)
        source_layout.addWidget(self.file_lbl)

        source_tab.addWidget(source_card)
        source_tab.addStretch()

        foreign_card, foreign_layout = self.card("FOREIGN OBJECT DETECTOR · SYNTHETIC STARTER")
        self.foreign_switch=QCheckBox("Enable foreign detector")
        self.foreign_switch.setChecked(get_state("foreign_enabled","0")=="1")
        self.foreign_switch.toggled.connect(self.foreign_enabled_changed)
        foreign_layout.addWidget(self.foreign_switch)
        self.foreign_status_lbl=QLabel("Ready for next start" if self.foreign_switch.isChecked() else "Disabled")
        foreign_layout.addWidget(self.foreign_status_lbl)
        self.foreign_conf=QSlider(Qt.Horizontal);self.foreign_conf.setRange(30,95)
        self.foreign_conf.setValue(int(get_state("foreign_confidence","45")))
        self.foreign_conf_lbl=QLabel()
        self.foreign_every=QSlider(Qt.Horizontal);self.foreign_every.setRange(1,20)
        self.foreign_every.setValue(int(get_state("foreign_every","5")))
        self.foreign_every_lbl=QLabel()
        foreign_layout.addWidget(self.foreign_conf_lbl);foreign_layout.addWidget(self.foreign_conf)
        foreign_layout.addWidget(self.foreign_every_lbl);foreign_layout.addWidget(self.foreign_every)
        self.region_sliders=[]
        for key,title,default in [("top","Region top",3),("bottom","Region bottom",98),
                                  ("left","Region bottom-left",18),("right","Region bottom-right",82)]:
            label=QLabel();slider=QSlider(Qt.Horizontal);slider.setRange(0,100)
            slider.setValue(int(get_state("foreign_region_"+key,str(default))))
            self.region_sliders.append((key,title,label,slider))
            foreign_layout.addWidget(label);foreign_layout.addWidget(slider)
            slider.valueChanged.connect(self.save_foreign_settings)
        self.foreign_conf.valueChanged.connect(self.save_foreign_settings)
        self.foreign_every.valueChanged.connect(self.save_foreign_settings)
        self.save_foreign_settings()
        note=QLabel("Stop before changing settings. Orange trapezoid marks the conveyor region while running. A box centre must be inside it. Three matching detections latch the alarm. Only wheel_rim and person are trained. OAK processes every received foreign result; the frame interval applies to PC video tests. Video tests can also trigger the alarm.")
        foreign_layout.addWidget(note)
        self.alarm_reset_btn=QPushButton("Admin: reset alarm")
        self.alarm_reset_btn.clicked.connect(self.reset_foreign_alarm)
        foreign_layout.addWidget(self.alarm_reset_btn)
        admin_btn=QPushButton("Set / change admin password")
        admin_btn.clicked.connect(self.configure_alarm_admin);foreign_layout.addWidget(admin_btn)
        incidents_btn=QPushButton("Open incident records")
        incidents_btn.clicked.connect(lambda:os.startfile(str(DATA/"foreign_incidents")) if sys.platform=="win32" else None)
        (DATA/"foreign_incidents").mkdir(exist_ok=True)
        foreign_layout.addWidget(incidents_btn)
        foreign_tab.addWidget(foreign_card);foreign_tab.addStretch()

        line_card, line_layout = self.card(
            "02  COUNTING CONFIGURATION"
        )

        self.dir_combo = QComboBox()
        self.dir_combo.addItems(
            [
                "Either Direction",
                "Bottom to Top",
                "Top to Bottom",
            ]
        )
        self.dir_combo.setCurrentText(
            "Bottom to Top"
        )
        line_layout.addWidget(
            QLabel("Crossing direction")
        )
        line_layout.addWidget(self.dir_combo)

        conf_row = QHBoxLayout()
        conf_row.addWidget(
            QLabel("Detection sensitivity")
        )
        conf_row.addStretch()

        self.conf_lbl = QLabel("35%")
        self.conf_lbl.setObjectName("settingValue")
        conf_row.addWidget(self.conf_lbl)
        line_layout.addLayout(conf_row)

        self.conf = QSlider(Qt.Horizontal)
        self.conf.setRange(20, 80)
        self.conf.setValue(35)
        self.conf.valueChanged.connect(
            lambda value: self.conf_lbl.setText(
                f"{value}%"
            )
        )
        line_layout.addWidget(self.conf)

        try:
            saved_position = int(
                float(get_state("line_ratio", "0.59"))
                * 100
            )
        except (ValueError, TypeError):
            saved_position = 59

        try:
            saved_angle = int(
                get_state("line_angle", "0")
            )
        except (ValueError, TypeError):
            saved_angle = 0

        try:
            saved_length = round(
                float(
                    get_state("line_length", "1.0")
                ) * 100
            )
        except (ValueError, TypeError):
            saved_length = 100

        self.line_pos = QSlider(Qt.Horizontal)
        self.line_pos.setRange(10, 90)
        self.line_pos.setValue(
            max(10, min(90, saved_position))
        )

        self.line_angle = QSlider(Qt.Horizontal)
        self.line_angle.setRange(-30, 30)
        self.line_angle.setValue(
            max(-30, min(30, saved_angle))
        )

        self.line_length = QSlider(Qt.Horizontal)
        self.line_length.setRange(10, 100)
        self.line_length.setValue(
            max(10, min(100, saved_length))
        )

        self.line_pos_lbl = QLabel()
        self.line_angle_lbl = QLabel()
        self.line_length_lbl = QLabel()

        for heading, slider, value_label in (
            (
                "Count line — UP / DOWN",
                self.line_pos,
                self.line_pos_lbl,
            ),
            (
                "Count line — ANGLE",
                self.line_angle,
                self.line_angle_lbl,
            ),
            (
                "Count line — LENGTH",
                self.line_length,
                self.line_length_lbl,
            ),
        ):
            line_layout.addWidget(
                QLabel(heading)
            )
            line_layout.addWidget(slider)
            line_layout.addWidget(value_label)
            slider.valueChanged.connect(
                self.line_changed
            )

        line_buttons = QHBoxLayout()
        up = QPushButton("▲ UP")
        down = QPushButton("▼ DOWN")
        reset = QPushButton("RESET LINE")

        up.clicked.connect(
            lambda: self.line_pos.setValue(
                max(10, self.line_pos.value() - 1)
            )
        )
        down.clicked.connect(
            lambda: self.line_pos.setValue(
                min(90, self.line_pos.value() + 1)
            )
        )
        reset.clicked.connect(self.reset_line)

        self.line_buttons = [up, down, reset]
        for button in self.line_buttons:
            line_buttons.addWidget(button)

        line_layout.addLayout(line_buttons)
        line_tab.addWidget(line_card)

        hint = QLabel(
            "Stop counting to adjust the line. "
            "Drag the yellow count line to move it; drag either end "
            "to shorten or tilt it. Each tracked ID counts once when it crosses "
            "the line in the selected direction."
        )
        hint.setWordWrap(True)
        line_tab.addWidget(hint)
        line_tab.addStretch()

        reports_card, reports_layout = self.card(
            "3  RUN & REPORT"
        )

        export_btn = QPushButton(
            "Export today's results"
        )
        export_btn.clicked.connect(
            self.export_results
        )

        self.reset_btn = QPushButton(
            "Reset session totals"
        )
        self.reset_btn.clicked.connect(
            self.reset_session
        )

        recordings_btn = QPushButton(
            "Open OAK recordings folder"
        )
        recordings_btn.clicked.connect(
            self.open_oak_recordings
        )

        supervisor_btn = QPushButton("Supervisor No Count Reports")
        supervisor_btn.setToolTip("View supervisor replies, outstanding incidents and comments")
        supervisor_btn.clicked.connect(self.open_supervisor_reports)
        reports_layout.addWidget(supervisor_btn)

        reports_layout.addWidget(export_btn)
        reports_layout.addWidget(
            self.reset_btn
        )
        reports_layout.addWidget(
            recordings_btn
        )

        reports_tab.addWidget(reports_card)
        reports_tab.addStretch()

        best_card, best_layout = self.card(
            "BEST COMPLETED PRODUCTION HOUR"
        )
        self.best_hour_period = QLabel()
        self.best_hour_total = QLabel()
        self.best_hour_types = QLabel(
            "OAK tyre counts only"
        )

        for label in (
            self.best_hour_period,
            self.best_hour_total,
            self.best_hour_types,
        ):
            label.setWordWrap(True)
            best_layout.addWidget(label)

        hours_tab.addWidget(best_card)

        lowest_card, lowest_layout = self.card(
            "LOWEST COMPLETED PRODUCTION HOUR"
        )
        self.lowest_hour_period = QLabel()
        self.lowest_hour_total = QLabel()
        self.lowest_hour_note = QLabel(
            "OAK tyre counts only; "
            "zero-count hours excluded"
        )

        for label in (
            self.lowest_hour_period,
            self.lowest_hour_total,
            self.lowest_hour_note,
        ):
            label.setWordWrap(True)
            lowest_layout.addWidget(label)

        hours_tab.addWidget(lowest_card)
        hours_tab.addStretch()

        # Wrap control descriptions and allow pages to fit their actual viewport.
        # Long text must not force an oversized page behind a hidden scrollbar.
        for page_index in range(tabs.count()):
            page_scroll = tabs.widget(page_index)
            host = page_scroll.widget()
            host.setMinimumWidth(0)
            host.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            for frame in host.findChildren(QFrame):
                frame.setMinimumWidth(0)
                frame.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            for label in host.findChildren(QLabel):
                label.setWordWrap(True)
                label.setMinimumWidth(0)
                label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            for control in host.findChildren(QComboBox) + host.findChildren(QPushButton) + host.findChildren(QCheckBox):
                control.setMinimumWidth(0)
                control.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.line_changed()
        self.update_source_status()

        self.setStyleSheet(
            """
            QMainWindow,QWidget{
                background:#101b20;color:#f2f6f4;
                font-family:Segoe UI;font-size:14px
            }
            #brand{
                font-size:21px;font-weight:800;
                color:#ffffff;letter-spacing:1px
            }
            #subtitle,#helper,#footer{
                color:#a8bdb7;font-size:12px
            }
            #health{
                color:#8de2a4;font-weight:800;
                padding:9px 15px;background:#183c32;
                border:1px solid #36795d;
                border-radius:12px
            }
            #card{
                background:#1b2d32;
                border:1px solid #2d4050;
                border-radius:14px
            }
            #cardTitle{
                font-size:13px;font-weight:800;
                color:#a9bfd0
            }
            #metric{
                background:#172633;
                border:1px solid #38535a;
                border-radius:12px
            }
            #metricName{
                color:#c2d3ce;font-size:10px;
                font-weight:800
            }
            #metricValue{
                font-size:22px;font-weight:900;
                color:#f7fbff
            }
            #settingValue{
                color:#9ce6aa;font-weight:800
            }
            QPushButton{
                background:#284047;
                border:1px solid #45666a;
                border-radius:9px;padding:10px;
                font-weight:700;text-align:center
            }
            QPushButton:hover{background:#315066}
            QPushButton:disabled{
                color:#819793;background:#202f34;
                border-color:#34464b
            }
            #start{
                background:#287a49;
                border-color:#78c67e;
                color:white;font-size:17px;
                padding:8px
            }
            #start:hover{background:#35945a}
            #stop{
                background:#772f37;
                border-color:#a8535c;
                color:white;font-size:15px;
                padding:8px
            }
            QTabWidget::pane{
                border:1px solid #38535a;
                border-radius:8px
            }
            QTabBar::tab{
                background:#1b2d32;color:#c2d3ce;
                padding:4px 5px;
                font-size:10px;
                min-height:18px
            }
            QTabBar::tab:selected{
                background:#284047;
                color:#9ce6aa
            }
            QComboBox{
                background:#16282c;
                border:1px solid #45666a;
                border-radius:7px;padding:8px
            }
            QSlider::groove:horizontal{
                height:6px;background:#395458;
                border-radius:3px
            }
            QSlider::handle:horizontal{
                background:#9add8b;width:18px;
                margin:-6px 0;border-radius:9px
            }
            #status{
                background:#1b2d32;
                border:1px solid #38535a;
                border-radius:9px;padding:8px;
                color:#edf4f0;font-weight:600
            }
            """
        )

    def foreign_enabled_changed(self, enabled):
        set_state("foreign_enabled","1" if enabled else "0")
        self.on_foreign_status("Enabled · takes effect on next Start" if enabled else "Disabled")

    def foreign_region_values(self):
        return tuple(slider.value()/100 for _,_,_,slider in self.region_sliders)

    def save_foreign_settings(self):
        self.foreign_conf_lbl.setText(f"Foreign confidence: {self.foreign_conf.value()}%")
        self.foreign_every_lbl.setText(f"Check every {self.foreign_every.value()} processed frames")
        set_state("foreign_confidence",self.foreign_conf.value())
        set_state("foreign_every",self.foreign_every.value())
        for key,title,label,slider in self.region_sliders:
            label.setText(f"{title}: {slider.value()}%")
            set_state("foreign_region_"+key,slider.value())

    def update_alarm_banner(self):
        if self.alarm_state.get("latched"):
            stamp=self.alarm_state.get("timestamp","")
            self.alarm_banner.setText(f"FOREIGN OBJECT ALARM — {self.alarm_state.get('object','Object detected')} | {stamp} | Admin reset required")
            self.alarm_banner.setStyleSheet("background:#a51d2d;color:white;font-size:18px;font-weight:900;padding:10px;border:3px solid #ff6a6a;")
        elif self.foreign_fault:
            self.alarm_banner.setText("FOREIGN DETECTOR FAULT — " + self.foreign_fault)
            self.alarm_banner.setStyleSheet("background:#704800;color:#ffe4a6;font-weight:800;padding:8px;")
        else:
            self.alarm_banner.setText("Foreign detector · STARTER · " + self.foreign_monitor_status)
            self.alarm_banner.setStyleSheet("background:#18352d;color:#b9e6cb;padding:5px;")

    def on_foreign_status(self, text):
        self.foreign_monitor_status=text
        self.foreign_status_lbl.setText(text)
        self.foreign_fault=text if text.startswith("FAULT") else ""
        self.update_alarm_banner()

    def on_foreign_alarm(self, event):
        if self.alarm_state.get("latched"):return
        record={k:v for k,v in event.items() if k!='frame'}
        record['object']=CLASSES[event['class_id']]
        record['snapshot']="Not saved"
        record['latched']=True
        self.alarm_state=record
        self.update_alarm_banner()
        self.alarm_tick()
        try:
            # Persist the latch before writing potentially larger image evidence.
            write_json(self.alarm_state_path,record)
            folder=DATA/"foreign_incidents";folder.mkdir(exist_ok=True)
            name=f"incident_{int(time.time()*1000)}"
            snapshot=folder/(name+'.jpg')
            evidence=event['frame'].copy()
            x1,y1,x2,y2=map(int,event['box'])
            cv2.rectangle(evidence,(x1,y1),(x2,y2),(0,0,255),2)
            cv2.putText(evidence,record['object'],(x1,max(15,y1-5)),cv2.FONT_HERSHEY_SIMPLEX,.5,(0,0,255),1)
            saved=cv2.imwrite(str(snapshot),evidence)
            record['snapshot']=str(snapshot) if saved else "Snapshot write failed"
            write_json(self.alarm_state_path,record)
            write_json(folder/(name+'.json'),record)
            logfile=folder/'incidents.csv';exists=logfile.exists()
            with logfile.open('a',newline='',encoding='utf-8') as f:
                writer=csv.writer(f)
                if not exists:writer.writerow(['Brisbane timestamp','source kind','source','object','confidence','snapshot'])
                writer.writerow([event['timestamp'],event['source_kind'],event['source'],record['object'],event['confidence'],record['snapshot']])
        except Exception:
            log("Incident persistence failed:\n"+traceback.format_exc())
            self.foreign_status_lbl.setText("ALARM active; incident could not be saved — check disk")

    def stop_alarm_sound(self):
        if sys.platform=="win32":
            import winsound
            winsound.PlaySound(None,0)
        self.alarm_sound_active=False

    def alarm_tick(self):
        if not self.alarm_state.get("latched"):return
        if sys.platform=="win32":
            if not self.alarm_sound_active:
                try:
                    import winsound
                    tone=make_tone(DATA/"foreign_alarm.wav")
                    winsound.PlaySound(str(tone),winsound.SND_FILENAME|winsound.SND_ASYNC|winsound.SND_LOOP)
                    self.alarm_sound_active=True
                except Exception:
                    QApplication.beep()
        else:
            QApplication.beep()

    def ask_admin(self):
        password,ok=QInputDialog.getText(self,"Alarm admin","Admin password:",QLineEdit.Password)
        if not ok:return False
        try:valid=verify_password(self.admin_path,password)
        except Exception:valid=False
        if not valid:QMessageBox.warning(self,"Admin access","Incorrect password or unreadable admin credentials.")
        return valid

    def configure_alarm_admin(self):
        if self.admin_path.exists() and not self.ask_admin():return False
        password,ok=QInputDialog.getText(self,"Alarm admin","New password (at least 6 characters):",QLineEdit.Password)
        if not ok:return False
        confirm,ok=QInputDialog.getText(self,"Alarm admin","Repeat new password:",QLineEdit.Password)
        if not ok:return False
        if password!=confirm:
            QMessageBox.warning(self,"Alarm admin","Passwords do not match.");return False
        try:set_password(self.admin_path,password)
        except Exception as exc:
            QMessageBox.warning(self,"Alarm admin",str(exc));return False
        QMessageBox.information(self,"Alarm admin","Admin password saved.")
        return True

    def reset_foreign_alarm(self):
        if not self.alarm_state.get("latched"):return
        if not self.admin_path.exists():
            QMessageBox.information(self,"Alarm admin","Set the first admin password before resetting the alarm.")
            if not self.configure_alarm_admin():return
        if not self.ask_admin():return
        state={"latched":False,"reset_at":brisbane_now().isoformat()}
        try:write_json(self.alarm_state_path,state)
        except Exception as exc:
            QMessageBox.warning(self,"Reset failed",f"Could not save reset: {exc}");return
        self.alarm_state=state
        if self.worker is not None:
            self.worker.foreign_reset_pending=True
            self.worker.foreign_latched=False
        self.stop_alarm_sound();self.update_alarm_banner()

    def update_overlay_mode(self, mode):
        worker = getattr(self, "worker", None)
        if worker is not None:
            worker.overlay_mode = "Bounding Boxes" if self.oak_view_combo.currentText() == "Show Boxes" else "None"
        self._oak_preview_sequence = -1

    def update_tyre_label_visibility(self, checked):
        worker = getattr(self, "worker", None)

        if worker is not None:
            worker.show_tyre_labels = bool(checked)
        self._oak_preview_sequence = -1

    def toggle_counters(self):
        visible = not self.counters_panel.isVisible()
        self.counters_panel.setVisible(visible)
        self.counters_btn.setText("Hide counters" if visible else "Show counters")

    def toggle_controls(self):
        visible = not self.controls_tabs.isVisible()
        self.controls_tabs.setVisible(visible)
        self.controls_btn.setText(
            "Hide controls"
            if visible else "Show controls"
        )

    def open_supervisor_reports(self):
        """Read-only incident register; does not interfere with counting or Gmail."""
        dialog = QDialog(self)
        dialog.setWindowTitle("TyreTrack | Supervisor No Count Reports")
        dialog.resize(1050, 610)
        layout = QVBoxLayout(dialog)
        heading = QLabel("SUPERVISOR NO COUNT REPORTS")
        heading.setStyleSheet("font-size: 19px; font-weight: bold;")
        layout.addWidget(heading)
        summary_label = QLabel("Loading incidents…")
        layout.addWidget(summary_label)
        controls = QHBoxLayout()
        today_only = QCheckBox("Today only (Brisbane)")
        today_only.setChecked(True)
        refresh = QPushButton("Refresh")
        controls.addWidget(today_only)
        controls.addStretch()
        controls.addWidget(refresh)
        layout.addLayout(controls)
        table = QTableWidget(0, 7)
        table.setHorizontalHeaderLabels(["Incident ID", "Started (Brisbane)", "Duration", "Status", "Reason", "Supervisor", "Comments"])
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(0, 195)
        table.setColumnWidth(1, 155)
        table.setColumnWidth(2, 100)
        table.setColumnWidth(3, 150)
        table.setColumnWidth(4, 160)
        table.setColumnWidth(5, 125)
        layout.addWidget(table)
        detail = QLabel("Select an incident to view its full comments.")
        detail.setWordWrap(True)
        layout.addWidget(detail)

        def load_rows():
            try:
                # Database connection is always closed before returning to Qt.
                with no_count_replies.db_connection(DB) as con:
                    no_count_replies.ensure_table(con)
                    sql = ("SELECT incident_id, started_brisbane, ended_brisbane, "
                           "status, COALESCE(reason,''), COALESCE(supervisor,''), "
                           "COALESCE(comments,'') FROM supervisor_no_count")
                    params = ()
                    if today_only.isChecked():
                        sql += " WHERE started_brisbane LIKE ?"
                        params = (brisbane_now().strftime('%Y-%m-%d') + '%',)
                    sql += " ORDER BY started_brisbane DESC LIMIT 2000"
                    rows = con.execute(sql, params).fetchall()
                table.setRowCount(len(rows))
                complete = 0
                for row_i, (incident, started, ended, status, reason, supervisor, comments) in enumerate(rows):
                    complete += status == 'COMPLETED'
                    duration = 'Ongoing'
                    if ended:
                        try:
                            minutes = max(0, int((datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds() // 60))
                            duration = f'{minutes // 60}h {minutes % 60}m'
                        except (ValueError, TypeError):
                            duration = 'Unknown'
                    values = (incident, started, duration, status, reason or 'Awaiting reply', supervisor or '—', comments)
                    for col_i, value in enumerate(values):
                        cell = QTableWidgetItem(str(value))
                        cell.setToolTip(str(value))
                        table.setItem(row_i, col_i, cell)
                summary_label.setText(f"Incidents: {len(rows)}    Completed: {complete}    Awaiting reply: {len(rows)-complete}")
                detail.setText("Select an incident to view its full comments.")
            except Exception as exc:
                summary_label.setText("Unable to read supervisor incidents")
                detail.setText(str(exc))
                log(f"Supervisor incident viewer failed: {exc}")

        def show_details():
            row = table.currentRow()
            if row >= 0:
                ident = table.item(row, 0)
                comments = table.item(row, 6)
                detail.setText(f"{ident.text() if ident else ''} — Comments: {comments.text() if comments and comments.text() else 'None supplied'}")

        refresh.clicked.connect(load_rows)
        today_only.toggled.connect(load_rows)
        table.itemSelectionChanged.connect(show_details)
        # Refresh while visible to show new Gmail replies without reopening.
        timer = QTimer(dialog)
        timer.timeout.connect(load_rows)
        timer.start(30000)
        load_rows()
        dialog.exec_()

    def open_fullscreen(self):
        if self.fullscreen_dialog is not None:
            self.fullscreen_dialog.raise_()
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(
            "TyreTrack — Fullscreen video"
        )

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)

        video = VideoLabel()
        video.editable = False
        video.fullscreen_requested.connect(
            dialog.reject
        )
        layout.addWidget(video, 1)

        close_btn = QPushButton(
            "Return to dashboard  •  "
            "Esc or double-click video"
        )
        close_btn.clicked.connect(
            dialog.reject
        )
        layout.addWidget(close_btn)

        if self.video._pix is not None:
            video._pix = QPixmap(
                self.video._pix
            )
            video._redraw()

        self.fullscreen_dialog = dialog
        self.fullscreen_video = video
        dialog.finished.connect(
            self.fullscreen_closed
        )
        dialog.showFullScreen()

    def fullscreen_closed(self, _result):
        dialog = self.fullscreen_dialog
        self.fullscreen_dialog = None
        self.fullscreen_video = None

        self.video._redraw()
        if dialog is not None:
            dialog.deleteLater()

    def display_frame(self, value):
        emitted_at = None
        frame = value

        if isinstance(value, tuple):
            frame, emitted_at = value

        started = time.monotonic()
        prepared = self.video.prepare_frame(frame)
        self.video.set_prepared_frame(prepared, redraw=self.fullscreen_video is None)
        if self.fullscreen_video is not None:
            self.fullscreen_video.set_prepared_frame(prepared)
        self.gui_timing("frame_prepare", (time.monotonic()-started)*1000)

        if emitted_at is not None:
            if not hasattr(self, "_perf_gui_since"):
                self._perf_gui_since = time.monotonic()
                self._perf_gui_frames = 0
                self._perf_gui_delay_sum = 0.0
                self._perf_gui_delay_max = 0.0

            delay = max(
                0.0,
                time.monotonic() - emitted_at,
            )
            self._perf_gui_frames += 1
            self._perf_gui_delay_sum += delay
            self._perf_gui_delay_max = max(
                self._perf_gui_delay_max,
                delay,
            )

            elapsed = (
                time.monotonic()
                - self._perf_gui_since
            )
            if elapsed >= 10:
                count = max(
                    1, self._perf_gui_frames
                )
                log(
                    "PERF GUI display: "
                    f"fps={self._perf_gui_frames / elapsed:.1f}, "
                    "queue_avg_ms="
                    f"{1000 * self._perf_gui_delay_sum / count:.0f}, "
                    "queue_max_ms="
                    f"{1000 * self._perf_gui_delay_max:.0f}"
                )

                self._perf_gui_since = (
                    time.monotonic()
                )
                self._perf_gui_frames = 0
                self._perf_gui_delay_sum = 0.0
                self._perf_gui_delay_max = 0.0

    def gui_timing(self, stage, milliseconds):
        if not hasattr(self, "_gui_timings"):
            self._gui_timings = {}
            self._gui_timing_since = time.monotonic()
        values = self._gui_timings.setdefault(stage, [0, 0.0, 0.0])
        values[0] += 1; values[1] += milliseconds; values[2] = max(values[2], milliseconds)
        now = time.monotonic()
        if now-self._gui_timing_since >= 10:
            details = ", ".join(f"{key}_avg_ms={total/max(1,count):.1f}, {key}_max_ms={maximum:.1f}"
                                for key,(count,total,maximum) in sorted(self._gui_timings.items()))
            active = self.fullscreen_video if self.fullscreen_video is not None else self.video
            log("PERF GUI stages: "+details+f", image_paint_max_ms={getattr(active, '_max_image_paint_ms', 0):.1f}")
            active._max_image_paint_ms = 0
            self._gui_timings = {}; self._gui_timing_since = now

    def display_oak_preview(self):
        now = time.monotonic()
        previous = getattr(self, "_last_preview_callback", now)
        self._last_preview_callback = now
        self.gui_timing("preview_callback_interval", (now-previous)*1000)
        worker = self.worker

        if (
            worker is None
            or not worker.isRunning()
            or worker.source_kind != "oak"
            or worker.oak_source is None
        ):
            return

        detections = ()
        mode = self.overlay_mode_combo.currentText()
        matched_view = self.oak_view_combo.currentText() == "Show Boxes"
        # AI boxes and labels stay on the exact analysed frame; live view is independent.
        if matched_view:
            overlay = worker.latest_oak_overlay()
            if overlay is None:
                return
            captured_at, sequence, dimensions, detections, processed_frame, result = overlay
            frame = processed_frame.copy()
        else:
            frame, sequence, captured_at = worker.oak_source.preview_snapshot()
        display_key = (matched_view, sequence)
        if frame is None or display_key == self._oak_preview_sequence:
            return
        self._oak_preview_sequence = display_key
        if matched_view:
            draw_detections(frame, detections)
        if not matched_view:
            cv2.putText(frame, "SMOOTH LIVE", (8,18), cv2.FONT_HERSHEY_SIMPLEX,.42,(255,255,255),1)

        draw_line_and_total(
            frame,
            worker.line_ratio,
            worker.line_angle,
            worker.line_length,
            worker.session_count,
        )

        self.display_frame(frame)

        self._oak_preview_frames += 1
        elapsed = (
            time.monotonic()
            - self._oak_preview_since
        )

        if elapsed >= 10:
            age_ms = (
                1000 * max(
                    0.0,
                    time.monotonic() - captured_at,
                )
                if captured_at is not None
                else -1
            )
            log(
                f"PERF OAK preview ({'matched' if matched_view else 'live'}): "
                f"fps={self._oak_preview_frames / elapsed:.1f}, "
                f"latest_frame_age_ms={age_ms:.0f}"
            )
            self._oak_preview_since = (
                time.monotonic()
            )
            self._oak_preview_frames = 0

    def update_source_status(self):
        source = self.source_combo.currentText()
        is_file = source == "Video File"

        self.browse_btn.setVisible(is_file)
        self.file_lbl.setVisible(is_file)

        if is_file:
            name = (
                Path(self.video_path).name
                if self.video_path
                else "select a video"
            )
            self.source_status.setText(
                f"Source: {name}"
            )
        else:
            self.source_status.setText(
                f"Source: {source}"
            )

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select conveyor video",
            "",
            "Video (*.mp4 *.avi *.mov *.mkv)",
        )
        if not path:
            return

        self.video_path = path
        self.file_lbl.setText(
            Path(path).name
        )
        self.source_combo.setCurrentText(
            "Video File"
        )
        self.update_source_status()

        cap = cv2.VideoCapture(path)
        try:
            ok, frame = cap.read()
            if ok:
                self.display_frame(frame)
        finally:
            cap.release()

    def auto_schedule_changed(self, enabled):
        set_state("auto_schedule", "1" if enabled else "0")
        if enabled:
            set_state("auto_paused_day", "")
            self.auto_retry_at = 0.0

    def schedule_tick(self):
        if self.closing or not self.auto_schedule.isChecked():
            return
        now = brisbane_now()
        running = self.worker is not None and self.worker.isRunning()
        if running and self.worker.source_kind == "oak" and getattr(self.worker,"manual_test",False):
            deadline=getattr(self.worker,"manual_test_end",None)
            if deadline is not None and now>=deadline:
                self.stop(scheduled=True)
                self.auto_retry_at=0.0
            return
        if not oak_in_operating_hours():
            if running and self.worker.source_kind == "oak":
                self.stop(scheduled=True)
            return
        if running or get_state("auto_paused_day", "") == now.date().isoformat():
            return
        if time.monotonic() < self.auto_retry_at:
            return
        self.auto_retry_at = time.monotonic() + 60
        self.source_combo.setCurrentText(OAK_NAME)
        self.start(automatic=True)

    def selected_source(self):
        source = self.source_combo.currentText()

        if source == "Video File":
            if not self.video_path:
                self.browse()

            if not self.video_path:
                return None

            return "video", self.video_path

        if source == "Camera 0":
            return "camera", 0

        if source == "Camera 1":
            return "camera", 1

        return "oak", ""

    def set_running_controls(self, running):
        self.manual_oak_test.setEnabled(not running)
        self.foreign_switch.setEnabled(not running)
        self.foreign_conf.setEnabled(not running)
        self.foreign_every.setEnabled(not running)
        for _,_,_,slider in self.region_sliders:slider.setEnabled(not running)
        self.video.editable = not running
        self.video.update()

        self.start_btn.setEnabled(
            not running
        )
        self.stop_btn.setEnabled(running)
        self.reset_btn.setEnabled(
            not running
        )
        self.source_combo.setEnabled(
            not running
        )
        self.browse_btn.setEnabled(
            not running
        )
        self.dir_combo.setEnabled(
            not running
        )
        self.conf.setEnabled(
            not running
        )

        for slider in (
            self.line_pos,
            self.line_angle,
            self.line_length,
        ):
            slider.setEnabled(
                not running
            )

        for button in self.line_buttons:
            button.setEnabled(
                not running
            )

    def start(self, checked=False, automatic=False):
        if (
            self.worker is not None
            and self.worker.isRunning()
        ):
            return

        selected = self.selected_source()
        if selected is None:
            return

        self.auto_attempt = automatic
        manual_test = selected[0] == "oak" and not automatic and self.manual_oak_test.isChecked()
        if selected[0] == "oak" and not manual_test:
            set_state("auto_paused_day", "")
        try:
            if selected[0] == "oak" and not oak_session_allowed(manual_test):
                raise ValueError("Outside 05:00–17:05 Brisbane production hours. Enable 'Manual OAK test · any time', then press Start.")
            if self.foreign_switch.isChecked():
                t,b,l,r=self.foreign_region_values()
                if not (t<b and l<r):raise ValueError("Foreign region requires top < bottom and left < right")
            self.session = 0
            self.test_events = []
            self.refresh_counts()

            worker = CounterWorker(
                selected[0],
                selected[1],
                self.dir_combo.currentText(),
                self.conf.value() / 100,
                IOU_DEFAULT,
                self.line_pos.value() / 100.0,
                self.line_angle.value(),
                self.line_length.value() / 100.0,
            )
            worker.manual_test=manual_test
            worker.session_started_monotonic=time.monotonic()
            if manual_test:
                now=brisbane_now()
                next_start=now.replace(hour=5,minute=0,second=0,microsecond=0)
                worker.manual_test_end=next_start if now<next_start else next_start+timedelta(days=1)
            worker.overlay_mode = "Bounding Boxes" if self.oak_view_combo.currentText() == "Show Boxes" else "None"
            worker.show_tyre_labels = (
                self.show_tyre_labels.isChecked()
            )
            worker.foreign_enabled=self.foreign_switch.isChecked()
            worker.foreign_confidence=self.foreign_conf.value()/100
            worker.foreign_every=self.foreign_every.value()
            worker.foreign_region=self.foreign_region_values()
            worker.foreign_latched=bool(self.alarm_state.get("latched"))
            worker.foreign_event.connect(self.on_foreign_alarm)
            worker.foreign_status.connect(self.on_foreign_status)
            self.on_foreign_status("Loading foreign model..." if worker.foreign_enabled else "Disabled")
            self.worker = worker
            self.refresh_counts()

            worker.frame_ready.connect(
                self.display_frame
            )
            worker.counted.connect(
                self.on_count
            )
            worker.status.connect(
                self.on_status
            )
            worker.finished_clean.connect(
                self.worker_done
            )

            self.set_running_controls(True)
            worker.start()

            if selected[0] == "oak":
                self._oak_preview_sequence = 0
                self._oak_preview_since = (
                    time.monotonic()
                )
                self._oak_preview_frames = 0
                self.oak_preview_timer.start()

        except Exception as exc:
            log(
                "START failed:\n"
                + traceback.format_exc()
            )
            self.set_running_controls(False)
            if not automatic:
                QMessageBox.critical(self, "Start Failed", str(exc))
            else:
                self.status_lbl.setText(f"Auto OAK start failed; retry in 60 seconds: {exc}")

    def _confirm_admin_pin(self, action):
        """Guard operator-initiated shutdowns; scheduled stops are automatic."""
        pin, accepted = QInputDialog.getText(
            self, "TyreTrack administrator", f"Enter 4-digit PIN to {action}:",
            QLineEdit.Password,
        )
        if not accepted:
            return False
        if pin != "0808":
            QMessageBox.warning(self, "Administrator PIN", "Incorrect PIN. Action cancelled.")
            return False
        return True

    def stop(self, checked=False, scheduled=False):
        if not scheduled and self.worker is not None and self.worker.isRunning():
            if not self._confirm_admin_pin("stop counting"):
                return
        if not scheduled and self.worker is not None and self.worker.source_kind == "oak" and not self.worker.manual_test:
            set_state("auto_paused_day", brisbane_now().date().isoformat())
        if self.worker is not None:
            self.worker.stop()

        self.stop_btn.setEnabled(False)
        self.status_lbl.setText(
            "Stopping… finishing the current frame"
        )

    def worker_done(self):
        if self.worker is not None and self.worker.foreign_enabled and not self.foreign_fault:
            self.on_foreign_status("Stopped · foreign detector offline")
        self.oak_preview_timer.stop()
        self.set_running_controls(False)

    def on_status(self, status):
        messages = {
            "COUNTING":
                "Counting in progress  •  Tyres count "
                "when they cross the yellow line",
            "STOPPED":
                "Stopped  •  Session totals are "
                "ready to export",
        }
        self.status_lbl.setText(
            messages.get(status, status)
        )

        if status.startswith("ERROR:"):
            self.health.setText(
                "● CHECK SYSTEM"
            )
            self.health.setStyleSheet(
                "color:#ff9b9b"
            )
            if not self.auto_attempt:
                QMessageBox.critical(
                    self,
                    "TYRETRACK - System Error",
                    status[6:].strip()
                    + "\n\nFull details: "
                    + str(LOG),
                )
        elif status == "COUNTING":
            is_test=self.worker is not None and self.worker.manual_test
            if is_test:
                self.status_lbl.setText("OAK MANUAL TEST · live camera · counts excluded from production")
            self.health.setText("● OAK TEST" if is_test else "● COUNTING")
            self.health.setStyleSheet(
                "color:#7de7bc"
            )
        elif status == "STOPPED":
            self.health.setText(
                "● READY"
            )
            self.health.setStyleSheet("")

    def on_count(self, _track_id, _confidence):
        # Already committed to SQLite in CounterWorker.
        self.session += 1
        if self.worker is not None and (self.worker.source_kind == "video" or self.worker.manual_test):
            elapsed=(self.worker.processing_frame / max(1.0,self.worker.video_fps)
                     if self.worker.source_kind=="video" else time.monotonic()-self.worker.session_started_monotonic)
            self.test_events.append(elapsed)

        if self.worker is not None and self.worker.source_kind == "oak" and not self.worker.manual_test:
            if not hasattr(self, "_csv_exporter"):
                from background_export import CoalescedExport
                self._csv_exporter = CoalescedExport(
                    lambda: (export_daily_csv(), export_classification_csv()),
                    lambda exc: log(f"Daily CSV update failed: {exc}"))
            self._csv_exporter.request()
        # SQLite already contains the count. The existing 1-second timer refreshes cards;
        # never scan all reports or write CSV synchronously for every crossing.

    @staticmethod
    def _slot_minutes(slot):
        """Duration for both datetime production slots and float video-test slots."""
        duration = slot["end"] - slot["start"]
        seconds = duration.total_seconds() if hasattr(duration, "total_seconds") else float(duration)
        return max(seconds / 60.0, 1.0 / 60.0)

    def refresh_production_hours(self, test_mode=False, test_seconds=0.0):
        if test_mode:
            # Recorded footage uses a virtual production clock beginning at 05:00.
            # These values never affect the real OAK production database.
            slots = []
            for index in range(13):
                begin = index * 3600.0
                finish = min((index + 1) * 3600.0, 12 * 3600.0 + 300.0)
                slots.append(dict(number=index+1, start=begin, end=finish,
                                  count=sum(begin <= t < finish for t in self.test_events),
                                  completed=test_seconds >= finish))
            completed = [s for s in slots if s["completed"]]
            best = max(completed, key=lambda s:s["count"]) if completed else None
            lowest = min(completed, key=lambda s:s["count"]) if completed else None
        else:
            if self._dashboard_cache is None: return
            slots, best, lowest = self._dashboard_cache['hourly']

        def display(slot):
            return f"H{slot['number']}  ·  {slot['count']:,}" if slot else "0  ·  waiting"

        completed_slots = [slot for slot in slots if slot['completed']]
        average = (sum(slot['count'] for slot in completed_slots) / len(completed_slots)) if completed_slots else 0.0
        self.production_average.setText(f"{average:,.1f} tyres/hr")
        # Best sustained flow measured across completed hourly slots, including zero-count hours.
        flow = max((slot['count'] / self._slot_minutes(slot)
                    for slot in completed_slots), default=0.0)
        self.production_flow_best.setText(f"{flow:.2f} tyres per min")
        self.session_lbl.setText(str(best['count'] if best else 0))
        if best:
            if test_mode:
                a = 5 * 3600 + best['start']; b = 5 * 3600 + best['end']
                period_label = f"{int(a//3600):02d}:{int(a%3600//60):02d}–{int(b//3600):02d}:{int(b%3600//60):02d}"
            else:
                period_label = f"{best['start']:%H:%M}–{best['end']:%H:%M}"
            self.best_hour_card.setToolTip(f"Best completed hour: {period_label} Brisbane")
        for prefix, slot in (("best", best), ("lowest", lowest)):
            getattr(self, prefix + "_hour_total").setText(display(slot))
            if slot:
                if test_mode:
                    a = 5 * 3600 + slot['start']
                    b = 5 * 3600 + slot['end']
                    period = f"{int(a//3600):02d}:{int(a%3600//60):02d}–{int(b//3600):02d}:{int(b%3600//60):02d} VIDEO TEST"
                else:
                    period = f"{slot['start']:%H:%M:%S}–{slot['end']:%H:%M:%S} Brisbane"
            else:
                period = "No completed hour yet"
            getattr(self, prefix + "_hour_period").setText(period)
        self.best_hour_types.setText("VIDEO TEST · virtual start 05:00" if test_mode else
                                     "Production day · closes 17:05 Brisbane UTC+10")
        self.lowest_hour_note.setText("Completed empty hours included; current/future excluded")
        self.history_heading.setText("HOURLY HISTORY · VIDEO TEST (05:00 virtual)" if test_mode else
                                     "HOURLY HISTORY · starts 05:00 · cutoff 17:05 Brisbane UTC+10")
        now = brisbane_now()
        active = 0
        for index, slot in enumerate(slots):
            completed = slot['completed']
            current = (slot['start'] <= test_seconds < slot['end']) if test_mode else (slot['start'] <= now < slot['end'])
            colour = "#38D996" if completed else "#42B7FF" if current else "#84929E"
            tag = "✓" if completed else "live" if current else "–"
            self.production_slots[index].setText(f"H{index+1} {tag}\n{slot['count']:,}")
            style = f"color:{colour};font-size:13px;font-weight:700;padding:2px;"
            if self.production_slots[index].styleSheet() != style:
                self.production_slots[index].setStyleSheet(style)
            if test_mode:
                self.production_slots[index].setToolTip("Video playback time, starting at virtual 05:00")
            else:
                self.production_slots[index].setToolTip(
                    f"{slot['start']:%H:%M:%S}–{slot['end']:%H:%M:%S}")
            if completed or current:
                active = index
        self.production_scroll.ensureWidgetVisible(self.production_slots[active])

    def request_dashboard_data(self):
        if not hasattr(self, "_dashboard_loader"):
            from background_export import CoalescedExport
            def collect():
                started = time.monotonic()
                metrics = production_snapshot()
                now = brisbane_now()
                with sqlite3.connect(DB,timeout=15) as con:
                    types = con.execute("SELECT tyre_type,COUNT(*) FROM counts WHERE source=? AND metric_utc>=? AND metric_utc<? GROUP BY tyre_type",
                        (OAK_NAME,metrics['day_start'].timestamp(),min(now,metrics['day_end']).timestamp())).fetchall()
                    hourly = hourly_snapshot(con, source=OAK_NAME)
                best = best_daily_record()
                self.dashboard_ready.emit(dict(metrics=metrics, types=types, hourly=hourly,
                    best=best, collect_ms=(time.monotonic()-started)*1000))
            self._dashboard_loader = CoalescedExport(collect,
                lambda exc: log(f"Dashboard background refresh failed: {exc}"))
        self._dashboard_loader.request()

    def dashboard_data_ready(self, data):
        if getattr(self, "closing", False): return
        self._dashboard_cache = data
        self.gui_timing("dashboard_collect_background", data['collect_ms'])
        started = time.monotonic()
        self.refresh_counts(use_cached=True)
        self.gui_timing("dashboard_apply", (time.monotonic()-started)*1000)

    def refresh_counts(self, use_cached=False):
        if not use_cached:
            self.request_dashboard_data()
        worker = self.worker
        test_mode = bool(worker is not None and (worker.source_kind == "video" or worker.manual_test))
        if test_mode:
            elapsed = (worker.processing_frame / max(1.0,worker.video_fps) if worker.source_kind=="video"
                       else time.monotonic()-worker.session_started_monotonic)
            events = sorted(self.test_events)
            hour_index = min(12, int(elapsed // 3600))
            this_hour = sum(hour_index*3600 <= t < min((hour_index+1)*3600, 43500) for t in events)
            last_five = sum(max(0, elapsed-300) <= t <= elapsed for t in events)
            gaps = [b-a for a,b in zip(events,events[1:]) if b-a >= 600]
            trailing = max(0, elapsed-events[-1]) if events else 0
            if trailing >= 600:
                gaps.append(trailing)
            types = worker.session_type_counts
            # Worker commits before emitting the counted signal, so session totals agree.
            self.hour_lbl.setText(str(this_hour))
            self.day_lbl.setText(str(len(events)))
            self.total_lbl.setText(str(len(events)))
            completed = [sum(i*3600 <= t < min((i+1)*3600,43500) for t in events)
                         for i in range(13) if elapsed >= min((i+1)*3600,43500)]
            # Best-hour count is refreshed from completed slots below.
            test_starts = [a + 600 for a,b in zip(events, events[1:]) if b-a >= 600]
            if events and trailing >= 600:
                test_starts.append(events[-1] + 600)
            test_stamp = str(timedelta(seconds=int(test_starts[-1]))) if test_starts else "None"
            self.no_count_lbl.setText(f"{len(gaps)} · TEST {test_stamp}")
            self.no_count_lbl.setToolTip("Video elapsed time when the 10-minute threshold was reached")
            self.set_flow_rate(last_five / max(1/60, min(300, max(elapsed,1))) * 60)
            self.gap_lbl.setText(f"{sum(gaps)/60:.0f} min")
            self.production_status_lbl.setText("TEST · NO COUNT" if elapsed >= 600 and (trailing >= 600 or not events) else "OAK MANUAL TEST" if worker.manual_test else "TEST MODE")
            self.all_time_lbl.setText("TEST SESSION  " + str(len(events)))
            self.passenger_lbl.setText(str(types.get("passenger",0)))
            self.suv_lbl.setText(str(types.get("4x4",0)))
            self.refresh_production_hours(test_mode=True,test_seconds=elapsed)
        else:
            if self._dashboard_cache is None: return
            metrics = self._dashboard_cache['metrics']
            self.hour_lbl.setText(str(metrics["current_hour"]))
            self.day_lbl.setText(str(metrics["daily"]))
            self.total_lbl.setText(str(metrics["all_time"]))
            # Best-hour count is refreshed from completed slots below.
            stamps = metrics["alert_times"]
            latest = stamps[-1].strftime("%d %b %H:%M:%S") if stamps else "None today"
            self.no_count_lbl.setText(f"{metrics['no_count_incidents']} · {latest}")
            self.no_count_lbl.setToolTip("Brisbane alert threshold times (10 min without counts):\n" +
                                         "\n".join(t.strftime("%Y-%m-%d %H:%M:%S") for t in stamps))
            self.set_flow_rate(metrics['flow_per_minute'])
            self.gap_lbl.setText(f"{metrics['no_flow_minutes']:.0f} min")
            self.production_status_lbl.setText(metrics["no_count_status"])
            self.all_time_lbl.setText("ALL TIME  " + str(metrics["all_time"]))
            types = self._dashboard_cache['types']
            class_totals = {"passenger":0,"4x4":0}
            for name,n in types:
                key=tyre_class_name(name)
                if key in class_totals:
                    class_totals[key]+=n
            self.passenger_lbl.setText(str(class_totals['passenger']))
            self.suv_lbl.setText(str(class_totals['4x4']))
            self.refresh_production_hours()
        best_date, best_count = self._dashboard_cache['best'] if self._dashboard_cache else (None, 0)
        self.best_daily_lbl.setText(f"{best_count:,} tyres")
        self.best_daily_lbl.setToolTip(f"Best production day: {best_date or 'No OAK counts yet'} · 05:00–17:05 Brisbane; includes today")
        if best_date:
            self.best_daily_lbl.setText(f"{best_count:,} tyres\n{best_date}")
        self.gap_lbl.setStyleSheet("color:#FF5C68;font-size:22px;font-weight:900;")
        self.no_count_lbl.setStyleSheet("color:#FF5C68;font-size:16px;font-weight:900;")
        alert = "NO COUNT" in self.production_status_lbl.text()
        self.production_status_lbl.setStyleSheet(
            "color:#FF5C68;font-size:22px;font-weight:900;" if alert else "")
        if getattr(self, "_last_dashboard_snapshot", 0) + 30 < time.monotonic():
            self._last_dashboard_snapshot = time.monotonic()
            self.grab().save(str(DASHBOARD_SNAPSHOT), "PNG")

    def set_flow_rate(self, rate):
        self.flow_lbl.setText(f"{rate:.1f} tyres per min")
        if rate < 5:
            if not self.flow_flash_timer.isActive():
                self._flow_flash_red = False
                self.flash_low_flow()
                self.flow_flash_timer.start()
        else:
            self.flow_flash_timer.stop()
            self._flow_flash_red = False
            self.flow_lbl.setStyleSheet("")

    def flash_low_flow(self):
        self._flow_flash_red = not self._flow_flash_red
        self.flow_lbl.setStyleSheet(
            "color:#FF5C68;font-size:22px;font-weight:900;" if self._flow_flash_red else "")

    def reset_session(self):
        if (
            self.worker is not None
            and self.worker.isRunning()
        ):
            QMessageBox.information(
                self,
                "Stop counting first",
                "Stop the current run before "
                "resetting its total.",
            )
            return

        if self.session:
            answer = QMessageBox.question(
                self,
                "Reset session total",
                "Clear the session total on this "
                "screen? Saved records remain.",
            )
            if answer != QMessageBox.Yes:
                return

        self.session = 0
        self.test_events = []
        if self.worker is not None:
            self.worker.session_count = 0
            self.worker.session_type_counts = {"passenger": 0, "4x4": 0, "unknown": 0}
            self.worker.counted_ids.clear()
        self.refresh_counts()

    def export_results(self):
        try:
            suggested = (
                f"TyreTrack_"
                f"{datetime.now().date().isoformat()}.csv"
            )
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Export TyreTrack Results",
                suggested,
                "CSV Files (*.csv)",
            )
            if not path:
                return

            if not path.lower().endswith(".csv"):
                path += ".csv"

            report = export_daily_csv()
            if Path(report).resolve() != Path(path).resolve():
                shutil.copy2(str(report), path)
            QMessageBox.information(
                self,
                "Export Complete",
                f"Results exported to:\n{path}",
            )
        except Exception as exc:
            log(
                "Manual export failed:\n"
                + traceback.format_exc()
            )
            QMessageBox.critical(
                self,
                "Export Failed",
                str(exc),
            )

    def open_oak_recordings(self):
        try:
            os.startfile(
                str(OAK_RECORDINGS)
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                "OAK recordings",
                f"Cannot open folder: {exc}\n\n"
                f"{OAK_RECORDINGS}",
            )

    def mouse_line_changed(
        self, ratio, angle, length
    ):
        sliders = (
            self.line_pos,
            self.line_angle,
            self.line_length,
        )

        for slider in sliders:
            slider.blockSignals(True)

        self.line_pos.setValue(
            round(ratio * 100)
        )
        self.line_angle.setValue(angle)
        self.line_length.setValue(
            round(length * 100)
        )

        for slider in sliders:
            slider.blockSignals(False)

        self.line_changed()

    def line_changed(self):
        if (
            self.worker is not None
            and self.worker.isRunning()
        ):
            return

        ratio = self.line_pos.value() / 100.0
        angle = self.line_angle.value()
        length = self.line_length.value() / 100.0

        self.line_pos_lbl.setText(
            f"Position: {self.line_pos.value()}%"
        )
        self.line_angle_lbl.setText(
            f"Angle: {angle:+d}°"
        )
        self.line_length_lbl.setText(
            f"Length: {self.line_length.value()}%"
        )

        self.video.line_ratio = ratio
        self.video.line_angle = angle
        self.video.line_length = length
        self.video.update()

        set_state("line_ratio", ratio)
        set_state("line_angle", angle)
        set_state("line_length", length)

    def reset_line(self):
        if (
            self.worker is not None
            and self.worker.isRunning()
        ):
            return

        self.line_pos.setValue(59)
        self.line_angle.setValue(0)
        self.line_length.setValue(100)
        self.line_changed()

    def tick(self):
        for task_name, task in (("Alarm timer", self.alarm_tick),
                                ("Auto-schedule timer", self.schedule_tick)):
            try:
                task()
            except Exception:
                log(task_name + " failed (dashboard remains open):\n" + traceback.format_exc())
        refresh_started = time.monotonic()
        try:
            self.refresh_counts()
        except Exception:
            log(
                "Count refresh failed:\n"
                + traceback.format_exc()
            )

        self.gui_timing("dashboard_refresh", (time.monotonic()-refresh_started)*1000)
        # Manual OAK and video tests never send live production reports or alerts.
        if self.worker is not None and self.worker.manual_test:
            return
        if self.worker is not None and self.worker.source_kind != "oak":
            return
        if not self.auto_schedule.isChecked() and (self.worker is None or not self.worker.isRunning()):
            return

        email_thread_idle = (
            self.email_thread is None
            or not self.email_thread.is_alive()
        )
        if (
            email_thread_idle
            and time.monotonic()
            >= self.email_retry_at
        ):
            self.email_retry_at = (
                time.monotonic() + 60
            )
            self.email_thread = threading.Thread(
                target=process_scheduled_emails,
                args=(brisbane_now(),),
                daemon=True,
            )
            self.email_thread.start()

    def closeEvent(self, event):
        if not self._confirm_admin_pin("close TyreTrack"):
            event.ignore()
            return
        self.closing = True
        self.oak_preview_timer.stop()

        if (
            self.worker is not None
            and self.worker.isRunning()
        ):
            self.worker.stop()

            if not self.worker.wait(15000):
                QMessageBox.warning(
                    self,
                    "Still stopping",
                    "The detector has not finished "
                    "its current frame. Wait, then "
                    "close the app again.",
                )
                if self.worker.source_kind == "oak":
                    self.oak_preview_timer.start()
                self.closing = False
                event.ignore()
                return

        loader = getattr(self, "_dashboard_loader", None)
        if loader is not None: loader.wait(3)
        exporter = getattr(self, "_csv_exporter", None)
        if exporter is not None and not exporter.wait(3):
            log("CSV export still finishing at close; committed counts remain in SQLite")
        if hasattr(self, "remote_timer"): self.remote_timer.stop()
        if hasattr(self, "remote_viewer"): self.remote_viewer.close()
        self.stop_alarm_sound()
        event.accept()


if __name__ == "__main__":
    if "--oak-hardware-test" in sys.argv:
        from oak_hardware_test import main as hardware_main
        index=sys.argv.index("--oak-hardware-test")
        sys.exit(hardware_main(sys.argv[index+1:],root=resource_path(".")))
    if "--build-smoke-test" in sys.argv:
        from build_support.smoke_test import run
        output = sys.argv[sys.argv.index("--build-smoke-test") + 1]
        sys.exit(run(sys.modules[__name__], output))

    def _uncaught_exception(exc_type, exc_value, exc_tb):
        details = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log("UNHANDLED APPLICATION ERROR:\n" + details)
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _uncaught_exception
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = Main()
    window.show()

    sys.exit(app.exec_())

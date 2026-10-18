TYRETRACK STABILITY PATCH - 18 OCTOBER 2026

This is the full original Supervisor No Count Dashboard source ZIP with ONLY
TyreTrack_Production_Monitor.py replaced by the corrected version.

CHANGES
1. Fix float/datetime slot duration bug during video tests.
2. Catch and log alarm/schedule timer exceptions rather than letting them
   escape the Qt timer callback.
3. Log unhandled Python errors to data/tyretrack.log.

NOT CHANGED: detector weights, OAK firmware/models, count line logic,
foreign-object alarm logic, supervisor email reply handling, database schema,
automatic operating hours (05:00-17:05 Brisbane).

INSTALL
Build a new Windows portable application from this source ZIP using your
existing GitHub workflow. Do not overwrite the currently working installation
until the new build has been tested. The .py file alone does not update an EXE.
Preserve the production data/ directory and private email settings when
installing the new build.

VALIDATION
Python syntax compilation passed. Both float and datetime slot duration
tests passed. Live OAK/Windows GUI integration has NOT been tested here.
This patch fixes the confirmed error but does not prove the reason for every
unexpected process termination.

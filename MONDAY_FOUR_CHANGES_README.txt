TYRETRACK — WORKING DUAL OAK SOURCE + FOUR TARGETED CHANGES
Based on: TyreTrack_Dual_OAK_AI_Integrated_Source (3)(4).zip

Changes in TyreTrack_Production_Monitor.py:
1. Auto operation only Monday-Friday, 05:00-17:05 Brisbane local time. The existing automatic stop bypasses the manual PIN.
2. No Count dashboard incidents are calculated from the current Brisbane 05:00 shift boundary; no new weekend incidents are generated. Prior-day counts and all-time totals remain preserved.
3. Manual Stop while a worker is running and application Close require four-digit PIN 0808. Cancel or wrong PIN leaves the operation running/open. PIN is embedded in source: change it before distributing a public repository build.
4. OAK display options read 'Smooth' (clean live preview) and 'Show Boxes' (matched-frame detection boxes); the counting engine is unchanged.

Verification performed: Python syntax compilation, hardware-independent regression tests, existing schedule metrics test. Not verified: Windows GUI, live OAK hardware, foreign-object detection accuracy, packaged EXE, or full CI test suite.

Do not replace a running yard PC with this source archive. Build a Windows portable EXE and test on a nonproduction machine first.

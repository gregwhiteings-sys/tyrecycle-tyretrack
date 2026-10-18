TYRETRACK WINDOWS BUILD - TIMEOUT FIX

This package uses the uploaded last-working Dual OAK source with the four requested control changes.

Fix: BUILD_WINDOWS.py no longer runs full YOLO inference and camera pipeline setup as a blocking pre-packaging smoke test.
The quick smoke verifies application imports, essential bundled resources and offscreen Qt startup.
The existing run_ci_tests.py still performs the independent regression tests with 90-second per-test timeouts.
Detailed AI/hardware verification remains necessary on the Australian PC.

Upload the extracted ZIP contents to your PRIVATE GitHub repository root, replacing files, then Actions -> TyreTrack Windows Portable (OAK Onboard AI) -> Run workflow.

A successful GitHub build proves packaging and automated checks only; it does not prove live OAK camera operation or counting accuracy.
Do not replace the working production installation before testing the EXE on a separate copy.

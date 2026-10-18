TYRETRACK — DETECTION-ONLY DUAL OAK SOURCE UPDATE

Segmentation is removed from both OAK inference and PC app processing.
OAK uses the previously included verified 384x384 detection-only tyre blob.
The PC video path uses the included detection-only tyre_detector.pt.
The separate 416x416 wheel_rim/person foreign detector remains on OAK.
No segmentation model is shipped; no mask tensors, mask reconstruction or
mask overlay code is used. Counting uses the red dot at each bounding-box centre.
The two networks perform inference on OAK; CPU decodes boxes, tracks IDs,
counts crossings, votes Passenger/4x4 classes, handles dashboard/reports/alarms.
The OAK compresses 640x360 H264; PC remuxes/saves without re-encoding.

BUILD
This is source, not a prebuilt Windows executable. Extract and upload the
CONTENTS to your existing GitHub repository, including .github/workflows.
Run the included windows-portable.yml workflow and download its portable artifact.
Use BUILD_WINDOWS.py through the workflow; older build notes/scripts are superseded.
The PC must stay awake with TyreTrack open for automatic production operation.

AUTOMATIC SCHEDULE
Every app launch enables automatic OAK connection and clears old Auto-off/pause
settings. Production starts at 05:00 Brisbane UTC+10 (or immediately if launched
during shift), retries failures every 60 seconds and stops at 17:05, every day.
Stop still pauses automatic restart for the rest of that day in the current app
session. Re-enable Auto or reopen to resume. Windows login startup/wake is not installed.
Manual OAK tests outside production hours remain separate from production counts.

PHYSICAL CAMERA TEST
Close other camera apps and run RUN_OAK_HARDWARE_TEST.bat with the OAK connected.
From a built portable folder it runs both networks, preview and recording for
60 seconds and saves oak_hardware_report.json and oak_probe_recording MP4(s).
This test does not count tyres, send production email, or clear alarms.
The hardware test checks streams/FPS/latency, not actual detection accuracy.
Send the JSON back for review.
For a specific PoE camera IP:
 TyreTrack.exe --oak-hardware-test --ip 192.168.x.x --seconds 60 --output oak_hardware_report.json
For tyre-only comparison:
 TyreTrack.exe --oak-hardware-test --tyre-only --seconds 60 --output oak_tyre_only.json
For source testing, substitute: py -3.12 oak_hardware_test.py [options]

COUNTING AND ALARM VALIDATION
Use an isolated app copy to test known videos and manual OAK counts.
The red dot is the exact counting anchor. ByteTrack IDs are retained; each ID counts once after its centre crosses the yellow line in the selected direction. A stable approach arms the track; touching the line alone does not count. Centre crossings differ from old box-bottom/mask-edge crossings. Validate
overlapping tyres and line placement against a known manual count before production.
Enable the foreign detector before Start and tune the orange belt ROI.
Three spatial confirmations latch the alarm; sound/banner persist until admin reset.
Video tests can also trigger this alarm. Test sound and authenticated reset on Windows.
Only wheel_rim and person are trained, using a small synthetic 39-frame dataset;
general debris and real-world alarm accuracy are not established.
Wheel/rim alarms do not automatically subtract tyres from normal counts.
Never place a person on or near a moving conveyor for testing.

GEOMETRY AND LOAD
The 480x270 BGR live preview remains independent of inference speed.
Tyre inference uses a 384x384 RGB thumbnail with 84px vertical padding;
foreign inference uses 416x416 BGR with 91px padding. Host maps both back to
the same full 480x270 scene. Normalization is embedded in each compiled model.
Tyre tensor output is 18,144 FP16 values = 36,288 bytes per inference, down
from the previous segmentation export's 969,046 bytes (about 96% less output).
This is not a claimed 96% FPS/CPU improvement. Physical performance is unmeasured.
Each network pairs its own outputs and passthrough by camera frame sequence.
Foreign frame interval slider affects PC video tests only; live OAK uses each
newly received foreign result once. A foreign stream silence >6 seconds faults.

VERIFICATION
See integration_checks.json and source-smoke.json for offline checks.
No physical OAK test or Windows executable build ran in this workspace.

RED DOT DISPLAY
Select OAK viewing mode: Matched AI overlays, and AI display: Bounding Boxes
to see the centre counting dot on its exact analysed frame. Smooth live remains
available for independent preview; it intentionally does not draw delayed AI dots.
Full source validation is summarized in FINAL_VALIDATION.json.

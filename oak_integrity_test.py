"""Windows CRLF checkout must pass, but changed model/config content must fail."""
from pathlib import Path
import tempfile,shutil
import depthai as dai
from oak_rvc2 import validate_models
ROOT=Path(__file__).resolve().parent
with tempfile.TemporaryDirectory() as folder:
    base=Path(folder)
    shutil.copy2(ROOT/'tyre_detector_oak.blob',base/'tyre_detector_oak.blob')
    models=base/'rvc2_models';shutil.copytree(ROOT/'rvc2_models',models)
    # Start from LF exports regardless of checkout defaults.
    for p in (models/'foreign').glob('*.json'):
        p.write_bytes(p.read_bytes().replace(b'\r\n',b'\n'))
    assert validate_models(dai,models)
    for p in (models/'foreign').glob('*.json'):
        p.write_bytes(p.read_bytes().replace(b'\n',b'\r\n'))
    assert validate_models(dai,models), 'Windows line endings must not invalidate JSON'
    config=models/'foreign/config.json';original=config.read_bytes()
    config.write_bytes(original.replace(b'wheel_rim',b'wrong_rim'))
    try:validate_models(dai,models);raise AssertionError('Changed JSON accepted')
    except ValueError as exc:assert 'integrity mismatch' in str(exc)
    config.write_bytes(original)
    blob=models/'foreign/foreign_object.blob';original=blob.read_bytes()
    blob.write_bytes(original[:-1]+bytes([original[-1]^1]))
    try:validate_models(dai,models);raise AssertionError('Changed binary accepted')
    except ValueError as exc:assert 'integrity mismatch' in str(exc)
print('PASS: LF and Windows CRLF metadata accepted; actual JSON changes and binary corruption rejected')

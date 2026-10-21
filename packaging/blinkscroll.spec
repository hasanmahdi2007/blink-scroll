# PyInstaller spec for the Blink Scroll desktop app (one-folder build, no console window).
#   .venv\Scripts\python.exe -m PyInstaller packaging\blinkscroll.spec --noconfirm
# Output: dist\BlinkScroll\BlinkScroll.exe (then packaging\installer.iss builds the setup).

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).parent  # noqa: F821 (SPECPATH is provided by PyInstaller)

datas = [(str(ROOT / "models" / "face_landmarker.task"), "models")]
datas += collect_data_files("mediapipe", excludes=["**/*.py", "**/__pycache__/**"])
binaries = collect_dynamic_libs("mediapipe")  # libmediapipe.dll, kept at its package path

a = Analysis(
    [str(ROOT / "blinkscroll_app.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "pynput.keyboard._win32", "pynput.mouse._win32", "pystray._win32",
        "mediapipe.tasks.python.vision.face_landmarker",
    ],
    excludes=["pytest", "IPython", "jupyter", "scipy", "pandas", "PyQt5",
              "PySide6", "tensorflow", "torch"],
    noarchive=False,
)
# Not needed: the camera is read through DirectShow/MSMF, and no AVIF images are used.
_UNUSED = ("opencv_videoio_ffmpeg", "_avif")
a.binaries = [b for b in a.binaries if not any(u in b[0] for u in _UNUSED)]

pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="BlinkScroll",
    console=os.environ.get("BS_CONSOLE") == "1",  # BS_CONSOLE=1: debug build
    icon=str(ROOT / "packaging" / "blinkscroll.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="BlinkScroll", upx=False)



"""One-time setup for the Haven project (Windows, macOS, Linux). Run from the project root:

    python setup_haven.py            # backend venv + deps, frontend build, logins
    python setup_haven.py --face     # also installs the face AI worker (InsightFace)

Creates backend/.env with fresh random test logins if it does not exist, copies the worker / commander logins into
ai/face/.env, and prints them once. Nothing here is downloaded from outside PyPI and npm.
"""
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
FACE = ROOT / "ai" / "face"
WIN = os.name == "nt"


def venv_python(d: Path) -> Path:
    return d / ".venv" / ("Scripts/python.exe" if WIN else "bin/python")


def run(cmd, cwd=None):
    print("  $", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], cwd=cwd, check=True, shell=WIN and str(cmd[0]) in ("npm",))


def env_file() -> dict:
    p = BACKEND / ".env"
    if not p.exists():
        lines = [
            "# Haven test logins for the local demo. Do not upload this file.",
            "COMMANDER_EMAIL=commander@haven.test", f"COMMANDER_PASSWORD={secrets.token_urlsafe(15)}",
            "WORKER_EMAIL=worker@haven.test", f"WORKER_PASSWORD={secrets.token_urlsafe(15)}",
            "OFFICER_EMAIL=officer@haven.test", f"OFFICER_PASSWORD={secrets.token_urlsafe(15)}",
            "# optional: require X-API-Key on POST /events and /snapshots", "HAVEN_INGEST_KEY=", "",
        ]
        p.write_text("\n".join(lines), encoding="utf-8")
        print("  created backend/.env with new test logins")
    kv = dict(l.split("=", 1) for l in p.read_text(encoding="utf-8").splitlines() if "=" in l and not l.startswith("#"))
    face_env = FACE / ".env"
    if FACE.exists():
        face_env.write_text("\n".join([
            "HAVEN_API=http://localhost:8000",
            f"WORKER_EMAIL={kv['WORKER_EMAIL']}", f"WORKER_PASSWORD={kv['WORKER_PASSWORD']}",
            f"COMMANDER_EMAIL={kv['COMMANDER_EMAIL']}", f"COMMANDER_PASSWORD={kv['COMMANDER_PASSWORD']}", "",
        ]), encoding="utf-8")
    return kv


def backend():
    print("\n[1/3] backend")
    if not venv_python(BACKEND).exists():
        run([sys.executable, "-m", "venv", ".venv"], cwd=BACKEND)
    run([venv_python(BACKEND), "-m", "pip", "install", "-q", "-r", "requirements.txt"], cwd=BACKEND)


def frontend():
    print("\n[2/3] frontend (Kavrix)")
    if not shutil.which("npm"):
        print("  npm not found: install Node 22+, then run this again. Skipping the build.")
        return
    run(["npm", "ci", "--no-audit", "--no-fund"], cwd=FRONTEND)
    run(["npm", "run", "build"], cwd=FRONTEND)


def face():
    """InsightFace 0.7.3 has no Windows wheel and its optional 3D-mesh C++ extension needs a compiler.
    Detection and recognition do not use it, so on Windows we build from the PyPI source without it."""
    print("\n[face] face AI worker")
    if not venv_python(FACE).exists():
        run([sys.executable, "-m", "venv", ".venv"], cwd=FACE)
    py = venv_python(FACE)
    run([py, "-m", "pip", "install", "-q", "setuptools", "wheel", "cython", "numpy", "onnxruntime",
         "opencv-python-headless", "httpx", "python-dotenv", "scikit-image", "scikit-learn", "scipy", "tqdm",
         "requests", "matplotlib", "prettytable", "easydict", "albumentations"], cwd=FACE)
    try:
        run([py, "-m", "pip", "install", "-q", "insightface==0.7.3"], cwd=FACE)
        return
    except subprocess.CalledProcessError:
        print("  prebuilt install failed (no C++ compiler); building from PyPI source without the 3D-mesh extension")
    import json, tarfile, tempfile, urllib.request, hashlib
    meta = json.load(urllib.request.urlopen("https://pypi.org/pypi/insightface/0.7.3/json"))
    sd = next(f for f in meta["urls"] if f["packagetype"] == "sdist")
    tmp = Path(tempfile.mkdtemp())
    tgz = tmp / "insightface.tar.gz"
    urllib.request.urlretrieve(sd["url"], tgz)
    assert hashlib.sha256(tgz.read_bytes()).hexdigest() == sd["digests"]["sha256"], "checksum mismatch"
    with tarfile.open(tgz) as t:
        t.extractall(tmp)
    src = tmp / "insightface-0.7.3"
    s = (src / "setup.py").read_text(encoding="utf8")
    s = s.replace("from Cython.Distutils import build_ext\n", "").replace("from Cython.Build import cythonize\n", "")
    s = s.replace("ext_modules=cythonize(extensions)", "ext_modules=[]").replace("    'cython',\n", "")
    (src / "setup.py").write_text(s, encoding="utf8")
    mesh = src / "insightface/thirdparty/face3d/mesh/__init__.py"
    body = mesh.read_text(encoding="utf8")
    mesh.write_text("try:\n" + "".join("    " + l + "\n" for l in body.splitlines()) + "except ImportError:\n    pass\n",
                    encoding="utf8")
    run([py, "-m", "pip", "install", "-q", "--no-build-isolation", str(src)])


if __name__ == "__main__":
    backend()
    frontend()
    if "--face" in sys.argv:
        face()
    print("\n[3/3] logins")
    kv = env_file()
    print(f"  commander  {kv['COMMANDER_EMAIL']}  /  {kv['COMMANDER_PASSWORD']}")
    print(f"  officer    {kv['OFFICER_EMAIL']}  /  {kv['OFFICER_PASSWORD']}")
    print(f"  worker     {kv['WORKER_EMAIL']}  (used by the AI workers, from ai/face/.env)")
    print("\nStart:  python start_haven.py    then open http://localhost:8000")

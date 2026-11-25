"""Start the whole Haven system: backend API + live stream + the Kavrix dashboard, on http://localhost:8000

    python start_haven.py

Then, in the dashboard, use "Simulated · sign in" (top right) with the commander login from backend/.env.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
py = BACKEND / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if not py.exists():
    sys.exit("Run  python setup_haven.py  first.")
if not (ROOT / "frontend" / "dist" / "index.html").exists():
    print("warning: frontend not built (npm run build in 'frontend'); the API still runs")
print("Haven running on http://localhost:8000  (Ctrl+C to stop)")
sys.exit(subprocess.call([str(py), "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8000"], cwd=BACKEND))

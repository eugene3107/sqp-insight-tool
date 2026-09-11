"""Streamlit Community Cloud entry point — makes the local package importable, then runs the dashboard."""
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

runpy.run_path(str(ROOT / "sqp_tool" / "dashboard.py"), run_name="__main__")

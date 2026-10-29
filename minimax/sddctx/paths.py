from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample_app"
LEDGER = SAMPLE / "ledger.py"
BEFORE = SAMPLE / "snapshots" / "before" / "ledger.py"
MANIFEST = SAMPLE / "manifest.json"
TASK = ROOT / "tasks" / "add-min-amount.md"
DATA = ROOT / "data"
SEEKDB = DATA / "seekdb"
STATE_DB = DATA / "task-state.sqlite"
RECORDS = ROOT / "records"
REQUIRED = (
    ("spec", "SPEC-AMT-001", "sample_app/specs/amount.md"),
    ("spec", "SPEC-CAT-001", "sample_app/specs/category.md"),
    ("spec", "SPEC-COMPAT-001", "sample_app/specs/compat.md"),
    ("code", "filter_expenses", "sample_app/ledger.py"),
    ("test", "test_min_amount", "sample_app/tests/test_min_amount.py"),
    ("test", "test_category", "sample_app/tests/test_category.py"),
)

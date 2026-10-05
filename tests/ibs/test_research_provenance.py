"""Static provenance checks for the Phase 12 research scripts and results doc (IBS-10).

Never imports or executes the scripts (they download from yfinance on import).
"""
import ast
import py_compile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "backtester" / "experimental" / "ibs_search"
SCRIPTS = ("strategy_search.py", "strategy_search_r2.py", "ibs_robust.py",
           "r5_robust.py", "ibs_sizing.py", "screen_study.py")



def test_research_scripts_present_and_compile(tmp_path):
    for name in SCRIPTS:
        path = SCRIPT_DIR / name
        assert path.is_file(), name
        py_compile.compile(str(path), cfile=str(tmp_path / (name + "c")), doraise=True)


def test_research_scripts_have_no_absolute_or_scratch_paths():
    for name in SCRIPTS:
        text = (SCRIPT_DIR / name).read_text()
        assert "/Users/" not in text, name
        assert ".scratch" not in text, name


def test_strategy_search_defines_simulate():
    tree = ast.parse((SCRIPT_DIR / "strategy_search.py").read_text())
    fn = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "simulate"]
    assert len(fn) == 1
    assert [a.arg for a in fn[0].args.args] == [
        "px", "entry", "exit_", "rank", "cost", "slots", "max_hold", "next_open"]

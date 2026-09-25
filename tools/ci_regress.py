"""Этап 7: CI-регресс сверки Rust/Python.

Гоняет cargo test и pytest, парсит счётчики и сравнивает с базлайном
(tools/ci_baseline.json). Любая регрессия (тестов меньше, появились
падения/ошибки) - ненулевой код выхода: годится для CI-джобы и для
ручного прогона после крупных правок.

Запуск:  python tools/ci_regress.py [--update]
Ключ --update перезаписывает базлайн текущими зелёными счётчиками.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tools" / "ci_baseline.json"
JUNIT = ROOT / "build" / "ci_regress_junit.xml"


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)


def cargo_counts() -> dict:
    proc = run(["cargo", "+stable-x86_64-pc-windows-gnu", "test"],
               ROOT / "rust" / "swagscan")
    text = proc.stdout + proc.stderr
    m = re.search(r"test result: (\w+)\. (\d+) passed; (\d+) failed", text)
    if not m:
        return {"passed": -1, "failed": -1}
    return {"passed": int(m.group(2)), "failed": int(m.group(3))}


def pytest_counts() -> dict:
    if JUNIT.exists():
        JUNIT.unlink()
    proc = run([sys.executable, "-m", "pytest", "tests", "-q",
                "-p", "no:cacheprovider", "--junitxml", str(JUNIT)], ROOT)
    if proc.returncode not in (0, 1) or not JUNIT.exists():
        return {"tests": -1, "failures": -1, "errors": -1, "skipped": -1}
    root = ET.parse(JUNIT).getroot()
    suites = root.iter("testsuite")
    tests = failures = errors = skipped = 0
    for suite in suites:
        tests += int(suite.get("tests", 0))
        failures += int(suite.get("failures", 0))
        errors += int(suite.get("errors", 0))
        skipped += int(suite.get("skipped", 0))
    return {"tests": tests, "failures": failures, "errors": errors,
            "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true",
                        help="перезаписать базлайн текущими счётчиками")
    args = parser.parse_args()

    cargo = cargo_counts()
    pytest_ = pytest_counts()
    current = {"cargo": cargo, "pytest": pytest_}
    print(json.dumps(current, indent=2))

    if args.update:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(current, indent=2), encoding="utf-8")
        print(f"базлайн обновлён: {BASELINE}")
        return 0

    if not BASELINE.exists():
        print("базлайна нет: прогоните --update после зелёного прогона")
        return 2

    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    problems: list[str] = []
    if cargo["failed"] > 0 or cargo["failed"] == -1:
        problems.append(f"cargo: падения/не запустился ({cargo})")
    elif cargo["passed"] < base["cargo"]["passed"]:
        problems.append(
            f"cargo: тестов меньше ({cargo['passed']} < {base['cargo']['passed']})")
    if pytest_["failures"] or pytest_["errors"] or pytest_["tests"] == -1:
        problems.append(f"pytest: падения/ошибки ({pytest_})")
    elif pytest_["tests"] < base["pytest"]["tests"]:
        problems.append(
            f"pytest: тестов меньше ({pytest_['tests']} < {base['pytest']['tests']})")

    if problems:
        for p in problems:
            print("РЕГРЕСС: " + p)
        return 1
    print("регрессий нет: счётчики не ниже базлайна")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

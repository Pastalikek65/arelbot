import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _pytest_ini_options() -> dict:
    return _pyproject()["tool"]["pytest"]["ini_options"]


def _pinned_versions(path: Path) -> dict[str, str]:
    """Parse a pip requirements file into ``package -> version`` pins.

    Comment lines and pip directives such as ``-c constraints.txt`` are
    skipped, so a commented-out plugin can never satisfy an assertion.
    """

    pins: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        package, separator, version = line.partition("==")
        if not separator:
            continue
        pins[package.strip().lower()] = version.strip()
    return pins


def test_pyproject_declares_pytest_timeout():
    assert _pytest_ini_options()["timeout"] == 300


def test_pyproject_declares_coverage_threshold():
    report = _pyproject()["tool"]["coverage"]["report"]
    assert report["fail_under"] == 58


def test_pyproject_enables_branch_coverage():
    run = _pyproject()["tool"]["coverage"]["run"]
    assert run["branch"] is True
    assert run["source"] == ["src"]


def test_pytest_pins_the_coverage_config_path():
    """Subprocesses must read the same coverage configuration as the parent.

    Without the pinned path a subprocess that starts in another working
    directory finds no configuration, records statement-only data and aborts
    the coverage run with "Can't combine branch coverage data with statement
    data".
    """

    assert _pytest_ini_options()["addopts"] == ["--cov-config=pyproject.toml"]


def test_requirements_pin_timeout_and_coverage_plugins():
    pins = _pinned_versions(REPO_ROOT / "requirements.txt")
    assert pins["pytest-timeout"] == "2.3.1"
    assert pins["pytest-cov"] == "5.0.0"


def test_constraints_pin_timeout_and_coverage_plugins():
    pins = _pinned_versions(REPO_ROOT / "constraints.txt")
    assert pins["pytest-timeout"] == "2.3.1"
    assert pins["pytest-cov"] == "5.0.0"
    assert pins["coverage"] == "7.16.2"

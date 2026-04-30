"""Test runner script with coverage reporting.

This module provides a Poetry console script for running tests.
Script is registered in pyproject.toml under [project.scripts]
and invoked via `poetry run test`.

References:
    - Poetry scripts: https://python-poetry.org/docs/pyproject/#scripts
    - pytest: https://docs.pytest.org/
    - coverage: https://coverage.readthedocs.io/
"""

import subprocess
import sys


def main() -> int:
    """Run pytest with coverage reports."""
    print("Running tests with coverage...\n")

    result = subprocess.run(
        [
            "pytest",
            "tests/",
            "--cov=src/featherweather",
            "--cov-report=html",
            "--cov-report=term",
            "-v",
        ]
    )

    if result.returncode == 0:
        print("\nAll tests passed!")
        print("Coverage report: reports/htmlcov/index.html")
    elif result.returncode == 5:
        print("\nNo tests collected.")
    else:
        print("\nSome tests failed")

    return result.returncode


if __name__ == "__main__":
    sys.exit(main())

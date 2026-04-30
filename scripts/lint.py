"""Lint script that runs isort, black, and flake8 on the src tree.

This module provides Poetry console scripts for code quality tooling.
Scripts are registered in pyproject.toml under [project.scripts]
and invoked via `poetry run lint` or `poetry run format`.

References:
    - Poetry scripts: https://python-poetry.org/docs/pyproject/#scripts
    - PEP 621 (project metadata): https://peps.python.org/pep-0621/#entry-points
"""

import subprocess
import sys


def main() -> int:
    """Run all linting tools on src and tests directories."""
    targets = ["src", "tests"]
    failed = False

    print("Running isort...")
    result = subprocess.run(["isort", "--check-only", "--diff", *targets])
    if result.returncode != 0:
        failed = True

    print("\nRunning black...")
    result = subprocess.run(["black", "--check", "--diff", *targets])
    if result.returncode != 0:
        failed = True

    print("\nRunning flake8...")
    result = subprocess.run(["flake8", *targets])
    if result.returncode != 0:
        failed = True

    if failed:
        print("\nLinting failed. Run 'poetry run format' to auto-fix issues.")
        return 1

    print("\nAll linting checks passed!")
    return 0


def format_code() -> int:
    """Auto-format code with isort and black."""
    targets = ["src", "tests"]

    print("Running isort...")
    subprocess.run(["isort", *targets])

    print("\nRunning black...")
    subprocess.run(["black", *targets])

    print("\nFormatting complete!")
    return 0


if __name__ == "__main__":
    sys.exit(main())

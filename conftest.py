"""Root conftest — prevents project code.py from shadowing stdlib 'code'.

pytest's built-in debugging plugin does ``import pdb``, and pdb does
``import code`` (the stdlib interactive console).  Because the project root is
on sys.path and contains code.py (the CircuitPython firmware entry point),
Python would find the wrong module — and *executing* code.py on the host pulls
in the real Blinka ``board`` module before tests/conftest.py has stubbed it,
which on a Raspberry Pi fails with "platform library 'lgpio' was not found".

We pre-load the real stdlib module here — at conftest import time, which runs
before the debugging plugin's ``pytest_configure`` hook fires.

The project root can appear on sys.path in more than one form: ``''`` (cwd)
and the absolute path written to ``.venv/.../featherweather.pth`` by Poetry
(because ``{include = "scripts"}`` in pyproject.toml makes the root a package
location).  All of them must be removed while importing the stdlib module.
"""

import os as _os
import sys as _sys

if "code" not in _sys.modules:
    _root = _os.path.dirname(_os.path.abspath(__file__))
    _saved = _sys.path[:]
    _sys.path = [p for p in _sys.path if _os.path.abspath(p or _os.curdir) != _root]
    try:
        import code as _stdlib_code  # noqa: PLC0415

        _sys.modules.setdefault("code", _stdlib_code)
    except ImportError:
        pass
    finally:
        _sys.path = _saved

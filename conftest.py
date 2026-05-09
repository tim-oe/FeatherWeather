"""Root conftest — prevents project code.py from shadowing stdlib 'code'.

pytest's built-in debugging plugin does ``import pdb``, and pdb does
``import code`` (the stdlib interactive console).  Because ``''`` (cwd) is
in sys.path and the project root contains code.py, Python finds the wrong
module.  We pre-load the real stdlib module here — at conftest import time,
which runs before the debugging plugin's ``pytest_configure`` hook fires.
"""

import sys as _sys

if "code" not in _sys.modules:
    # Remove '' from sys.path so the stdlib code.py wins over ./code.py.
    _saved = _sys.path[:]
    _sys.path = [p for p in _sys.path if p != ""]
    try:
        import code as _stdlib_code  # noqa: PLC0415

        _sys.modules.setdefault("code", _stdlib_code)
    except ImportError:
        pass
    finally:
        _sys.path = _saved

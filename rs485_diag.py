"""RS485-only diagnostic entry point — deploy as CIRCUITPY/code.py.

Does not initialise the OLED stack; only opens the shared RS485 UART and
runs ``Rs485Diagnostic`` from ``featherweather.hardware.rs485_diagnostic``.

Deploy::

    poetry run deploy --rs485-diag
    poetry run deploy --serial --rs485-diag

Restore normal operation by deploying the regular ``code.py`` afterwards.
"""

import time

from featherweather.hardware.rs485_diagnostic import Rs485Diagnostic

Rs485Diagnostic().run_all()

while True:
    time.sleep(3600)

"""Package-wide logging setup.

vorflow reports progress through the standard :mod:`logging` module under the
``"vorflow"`` logger. Messages keep the plain look of the old ``print()``
output, but can now be silenced, made more verbose, or redirected to a file
without touching library code.

Verbosity mapping (same scale MeshGenerator has always documented):

- ``0`` — silent: only warnings and errors are shown.
- ``1`` — basic progress messages (the package default).
- ``2`` — debug diagnostics (the ``[DIAG]`` output).
"""
from __future__ import annotations

import logging

_LEVELS = {0: logging.WARNING, 1: logging.INFO}

_handler = None


def set_verbosity(verbosity):
    """Set how talkative vorflow is on the console.

    Args:
        verbosity (int): 0 = warnings/errors only, 1 = progress messages
            (default), 2 or more = debug diagnostics.
    """
    global _handler
    logger = logging.getLogger("vorflow")
    if _handler is None:
        _handler = logging.StreamHandler()
        _handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(_handler)
        # Keep messages out of the root logger so applications that configure
        # their own logging don't see vorflow output twice. Attach handlers to
        # the "vorflow" logger to customize destination/format.
        logger.propagate = False
    logger.setLevel(_LEVELS.get(int(verbosity), logging.DEBUG))

"""IBKR Client Portal Web API integration via the ``ibind`` library.

This package replaces the prior ``ib_async``-based modules.  The Client Portal
Gateway runs as a separate process (managed by IBeam); this code talks to it
over HTTPS at ``localhost:5000``.
"""


class ShutdownRequested(RuntimeError):
    """Raised by long-running CPAPI calls when ``should_stop_fn`` returns True.

    Subclass of ``RuntimeError`` so any pre-existing ``except RuntimeError``
    handler still catches it, but callers that care about the distinction
    (logging, scheduler shutdown path) can match the specific type to
    differentiate "user requested SIGTERM" from "gateway died unexpectedly."
    """

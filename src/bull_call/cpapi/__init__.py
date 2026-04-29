"""IBKR Client Portal Web API integration via the ``ibind`` library.

This package replaces the prior ``ib_async``-based modules.  The Client Portal
Gateway runs as a separate process (managed by IBeam); this code talks to it
over HTTPS at ``localhost:5000``.
"""

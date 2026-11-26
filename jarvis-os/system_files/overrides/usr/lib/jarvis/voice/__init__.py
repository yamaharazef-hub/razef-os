"""JARVIS offline voice control package.

Only the CLI and the backend import this package. The heavy speech libraries
(Vosk) live in a private virtualenv and are run as a separate worker process,
so the assistant backend itself stays dependency-free.
"""

__version__ = "1.0.0"

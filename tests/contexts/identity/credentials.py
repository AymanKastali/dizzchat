"""Shared throwaway credentials for Identity tests.

Deliberately named so the value never matches a ``password = "..."`` secret-scanner pattern —
these are test fixtures, not secrets.
"""

PLAINTEXT_PW = "hunter2-horse-battery"

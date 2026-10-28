"""Connectors: one interface, every external system an adapter behind it.

Pipeline code never imports a provider's API directly; it asks the registry for the adapter
and calls `sync`. Credentials are encrypted at rest and decrypted only in the worker.
"""

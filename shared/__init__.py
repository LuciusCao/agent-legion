"""Neutral home for code shared by the Host (``server``) and the Agent Worker.

Modules here must stay dependency-free (stdlib only) so the worker Docker
image can ``COPY shared`` wholesale without pulling in server internals.
"""

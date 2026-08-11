"""Marks this directory as a package so its test modules have qualified
names.

Without it, pytest imports a test module under its bare basename, and two
directories holding a `test_boundary.py` collide at collection time — which is
exactly what happened when the product plane arrived with a boundary suite of
its own. `tests/fabops/` already had one; the others did not, and the gap was
latent rather than harmless.
"""

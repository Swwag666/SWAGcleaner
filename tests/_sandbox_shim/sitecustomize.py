"""Sandbox workaround: os.mkdir/mkdtemp with explicit 0700 mode corrupts
the security descriptor of created directories in the DSH file sandbox.

Patch os.mkdir to always use the default mode (inherited from parent).
"""
import os as _os

_orig_mkdir = _os.mkdir

def _patched_mkdir(path, mode=0o777, *, dir_fd=None):
    if dir_fd is not None:
        return _orig_mkdir(path, dir_fd=dir_fd)
    return _orig_mkdir(path)

_os.mkdir = _patched_mkdir

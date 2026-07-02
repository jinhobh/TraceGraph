import typing as t
from typing import TYPE_CHECKING as TC

if t.TYPE_CHECKING:
    import pkg.a

if TC:
    import pkg.c

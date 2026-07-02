from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pkg import b

try:
    import pkg.d
except ImportError:
    pkg_d = None


def use_c():
    import pkg.c

    return pkg.c

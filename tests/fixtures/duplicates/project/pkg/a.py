import pkg.b
import pkg.b
from pkg import b


def use():
    import pkg.b

    return pkg.b


__all__ = ["b", "use"]

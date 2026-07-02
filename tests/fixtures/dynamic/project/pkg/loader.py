import importlib


def load(suffix):
    static = importlib.import_module("pkg.plugin")
    computed = importlib.import_module("pkg." + suffix)
    return static, computed

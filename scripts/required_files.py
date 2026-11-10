"""List the files a submission needs: the pickle plus the local .py files it
imports, directly or through its own modules.

Usage: python scripts/required_files.py <repo-folder>
Prints JSON: {"pkl": "...", "sources": ["..."], "globals": [...]}

This mirrors submit.py's local_modules and arena/vet.py's pickle_globals.
"""

import ast
import io
import json
import os
import pickle
import sys

RESERVED = frozenset(
    {
        "card_utils", "engine", "game_interface", "game_state",
        "player_action", "arena", "evals", "submit", "tests",
    }
) | frozenset(sys.stdlib_module_names)


class _Thing:
    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return _Thing()

    def __setstate__(self, state):
        pass

    def __setitem__(self, key, value):
        pass

    def append(self, *args):
        pass

    def extend(self, *args):
        pass

    def add(self, *args):
        pass

    def update(self, *args, **kwargs):
        pass


class _Recorder(pickle.Unpickler):
    def find_class(self, module, name):
        self.seen.add(f"{module}.{name}")
        if module in ("builtins", "__builtin__") and name in (
            "set", "frozenset", "dict", "list", "tuple", "int", "float",
            "complex", "str", "bytes", "bytearray", "bool", "object",
            "range", "slice",
        ):
            return getattr(__import__("builtins"), name)
        return _Thing

    def persistent_load(self, pid):
        raise pickle.UnpicklingError("persistent ids are not allowed")


def pickle_globals(path):
    with open(path, "rb") as file:
        data = file.read()
    recorder = _Recorder(io.BytesIO(data))
    recorder.seen = set()
    recorder.load()
    return sorted(recorder.seen)


def local_modules(start, folder):
    found = {}
    queue = sorted(start)
    while queue:
        name = queue.pop()
        path = os.path.join(folder, name + ".py")
        if name in found or name in RESERVED:
            continue
        if not os.path.isfile(path):
            continue
        found[name] = path
        with open(path, "rb") as file:
            try:
                tree = ast.parse(file.read())
            except SyntaxError:
                continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                queue += [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                queue.append((node.module or "").split(".")[0])
    return [found[name] for name in sorted(found)]


def find_pkl(folder):
    candidates = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            if name.endswith(".pkl"):
                candidates.append(os.path.join(root, name))
    if not candidates:
        return None
    root_level = [c for c in candidates if os.path.dirname(c) == folder]
    for preferred in ("bot.pkl",):
        for c in root_level:
            if os.path.basename(c) == preferred:
                return c
    if root_level:
        return root_level[0]
    return candidates[0]


def main():
    folder = sys.argv[1]
    pkl = find_pkl(folder)
    if not pkl:
        print(json.dumps({"error": "no pickle"}))
        return
    try:
        globals_ = pickle_globals(pkl)
    except Exception as error:
        print(json.dumps({"error": f"pickle: {type(error).__name__}: {error}"}))
        return
    modules = {g.rpartition(".")[0].split(".")[0] for g in globals_}
    sources = local_modules(modules, folder)
    print(json.dumps({
        "pkl": os.path.relpath(pkl, folder),
        "sources": [os.path.relpath(p, folder) for p in sources],
        "globals": globals_,
    }))


if __name__ == "__main__":
    main()

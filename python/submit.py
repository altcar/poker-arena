"""
Make and check your submission.

    python -m submit --team myteam

From the repo root, with your Model in model.py (or --model module:Class):
this pickles your model to myteam.pkl (pickle.dump, nothing more), then
checks the files exactly as the organisers will: it lists what you must
upload, shows anything the organisers' code review will flag, and plays
your model from a clean copy of just those files against dummy bots.

Upload ALL the files it lists (the .pkl and the .py files) in the form. The
pickle only stores which class your model is; the code comes from the .py
files, and the organisers read it before anything runs.
"""

import argparse
import ast
import importlib
import os
import pickle
import re
import shutil
import sys
import tempfile

from arena import vet

TEAM_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def local_modules(start: set[str], folder: str) -> list[str]:
    """The .py files in folder that start names, and the ones they import,
    and so on: everything the organisers need to unpickle your model."""
    found: dict[str, str] = {}
    queue = sorted(start)
    while queue:
        name = queue.pop()
        path = os.path.join(folder, name + ".py")
        if name in found or name in vet.RESERVED:
            continue  # seen, or the organisers' own code
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m submit")
    parser.add_argument("--team", required=True,
                        help="your team name: a-z, 0-9, - and _")
    parser.add_argument("--model", default="model:Model",
                        help="your model as module:Class (default "
                             "model:Model)")
    parser.add_argument("--out", default=".", help="folder for the .pkl")
    args = parser.parse_args(argv)

    if not TEAM_NAME.match(args.team):
        print("A team name is 1-32 characters of a-z, 0-9, '-' and '_', "
              "starting with a letter or digit.")
        return 1
    sys.path.insert(0, os.getcwd())
    module_name, _, class_name = args.model.partition(":")
    try:
        model = getattr(importlib.import_module(module_name),
                        class_name or "Model")()
    except Exception as error:
        print(f"Could not build your model ({args.model}): "
              f"{type(error).__name__}: {error}")
        return 1
    pkl = os.path.join(args.out, f"{args.team}.pkl")
    try:
        with open(pkl, "wb") as file:
            pickle.dump(model, file, protocol=4)
    except Exception as error:
        print("Your model cannot be pickled "
              f"({type(error).__name__}: {error}). Keep plain data in its "
              "attributes: no lambdas, open files, sockets or threads, and "
              "load weights in __init__.")
        return 1
    print(f"Wrote {pkl}")

    with tempfile.TemporaryDirectory() as stage:
        try:
            globals_ = vet.pickle_globals(pkl)
        except Exception as error:
            print(f"Cannot read your pickle: {error}")
            return 1
        modules = {g.rpartition(".")[0].split(".")[0] for g in globals_}
        sources = local_modules(modules, os.getcwd())
        shutil.copyfile(pkl, os.path.join(stage, f"{args.team}.pkl"))
        staged = []
        for path in sources:
            target = os.path.join(stage, os.path.basename(path))
            shutil.copyfile(path, target)
            staged.append(target)
        report = vet.vet(os.path.join(stage, f"{args.team}.pkl"), staged)
        for line in report.lines():
            print("  " + line)
        if report.refused:
            print("\nThis would be refused. Fix the lines marked 'refused', "
                  "then run this again.")
            return 1
        if report.needs_review:
            print("\nThe lines above will be read by the organisers before "
                  "your bot plays. That is normal; remove what you do not "
                  "need to be waved through faster.")
        from arena.event import validate

        print("\nPlaying your model against dummy bots...")
        problem = validate(os.path.join(stage, f"{args.team}.pkl"))
        if problem:
            print(f"FAILED: {problem}")
            return 1
        print("OK: played legally against every dummy.")

    print("\nUpload these files in the form (all of them):")
    print(f"  {args.team}.pkl")
    for path in sources:
        print(f"  {os.path.basename(path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

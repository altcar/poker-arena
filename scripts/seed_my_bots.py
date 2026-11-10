"""Create all-teams/<bot>/ for the five bots in the fork: a pickle plus the
Python files it needs. The other teams are downloaded by
scripts/download_team_files.myself (see download_team_files.mjs).
"""

import os
import pickle
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "python"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from required_files import local_modules  # noqa: E402

from model_a import Model as A  # noqa: E402
from model import Model as B  # noqa: E402
from model_c import Model as C  # noqa: E402
from model_d import Model as D  # noqa: E402
from model_e import Model as E  # noqa: E402

BOTS = [
    ("conservative", "model_a.py", A),
    ("workhorse", "model.py", B),
    ("adaptive", "model_c.py", C),
    ("mixed", "model_d.py", D),
    ("aggressive", "model_e.py", E),
]


def main():
    base = os.path.join(ROOT, "all-teams")
    os.makedirs(base, exist_ok=True)
    for key, model_file, model in BOTS:
        folder = os.path.join(base, key)
        os.makedirs(folder, exist_ok=True)
        pkl = os.path.join(folder, f"{key}.pkl")
        with open(pkl, "wb") as file:
            pickle.dump(model(), file, protocol=4)
        # The pickle references the model module; copy it and its local imports.
        sources = local_modules({model_file[:-3]}, os.path.join(ROOT, "python"))
        for source in sources:
            shutil.copyfile(source, os.path.join(folder, os.path.basename(source)))
        print(f"{key}: {os.path.basename(pkl)} + {', '.join(os.path.basename(s) for s in sources)}")


if __name__ == "__main__":
    main()

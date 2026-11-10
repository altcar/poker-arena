# All teams

Each folder holds the files needed to run one bot: the `.pkl` plus the `.py`
files it imports (the same set `submit.py` lists for a submission).

- `conservative/`, `workhorse/`, `adaptive/`, `mixed/`, `aggressive/` — the
  five bots from this fork, created by `scripts/seed_my_bots.py`.
- one folder per competition team — downloaded by
  `scripts/download_team_files.mjs`, which finds each team's public
  repository, clones it, and copies only the required files.

`data/all-teams-report.json` records which teams were found and which were
not. The competition's submission form (which held the repository links) is
private; if you have a team's repository URL, add it to
`data/team_repos.json` and re-run the script.

At the time of writing the five fork bots and 16 competition teams are here;
the other competition teams could not be found by public search because the
submission form's repository links were never published.

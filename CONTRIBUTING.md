# Contributing to Freight Forecast

I welcome focused fixes, clearer examples and results that challenge an assumption in the project. If something looks wrong, I would rather have a small case I can run than a broad claim that it is broken.

## Start locally

Use Python 3.11 or later and run these commands from the repository root. A virtual environment keeps the development dependencies separate from your other projects.

```sh
python -m venv .venv
```

Activate it with `.venv\Scripts\Activate.ps1` in PowerShell, or `source .venv/bin/activate` on macOS or Linux. Then install what this project needs:

```sh
python -m pip install -r requirements-dev.txt
python -m src.train
```

## Check a change

```sh
python -m pytest -q
ruff check src tests
ruff format --check src tests
```

Start with [feature construction](src/features.py), [training](src/train.py) and [serving](src/serve.py). The [verification record](VERIFICATION.md) explains the local container evidence.

## Report a bug or propose a change

Check the existing issues first. Include the revision, Python version, operating system, command, expected behavior and actual output. For a numerical issue, include the smallest input that demonstrates it. Remove credentials and private data from logs before posting.

Keep a pull request focused on one problem. Explain what changes for someone using the project, why the approach fits and which checks you ran. Add a regression test when it captures a real failure. Documentation changes should be checked against the current code and examples.

## Evidence and scope

Keep the train and test periods separate. Compare predictions on the same months and retain the seasonal baseline. Label synthetic data clearly. For serving changes, include the request shape, machine conditions and raw timing samples. A faster response must return the same forecast.

Useful next work includes evaluating prediction intervals on held out months and testing a second dataset with a documented license. Those are research directions, not completed features or a promised release schedule.

## Writing

Use plain language and concrete examples. Avoid em dashes and unnecessary hyphens in authored prose. Preserve the exact spelling of code, commands, paths, package names, links and quoted evidence. Claims about performance should link to measurements and say what was actually tested.

Be respectful when discussing a change. Questions and disagreements are welcome; keep them about the work.

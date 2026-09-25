# Working on Freight Forecast

The question I care about is whether a change improves the forecast or the service, and whether someone else can reproduce that result. A smaller error on a different set of months does not answer the same question.

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
ruff check src tests tools
ruff format --check src tests tools
```

Start with [feature construction](src/features.py), [training](src/train.py) and [serving](src/serve.py). The [verification record](VERIFICATION.md) explains the local container evidence.

## Report a bug or propose a change

For a bug, include the command or API request, revision, expected result and actual output. For a model comparison, include the data source, split dates and monthly errors. Label generated data and keep operational or personal records out of public issues.

For a speed claim, retain raw samples and machine conditions, and check the response values. The [serving measurements](docs/serving-performance.md) show the distinction between a faster response and a different forecast. Include regressions as well as gains.

## Evidence and scope

Keep the train and test periods separate. Compare predictions on the same months and retain the seasonal baseline. Label synthetic data clearly. For serving changes, include the request shape, machine conditions and raw timing samples. A faster response must return the same forecast.

Useful next work includes evaluating prediction intervals on held out months and testing a second dataset with a documented license. Those are research directions, not completed features or a promised release schedule.

Run `python -m src.backtest --output reports/backtest.json` to evaluate monthly
refitting without changing the serving model. The [backtest notes](docs/backtesting.md)
explain the temporal split, raw prediction records, and interpretation limits.
When changing evaluation logic, keep the tests that perturb future observations
and independently reconcile the reported errors.

## Development container and public site

Open this repository in Codespaces or use VS Code Dev Containers. The container uses Python 3.11 and installs the project into `.venv` during setup. Its image is pinned by digest. The `Project access` workflow builds that same environment and runs `.devcontainer/smoke.sh`. Runtime dependencies still follow the project configuration. Codespaces uses the creating account's compute and storage allowance.

Run `python tools/build_site.py` to assemble the public page in `_site`, then `python -m http.server 8080 --directory _site` to preview it. The builder copies only the listed example files and generates the BTS reading report from retained predictions. The page reads saved evidence; it does not silently rerun the experiment or claim current results. Pages deploys from `main` after the site and development environment checks pass.

The container prepares dependencies but does not train automatically. Run `python -m src.train` before starting `uvicorn src.serve:app --host 0.0.0.0 --port 8000`. Port 8000 is configured for forwarding; keep the Codespaces port private unless you deliberately need to share it.

For presentation changes, follow [the report checks](docs/presentation.md). Preserve
the original synthetic chart and experiment records. The report renderer generates
new HTML/SVG renditions without retraining. Keep both appearances semantically
equivalent and record local Inter lookup separately from system fallback.

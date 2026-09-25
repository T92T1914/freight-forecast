# Reading the saved results

The public page and the BTS report use Clair, Obscur and Auto. Auto follows the
system preference. An explicit choice stays in this browser until Auto is selected
again. The versioned storage key is `freight-forecast.appearance.v1`. Blocked
storage still allows switching for the current page. With JavaScript disabled,
Auto and the report's chart, tables and downloads remain available.

The shipment examples still use synthetic data and units of moves. The separate
[BTS evaluation](real-data-evaluation.md) uses seasonally adjusted national index
points. Its monthly-refit Ridge model loses to last observation. Changing the
appearance does not change the observations, methods, results or that conclusion.

## Generate the report

From the source checkout, with Python 3.11 or later:

```sh
python tools/render_real_report.py --output reports/bts-reading
```

Open `reports/bts-reading/real-data.html`, or run `python tools/build_site.py` to
include the report in `_site`. The renderer uses the standard library and reads
`docs/real-data-example.json`. It does not import the estimator, train, predict,
fetch data or replace a serving artifact. It is deliberately scoped to the saved
2020 to 2025 result, rather than accepting arbitrary series with the same narrative.

The bundle contains one HTML report, explicit Clair and Obscur SVGs, the unchanged
saved JSON, and the presentation assets. SVGs preserve text. Their metadata and
`presentation.json` distinguish the original evaluated revision from the current
presentation revision. Text hashes use UTF-8 with LF line endings so checkout
conversion does not look like a new experiment. A dirty build says so explicitly.
Keep the bundle together when sharing it. The report's project link is online,
while its data, styles and chart do not need a connection.

Both SVGs have the same points, scale domain, units, line styles and marker shapes.
Series colors are keyed by method names rather than their position in a list.
The table retains every month's value, including poor forecasts. Missing or
nonfinite values fail the build instead of being painted as zero. No uncertainty
intervals were recorded in this experiment, so none are drawn.

## Appearance and fonts

`presentation/tokens.json` copies the required roles from
[Clair and Obscur at 7a57fe7](https://github.com/T92T1914/clair-obscur-themes/blob/7a57fe750ff50205a17e1d342106a0d3f2777159/tokens.json).
It records the source revision, file hash and MIT license. The role-content check
guards that pin. Additional categorical colors belong to Freight's named methods,
with distinct markers and line styles. There is no runtime fetch of a mutable
theme definition. Original evidence images keep their recorded bytes and colors.

The page and new charts request installed Inter Regular 400, SemiBold 600 and Bold
700, with genuine italic faces. They use local font lookup only. A reader without
Inter receives the system fallback. This is not universal Inter delivery, and the
SVGs do not embed fonts or guarantee identical text metrics on another machine.
Code retains its monospace stack. Emoji and unsupported language glyphs use their
normal font fallback. Print uses Clair without changing the saved screen choice.

## Check a presentation change

```sh
python -m unittest tests.test_presentation -v
node --test tests/appearance.test.mjs tests/selection-state.test.mjs
python tools/build_site.py
npm ci --ignore-scripts
npx playwright install chromium
npm run test:browser
```

The browser harness binds an owned server to loopback and launches sandboxed,
headless Chromium with fresh contexts. It has no visible fallback or access to a
saved profile. Page requests outside that loopback origin fail the test. It checks
appearance changes, selection and history, reload, blocked storage, no JavaScript,
both renditions, phone width, enlarged text, keyboard focus and print preference.

On a machine with the six Inter faces installed, set `FREIGHT_REQUIRE_INTER=1`
before the browser test. `FREIGHT_BROWSER_CHANNEL=chrome` uses ordinary Chrome's
executable in an isolated headless process. The strict check requires each intended
PostScript face to supply actual glyphs in a labeled diagnostic sample. It also
checks the report's real heading, paragraph and appearance label. Without the
strict flag, that installed-font test is explicitly skipped. A deliberately
missing-font control is a separate check, not evidence that Inter rendered.

These checks concern the website and generated report. They do not certify native
Chrome themes, Equibop, monitor calibration, Windows restart persistence or human
reading comfort. The first phone-width check caught an overflowing retrieval
timestamp at doubled body text size. Allowing that timestamp to wrap fixed the
case while retaining its exact value.

The existing `Project access` workflow builds and tests this same output before
Pages deploys from main. It uses the runner's installed Chrome through Playwright's
supported channel, with the browser sandbox enabled. The model CI and tag
publication path remain separate.

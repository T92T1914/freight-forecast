const byId = id => document.getElementById(id);
const format = value => value.toLocaleString('en-US', {
  minimumFractionDigits: 1, maximumFractionDigits: 1
});

function metric(value, label) {
  const box = document.createElement('div');
  box.className = 'metric';
  const number = document.createElement('strong');
  number.textContent = value;
  const caption = document.createElement('span');
  caption.textContent = label;
  box.append(number, caption);
  return box;
}

try {
  const response = await fetch('backtest.json');
  if (!response.ok) throw new Error('Backtest report unavailable');
  const report = await response.json();
  const slices = [
    ['All 24 months', report.overall, () => true],
    ...Object.entries(report.by_year).map(([year, summary]) => [
      year, summary, row => row.month.startsWith(year)
    ]),
    ['Peak: May to August', report.by_season.peak, row => row.season === 'peak'],
    ['Other months', report.by_season.off_peak, row => row.season === 'off_peak']
  ];
  slices[0][0] = `All ${report.overall.months} months`;
  const select = byId('backtest-slice');
  select.replaceChildren(...slices.map(([label], index) => {
    const option = document.createElement('option');
    option.value = String(index);
    option.textContent = label;
    return option;
  }));
  function update() {
    const [, summary, matches] = slices[Number(select.value)];
    byId('backtest-metrics').replaceChildren(
      metric(format(summary.model_mae), 'monthly refit MAE, moves'),
      metric(format(summary.naive_mae), 'seasonal baseline MAE, moves'),
      metric(`${summary.model_better_months} / ${summary.months}`, 'months the model wins')
    );
    const delta = summary.mae_delta_model_minus_naive;
    byId('backtest-finding').textContent = delta < 0
      ? `The refitted model averages ${format(-delta)} fewer moves of error in this slice.`
      : delta > 0
        ? `The seasonal baseline averages ${format(delta)} fewer moves of error in this slice.`
        : 'Both approaches have the same average absolute error in this slice.';
    byId('backtest-rows').replaceChildren(...report.predictions.filter(matches).map(row => {
      const tr = document.createElement('tr');
      const values = [row.month, row.trained_through, format(row.actual),
        format(row.model_absolute_error), format(row.naive_absolute_error),
        row.model_absolute_error < row.naive_absolute_error ? 'Model'
          : row.model_absolute_error > row.naive_absolute_error ? 'Baseline' : 'Tie'];
      values.forEach((value, index) => {
        const cell = document.createElement(index === 0 ? 'th' : 'td');
        if (index === 0) cell.scope = 'row';
        cell.textContent = value;
        tr.append(cell);
      });
      return tr;
    }));
  }
  update();
  select.addEventListener('change', update);
  byId('backtest-content').hidden = false;
} catch (error) {
  byId('backtest-error').hidden = false;
}

# A walkthrough for the person who would use the forecast

This page is for a freight planner who wants to know what the service gives back
and how far to trust it. The first part is one page for a reader who is not an
engineer. Every result here is quoted from a report in this repository, linked
beside it.

## The one page version

**What it is.** A small program that estimates monthly freight volume one step past
the data it holds. It is a project built to be inspected. It is not running anyone's
operation.

**What data it uses.** The service you can run uses made up monthly shipment
counts with a summer peak that I designed. They are not service records or anyone's
shipments. Separate studies use a real public number, the Freight Transportation
Services Index from the Bureau of Transportation Statistics. It scores domestic for
hire freight activity, with the 2000 average equal to 100. The usual seasonal swings
have been removed from it, which statisticians call seasonally adjusted. It is not a
count of your loads.

**What I found.** On the made up data, a forecasting model fitted once beats a simple
rule, "expect the same as this month last year" ([README](../README.md#results-beat-same-month-last-year)).
On the real index, with the model refitted every month, it also does better than that
rule on average over six years, though not in every year. It does worse than an even
simpler rule, "expect the same as the latest month". That second result is the one a
planner should remember ([real data study](real-data-evaluation.md),
[replay on the values published at the time](real-time-evaluation.md)). The program
you can run was not itself tested on the real index.

**How firm that is.** I tested these gaps afterwards
([comparison tests](forecast-comparison.md)). Against the index as later revised,
both of my paired tests tell the lead of the latest month over the model apart from
chance variation. Against the figure first published for each month they do not
both do so, although a second method does set the model aside. On this index the
two tests never both support the lead of the model over the same month last year,
whichever figure is used.

**What to do with that.** For the next value of the national index, start from the
latest published one. Do not carry that over to your own freight, which may well have
a season in it, until the same comparison has been run on your own history.

**What it cannot tell you.** How many trucks, people or square feet you need, what
a miss would cost, or what happens in a month unlike the ones it was tested on.

## What the service returns

You ask for one month and get the model's estimate with last year's figure beside
it. This is the example from the [README](../README.md):

```json
{"month":"2025-01","predicted_volume":3297,"naive_same_month_last_year":3477,"trained_through":"2022-12-01"}
```

| Field | How to read it |
|---|---|
| `month` | The month the estimate is for |
| `predicted_volume` | The model's single estimate, in synthetic moves for that month |
| `naive_same_month_last_year` | What the same month a year earlier recorded, shown for comparison |
| `trained_through` | The last month the model learned from. Newer months can feed its inputs without it learning from them again |
| `model_id`, `history_id` | Fingerprints of the model and the data in use, left out of the example above. Keep them with any number you record, so you can say what produced it |

Ask `GET /forecast-window` first. It lists the months the service will answer for,
the last month it has observed and the next month it has not seen. It looks one
month past its data and no further, whatever today's date is. A month outside that range is refused with
the range in the message. A badly written month is refused too.

Earlier months in the range can be asked for as well. Some fall in the months the
model learned from, and an answer for one of those is no test of it. The months it
was tested on are 2023 and 2024, and one answer from them says little about the next
estimate. Replacing the data files changes
nothing until the service is restarted.

## How to read a forecast

On the synthetic data the model's average miss over 24 test months was 226 moves a
month, against 327 for the same month last year
([README results](../README.md#results-beat-same-month-last-year)). An average
hides where the misses fall. In the May to August peak it was 418, and in the
other months 130 ([limits](../README.md#what-this-does-not-do)). As a share of
volume the two are close, 3.83% and 3.11%. Capacity is booked in moves, so the miss
is largest in the months that matter most to the plan.

A [monthly refit test](backtesting.md) on the same series shows that the ranking
can turn over. The refit model beat the same month last year over both years
together and lost to it in 2023. Refitting also did worse overall than fitting once,
245 against 226.

## How to read a range

**The service returns no range.** It gives one number, and nothing in the response
says how wide the doubt around it is.

Ranges exist only in a [separate experiment](interval-methods.md) on the real
index. It put ranges aimed at 90 percent around forecasts already saved for 2020
to 2025, by looking at how large earlier misses had been. Those forecasts come from
the one later snapshot of the data, not the values published at the time.

* Around the model's forecast, a fixed set of past misses covered 59 of 72 months.
  A rough range for that share is 71.5 to 89.1 percent, so the 90 percent target
  sits just above it.
* Around the same forecast, ranges that kept updating covered 66 of 72, and were
  wider.
* Ranges around "the latest month" scored better than ranges around the model on a
  measure that weighs width against misses, under all three ways of building them.

Read a range as "this is how often ranges built this way held the outcome in that
period", not as a promise about next month. A range can be made to cover more by
making it wider, so look at both. The months depend on one another, and the report
claims no guarantee for its ranges.

## Why the latest value is the better input today

This section is about the national index only. Lower average misses are better, in
index points, for the 72 months from 2020 to 2025.

| Study | Model, refit monthly | Same as latest month | Same month last year |
|---|---:|---:|---:|
| [One later snapshot of the data](real-data-evaluation.md) | 1.778 | 1.260 | 2.783 |
| [Values as published at the time, scored on first releases](real-time-evaluation.md) | 1.878 | 1.551 | 3.138 |
| [Values as published at the time, scored on latest values](real-time-evaluation.md) | 2.494 | 1.850 | 2.828 |

The rule "same as the latest month" had the lowest average miss all three ways, and
it needs no model to run or maintain. Keep these points beside it.

* Every comparison here is one step, from the latest published month to the next one.
* It did not win every year. Scored on first releases, the model had a lower average
  miss than the latest month in 2023 and 2024. In 2024 the same month last year beat
  both, at 0.925 against 1.552 for the model and 1.617 for the latest month. It also
  beat the model in 2025, at 1.108 against 1.453. Scored on latest values, the latest
  month beat the model in every year.
* It did not win on every kind of freight. In a
  [test on seven related series](real-time-components.md), the model was ahead of
  the latest month on one series of seven against first releases, natural gas
  consumption.
* The index gets revised after it is published. Between first release and the version
  of November 1, 2026, the mean size of the change was 1.49 index points and the
  largest was 4.60. The report calls that the same size as the errors being compared.
* One step ahead is not a month of notice. The month being estimated has already
  started when the figures for the month before it appear.
* The replay dates each figure by when an archive took it in, which trails the real
  release by days or weeks. The report says this makes the replay slightly kinder
  than the real wait.
* These are averages over one stretch of 72 months. An average gap alone does not
  show that it would appear again in other months.

### How far the ranking holds

I tested the gaps in the two replay rows afterwards. The
[protocol](forecast-comparison-protocol.json) was merged before any test statistic
was computed, and written after I knew the averages. The
[comparison report](forecast-comparison.md) calls a gap told apart from noise when
two tests both give a p value below 0.05, unsettled when only one does and not told
apart when neither does. The row from one later snapshot was not tested this way.

| Freight index comparison | Scored on | Miss as it is | Miss squared |
|---|---|---|---|
| Model against the latest month | First releases | Unsettled | Not told apart |
| Model against the latest month | Latest values | Told apart | Told apart |
| Model against last year | First releases | Unsettled | Unsettled |
| Model against last year | Latest values | Not told apart | Not told apart |
| Latest month against last year | First releases | Told apart | Unsettled |
| Latest month against last year | Latest values | Unsettled | Unsettled |

Here "last year" is the same month last year, and squaring counts large misses more
heavily. Wherever the table says told apart, the latest month has the lower miss.

* A second method in the report, a model confidence set, keeps only the latest
  month on first releases both ways, with the p value of the model just under 0.05.
  The report lets neither method overrule the other.
* The index and the seven related series give 32 readings of the model against the
  latest month. 5 are told apart, 7 are unsettled and 20 are not told apart. The
  seven related series are read after an adjustment for testing seven of them.
* Of the 5, the latest month has the lower miss in 3. The model has it in 2, both
  on natural gas consumption against latest values.
* Of the 32 readings of the model against the same month last year, 3 are told
  apart, 21 are unsettled and 8 are not told apart. All 3 are on rail intermodal,
  with the lower miss for the model.
* The readings are not independent pieces of evidence. Nothing adjusts across the
  three comparisons, the two figures scored on or the two ways of counting a miss.
* A gap that cannot be told apart is not evidence that two methods are equal.
* 72 months is a short sample for these tests. The report
  [lists their limits](forecast-comparison.md#limits).

## What the ledger proves, and what it does not

The [ledger](prospective-ledger.md) is where I write a forecast down before the
answer exists. Its first public record targets December 2026. I issued it on
November 16, 2026 at 17:39 UTC. It says 135.8 index points, which is the latest
value it had, from September. The same month last year was 137.0.

This record reaches three steps ahead, from September to December. The comparisons
above are all one step, so they do not show how well the latest value does at this
distance.

What it shows:

* The number, the data behind it and the rule that produced it were written down
  and committed to this repository before December began.

I will keep a bad result beside the forecast. That is my commitment, and nothing in
a local database could force it.

What it does not show:

* Accuracy. The December value is not due until the February 2027 release, so the
  record is unscored. When it is scored it will be one forecast.
* An independent timestamp. A commit date and a database file are my own record.
  They are evidence of the order things happened in, and no outside party has
  certified them.
* A complete history. I lost two earlier local records from October 14, 2026
  before either could be scored. I did not retype them, because a retyped record
  would prove nothing.

## Before using anything like this on your own freight

1. Write down the decision the number feeds and what a high or low miss costs.
2. Run the latest month and the same month last year on your own history. A model
   has to beat both to earn its upkeep.
3. Score it using only what you would have known on each date, including late and
   revised figures.
4. Look at the peak months on their own.
5. Agree who watches the misses and what they do. This service has monitoring that
   shows requests, response times and returned values. Nothing in it alerts anyone or
   retrains the model ([limits](../README.md#what-this-does-not-do)).

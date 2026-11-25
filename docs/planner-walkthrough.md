# A walkthrough for the person who would use the forecast

This page is for a freight planner who wants to know what the service gives back
and how far to trust it. The first part is one page for a reader who is not an
engineer. Every result here is quoted from a report in this repository, linked
beside it.

## The one page version

**What it is.** A small program that estimates how much freight movement to expect
one month ahead. It is a project built to be inspected. It is not running anyone's
operation.

**What data it uses.** The service you can run uses made up monthly shipment
counts with a summer peak that I designed. They are not service records or anyone's
shipments. A separate study uses a real public number, the Freight Transportation
Services Index from the Bureau of Transportation Statistics. The index describes
national freight activity as a score where the 2000 average equals 100. It is not
a count of your loads.

**What I found.** On the made up data, the forecasting model beats a simple rule,
"expect the same as this month last year". On the real index it also does better
than that rule on average, but it does worse than an even simpler one, "expect the
same as the latest month". That second result is the one a planner should
remember ([real data study](real-data-evaluation.md),
[replay on the values published at the time](real-time-evaluation.md)).

**What to do with that.** For the national index, start from the latest published
value. For your own freight, none of these numbers apply until the same comparison
has been run on your own history.

**What it cannot tell you.** How many trucks, people or square feet you need, what
a miss would cost, or what happens in a month unlike the ones it was tested on.

## What the service returns

You ask for one month and get one number. This is the example from the
[README](../README.md):

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
the last month it has observed and the one future month it can estimate. It looks
one month past its data and no further. A month outside that range is refused with
the range in the message. A badly written month is refused too.

Earlier months in the range can be asked for as well. Those answers are lookups
about the past, and some fall in the months the model learned from, so they say
nothing about how good the next estimate will be. Replacing the data files changes
nothing until the service is restarted.

## How to read a forecast

On the synthetic data the model's average miss over 24 test months was 226 moves a
month, against 327 for the same month last year
([README results](../README.md#results-beat-same-month-last-year)). An average
hides where the misses fall. In the May to August peak it was 418, and in the
other months 130 ([limits](../README.md#what-this-does-not-do)). Capacity is
booked in moves, so the estimate is least precise in the months that matter most
to the plan.

A [monthly refit test](backtesting.md) on the same series shows that the ranking
can turn over. The refit model beat the same month last year over both years
together, and lost to it in 2023.

## How to read a range

**The service returns no range.** It gives one number, and nothing in the response
says how wide the doubt around it is.

Ranges exist only in a [separate experiment](interval-methods.md) on the real
index. It put ranges aimed at 90 percent around forecasts already saved for 2020
to 2025, by looking at how large earlier misses had been.

* A fixed set of past misses around the model's forecast covered 59 of 72 months.
* Ranges that kept updating covered 66 of 72, and were wider.
* The rule "same as the latest month" scored better than the model on a measure
  that weighs width against misses, under all three kinds of range.

Read a range as "this is how often ranges built this way held the outcome in that
period", not as a promise about next month. A range can be made to cover more by
making it wider, so look at both. The months depend on one another, and the report
says its ranges carry no guarantee.

## Why the latest value is the better input today

This section is about the national index only. Lower average misses are better, in
index points, for the 72 months from 2020 to 2025.

| Study | Model, refit monthly | Same as latest month | Same month last year |
|---|---:|---:|---:|
| [One later snapshot of the data](real-data-evaluation.md) | 1.778 | 1.260 | 2.783 |
| [Values as published at the time, scored on first releases](real-time-evaluation.md) | 1.878 | 1.551 | 3.138 |
| [Values as published at the time, scored on latest values](real-time-evaluation.md) | 2.494 | 1.850 | 2.828 |

The simpler rule had the lower average miss all three ways, and it needs no model
to run or maintain. Keep these points beside it.

* It did not win every year. Scored on first releases, the model had a slightly
  lower average miss than the latest value in 2023 and in 2024.
* It did not win on every kind of freight. In a
  [test on seven related series](real-time-components.md), the model was ahead of
  the latest value on one series of seven against first releases, natural gas
  consumption.
* The index gets revised after it is published. The average change from first
  release to the latest value was 1.49 index points and the largest was 4.60,
  the same size as the misses being compared.
* The month being estimated has already started by the time the earlier value is
  available to you, and the replay is slightly kinder than the real wait.
* These are averages over one stretch of 72 months. An average gap alone does not
  show that it would appear again in other months.

## What the ledger proves, and what it does not

The [ledger](prospective-ledger.md) is where I write a forecast down before the
answer exists. Its first public record targets December 2026. I issued it on
November 16, 2026 at 17:39 UTC. It says 135.8 index points, which is the latest
value it had, from September. The same month last year was 137.0.

What it shows:

* The number, the data behind it and the rule that produced it were written down
  and committed to this repository before December began.
* A bad result will stay there beside the forecast.

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
   shows requests, delays and returned values. Nothing in it alerts anyone or
   retrains the model ([limits](../README.md#what-this-does-not-do)).

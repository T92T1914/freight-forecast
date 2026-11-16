# The same real time test on seven component series

The [real time report](real-time-evaluation.md) uses one series, the Freight
Transportation Services Index. One series is a point estimate. This report runs
the same [protocol](real-time-protocol.json) on seven monthly series that BTS
publishes alongside the index. Each gets the same three forecasters, the same 72
origins from January 2020 to December 2025 and the same two outcomes. Nothing was
tuned, and the estimator is unchanged. The saved numbers are in
[real-time-components.json](real-time-components.json) and the
[code](../src/real_time_panel.py) is in the repository.

The series come from ALFRED, with all vintages fetched by the same
[script](../tools/fetch_alfred_vintages.py) with no account and no key. The seven
are truck tonnage, rail carloads, rail intermodal traffic, inland waterway
tonnage, air freight ton miles, pipeline petroleum movement and natural gas
consumption. Their units differ, so the table uses a scaled error. It is the
model's mean absolute error divided by a naive baseline's on the same origins. A
value above 1 means the model lost to that baseline.

## Result

Scored against the first release:

| Series | Model vs last observation | Model vs same month last year |
|---|---:|---:|
| Truck tonnage | 1.106 | 0.706 |
| Rail carloads | 1.128 | 0.451 |
| Rail intermodal | 1.403 | 0.483 |
| Inland waterway tonnage | 1.070 | 0.590 |
| Air freight ton miles | 1.297 | 0.616 |
| Pipeline petroleum | 1.494 | 0.507 |
| Natural gas consumption | 0.870 | 0.808 |
| Mean across series | 1.196 | 0.594 |
| Median across series | 1.128 | 0.590 |

The model beats last observation on one series of seven and the same month last
year on all seven. Against the latest values the model beats last observation on
two series, waterway tonnage at 0.970 and natural gas at 0.773, and beats the
same month last year on six of seven. Waterway tonnage is the exception at 1.085.
The mean and median of the scaled error against last observation are 1.183 and
1.219. The saved report holds both outcomes for every series.

This agrees with the single series result. Last observation is the baseline to
beat on these monthly series, and the fitted model beats the same month last year
on most of them. It does not show that the model is better, and a loss to last
observation on six of seven series is a result worth keeping.

## Which values are committed

Every series page on FRED is tagged public domain with a citation requested and
names the Bureau of Transportation Statistics as the source. Truck tonnage and
rail traffic are collected by the American Trucking Associations and the
Association of American Railroads, which are private bodies, so the page does not
settle the rights for their values. I committed the values for air freight,
waterway tonnage, pipeline petroleum and natural gas, and for those four a test
recomputes the saved result. For truck tonnage and the two rail series only the
manifest with its source, retrieval clock and hash is committed. Fetch the values
again with the script, check the hash and run the panel to reproduce them.

## Limits

1. These series are seasonally adjusted national totals, not shipment counts.
2. ALFRED vintage dates trail the BTS release, and the vintages begin in March 2015.
3. Seven series from one agency are not independent. Their errors move together.
4. 72 dependent months per series, no significance tests, and no winner chosen
   after the outcomes.
5. The model does not change, so nothing here is a claim about a better model.

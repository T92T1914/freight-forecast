# Which forecast differences can be told apart from noise

The [real time report](real-time-evaluation.md) and the
[seven series report](real-time-components.md) rank three forecasters by their
average errors. This report asks whether those gaps are larger than 72 months of
noise could produce. For the Ridge model against last observation, most of them
cannot be told apart.

The [protocol](forecast-comparison-protocol.json) was merged before any statistic
here was computed. It was written after I knew the average errors, and it departs
from the earlier real time protocol, which declared no significance tests. The
[code](../src/forecast_comparison.py), the
[saved statistics](forecast-comparison.json) and the
[table writer](../tools/render_forecast_comparison.py) are in the repository. No
forecast, model, gate or published number changes.

## What was computed

Eight series, 72 target months from January 2020 to December 2025, two outcomes
and two losses. Each pair of forecasters gets a loss difference for every month,
the first method's loss minus the second's. A negative mean says the first method
had the lower loss. The differences are in the units of each series, squared for
squared loss, so they do not compare across series.

* The primary test is Diebold Mariano at one observation step with the Harvey,
  Leybourne and Newbold correction.
* The check is a fixed smoothing test on the first 4 periodogram ordinates, read
  against a t distribution with 8 degrees of freedom. It allows for loss
  differences that depend on earlier months, which the primary test does not.
* For the seven component series both tests carry a Holm adjustment across the
  seven. The freight index stands alone and is not adjusted.
* A pair is **told apart** when both tests have a p value below 0.05,
  **unsettled** when only one does, and **not told apart** when neither does.
  Component series are read on their adjusted p values.
* A model confidence set at size 0.05 keeps the methods that cannot be rejected
  as the best of the three. Block length 4 gives the stated set, and 12 is a
  declared sensitivity.

I did not run the Clark and West test. It compares nested models, and neither
simple baseline is a restricted form of the Ridge fit, which has no feature for
the value one month back.

The Ridge model is refit on an expanding window. The Giacomini and White theory
for comparing forecasting methods assumes a window of limited length, so these
tests describe the average loss difference of the saved forecasts and assume that
difference is covariance stationary.

## What came out

There are 32 readings for each pair, from eight series, two outcomes and two
losses. They are not 32 independent pieces of evidence.

**Ridge against last observation.** 5 are told apart, 7 are unsettled and 20 are
not told apart.

* Last observation has the lower loss, told apart, on the freight index against
  the latest values under both losses, and on rail intermodal against first
  releases under absolute loss.
* The Ridge model has the lower loss, told apart, on natural gas against the
  latest values under both losses.
* On the freight index against first releases, the comparison the real time
  report leads with, absolute loss is unsettled, with only the primary test below
  0.05. Squared loss is not told apart, with neither test below 0.05.
* Truck tonnage, rail carloads and inland waterway tonnage are not told apart
  under any outcome or loss.

**Ridge against the same month last year.** 3 are told apart, 21 are unsettled and
8 are not told apart. All 3 are rail intermodal, with the lower loss for the
Ridge model. The 8 are inland waterway and natural gas readings and the freight
index against the latest values. The Ridge model has the lower mean loss in 30 of
the 32 readings and the higher one on inland waterway against the latest values,
under both losses. The build gate compares against this baseline on the synthetic
data. On these real series both tests support a lower loss for the model only in
those 3 readings.

**Last observation against the same month last year.** 7 are told apart, 19 are
unsettled and 6 are not told apart. All 7 have the lower loss for last
observation, on the freight index, rail intermodal and pipeline petroleum. The 6
are inland waterway against the latest values and natural gas.

In 46 of the 47 unsettled readings across the three pairs, the primary test is the
one below 0.05. In 31 of those 46 the fixed smoothing check is at or above 0.05
even before any Holm adjustment, and in the other 15 the adjustment decides it. In
all 46 the fixed statistic is the smaller of the two in size. I have not measured
why. An unsettled gap is not a finding.

**Confidence sets.** At block length 4, last observation stays in the set in 29 of
32 cases. The 3 exceptions are natural gas. The Ridge model is left out in 11,
among them all four freight index cases and all four pipeline petroleum cases.
A set can leave a method out where the pair reading does not tell it apart. On the
freight index against first releases the set holds only last observation under both
losses, with a p value for the Ridge model just under 0.05, while the pair reading
is unsettled under absolute loss and not told apart under squared loss. The
protocol reports the two side by side and lets neither overrule the other. At
block length 12, 8 of the 32 sets differ, which is one more reason to read a
single exclusion with care.

So the earlier ranking stands as a description of these 72 months. It does not
show that last observation is better than the Ridge model on every series, and it
does not show that the two are equal.

## Tables

DM is the primary test and Fixed is the fixed smoothing check. In the pair names,
Last is last observation and Last year is the same month last year.

<!-- tables start -->

### Readings across the eight series

| Pair | Outcome | Loss | Told apart | Unsettled | Not told apart | Not computed |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| Ridge minus last | first release | absolute loss | 1 | 2 | 5 | 0 |
| Ridge minus last | first release | squared loss | 0 | 2 | 6 | 0 |
| Ridge minus last | latest values | absolute loss | 2 | 2 | 4 | 0 |
| Ridge minus last | latest values | squared loss | 2 | 1 | 5 | 0 |
| Ridge minus last year | first release | absolute loss | 1 | 6 | 1 | 0 |
| Ridge minus last year | first release | squared loss | 0 | 7 | 1 | 0 |
| Ridge minus last year | latest values | absolute loss | 1 | 4 | 3 | 0 |
| Ridge minus last year | latest values | squared loss | 1 | 4 | 3 | 0 |
| Last minus last year | first release | absolute loss | 3 | 4 | 1 | 0 |
| Last minus last year | first release | squared loss | 1 | 6 | 1 | 0 |
| Last minus last year | latest values | absolute loss | 2 | 4 | 2 | 0 |
| Last minus last year | latest values | squared loss | 1 | 5 | 2 | 0 |

### Pairs, first release, absolute loss

| Series | Pair | Mean difference | DM statistic | DM p | DM Holm p | Fixed statistic | Fixed p | Fixed Holm p | Reading |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Freight index | Ridge minus last | 0.3266 | 2.016 | 0.0476 | none | 1.947 | 0.0874 | none | Unsettled |
| Freight index | Ridge minus last year | -1.26 | -3.455 | 0.0009 | none | -2.181 | 0.0608 | none | Unsettled |
| Freight index | Last minus last year | -1.586 | -4.489 | <0.0001 | none | -2.384 | 0.0442 | none | Told apart |
| Truck tonnage | Ridge minus last | 0.2006 | 1.106 | 0.2724 | 0.8172 | 1.005 | 0.3442 | 1.0000 | Not told apart |
| Truck tonnage | Ridge minus last year | -0.873 | -2.720 | 0.0082 | 0.0164 | -1.434 | 0.1896 | 0.3449 | Unsettled |
| Truck tonnage | Last minus last year | -1.074 | -3.777 | 0.0003 | 0.0007 | -2.144 | 0.0643 | 0.1940 | Unsettled |
| Rail carloads | Ridge minus last | 2756 | 1.053 | 0.2961 | 0.8172 | 0.968 | 0.3612 | 1.0000 | Not told apart |
| Rail carloads | Ridge minus last year | -2.957e+04 | -4.857 | <0.0001 | <0.0001 | -2.532 | 0.0351 | 0.2109 | Unsettled |
| Rail carloads | Last minus last year | -3.232e+04 | -4.695 | <0.0001 | <0.0001 | -2.515 | 0.0361 | 0.1804 | Unsettled |
| Rail intermodal | Ridge minus last | 1.223e+04 | 3.175 | 0.0022 | 0.0133 | 4.101 | 0.0034 | 0.0240 | Told apart |
| Rail intermodal | Ridge minus last year | -4.559e+04 | -4.775 | <0.0001 | <0.0001 | -4.484 | 0.0020 | 0.0143 | Told apart |
| Rail intermodal | Last minus last year | -5.782e+04 | -7.179 | <0.0001 | <0.0001 | -5.626 | 0.0005 | 0.0035 | Told apart |
| Inland waterway | Ridge minus last | 0.1119 | 0.904 | 0.3689 | 0.8172 | 0.881 | 0.4038 | 1.0000 | Not told apart |
| Inland waterway | Ridge minus last year | -1.181 | -4.108 | 0.0001 | 0.0004 | -2.223 | 0.0569 | 0.2275 | Unsettled |
| Inland waterway | Last minus last year | -1.293 | -4.116 | 0.0001 | 0.0003 | -2.326 | 0.0485 | 0.1940 | Unsettled |
| Air freight | Ridge minus last | 3.752e+04 | 2.430 | 0.0176 | 0.0881 | 1.620 | 0.1440 | 0.5760 | Not told apart |
| Air freight | Ridge minus last year | -1.021e+05 | -3.582 | 0.0006 | 0.0019 | -1.594 | 0.1497 | 0.3449 | Unsettled |
| Air freight | Last minus last year | -1.396e+05 | -4.695 | <0.0001 | <0.0001 | -1.868 | 0.0987 | 0.1973 | Unsettled |
| Pipeline petroleum | Ridge minus last | 3609 | 3.968 | 0.0002 | 0.0012 | 3.253 | 0.0116 | 0.0699 | Unsettled |
| Pipeline petroleum | Ridge minus last year | -1.061e+04 | -5.438 | <0.0001 | <0.0001 | -2.507 | 0.0366 | 0.2109 | Unsettled |
| Pipeline petroleum | Last minus last year | -1.422e+04 | -7.734 | <0.0001 | <0.0001 | -3.492 | 0.0082 | 0.0490 | Told apart |
| Natural gas | Ridge minus last | -11.58 | -1.520 | 0.1329 | 0.5316 | -2.377 | 0.0448 | 0.2238 | Not told apart |
| Natural gas | Ridge minus last year | -18.48 | -1.866 | 0.0661 | 0.0661 | -1.768 | 0.1150 | 0.3449 | Not told apart |
| Natural gas | Last minus last year | -6.893 | -0.603 | 0.5486 | 0.5486 | -0.747 | 0.4765 | 0.4765 | Not told apart |

### Pairs, first release, squared loss

| Series | Pair | Mean difference | DM statistic | DM p | DM Holm p | Fixed statistic | Fixed p | Fixed Holm p | Reading |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Freight index | Ridge minus last | 1.738 | 1.816 | 0.0736 | none | 2.250 | 0.0545 | none | Not told apart |
| Freight index | Ridge minus last year | -13.65 | -3.498 | 0.0008 | none | -2.045 | 0.0751 | none | Unsettled |
| Freight index | Last minus last year | -15.39 | -4.059 | 0.0001 | none | -2.124 | 0.0665 | none | Unsettled |
| Truck tonnage | Ridge minus last | 1.151 | 0.836 | 0.4062 | 0.8124 | 1.064 | 0.3183 | 1.0000 | Not told apart |
| Truck tonnage | Ridge minus last year | -8.151 | -2.875 | 0.0053 | 0.0107 | -1.691 | 0.1293 | 0.3237 | Unsettled |
| Truck tonnage | Last minus last year | -9.303 | -3.305 | 0.0015 | 0.0045 | -1.953 | 0.0865 | 0.3461 | Unsettled |
| Rail carloads | Ridge minus last | 5.581e+08 | 1.257 | 0.2130 | 0.6389 | 0.968 | 0.3615 | 1.0000 | Not told apart |
| Rail carloads | Ridge minus last year | -5.813e+09 | -3.360 | 0.0013 | 0.0050 | -1.810 | 0.1079 | 0.3237 | Unsettled |
| Rail carloads | Last minus last year | -6.371e+09 | -3.144 | 0.0024 | 0.0049 | -1.729 | 0.1220 | 0.3661 | Unsettled |
| Rail intermodal | Ridge minus last | 1.334e+09 | 2.064 | 0.0426 | 0.2155 | 2.705 | 0.0269 | 0.1612 | Not told apart |
| Rail intermodal | Ridge minus last year | -8.368e+09 | -3.734 | 0.0004 | 0.0019 | -3.301 | 0.0108 | 0.0759 | Unsettled |
| Rail intermodal | Last minus last year | -9.702e+09 | -4.878 | <0.0001 | <0.0001 | -3.624 | 0.0067 | 0.0472 | Told apart |
| Inland waterway | Ridge minus last | 0.4231 | 0.759 | 0.4505 | 0.8124 | 0.880 | 0.4047 | 1.0000 | Not told apart |
| Inland waterway | Ridge minus last year | -10.53 | -3.905 | 0.0002 | 0.0013 | -2.288 | 0.0514 | 0.2110 | Unsettled |
| Inland waterway | Last minus last year | -10.96 | -3.839 | 0.0003 | 0.0013 | -2.225 | 0.0568 | 0.2838 | Unsettled |
| Air freight | Ridge minus last | 1.282e+10 | 1.538 | 0.1286 | 0.5144 | 1.162 | 0.2788 | 1.0000 | Not told apart |
| Air freight | Ridge minus last year | -7.741e+10 | -3.099 | 0.0028 | 0.0083 | -1.348 | 0.2145 | 0.3237 | Unsettled |
| Air freight | Last minus last year | -9.024e+10 | -3.521 | 0.0008 | 0.0030 | -1.420 | 0.1933 | 0.3867 | Unsettled |
| Pipeline petroleum | Ridge minus last | 1.107e+08 | 3.844 | 0.0003 | 0.0018 | 2.602 | 0.0315 | 0.1612 | Unsettled |
| Pipeline petroleum | Ridge minus last year | -5.306e+08 | -5.146 | <0.0001 | <0.0001 | -2.415 | 0.0422 | 0.2110 | Unsettled |
| Pipeline petroleum | Last minus last year | -6.412e+08 | -5.906 | <0.0001 | <0.0001 | -2.712 | 0.0266 | 0.1594 | Unsettled |
| Natural gas | Ridge minus last | -4416 | -2.139 | 0.0359 | 0.2155 | -3.823 | 0.0051 | 0.0355 | Unsettled |
| Natural gas | Ridge minus last year | -6776 | -1.993 | 0.0501 | 0.0501 | -2.950 | 0.0184 | 0.1105 | Not told apart |
| Natural gas | Last minus last year | -2360 | -0.586 | 0.5596 | 0.5596 | -1.121 | 0.2947 | 0.3867 | Not told apart |

### Pairs, latest values, absolute loss

| Series | Pair | Mean difference | DM statistic | DM p | DM Holm p | Fixed statistic | Fixed p | Fixed Holm p | Reading |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Freight index | Ridge minus last | 0.6443 | 3.811 | 0.0003 | none | 3.234 | 0.0120 | none | Told apart |
| Freight index | Ridge minus last year | -0.3335 | -0.899 | 0.3717 | none | -0.485 | 0.6409 | none | Not told apart |
| Freight index | Last minus last year | -0.9778 | -2.805 | 0.0065 | none | -1.324 | 0.2220 | none | Unsettled |
| Truck tonnage | Ridge minus last | 0.2967 | 1.700 | 0.0936 | 0.1872 | 1.633 | 0.1411 | 0.3178 | Not told apart |
| Truck tonnage | Ridge minus last year | -0.8908 | -3.242 | 0.0018 | 0.0057 | -1.592 | 0.1500 | 0.6313 | Unsettled |
| Truck tonnage | Last minus last year | -1.187 | -5.005 | <0.0001 | <0.0001 | -2.382 | 0.0444 | 0.2221 | Unsettled |
| Rail carloads | Ridge minus last | 6391 | 2.491 | 0.0151 | 0.0603 | 2.059 | 0.0735 | 0.2939 | Not told apart |
| Rail carloads | Ridge minus last year | -2.473e+04 | -3.867 | 0.0002 | 0.0012 | -1.707 | 0.1263 | 0.6313 | Unsettled |
| Rail carloads | Last minus last year | -3.112e+04 | -4.424 | <0.0001 | 0.0001 | -2.025 | 0.0774 | 0.3097 | Unsettled |
| Rail intermodal | Ridge minus last | 9564 | 2.479 | 0.0156 | 0.0603 | 2.710 | 0.0266 | 0.1599 | Not told apart |
| Rail intermodal | Ridge minus last year | -4.638e+04 | -5.029 | <0.0001 | <0.0001 | -5.764 | 0.0004 | 0.0030 | Told apart |
| Rail intermodal | Last minus last year | -5.594e+04 | -7.642 | <0.0001 | <0.0001 | -6.681 | 0.0002 | 0.0011 | Told apart |
| Inland waterway | Ridge minus last | -0.164 | -1.132 | 0.2614 | 0.2614 | -1.033 | 0.3320 | 0.3320 | Not told apart |
| Inland waterway | Ridge minus last year | 0.4208 | 1.058 | 0.2939 | 0.2939 | 0.428 | 0.6797 | 0.6797 | Not told apart |
| Inland waterway | Last minus last year | 0.5847 | 1.325 | 0.1895 | 0.3790 | 0.556 | 0.5936 | 1.0000 | Not told apart |
| Air freight | Ridge minus last | 4.246e+04 | 2.759 | 0.0074 | 0.0368 | 1.822 | 0.1059 | 0.3178 | Unsettled |
| Air freight | Ridge minus last year | -9.673e+04 | -3.322 | 0.0014 | 0.0057 | -1.446 | 0.1862 | 0.6313 | Unsettled |
| Air freight | Last minus last year | -1.392e+05 | -4.531 | <0.0001 | <0.0001 | -1.796 | 0.1102 | 0.3306 | Unsettled |
| Pipeline petroleum | Ridge minus last | 3585 | 3.590 | 0.0006 | 0.0042 | 2.660 | 0.0288 | 0.1599 | Unsettled |
| Pipeline petroleum | Ridge minus last year | -1.031e+04 | -5.188 | <0.0001 | <0.0001 | -2.499 | 0.0370 | 0.2219 | Unsettled |
| Pipeline petroleum | Last minus last year | -1.39e+04 | -7.287 | <0.0001 | <0.0001 | -3.584 | 0.0071 | 0.0429 | Told apart |
| Natural gas | Ridge minus last | -20.99 | -3.289 | 0.0016 | 0.0094 | -4.187 | 0.0031 | 0.0214 | Told apart |
| Natural gas | Ridge minus last year | -16.9 | -1.764 | 0.0819 | 0.1639 | -1.290 | 0.2332 | 0.6313 | Not told apart |
| Natural gas | Last minus last year | 4.093 | 0.373 | 0.7104 | 0.7104 | 0.271 | 0.7934 | 1.0000 | Not told apart |

### Pairs, latest values, squared loss

| Series | Pair | Mean difference | DM statistic | DM p | DM Holm p | Fixed statistic | Fixed p | Fixed Holm p | Reading |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Freight index | Ridge minus last | 4.494 | 3.511 | 0.0008 | none | 2.889 | 0.0202 | none | Told apart |
| Freight index | Ridge minus last year | -5.86 | -1.474 | 0.1450 | none | -0.783 | 0.4565 | none | Not told apart |
| Freight index | Last minus last year | -10.35 | -2.711 | 0.0084 | none | -1.319 | 0.2236 | none | Unsettled |
| Truck tonnage | Ridge minus last | 1.011 | 0.893 | 0.3751 | 0.3751 | 1.205 | 0.2628 | 0.7256 | Not told apart |
| Truck tonnage | Ridge minus last year | -6.245 | -3.137 | 0.0025 | 0.0105 | -1.791 | 0.1111 | 0.4826 | Unsettled |
| Truck tonnage | Last minus last year | -7.256 | -4.214 | <0.0001 | 0.0004 | -2.053 | 0.0741 | 0.3707 | Unsettled |
| Rail carloads | Ridge minus last | 1.163e+09 | 2.009 | 0.0484 | 0.1935 | 1.464 | 0.1814 | 0.7256 | Not told apart |
| Rail carloads | Ridge minus last year | -5.733e+09 | -3.101 | 0.0028 | 0.0105 | -1.416 | 0.1945 | 0.5834 | Unsettled |
| Rail carloads | Last minus last year | -6.896e+09 | -3.031 | 0.0034 | 0.0102 | -1.451 | 0.1848 | 0.7393 | Unsettled |
| Rail intermodal | Ridge minus last | 1.389e+09 | 2.321 | 0.0232 | 0.1159 | 2.781 | 0.0239 | 0.1433 | Not told apart |
| Rail intermodal | Ridge minus last year | -7.737e+09 | -4.102 | 0.0001 | 0.0006 | -5.018 | 0.0010 | 0.0072 | Told apart |
| Rail intermodal | Last minus last year | -9.126e+09 | -5.679 | <0.0001 | <0.0001 | -4.941 | 0.0011 | 0.0079 | Told apart |
| Inland waterway | Ridge minus last | -3.547 | -1.577 | 0.1192 | 0.3260 | -1.207 | 0.2621 | 0.7256 | Not told apart |
| Inland waterway | Ridge minus last year | 5.896 | 0.812 | 0.4195 | 0.4195 | 0.340 | 0.7426 | 0.7426 | Not told apart |
| Inland waterway | Last minus last year | 9.442 | 1.148 | 0.2548 | 0.5095 | 0.487 | 0.6391 | 1.0000 | Not told apart |
| Air freight | Ridge minus last | 1.333e+10 | 1.625 | 0.1087 | 0.3260 | 1.276 | 0.2377 | 0.7256 | Not told apart |
| Air freight | Ridge minus last year | -7.599e+10 | -3.194 | 0.0021 | 0.0105 | -1.312 | 0.2258 | 0.5834 | Unsettled |
| Air freight | Last minus last year | -8.931e+10 | -3.535 | 0.0007 | 0.0029 | -1.396 | 0.2001 | 0.7393 | Unsettled |
| Pipeline petroleum | Ridge minus last | 9.517e+07 | 3.470 | 0.0009 | 0.0062 | 2.349 | 0.0468 | 0.2339 | Unsettled |
| Pipeline petroleum | Ridge minus last year | -5.252e+08 | -4.958 | <0.0001 | <0.0001 | -2.441 | 0.0405 | 0.2431 | Unsettled |
| Pipeline petroleum | Last minus last year | -6.204e+08 | -5.691 | <0.0001 | <0.0001 | -2.774 | 0.0241 | 0.1449 | Unsettled |
| Natural gas | Ridge minus last | -6406 | -3.022 | 0.0035 | 0.0209 | -5.377 | 0.0007 | 0.0046 | Told apart |
| Natural gas | Ridge minus last year | -5759 | -2.169 | 0.0334 | 0.0668 | -1.883 | 0.0965 | 0.4826 | Not told apart |
| Natural gas | Last minus last year | 646.7 | 0.190 | 0.8495 | 0.8495 | 0.192 | 0.8528 | 1.0000 | Not told apart |

### Model confidence sets

| Series | Outcome | Loss | Set at block 4 | Ridge p | Last p | Last year p | Set at block 12 |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| Freight index | first release | absolute loss | Last | 0.0482 | 1.0000 | 0.0089 | Last |
| Freight index | first release | squared loss | Last | 0.0464 | 1.0000 | 0.0277 | Last |
| Freight index | latest values | absolute loss | Last, Last year | 0.0053 | 1.0000 | 0.0545 | Last, Last year |
| Freight index | latest values | squared loss | Last, Last year | 0.0161 | 1.0000 | 0.0662 | Last, Last year |
| Truck tonnage | first release | absolute loss | Ridge, Last | 0.2383 | 1.0000 | 0.0186 | Ridge, Last |
| Truck tonnage | first release | squared loss | Ridge, Last | 0.2781 | 1.0000 | 0.0372 | Ridge, Last, Last year |
| Truck tonnage | latest values | absolute loss | Ridge, Last | 0.0896 | 1.0000 | 0.0027 | Last |
| Truck tonnage | latest values | squared loss | Ridge, Last | 0.2765 | 1.0000 | 0.0155 | Ridge, Last |
| Rail carloads | first release | absolute loss | Ridge, Last | 0.3240 | 1.0000 | 0.0085 | Ridge, Last |
| Rail carloads | first release | squared loss | Ridge, Last, Last year | 0.3123 | 1.0000 | 0.0519 | Ridge, Last, Last year |
| Rail carloads | latest values | absolute loss | Last | 0.0374 | 1.0000 | 0.0190 | Last |
| Rail carloads | latest values | squared loss | Ridge, Last, Last year | 0.0807 | 1.0000 | 0.0729 | Ridge, Last, Last year |
| Rail intermodal | first release | absolute loss | Last | 0.0096 | 1.0000 | <0.0001 | Last |
| Rail intermodal | first release | squared loss | Ridge, Last | 0.0707 | 1.0000 | 0.0013 | Last |
| Rail intermodal | latest values | absolute loss | Ridge, Last | 0.0551 | 1.0000 | <0.0001 | Last |
| Rail intermodal | latest values | squared loss | Ridge, Last | 0.0515 | 1.0000 | 0.0001 | Last |
| Inland waterway | first release | absolute loss | Ridge, Last | 0.3831 | 1.0000 | 0.0096 | Ridge, Last |
| Inland waterway | first release | squared loss | Ridge, Last | 0.4184 | 1.0000 | 0.0147 | Ridge, Last |
| Inland waterway | latest values | absolute loss | Ridge, Last, Last year | 0.5419 | 0.5315 | 1.0000 | Ridge, Last, Last year |
| Inland waterway | latest values | squared loss | Ridge, Last, Last year | 0.6367 | 0.3007 | 1.0000 | Ridge, Last, Last year |
| Air freight | first release | absolute loss | Ridge, Last | 0.0710 | 1.0000 | 0.0159 | Ridge, Last, Last year |
| Air freight | first release | squared loss | Ridge, Last, Last year | 0.2146 | 1.0000 | 0.0809 | Ridge, Last, Last year |
| Air freight | latest values | absolute loss | Last | 0.0409 | 1.0000 | 0.0188 | Ridge, Last, Last year |
| Air freight | latest values | squared loss | Ridge, Last, Last year | 0.1741 | 1.0000 | 0.0812 | Ridge, Last, Last year |
| Pipeline petroleum | first release | absolute loss | Last | 0.0014 | 1.0000 | <0.0001 | Last |
| Pipeline petroleum | first release | squared loss | Last | 0.0062 | 1.0000 | 0.0016 | Last |
| Pipeline petroleum | latest values | absolute loss | Last | 0.0049 | 1.0000 | <0.0001 | Last |
| Pipeline petroleum | latest values | squared loss | Last | 0.0092 | 1.0000 | 0.0016 | Last |
| Natural gas | first release | absolute loss | Ridge, Last, Last year | 1.0000 | 0.1160 | 0.1160 | Ridge, Last, Last year |
| Natural gas | first release | squared loss | Ridge | 1.0000 | 0.0376 | 0.0376 | Ridge |
| Natural gas | latest values | absolute loss | Ridge, Last year | 1.0000 | 0.0004 | 0.1016 | Ridge, Last year |
| Natural gas | latest values | squared loss | Ridge | 1.0000 | 0.0024 | 0.0254 | Ridge, Last year |

### Origins that are not one step ahead in publication time

| Series | Targets already on the origin vintage | Targets |
| --- | ---: | ---: |
| Freight index | 8 | 72 |
| Truck tonnage | 9 | 72 |
| Rail carloads | 9 | 72 |
| Rail intermodal | 9 | 72 |
| Inland waterway | 9 | 72 |
| Air freight | 10 | 72 |
| Pipeline petroleum | 8 | 72 |
| Natural gas | 8 | 72 |

<!-- tables end -->

## Reproduce

The inputs are the saved freight index predictions and a replay of the seven
component series, and each replayed average error must match the saved one before
anything is computed. Four component tables are committed. Truck tonnage and the
two rail series are held back for the reason the
[seven series report](real-time-components.md) gives, so a full run needs those
three fetched with the [script](../tools/fetch_alfred_vintages.py) and checked
against their manifests. Put all seven tables in one directory, then:

```
python -m src.forecast_comparison --vintage-dir <directory> --output docs/forecast-comparison.json
python tools/render_forecast_comparison.py
```

A [test](../tests/test_forecast_comparison.py) checks both tests against the
`dieboldmariano` package and against the periodogram in `scipy`, and the Holm
adjustment against `statsmodels`, on synthetic values. Another test recomputes
the saved results for the freight index and the four committed series.

## Limits

1. The primary test ignores dependence between months in the loss difference. That
   suits optimal forecasts one step ahead, and none of these is shown to be
   optimal. It can reject too often, most plausibly against the same month last
   year.
2. Some origins read a vintage that already holds the target month. The value is
   dropped before forecasting, but those months are not one step ahead in
   publication time. They all stay in, and the last table counts them.
3. The yearly tables of the real time report show the gap between methods moving
   over time, which the stationarity assumption does not cover.
4. 72 months is a short sample. These tests can reject too often or have little
   power in it.
5. Three pairs, two outcomes and two losses are not adjusted against each other,
   and the confidence sets carry no adjustment across series.
6. The eight series are not independent. They are the freight index and what the
   earlier protocol names as its component series, all from one publisher.
7. A gap that cannot be told apart is not evidence that two methods are equal.
8. Nothing here changes the model or chooses one.

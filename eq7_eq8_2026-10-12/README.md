# Evidence bundle addendum, 2026-10-12 (EQ7 and EQ8)

Adds the two experiments introduced after the 2026-09-29 bundle. Benchmarks A and B, EQ4-EQ6, the
batched Gate timing, and the saturation sweep are in `evidence_bundle_anon_2026-09-29.zip`.
Both new scripts are deterministic, standard-library Python 3 (run with 3.13.5), with no randomness
and no seeds.

| Paper section | Script | Result file |
|---|---|---|
| 7.6 Profile selection probe (EQ7) | `code/profile_selection_probe.py` | `results/profile_selection_probe.json` |
| 7.7 Comparison with conventional gateways (EQ8) | `code/baseline_comparison.py` | `results/baseline_comparison_pi_run{1,2,3}.json` |

## Commands
```
python3 code/profile_selection_probe.py --json results/profile_selection_probe.json
# exits non-zero if the expected outcome (see script docstring) is not reproduced
python3 code/baseline_comparison.py --json results/baseline_comparison.json --repeats 7 --loops 200
# timing runs on the Pi: governor set to performance, pinned with taskset -c 2
taskset -c 2 python3 code/baseline_comparison.py --json out.json --repeats 7 --loops 200
```
The fault matrix and the change-scope counts are deterministic and identical in all three runs.
Only the timing block varies between runs.

## Environment of the timing runs (2026-10-12)
Raspberry Pi 5 Model B, Debian 13, kernel 6.18.34+rpt-rpi-v8, Python 3.13.5, `performance`
governor on all four cores during the runs (restored to `ondemand` afterwards), `taskset -c 2`,
no throttling (`vcgencmd get_throttled` = 0x0), 47-53 degC. Cyclic garbage collector disabled
inside the timed loop. Each batch is 200 loops over 255 frames with one timer pair around the
batch; 7 batches per run, 3 runs.

## Independent expected values
Expected values do not come from the mapping tables under test. In both scripts the frame
generator encodes a known true temperature (or humidity) with the profile's own encoder, and
the oracle compares the pipeline output against that true value and against the topic the
generator assigned to the endpoint. This is separate from the codecs in the pipeline only
through the true value; the encoder and decoder of one profile are written by the same author,
so a shared misreading of a payload layout would not be caught.

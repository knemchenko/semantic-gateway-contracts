# Evidence package: Ontology-Guided Semantic Unification in Multiprotocol Gateways for 6G-U IoT

Anonymized supplementary evidence archive submitted alongside the manuscript.
It contains everything needed to reproduce every number reported in the
manuscript's Evaluation section: the exact code, exact configuration, exact
run commands, pinned dependency versions, and the raw per-trial output from
the actual Raspberry Pi 5 runs the paper reports.

**This archive reflects the current manuscript only.** Earlier revision
rounds used a six-stage Benchmark B decomposition, a per-profile channel
"resolver" that was really a config-supplied constant, a four-constraint
Contract Gate, a single fixed traffic-generator seed reused across all five
"trials," and a Gate description that double-counted unit/concept
compatibility as a sixth independent constraint class. All of that has
since been corrected: channel resolution is now a genuine
provisioned-binding lookup timed as its own stage; the Gate checks five
independent candidate-level constraint classes (required fields, datatype,
concept equality, unit equality, range) plus a runtime `(q,u) in R_QU`
check retained as redundant defense-in-depth, since the Target Contract
registry now enforces `(q*, u) in R_QU` as a provisioning-time invariant
(manuscript Section 5) that makes a per-candidate compatibility check
logically implied by the concept- and unit-equality checks, not
independent of them; and each of the five trials uses its own seed (42-46),
so they are genuinely independent samples, not five replays of one fixed
sequence -- this archive's code and results match those corrections, not
any earlier round. `PROVENANCE_NOTE.md` documents a cross-session
decode-latency investigation: the fine-grained, seven-stage latency
decomposition reported in the manuscript is an exploratory,
session-specific instrumentation result, not a stable point estimate --
`results/earlier_session_reference/` includes an earlier measurement
session's own raw data so this can be independently checked, not just
taken on the manuscript's word.

**Session 2026-09-29.** Every result in this archive (Benchmark A, Benchmark B,
the Gate-disabled ablation, the batched Gate timing, the exploratory saturation
sweep and the EQ6 functional probe) was rerun in one session on 2026-09-29 on the
same hardware and with the same, unmodified code and configuration as the
2026-09-02 archive. The only new artefact is a two-line wrapper,
`code/run_gc_disabled.py`, which calls `gc.disable()` before running
`code/semantic_filtering_v2.py` unchanged. Reason: with CPython's default
cyclic garbage collector, one generation-2 collection of about 5.4 ms lands on a
single frame (index about 1590-1600, triggered while the harness accumulates
per-frame result rows) in 4 of 5 Benchmark B trials and 3 of 5 ablation trials.
That single pause raises the all-arrivals mean by about 0.5 us and dominates its
trial-to-trial spread. The manuscript therefore reports the collector-disabled runs
(`results/gc_disabled/`) as the steady-state Benchmark B and ablation figures and
the default-collector runs (`results/experiment3_repeats/`, `results/ablation_repeats/`)
as a sensitivity result. The earlier session's data remain in
`results/earlier_session_reference/` for the cross-session decode-latency comparison.

## Hardware / software environment

- Raspberry Pi 5 Model B (8 GB), Debian GNU/Linux 13 (trixie)
- Kernel 6.18.34+rpt-rpi-v8
- Python 3.13.5
- CPU: Cortex-A76 (4 cores @ 2.4 GHz), `performance` governor on all four
  cores (verified via `cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor`
  immediately before every batch of runs in this archive; restored to `ondemand`
  afterwards; no thermal throttling, `vcgencmd get_throttled` = 0x0, 47-55 degC),
  pinned to core 2 via `taskset -c 2`. Session date 2026-09-29.
- Dependencies: see `requirements-experiment1.txt` (Benchmark A) and `requirements-experiment3.txt` (Benchmark B / ablation / batched Gate timing)

## Contents

All scripts live in `code/`. All other paths below are relative to this
archive's own top-level directory (where this README sits) -- run every
command from there, not from inside `code/`.

- `NOTATION.md` -- full field-by-field notation correspondence table across this paper series (DUIKT / BlackSeaCom / this paper), referenced from the manuscript's Formal Model section instead of being reproduced there.
- `code/semantic_filtering_v2.py` -- Benchmark B (source-card/target-contract validation-gate prototype): seven timed stages (lookup, identity, **channel**, decode, normalize, gate, publication); the Gate checks five independent candidate-level constraint classes (required fields, datatype, concept equality, unit equality, range), plus a runtime `(q,u) in R_QU` check kept as redundant defense-in-depth, not a sixth independent class (manuscript Section 5/6); the traffic generator's seed is overridable via `--seed` so each of the 5 trials samples a genuinely different traffic ordering and mutation placement.
- `code/gateway_perf_v2.py` -- Benchmark A (cache-aware baseline), Source-Card-profile-keyed bounded LRU cache. Records both an accepted-frames-only latency distribution (`latency_ms`) and two ALL-arrivals distributions -- response time including queue wait (`response_ms_all_arrivals`) and service time excluding it (`service_ms_all_arrivals`) -- since rejected packets (unregistered profile / malformed payload) also consume real CPU before exiting early, and the admissibility bound's `lambda_max` must be computed over that same all-arrivals population, not accepted frames alone.
- `code/gate_batched_timing.py` -- a batched-timing corroboration of the Contract Gate's marginal cost: re-times the identical Gate-check logic used in `semantic_filtering_v2.py` (including the concept-equality and unit-equality checks) with a single timer pair bracketing a 2,000,000-call loop (5 repeated batches), amortizing per-call timer overhead to negligible levels, since the per-frame disabled-Gate mean falls below this hardware's own ~245 ns bare-pair timer floor and a marginal cost computed from it alone would inherit that unreliability.
- `code/channel_conversion_probe.py` -- EQ6's deterministic functional probe (19 hand-picked frames, two canonical devices, five channel bindings): exercises the provisioned channel-binding resolver J (distinguishing channels that share a device, a Source Card profile, or both), a genuinely non-identity concept-alignment step (a site-specific `ex:AirTemperature` alias canonicalized to `saref:Temperature`), and a genuinely non-identity, base-unit-composed affine unit conversion (Kelvin to Celsius).
- `experiment_config_v2.json` -- Benchmark B's fixed configuration: traffic mix (now nine classes, including separate `wrong_unit`/`wrong_concept` splits instead of one combined `incompatible_unit_concept` class), per-instance envelope endpoints (feeding channel resolution), calibration iterations, bulk device instances, invariance-probe bindings.
- `canonical_registry.json` -- the identity-link table, the **channel-binding table** (device, Source Card, endpoint -> channel; this operationalizes J), and the target-contract registry -- each target contract now also declares its canonical `concept`, not only its unit.
- `source_cards/*.json` -- the three Source Card artifacts (LoRaWAN, ZigBee, BLE temperature).
- `results/experiment3_repeats/` -- Benchmark B, 5 trials (seeds 42-46), **default garbage collector** (sensitivity result): `run_{1..5}_summary.json` plus raw per-frame logs in `raw_csv/`.
- `results/ablation_repeats/` -- Gate-disabled ablation, 5 trials (seeds 42-46), default garbage collector, same format.
- `results/gc_disabled/` -- the same Benchmark B (`gcoff_{1..5}_*`) and Gate-disabled ablation (`gcoff_ablation_{1..5}_*`) with the cyclic garbage collector disabled (seeds 42-46): the steady-state figures the manuscript reports as primary. `*_summary.json` plus per-frame `raw_csv/`.
- `code/run_gc_disabled.py` -- the wrapper (`gc.disable()` then `runpy` of `code/semantic_filtering_v2.py`, arguments passed through unchanged). The runs were executed with an inline equivalent of this file.
- `results/experiment1_repeats/` -- Benchmark A, 5 trials, default garbage collector: `run_{1..5}_summary.json`.
- `results/saturation_sweep/saturation_sweep.json` -- exploratory sweep results (5 rate points; see Limitations, not an EQ; rates were set relative to an earlier, accepted-frames-only lambda_max estimate, as the manuscript's Limitations section notes explicitly).
- `results/channel_conversion_probe/channel_conversion_probe_summary.json` -- EQ6 functional-probe results (19 deterministic frames; fully reproducible, no RNG).
- `results/gate_batched_timing/gate_batched_timing_output.txt` -- batched Gate-timing corroboration output (5 batches of 2,000,000 calls each, enabled and disabled).
- `results/earlier_session_reference/` -- an earlier RPi5 measurement session's own raw Benchmark B data (older six-stage/four-constraint schema), included so the cross-session decode-latency comparison in `PROVENANCE_NOTE.md` and the manuscript's Limitations section can be independently reproduced, not just read as prose; see that directory's own README, in particular the accept-only filtering caveat before recomputing any stage mean from the raw CSVs.
- `PROVENANCE_NOTE.md` -- full write-up of the cross-session decode-latency investigation (code diff, direct rerun of the earlier session's own script, archival cross-check of its recorded numbers, environment/governor/thermal checks).

Benchmark B's traffic generator uses a distinct seed per trial (42, 43, 44,
45, 46) rather than one fixed seed replayed five times, so cross-trial
statistics reflect genuinely independent traffic samples. Benchmark A and
the exploratory sweep use Python's default `random` module without an
explicit seed. The channel-conversion probe uses fixed, hand-picked inputs
and no RNG at all, and is byte-identical across reruns; the batched
Gate-timing script is deterministic in logic (only wall-clock noise varies
run to run).

## Run commands

All commands below are run from this archive's own top-level directory
(the one containing this README, `code/`, `results/`, etc.) -- every path
is given relative to that directory, including the `code/...` script paths.
This was verified by a clean-room extraction-and-rerun immediately before
submission, not assumed.

Benchmark B, steady-state protocol reported as primary (garbage collector disabled; 5 trials, distinct seeds):
```
for i in 1 2 3 4 5; do
  seed=$((41+i))
  taskset -c 2 python3 code/run_gc_disabled.py --config experiment_config_v2.json --seed $seed --out-prefix gcoff_${i}
  taskset -c 2 python3 code/run_gc_disabled.py --config experiment_config_v2.json --seed $seed --gate-disabled --out-prefix gcoff_ablation_${i}
done
```
(Run the wrapper from the archive root, like the plain script; the wrapper is under `code/`, so call it as `code/run_gc_disabled.py`.)

Benchmark B, default garbage collector (sensitivity result; 5 trials, distinct seeds):
```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-experiment3.txt
for i in 1 2 3 4 5; do
  seed=$((41+i))
  taskset -c 2 python3 code/semantic_filtering_v2.py --config experiment_config_v2.json --seed $seed --out-prefix run_${i}
done
```

Gate-disabled ablation (5 trials, same seeds):
```
for i in 1 2 3 4 5; do
  seed=$((41+i))
  taskset -c 2 python3 code/semantic_filtering_v2.py --config experiment_config_v2.json --seed $seed --gate-disabled --out-prefix ablation_${i}
done
```

Benchmark A (5 trials):
```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-experiment1.txt
for i in 1 2 3 4 5; do
  taskset -c 2 python3 code/gateway_perf_v2.py
  mv results_v2/summary_latency_v2.json results_v2/run_${i}_summary.json
done
```

Exploratory sweep (single run, 5 rate points; not a primary result, see Limitations):
```
taskset -c 2 python3 code/saturation_sweep.py
```

Channel-binding / concept-alignment / unit-conversion functional probe (single deterministic run, no arguments, no configuration file):
```
python3 code/channel_conversion_probe.py
```

Batched Gate-timing corroboration (single run, no arguments; run with `taskset -c 2` to match the paper's methodology):
```
taskset -c 2 python3 code/gate_batched_timing.py
```

**Before any of the above:** confirm the CPU governor is `performance` on
every core the benchmark will touch (`cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor`)
-- an `ondemand` or other power-saving governor measurably inflates and
destabilizes these latencies (observed directly during re-measurement:
Benchmark A's accepted-frame response mean read ~31us
under `ondemand` vs. ~26us under `performance` on the same hardware, a
~20% swing from governor state alone, not from any code change).

## Mapping results to manuscript numbers

- Benchmark A latency/cache results (accepted-only AND all-arrivals; service mean 16.1 us) <- `results/experiment1_repeats/*.json`
- Benchmark B validation filtering, primary figures (mean per frame 4.17 us, stage means, 0/0 false accepts/rejects; 7-stage decomposition -- exploratory, session-specific; see `PROVENANCE_NOTE.md` -- and 5-constraint Gate) <- `results/gc_disabled/gcoff_{1..5}_summary.json`
- Benchmark B default-collector sensitivity (mean 4.63 us, the single ~5.4 ms generation-2 pause per trial) <- `results/experiment3_repeats/*.json` and the `latency_ns` column of `results/experiment3_repeats/raw_csv/`
- Cross-session decode-latency comparison (Limitations, Section 10) <- `PROVENANCE_NOTE.md` and `results/earlier_session_reference/`
- Cross-protocol invariance (EQ4) <- `invariance_check` field inside each `experiment3_repeats` summary
- Gate ablation (EQ5) <- `results/gc_disabled/gcoff_ablation_{1..5}_summary.json` (primary) and `results/ablation_repeats/*.json` (default collector)
- Batched Gate marginal-cost corroboration (964.1 ns enabled / 82.3 ns disabled / ~882 ns marginal per frame) <- `results/gate_batched_timing/gate_batched_timing_output.txt`
- Exploratory saturation observation (Limitations, not an EQ) <- `results/saturation_sweep/saturation_sweep.json`
- Channel-binding / concept-alignment / unit-conversion functional probe (EQ6) <- `results/channel_conversion_probe/channel_conversion_probe_summary.json`

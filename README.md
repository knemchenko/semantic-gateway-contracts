# semantic-gateway-contracts

Code, configuration, and raw results for the paper

> K. Nemchenko, L. Globa, V. Yarmolenko, A. Vizzarri, F. Mazzenga: *Ontology-Guided Semantic Unification in Multiprotocol IoT
> Gateways: A Formal Gateway Model and Contract-Validation Framework* (Springer, PT-2026 recommended paper).

The paper splits per-device translation in a multiprotocol IoT gateway into a per-protocol **Source Card** and
a per-device/channel **Target Contract**, validates every decoded candidate with a **Contract Gate**, and
evaluates the prototypes on a Raspberry Pi 5. This repository holds everything needed to rerun the
experiments and to check every number in the Evaluation section.

## Layout

| Directory | Paper section | Contents |
|---|---|---|
| `benchmarks_2026-09-29/` | 7.2 Benchmark A, 7.3 Benchmark B, 7.4 invariance and Gate-disabled run, 7.5 EQ6 probe, saturation test, batched Gate timing | `code/` (Python prototypes and probes), configuration, pinned requirements, raw per-trial results from the 2026-09-29 Pi 5 session, an earlier session's data for the decode-latency comparison |
| `eq7_eq8_2026-10-12/` | 7.6 profile selection (EQ7), 7.7 comparison with conventional gateways (EQ8) | two deterministic scripts (no seeds), results of three timing runs on the Pi 5 |

Each directory has its own `README.md` with the exact commands, hardware, and software environment of the
runs. The two directories are unmodified copies of the evidence archives that accompany the submission
(`evidence_bundle_anon_2026-09-29.zip`, `evidence_bundle_anon_2026-10-12.zip`); only this top-level file is new.

## Quick start

```
# EQ7 and EQ8: standard-library Python 3, no seeds; exits non-zero if the expected outcome is not reproduced
cd eq7_eq8_2026-10-12
python3 code/profile_selection_probe.py
python3 code/baseline_comparison.py --repeats 7 --loops 200

# Benchmarks A and B: see benchmarks_2026-09-29/README.md for the commands and the pinned requirements
```

The fault matrix and change-scope counts of EQ8 are deterministic. Per-frame timings depend on the machine; the
reported ones come from a Raspberry Pi 5 with the `performance` governor, pinned to one core, with the cyclic
garbage collector disabled inside the timed loop.

## Caveats

- Results are for synthetic traffic and standalone in-process models of the pipeline. The EQ8 costs
  (about 1.0, 1.1, and 1.7 microseconds per frame for the three systems) are not comparable with
  Benchmark B's 4.17 microseconds per frame.
- All measurements come from one Raspberry Pi 5; across-session variability is documented only for the
  decode-stage latency (see `benchmarks_2026-09-29/PROVENANCE_NOTE.md`).
- No license has been chosen yet. Until the authors add one, all rights are reserved.

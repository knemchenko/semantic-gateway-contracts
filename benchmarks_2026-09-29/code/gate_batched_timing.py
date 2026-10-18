#!/usr/bin/env python3
"""
Batched-timing corroboration of the Contract Gate's marginal cost.

The existing per-frame instrumentation in semantic_filtering_v2.py times the
Gate check with one perf_counter_ns() pair per frame. The disabled-Gate mean
(109.5 ns) falls below this hardware's own measured ~245 ns bare-pair timer
floor, so a per-call point estimate there is not trustworthy on its own, and
neither -- by the same logic -- is a marginal cost computed as the
difference of two means where one of them is sub-floor.

This script re-measures the identical Gate-check logic (copied verbatim from
semantic_filtering_v2.py's Gate block, not re-implemented) using a SINGLE
perf_counter_ns() pair bracketing a tight loop of many repetitions, so the
per-call timer overhead is amortized across the whole batch rather than paid
once per call. This is a re-analysis/corroboration of the same mechanism
already reported, not a new experimental campaign: same registry, same
target contract, same R_QU_COMPATIBLE relation, same accepted-candidate
shape as Benchmark B's own valid frames.
"""

import statistics
import time

R_QU_COMPATIBLE = {("temperature", "Cel")}

TARGET = {
    "concept": "temperature",
    "schema_id": "temp_contract_v1",
    "version": 1,
    "unit": "Cel",
    "valid_range": [-40.0, 85.0],
}

# A representative accepted candidate, matching Benchmark B's valid-frame shape.
CANDIDATE = {
    "d": "canon_migrated_001",
    "a": "ch0",
    "q": "temperature",
    "w": 24.5,
    "u": "Cel",
    "t_e": 1755461234.12,
    "kappa_fam": TARGET["schema_id"],
    "nu": TARGET["version"],
}


def gate_check(candidate, target, gate_enabled):
    """Copied verbatim from semantic_filtering_v2.py's Gate block."""
    if not gate_enabled:
        return None
    if candidate["d"] is None or candidate["a"] is None or candidate["q"] is None \
            or candidate["w"] is None or candidate["u"] is None:
        return "missing_field"
    elif not isinstance(candidate["w"], (int, float)) or candidate["w"] != candidate["w"] \
            or candidate["w"] in (float("inf"), float("-inf")):
        return "wrong_datatype"
    elif candidate["q"] != target["concept"]:
        return "concept_mismatch"
    elif candidate["u"] != target["unit"]:
        return "unit_mismatch"
    elif (candidate["q"], candidate["u"]) not in R_QU_COMPATIBLE:
        return "incompatible_unit_concept"
    else:
        low, high = float(target["valid_range"][0]), float(target["valid_range"][1])
        if not (low <= candidate["w"] <= high):
            return "out_of_range"
    return None


def batched_mean_ns(gate_enabled: bool, batch_size: int) -> float:
    t0 = time.perf_counter_ns()
    for _ in range(batch_size):
        gate_check(CANDIDATE, TARGET, gate_enabled)
    t1 = time.perf_counter_ns()
    return (t1 - t0) / batch_size


def main(trials: int = 5, batch_size: int = 2_000_000) -> None:
    enabled_means = []
    disabled_means = []
    for trial in range(trials):
        # Warm up branch predictor / bytecode cache before each timed batch.
        batched_mean_ns(True, 50_000)
        batched_mean_ns(False, 50_000)
        enabled_means.append(batched_mean_ns(True, batch_size))
        disabled_means.append(batched_mean_ns(False, batch_size))

    e_mean = statistics.fmean(enabled_means)
    e_std = statistics.pstdev(enabled_means) if len(enabled_means) > 1 else 0.0
    d_mean = statistics.fmean(disabled_means)
    d_std = statistics.pstdev(disabled_means) if len(disabled_means) > 1 else 0.0
    marginal = e_mean - d_mean

    print(f"trials={trials} batch_size={batch_size}")
    print(f"enabled:  mean={e_mean:.2f} ns  std={e_std:.2f} ns  per-trial={['%.2f' % v for v in enabled_means]}")
    print(f"disabled: mean={d_mean:.2f} ns  std={d_std:.2f} ns  per-trial={['%.2f' % v for v in disabled_means]}")
    print(f"batched marginal cost = {marginal:.2f} ns/frame")


if __name__ == "__main__":
    main()

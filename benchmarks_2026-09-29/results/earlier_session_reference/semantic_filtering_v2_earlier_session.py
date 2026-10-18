#!/usr/bin/env python3
"""
Experiment 3 (revised): Source-Card / Target-Contract Semantic Filtering

This revision fixes a pipeline-order defect identified during review: the v1
script checked a range constraint on the raw decoded value *before*
normalization/projection, whereas the paper's Formal Model specifies the
Contract Gate (G) runs on the *normalized candidate contract* produced by the
Normalizer (N). This script reorders processing to:

    source-card lookup -> canonical-identity resolution -> decode
    -> normalize/unit-convert/project (N) -> Contract Gate (G) -> publish

It also splits the per-protocol "Device Card" of v1 into two distinct
artifacts:
  - a *Source Card* (one per (protocol, profile)): codec + source unit,
    exactly as in v1 -- this is what a Protocol Agent/Selector resolves from
    the *raw binding*.
  - a *canonical-identity link table* + *target contract registry*: maps a
    (protocol, stable_key) pair to one canonical device id `d`, and `d` to
    the shared northbound contract (schema id, version, required unit,
    validation range, topic anchor) it must be projected into -- independent
    of which source card/protocol produced the reading.

This directly operationalizes the paper's (revised) Proposition 1: three
different source cards (three different codecs/protocols) can resolve to the
same canonical device and must then be projected into the *same* target
contract schema and topic, despite having gone through completely different
decoding logic.

The Contract Gate now checks the full contract-validity set C^valid_sigma
declared for a target contract, not just a numeric range:
  - required fields present (d, a, q, w, u)
  - datatype (w is a finite real number)
  - concept/unit compatibility (q, u) in R_QU
  - value range (w in the target contract's declared range)

A `gate_enabled` config flag allows an ablation run: with the Gate disabled,
every structurally decodable, identity-resolved frame is accepted without
constraint checking, isolating the Gate's own marginal latency cost.

A timer-calibration pass (empty no-op call, same instrumentation pattern as
every pipeline stage) is run before the main loop to report the perf_counter
call-overhead floor alongside the measured stage means.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# --------------------------------------------------------------------------
# Source cards (per protocol/profile codec) -- unchanged decoding logic from
# v1, reused here as the "Source Card" half of the split model.
# --------------------------------------------------------------------------

def encode_int16(value: int, *, endian: str) -> bytes:
    if value < -32768 or value > 32767:
        raise ValueError(f"int16 out of range: {value}")
    return int(value).to_bytes(2, byteorder=endian, signed=True)


def temp_to_centi_c(temp_c: float) -> int:
    return int(round(temp_c * 100.0))


def encode_payload(protocol: str, temp_c: float) -> str:
    centi = temp_to_centi_c(temp_c)
    if protocol == "LoRaWAN":
        return encode_int16(centi, endian="big").hex()
    if protocol == "ZigBee":
        return bytes.fromhex("0000").hex() + encode_int16(centi, endian="little").hex()
    if protocol == "BLE":
        return encode_int16(centi, endian="little").hex()
    raise ValueError(f"Unsupported protocol profile: {protocol}")


def decode_lorawan_temperature_centi_c_be(payload_hex: str) -> float:
    payload = bytes.fromhex(payload_hex)
    if len(payload) != 2:
        raise ValueError("LoRaWAN payload must be exactly 2 bytes")
    centi = int.from_bytes(payload, byteorder="big", signed=True)
    return centi / 100.0


def decode_zigbee_temperature_zcl_attr_le(payload_hex: str) -> float:
    payload = bytes.fromhex(payload_hex)
    if len(payload) != 4:
        raise ValueError("ZigBee payload must be exactly 4 bytes")
    attr_id = payload[0:2]
    if attr_id != bytes.fromhex("0000"):
        raise ValueError("Unexpected ZigBee attribute id")
    centi = int.from_bytes(payload[2:4], byteorder="little", signed=True)
    return centi / 100.0


def decode_ble_temperature_centi_c_le(payload_hex: str) -> float:
    payload = bytes.fromhex(payload_hex)
    if len(payload) != 2:
        raise ValueError("BLE payload must be exactly 2 bytes")
    centi = int.from_bytes(payload, byteorder="little", signed=True)
    return centi / 100.0


CODECS = {
    "decode_lorawan_temperature_centi_c_be": decode_lorawan_temperature_centi_c_be,
    "decode_zigbee_temperature_zcl_attr_le": decode_zigbee_temperature_zcl_attr_le,
    "decode_ble_temperature_centi_c_le": decode_ble_temperature_centi_c_le,
}


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_source_cards(cards_dir: Path) -> Dict[str, Dict[str, Any]]:
    cards: Dict[str, Dict[str, Any]] = {}
    for card_path in sorted(cards_dir.glob("*.json")):
        card = load_json(card_path)
        cards[card["source_card_id"]] = card
    if not cards:
        raise RuntimeError(f"No source cards found in {cards_dir}")
    return cards


def decode_by_source_card(card: Dict[str, Any], payload_hex: str) -> float:
    codec_fn = CODECS[card["codec"]]
    return codec_fn(payload_hex)


# --------------------------------------------------------------------------
# Frame model
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Frame:
    frame_id: int
    protocol: str
    source_card_id: str
    stable_key: str
    channel: str
    traffic_type: str
    payload_hex: str
    expected_result: str
    inject_missing_field: bool = False
    inject_bad_datatype: bool = False
    inject_bad_unit: bool = False


@dataclass(frozen=True)
class ProcessingResult:
    actual_result: str
    rejection_reason: str
    canonical_device: str
    latency_ns: int
    lookup_ns: int
    identity_ns: int
    decode_ns: int
    normalize_ns: int
    gate_ns: int
    publication_decision_ns: int
    decoded_value_celsius: Optional[float]
    published_to_uns: bool
    output_topic: str


# --------------------------------------------------------------------------
# Canonical identity resolution + target contract registry
# --------------------------------------------------------------------------

def resolve_canonical_device(registry: Dict[str, Any], protocol: str, stable_key: str) -> Optional[str]:
    links = registry["identity_links"].get(protocol, {})
    return links.get(stable_key)


def target_contract_for(registry: Dict[str, Any], d: str) -> Optional[Dict[str, Any]]:
    return registry["target_contracts"].get(d)


R_QU_COMPATIBLE = {
    ("temperature", "Cel"),
}


def process_frame(frame: Frame, source_cards: Dict[str, Dict[str, Any]], registry: Dict[str, Any],
                   gate_enabled: bool) -> ProcessingResult:
    t_start = time.perf_counter_ns()

    # --- Selector: source-card lookup (by protocol/profile) ---
    t0 = time.perf_counter_ns()
    card = source_cards.get(frame.source_card_id)
    t1 = time.perf_counter_ns()
    lookup_ns = t1 - t0

    if card is None:
        t_end = time.perf_counter_ns()
        return ProcessingResult("reject", "unknown_source_card", "", t_end - t_start,
                                 lookup_ns, 0, 0, 0, 0, 0, None, False, "")

    # --- Selector: canonical-identity resolution (independent of decode) ---
    t2 = time.perf_counter_ns()
    canonical_device = resolve_canonical_device(registry, frame.protocol, frame.stable_key)
    t3 = time.perf_counter_ns()
    identity_ns = t3 - t2

    if canonical_device is None:
        t_end = time.perf_counter_ns()
        return ProcessingResult("reject", "unresolved_identity", "", t_end - t_start,
                                 lookup_ns, identity_ns, 0, 0, 0, 0, None, False, "")

    # --- N (part 1): decode raw payload via the source card's codec ---
    t4 = time.perf_counter_ns()
    try:
        decoded_value = decode_by_source_card(card, frame.payload_hex)
    except Exception:
        t5 = time.perf_counter_ns()
        t_end = time.perf_counter_ns()
        return ProcessingResult("reject", "malformed_payload", canonical_device, t_end - t_start,
                                 lookup_ns, identity_ns, t5 - t4, 0, 0, 0, None, False, "")
    t5 = time.perf_counter_ns()
    decode_ns = t5 - t4

    # --- N (part 2): look up target contract, map + unit-convert + project ---
    # (target-contract lookup belongs here, not in the selector/identity phase above:
    # it is N's sub-step 3, Section 6.1, and must be inside this timed bracket so the
    # six reported stages sum exhaustively to the per-frame latency -- an earlier
    # version of this script looked it up between identity resolution and decode,
    # outside any timed bracket, which silently dropped its cost from every reported
    # stage mean.)
    t6 = time.perf_counter_ns()
    target = target_contract_for(registry, canonical_device)
    if target is None:
        t7 = time.perf_counter_ns()
        t_end = time.perf_counter_ns()
        return ProcessingResult("reject", "no_target_contract", canonical_device, t_end - t_start,
                                 lookup_ns, identity_ns, decode_ns, t7 - t6, 0, 0, None, False, "")
    q = card["measurement_type"] if not frame.inject_missing_field else None
    a = frame.channel
    w = decoded_value if not frame.inject_bad_datatype else float("nan")
    u = target["unit"] if not frame.inject_bad_unit else "Pa"
    candidate = {
        "d": canonical_device,
        "a": a,
        "q": q,
        "w": w,
        "u": u,
        "t_e": time.time(),
        "kappa_fam": target["schema_id"],
        "nu": target["version"],
    }
    t7 = time.perf_counter_ns()
    normalize_ns = t7 - t6

    # --- G: Contract Gate -- full C^valid_sigma check on the candidate ---
    t8 = time.perf_counter_ns()
    if not gate_enabled:
        gate_reason = None
    else:
        gate_reason = None
        if candidate["d"] is None or candidate["a"] is None or candidate["q"] is None \
                or candidate["w"] is None or candidate["u"] is None:
            gate_reason = "missing_field"
        elif not isinstance(candidate["w"], (int, float)) or math.isnan(candidate["w"]) \
                or math.isinf(candidate["w"]):
            gate_reason = "wrong_datatype"
        elif (candidate["q"], candidate["u"]) not in R_QU_COMPATIBLE:
            gate_reason = "incompatible_unit_concept"
        else:
            low, high = float(target["valid_range"][0]), float(target["valid_range"][1])
            if not (low <= candidate["w"] <= high):
                gate_reason = "out_of_range"
    t9 = time.perf_counter_ns()
    gate_ns = t9 - t8

    if gate_reason is not None:
        t_end = time.perf_counter_ns()
        return ProcessingResult("reject", gate_reason, canonical_device, t_end - t_start,
                                 lookup_ns, identity_ns, decode_ns, normalize_ns, gate_ns,
                                 0, decoded_value, False, target["topic"])

    # --- Publication decision ---
    t10 = time.perf_counter_ns()
    published_to_uns = True
    output_topic = target["topic"]
    t11 = time.perf_counter_ns()
    publication_decision_ns = t11 - t10

    t_end = time.perf_counter_ns()
    return ProcessingResult("accept", "", canonical_device, t_end - t_start,
                             lookup_ns, identity_ns, decode_ns, normalize_ns, gate_ns,
                             publication_decision_ns, decoded_value, published_to_uns, output_topic)


# --------------------------------------------------------------------------
# Timer calibration
# --------------------------------------------------------------------------

def calibrate_timer_overhead(iterations: int = 100_000) -> Dict[str, float]:
    """Two floors, not one: a bracket with literally nothing inside it (the
    true minimum any two perf_counter_ns() calls can measure), and a bracket
    around a single dict.get() (the cheapest real operation this script's
    stages perform), since a prior version of this calibration wrapped a
    Python function call -- itself slower than a dict.get() -- and so
    reported a "floor" higher than some genuinely-measured stage means,
    which is not possible for a true floor."""
    d = {"k": 1}

    t_start = time.perf_counter_ns()
    for _ in range(iterations):
        t0 = time.perf_counter_ns()
        t1 = time.perf_counter_ns()
        _ = t1 - t0
    bare_floor_ns = (time.perf_counter_ns() - t_start) / iterations

    t_start = time.perf_counter_ns()
    for _ in range(iterations):
        t0 = time.perf_counter_ns()
        _ = d.get("k")
        t1 = time.perf_counter_ns()
        _ = t1 - t0
    dict_get_floor_ns = (time.perf_counter_ns() - t_start) / iterations

    return {"bare_ns_per_call": bare_floor_ns, "dict_get_ns_per_call": dict_get_floor_ns}


# --------------------------------------------------------------------------
# Traffic generation
# --------------------------------------------------------------------------

def make_malformed_payload(protocol: str, rng: random.Random) -> str:
    options = {
        "LoRaWAN": ["", "ff", "zzzz", "010203"],
        "ZigBee": ["", "0000", "0100ffff", "zzzzzzzz", "000001"],
        "BLE": ["", "aa", "zzzz", "010203"],
    }
    return rng.choice(options[protocol])


def weighted_traffic_types(total: int, traffic_mix: Dict[str, float]) -> List[str]:
    counts = {k: int(round(v * total)) for k, v in traffic_mix.items()}
    drift = total - sum(counts.values())
    if drift != 0:
        counts["valid"] = counts.get("valid", 0) + drift
    out: List[str] = []
    for name, count in counts.items():
        out.extend([name] * count)
    return out


def generate_bulk_frames(config: Dict[str, Any], registry: Dict[str, Any], start_id: int) -> List[Frame]:
    rng = random.Random(int(config.get("seed", 42)))
    total = int(config["total_frames"])
    traffic_types = weighted_traffic_types(total, config["traffic_mix"])
    rng.shuffle(traffic_types)

    valid_min = float(config["valid_temperature_generation_range_celsius"]["min"])
    valid_max = float(config["valid_temperature_generation_range_celsius"]["max"])
    out_values = [float(v) for v in config["out_of_range_temperature_values_celsius"]]
    instances = config["bulk_device_instances"]  # list of {protocol, source_card_id, stable_key, channel}

    frames: List[Frame] = []
    for i, traffic_type in enumerate(traffic_types):
        frame_id = start_id + i
        inst = rng.choice(instances)
        protocol = inst["protocol"]
        source_card_id = inst["source_card_id"]
        stable_key = inst["stable_key"]
        channel = inst["channel"]
        inject_missing = inject_dtype = inject_unit = False

        if traffic_type == "valid":
            temp_c = rng.uniform(valid_min, valid_max)
            payload_hex = encode_payload(protocol, temp_c)
            expected_result = "accept"
        elif traffic_type == "unknown_source_card":
            temp_c = rng.uniform(valid_min, valid_max)
            payload_hex = encode_payload(protocol, temp_c)
            source_card_id = f"unregistered_{protocol.lower()}"
            expected_result = "reject"
        elif traffic_type == "unresolved_identity":
            temp_c = rng.uniform(valid_min, valid_max)
            payload_hex = encode_payload(protocol, temp_c)
            stable_key = f"UNLINKED-{protocol}-{frame_id}"
            expected_result = "reject"
        elif traffic_type == "malformed_payload":
            payload_hex = make_malformed_payload(protocol, rng)
            expected_result = "reject"
        elif traffic_type == "missing_field":
            temp_c = rng.uniform(valid_min, valid_max)
            payload_hex = encode_payload(protocol, temp_c)
            inject_missing = True
            expected_result = "reject"
        elif traffic_type == "wrong_datatype":
            temp_c = rng.uniform(valid_min, valid_max)
            payload_hex = encode_payload(protocol, temp_c)
            inject_dtype = True
            expected_result = "reject"
        elif traffic_type == "incompatible_unit_concept":
            temp_c = rng.uniform(valid_min, valid_max)
            payload_hex = encode_payload(protocol, temp_c)
            inject_unit = True
            expected_result = "reject"
        elif traffic_type == "out_of_range":
            temp_c = rng.choice(out_values)
            payload_hex = encode_payload(protocol, temp_c)
            expected_result = "reject"
        else:
            raise ValueError(f"Unsupported traffic type: {traffic_type}")

        frames.append(Frame(frame_id, protocol, source_card_id, stable_key, channel,
                             traffic_type, payload_hex, expected_result,
                             inject_missing, inject_dtype, inject_unit))
    return frames


def generate_invariance_frames(config: Dict[str, Any], start_id: int) -> List[Frame]:
    """The Proposition-1 micro-experiment: one canonical device observed via
    three different protocols/source cards/stable keys, cycled evenly."""
    rng = random.Random(int(config.get("seed", 42)) + 1)
    per_protocol = int(config["invariance_frames_per_protocol"])
    valid_min = float(config["valid_temperature_generation_range_celsius"]["min"])
    valid_max = float(config["valid_temperature_generation_range_celsius"]["max"])
    bindings = config["invariance_bindings"]  # list of {protocol, source_card_id, stable_key, channel}

    frames: List[Frame] = []
    frame_id = start_id
    for binding in bindings:
        for _ in range(per_protocol):
            temp_c = rng.uniform(valid_min, valid_max)
            payload_hex = encode_payload(binding["protocol"], temp_c)
            frames.append(Frame(frame_id, binding["protocol"], binding["source_card_id"],
                                 binding["stable_key"], binding["channel"],
                                 "invariance_probe", payload_hex, "accept"))
            frame_id += 1
    return frames


# --------------------------------------------------------------------------
# Summaries
# --------------------------------------------------------------------------

def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    return float(np.percentile(np.array(values, dtype=float), p))


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows)
    accepted = sum(1 for r in rows if r["actual_result"] == "accept")
    rejected = total - accepted
    false_accept = sum(1 for r in rows if r["expected_result"] == "reject" and r["actual_result"] == "accept")
    false_reject = sum(1 for r in rows if r["expected_result"] == "accept" and r["actual_result"] == "reject")
    published_invalid = sum(1 for r in rows if r["expected_result"] == "reject" and r["published_to_uns"] == "true")

    lat_ms = [float(r["latency_ns"]) / 1_000_000.0 for r in rows]
    accepted_lat_ms = [float(r["latency_ns"]) / 1_000_000.0 for r in rows if r["actual_result"] == "accept"]

    rejection_reasons: Dict[str, int] = {}
    for r in rows:
        reason = str(r["rejection_reason"])
        if reason:
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

    stage_keys = ["lookup_ns", "identity_ns", "decode_ns", "normalize_ns", "gate_ns", "publication_decision_ns"]
    accepted_rows = [r for r in rows if r["actual_result"] == "accept"]
    stage_means_ns = {
        k: (statistics.fmean(float(r[k]) for r in accepted_rows) if accepted_rows else 0.0)
        for k in stage_keys
    }

    return {
        "total_frames": total,
        "accepted": accepted,
        "rejected": rejected,
        "false_accept": false_accept,
        "false_reject": false_reject,
        "published_invalid": published_invalid,
        "rejection_reasons": rejection_reasons,
        "latency_ms": {
            "mean": statistics.fmean(lat_ms) if lat_ms else 0.0,
            "p50": percentile(lat_ms, 50),
            "p95": percentile(lat_ms, 95),
            "p99": percentile(lat_ms, 99),
            "std": statistics.pstdev(lat_ms) if len(lat_ms) > 1 else 0.0,
        },
        "accepted_latency_ms": {
            "mean": statistics.fmean(accepted_lat_ms) if accepted_lat_ms else 0.0,
            "p50": percentile(accepted_lat_ms, 50),
            "p95": percentile(accepted_lat_ms, 95),
            "p99": percentile(accepted_lat_ms, 99),
        },
        "stage_means_ns": stage_means_ns,
    }


def check_invariance(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    probe_rows = [r for r in rows if r["traffic_type"] == "invariance_probe"]
    schemas = set()
    topics = set()
    devices = set()
    protocols_seen = set()
    for r in probe_rows:
        devices.add(r["canonical_device"])
        topics.add(r["output_topic"])
        protocols_seen.add(r["protocol"])
    all_accepted = all(r["actual_result"] == "accept" for r in probe_rows) if probe_rows else False
    return {
        "n_probe_frames": len(probe_rows),
        "protocols_exercised": sorted(protocols_seen),
        "distinct_canonical_devices": sorted(devices),
        "distinct_output_topics": sorted(topics),
        "all_accepted": all_accepted,
        "single_device_single_topic": len(devices) == 1 and len(topics) == 1,
    }


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["frame_id", "protocol", "source_card_id", "stable_key", "canonical_device",
                  "traffic_type", "expected_result", "actual_result", "rejection_reason",
                  "decoded_value_celsius", "latency_ns", "lookup_ns", "identity_ns", "decode_ns",
                  "normalize_ns", "gate_ns", "publication_decision_ns", "published_to_uns", "output_topic"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run(config_path: Path, gate_enabled: bool, out_prefix: str) -> None:
    config = load_json(config_path)
    base_dir = config_path.parent
    source_cards = load_source_cards(base_dir / str(config["source_cards_dir"]))
    registry = load_json(base_dir / str(config["canonical_registry"]))
    results_dir = base_dir / str(config["results_dir"])

    timer_floors = calibrate_timer_overhead(int(config.get("calibration_iterations", 100_000)))

    bulk_frames = generate_bulk_frames(config, registry, start_id=1)
    invariance_frames = generate_invariance_frames(config, start_id=len(bulk_frames) + 1)
    frames = bulk_frames + invariance_frames

    rows: List[Dict[str, Any]] = []
    for frame in frames:
        result = process_frame(frame, source_cards, registry, gate_enabled)
        rows.append({
            "frame_id": frame.frame_id,
            "protocol": frame.protocol,
            "source_card_id": frame.source_card_id,
            "stable_key": frame.stable_key,
            "canonical_device": result.canonical_device,
            "traffic_type": frame.traffic_type,
            "expected_result": frame.expected_result,
            "actual_result": result.actual_result,
            "rejection_reason": result.rejection_reason,
            "decoded_value_celsius": "" if result.decoded_value_celsius is None
                                      else f"{result.decoded_value_celsius:.2f}",
            "latency_ns": result.latency_ns,
            "lookup_ns": result.lookup_ns,
            "identity_ns": result.identity_ns,
            "decode_ns": result.decode_ns,
            "normalize_ns": result.normalize_ns,
            "gate_ns": result.gate_ns,
            "publication_decision_ns": result.publication_decision_ns,
            "published_to_uns": "true" if result.published_to_uns else "false",
            "output_topic": result.output_topic,
        })

    bulk_rows = rows[:len(bulk_frames)]
    summary = summarize(bulk_rows)
    summary["timer_floors_ns_per_call"] = timer_floors
    summary["gate_enabled"] = gate_enabled
    summary["invariance_check"] = check_invariance(rows)

    results_dir.mkdir(parents=True, exist_ok=True)
    write_csv(results_dir / f"{out_prefix}_results.csv", rows)
    with (results_dir / f"{out_prefix}_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f">>> {out_prefix}: bulk={len(bulk_frames)} invariance_probe={len(invariance_frames)} "
          f"gate_enabled={gate_enabled}")
    print(f"Timer floors: bare={timer_floors['bare_ns_per_call']:.1f} ns/call, "
          f"dict.get={timer_floors['dict_get_ns_per_call']:.1f} ns/call")
    print(f"Bulk: accepted={summary['accepted']} rejected={summary['rejected']} "
          f"false_accept={summary['false_accept']} false_reject={summary['false_reject']} "
          f"published_invalid={summary['published_invalid']}")
    print(f"Rejection reasons: {summary['rejection_reasons']}")
    print(f"Latency ms: mean={summary['latency_ms']['mean']:.6f} p50={summary['latency_ms']['p50']:.6f} "
          f"p95={summary['latency_ms']['p95']:.6f} p99={summary['latency_ms']['p99']:.6f}")
    print(f"Stage means (ns, accepted frames): {summary['stage_means_ns']}")
    print(f"Invariance check: {summary['invariance_check']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Experiment 3 (revised): source-card/target-contract filtering")
    parser.add_argument("--config", type=Path, default=Path("experiment_config_v2.json"))
    parser.add_argument("--gate-disabled", action="store_true", help="Run the Gate-disabled ablation")
    parser.add_argument("--out-prefix", type=str, default="v2")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(args.config.resolve(), gate_enabled=not args.gate_disabled, out_prefix=args.out_prefix)


if __name__ == "__main__":
    main()

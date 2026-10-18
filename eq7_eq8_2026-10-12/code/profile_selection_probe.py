#!/usr/bin/env python3
"""
Profile-selection probe (EQ7).

Part 1 (FPort collision): two LoRaWAN device profiles uplink on FPort 2 with
different payload layouts. Can the Source Card selector be keyed on FPort alone?
FPort 1-223 is application-specific, so two device types may share FPort 2.
  legacy_fport   Sigma keyed on (protocol, FPort): registering two profiles on the
                 same FPort silently overwrites one. Both registration orders.
  bound_profile  Sigma keyed on (protocol, profile_id); profile_id comes from a
                 provisioned binding B_pi keyed by (stable key, endpoint).

Part 2 (one device, two profiles): one stable key sends temperature on FPort 2 and
relative humidity on FPort 3 (two Source Cards, one device).
  key_only       B_pi keyed by the stable key alone: one profile per key.
  key_endpoint   B_pi keyed by (stable key, endpoint).

Profiles:
  lora_int16_c100     int16 big-endian, centi-degrees Celsius      (temperature)
  lora_uint16_off40   uint16 big-endian, raw/100 - 40 degrees C    (temperature)
  lora_uint16_rh100   uint16 big-endian, raw/100 percent RH        (humidity)
A temperature payload decoded with the wrong temperature layout, or a humidity
payload decoded as temperature, usually stays inside the declared range, so the
Contract Gate cannot detect the misdecoding.

Also checks provisioning-time validation of the binding table (a dangling profile
id, or a card whose concept differs from the contract of the channel it feeds).

Deterministic (no randomness). Standard library only.
Usage: python profile_selection_probe.py [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Callable, Dict, List, Optional, Tuple

TEMP, RH = "saref:Temperature", "saref:Humidity"

CARDS: Dict[str, dict] = {
    "lora_int16_c100": {
        "decode": lambda b: int.from_bytes(b, "big", signed=True) / 100.0,
        "encode": lambda t: int(round(t * 100)).to_bytes(2, "big", signed=True),
        "concept": TEMP},
    "lora_uint16_off40": {
        "decode": lambda b: int.from_bytes(b, "big") / 100.0 - 40.0,
        "encode": lambda t: int(round((t + 40.0) * 100)).to_bytes(2, "big"),
        "concept": TEMP},
    "lora_uint16_rh100": {
        "decode": lambda b: int.from_bytes(b, "big") / 100.0,
        "encode": lambda t: int(round(t * 100)).to_bytes(2, "big"),
        "concept": RH},
}
RANGE = {TEMP: (-40.0, 85.0), RH: (0.0, 100.0)}

# Part 1 devices (all uplink on FPort 2). stable key -> true profile.
FLEET = {"DEVEUI-A1": "lora_int16_c100", "DEVEUI-A2": "lora_int16_c100",
         "DEVEUI-B1": "lora_uint16_off40", "DEVEUI-B2": "lora_uint16_off40"}
# Part 2 device: endpoint -> true profile (one stable key).
MULTI_KEY = "DEVEUI-C1"
MULTI = {2: "lora_int16_c100", 3: "lora_uint16_rh100"}

# Provisioned binding B_pi keyed by (stable key, endpoint).
BINDING = {("LoRaWAN", k, 2): v for k, v in FLEET.items()}
BINDING.update({("LoRaWAN", MULTI_KEY, ep): v for ep, v in MULTI.items()})

TEMPS = [-10.0 + 0.5 * i for i in range(101)]      # -10 .. 40 C
RHS = [0.5 * i for i in range(101)]                # 0 .. 50 % RH
Frame = Tuple[str, int, bytes, float]              # (key, endpoint, payload, expected)


def part1_frames() -> List[Frame]:
    return [(k, 2, CARDS[p]["encode"](t), t) for k, p in FLEET.items() for t in TEMPS]


def part2_frames() -> List[Frame]:
    out = []
    for ep, p in MULTI.items():
        vals = TEMPS if CARDS[p]["concept"] == TEMP else RHS
        out += [(MULTI_KEY, ep, CARDS[p]["encode"](v), v) for v in vals]
    return out


# ------------------------------- selectors ----------------------------------
def make_legacy(order: List[str]) -> Callable[[str, int], Optional[str]]:
    table: Dict[Tuple[str, int], str] = {}
    for prof in order:
        table[("LoRaWAN", 2)] = prof        # silent overwrite on a key collision
    return lambda key, ep: table.get(("LoRaWAN", ep))


def bound_endpoint(key: str, ep: int) -> Optional[str]:
    return BINDING.get(("LoRaWAN", key, ep))


def key_only(key: str, ep: int) -> Optional[str]:
    """One profile per stable key: the first binding registered for the key wins."""
    for (p, k, e), prof in BINDING.items():
        if k == key:
            return prof
    return None


# ----------------------------- pipeline + Gate -------------------------------
def run(selector: Callable[[str, int], Optional[str]], fr: List[Frame]) -> dict:
    accepted = correct = silent_wrong = rejected = 0
    for key, ep, payload, expected in fr:
        prof = selector(key, ep)
        if prof is None or prof not in CARDS:
            rejected += 1
            continue
        w = CARDS[prof]["decode"](payload)
        lo, hi = RANGE[CARDS[prof]["concept"]]
        if not (lo <= w <= hi):
            rejected += 1
            continue
        accepted += 1
        if abs(w - expected) < 1e-6 and prof == (FLEET.get(key) or MULTI[ep]):
            correct += 1
        else:
            silent_wrong += 1
    return {"frames": len(fr), "accepted": accepted, "correct": correct,
            "silent_wrong": silent_wrong, "rejected": rejected}


# ------------------- provisioning-time validation of B_pi --------------------
def validate_provisioning(binding: Dict[Tuple[str, str, int], str],
                          cards: Dict[str, dict],
                          contract_concept: Dict[Tuple[str, int], str]) -> List[str]:
    errors = []
    for (proto, key, ep), prof in sorted(binding.items()):
        if prof not in cards:
            errors.append(f"{key}/{ep}: profile '{prof}' not registered")
            continue
        want = contract_concept.get((key, ep))
        if want is not None and cards[prof]["concept"] != want:
            errors.append(f"{key}/{ep}: card concept {cards[prof]['concept']} != contract {want}")
    return errors


def provisioning_checks() -> dict:
    contracts = {(k, 2): TEMP for k in FLEET}
    contracts.update({(MULTI_KEY, 2): TEMP, (MULTI_KEY, 3): RH})
    dangling = dict(BINDING)
    dangling[("LoRaWAN", "DEVEUI-A1", 2)] = "lora_typo_profile"
    wrong_cards = dict(CARDS)
    wrong_cards["lora_uint16_off40"] = dict(CARDS["lora_uint16_off40"], concept=RH)
    return {
        "clean_binding_errors": len(validate_provisioning(BINDING, CARDS, contracts)),
        "dangling_profile_errors": len(validate_provisioning(dangling, CARDS, contracts)),
        "concept_mismatch_errors": len(validate_provisioning(BINDING, wrong_cards, contracts)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", help="write full results to this path")
    args = ap.parse_args()

    p1, p2 = part1_frames(), part2_frames()
    results = {
        "part1_legacy_fport_A_then_B": run(make_legacy(["lora_int16_c100", "lora_uint16_off40"]), p1),
        "part1_legacy_fport_B_then_A": run(make_legacy(["lora_uint16_off40", "lora_int16_c100"]), p1),
        "part1_bound_profile": run(bound_endpoint, p1),
        "part2_key_only": run(key_only, p2),
        "part2_key_endpoint": run(bound_endpoint, p2),
        "provisioning": provisioning_checks(),
    }
    print(f"{'selector':30s} {'frames':>6s} {'accepted':>8s} {'correct':>7s} "
          f"{'silent_wrong':>12s} {'rejected':>8s}")
    for name, r in results.items():
        if name == "provisioning":
            continue
        print(f"{name:30s} {r['frames']:6d} {r['accepted']:8d} {r['correct']:7d} "
              f"{r['silent_wrong']:12d} {r['rejected']:8d}")
    print("provisioning:", json.dumps(results["provisioning"]))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print("wrote", args.json)

    b1, b2 = results["part1_bound_profile"], results["part2_key_endpoint"]
    ok = (b1["silent_wrong"] == 0 and b1["correct"] == b1["frames"]
          and b2["silent_wrong"] == 0 and b2["correct"] == b2["frames"]
          and all(results[k]["silent_wrong"] > 0 for k in
                  ("part1_legacy_fport_A_then_B", "part1_legacy_fport_B_then_A", "part2_key_only"))
          and results["provisioning"] == {"clean_binding_errors": 0,
                                          "dangling_profile_errors": 1,
                                          "concept_mismatch_errors": 2})
    if not ok:
        print("UNEXPECTED RESULT", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

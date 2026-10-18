#!/usr/bin/env python3
"""
Baseline comparison (EQ8): Source Card / Target Contract / Gate pipeline (O)
versus conventional gateways built without the card structure.

  C   conventional: per-profile handler *code* (decode + hard-coded unit
      conversion), a device table (stable key + endpoint -> handler, topic,
      expected concept/unit), and a range check. Concept and unit are implicit.
  C+  functionally equivalent baseline. Same data as C, plus the SAME checks as
      O, written as a configuration validator over a different organisation of
      the data:
        * handlers carry declared metadata (concept, output unit),
        * an inventory lists the provisioned endpoints,
        * the validator checks: handler references exist, every inventoried
          endpoint has a table row, handler metadata equals the table's expected
          concept/unit, topics are unique;
        * at run time the handler tags are compared with the table expectation.
  O   this paper's pipeline: profile binding B_pi, identity I, channel binding J,
      Source Cards (declared concept/unit), Target Contracts, Gate, and a
      provisioning validator over the declared fields.

Outputs:
  1. Fault matrix: six human mistakes, each injected in the form it takes in each
     system's artifacts. The workload is run with the faulty configuration
     ACTIVATED REGARDLESS of the validator result (the validator reports, it does
     not block). `wrong_if_gated` is the number of wrong publications if a
     blocking gate refused to activate a configuration with validator errors.
  2. Change scope: artifacts modified in four scenarios, with the list of changed
     fields; the canonical-unit migration (S4) is executed and verified on BOTH
     O and C+.
  3. Per-frame cost of the three pipelines (batch timing, one timer pair around a
     loop over all frames; no inner timers): n batches, mean, SD, median per
     invocation. Standalone models, not Benchmark B.

Deterministic, standard library only.
Usage: python baseline_comparison.py [--json out.json] [--repeats N] [--loops N]
"""

from __future__ import annotations

import argparse
import copy
import gc
import json
import statistics
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

TOL = 0.05
RANGE = {"Cel": (-40.0, 85.0), "K": (233.15, 358.15)}
CONCEPT = "saref:Temperature"
TEMPS = [-10.0 + i for i in range(51)]  # -10 .. 40 C, step 1


def _i16(b, order): return int.from_bytes(b, order, signed=True)
def _u16(b, order): return int.from_bytes(b, order, signed=False)


CODECS = {
    "x": (lambda b: _i16(b, "big") / 100.0,
          lambda c: int(round(c * 100)).to_bytes(2, "big", signed=True)),
    "y": (lambda b: _u16(b, "big") / 100.0 - 40.0,
          lambda c: int(round((c + 40) * 100)).to_bytes(2, "big")),
    "z": (lambda b: _i16(b, "little") / 100.0,
          lambda c: int(round(c * 100)).to_bytes(2, "little", signed=True)),
    "k": (lambda b: _u16(b, "big") / 100.0,
          lambda c: int(round((c + 273.15) * 100)).to_bytes(2, "big")),
    "f": (lambda b: _i16(b, "big") / 10.0,
          lambda c: int(round((c * 9 / 5 + 32) * 10)).to_bytes(2, "big", signed=True)),
}
PROFILE_CARDS = {   # profile id -> (codec, source concept, source unit)
    "px": ("x", CONCEPT, "Cel"), "py": ("y", CONCEPT, "Cel"), "pz": ("z", CONCEPT, "Cel"),
    "pk": ("k", CONCEPT, "K"), "pf": ("f", CONCEPT, "degF"),
}
UNIT_PARAMS = {"K": (1.0, 0.0), "Cel": (1.0, 273.15), "degF": (5 / 9, 459.67 * 5 / 9)}


def convert(w: float, us: str, ut: str) -> float:
    ms, bs = UNIT_PARAMS[us]
    mt, bt = UNIT_PARAMS[ut]
    return (ms * w + bs - bt) / mt


def mk_handler(pid: str, target: str):
    """C-style handler: decode AND convert to the target unit, in code."""
    codec, _, unit = PROFILE_CARDS[pid]
    dec = CODECS[codec][0]
    return lambda b: convert(dec(b), unit, target)


# endpoint spec: (proto, key, d, profile, endpoint, channel)
BASE_SPEC = [
    ("LoRaWAN", "DEVEUI-A1", "dev_A1", "px", 2, "ch0"),
    ("LoRaWAN", "DEVEUI-A1", "dev_A1", "px", 5, "ch1"),
    ("LoRaWAN", "DEVEUI-B1", "dev_B1", "py", 2, "ch0"),
    ("ZigBee", "EUI64-Z1", "dev_Z1", "pz", 1, "ch0"),
    ("LoRaWAN", "DEVEUI-F1", "dev_F1", "pf", 2, "ch0"),
]


def topic(d: str, a: str) -> str:
    return f"uns/site/{d}/{a}/temperature"


def world_O(spec, unit: str = "Cel") -> dict:
    w = {"cards": {}, "binding": {}, "identity": {}, "channels": {}, "contracts": {},
         "alignment": {CONCEPT: CONCEPT}, "r_qu": {(CONCEPT, unit)}}
    for proto, key, d, pid, ep, a in spec:
        codec, concept, su = PROFILE_CARDS[pid]
        w["cards"][pid] = {"codec": codec, "concept": concept, "unit": su}
        w["binding"][(proto, key, ep)] = pid          # B_pi: (key, endpoint) -> profile
        w["identity"][(proto, key)] = d
        w["channels"][(d, pid, ep)] = a
        w["contracts"][(d, a)] = {"concept": CONCEPT, "unit": unit, "range": RANGE[unit],
                                  "topic": topic(d, a)}
    return w


def world_C(spec, unit: str = "Cel") -> dict:
    w = {"handlers": {}, "meta": {}, "table": {}, "inventory": set(), "range": {"v": RANGE[unit]}}
    for proto, key, d, pid, ep, a in spec:
        w["handlers"][pid] = mk_handler(pid, unit)
        w["meta"][pid] = (CONCEPT, unit)
        w["table"][(proto, key, ep)] = (pid, topic(d, a), CONCEPT, unit)
        w["inventory"].add((proto, key, ep))
    return w


def frames(spec, offset_k: bool = False) -> List[tuple]:
    out = []
    for proto, key, d, pid, ep, a in spec:
        enc = CODECS[PROFILE_CARDS[pid][0]][1]
        for t in TEMPS:
            out.append((proto, key, ep, enc(t), t + (273.15 if offset_k else 0.0), topic(d, a)))
    return out


# -------------------------------- pipelines ----------------------------------
def proc_C(f, w, plus: bool) -> Optional[Tuple[str, float]]:
    proto, key, ep, payload = f[0], f[1], f[2], f[3]
    row = w["table"].get((proto, key, ep))
    if row is None:
        return None
    h = w["handlers"].get(row[0])
    if h is None:
        return None
    try:
        v = h(payload)
    except Exception:
        return None
    if plus:
        m = w["meta"].get(row[0])
        if m is None or m[0] != row[2] or m[1] != row[3]:
            return None
    lo, hi = w["range"]["v"]
    if not (lo <= v <= hi):
        return None
    return row[1], v


def proc_O(f, w) -> Optional[Tuple[str, float]]:
    proto, key, ep, payload = f[0], f[1], f[2], f[3]
    pid = w["binding"].get((proto, key, ep))
    card = w["cards"].get(pid)
    if card is None:
        return None
    d = w["identity"].get((proto, key))
    if d is None:
        return None
    a = w["channels"].get((d, pid, ep))
    if a is None:
        return None
    tau = w["contracts"].get((d, a))
    if tau is None:
        return None
    try:
        v = CODECS[card["codec"]][0](payload)
    except Exception:
        return None
    q = w["alignment"].get(card["concept"])
    if q is None:
        return None
    if card["unit"] not in UNIT_PARAMS or tau["unit"] not in UNIT_PARAMS:
        return None
    v = convert(v, card["unit"], tau["unit"])
    if q != tau["concept"] or (q, tau["unit"]) not in w["r_qu"]:
        return None
    if not (tau["range"][0] <= v <= tau["range"][1]):
        return None
    return tau["topic"], v


# ------------------------- provisioning-time validators -----------------------
def validate_Cplus(w) -> List[str]:
    errs, seen = [], {}
    for ep in sorted(w["inventory"], key=str):
        if ep not in w["table"]:
            errs.append(f"{ep}: inventoried endpoint has no table row")
    for k, (pid, tp, qe, ue) in sorted(w["table"].items(), key=lambda x: str(x[0])):
        if pid not in w["handlers"]:
            errs.append(f"{k}: handler '{pid}' missing")
        else:
            m = w["meta"].get(pid)
            if m is None or m != (qe, ue):
                errs.append(f"{k}: handler metadata {m} != expected {(qe, ue)}")
        if tp in seen:
            errs.append(f"{k}: topic {tp} duplicates {seen[tp]}")
        seen[tp] = k
    return errs


def validate_O(w) -> List[str]:
    errs, seen = [], {}
    for (proto, key, ep), pid in sorted(w["binding"].items(), key=lambda x: str(x[0])):
        if pid not in w["cards"]:
            errs.append(f"{key}/{ep}: profile '{pid}' not registered")
            continue
        card = w["cards"][pid]
        if (proto, key) not in w["identity"]:
            errs.append(f"{key}: no identity link")
            continue
        d = w["identity"][(proto, key)]
        a = w["channels"].get((d, pid, ep))
        if a is None:
            errs.append(f"{key}/{ep}: no channel binding for profile {pid}")
            continue
        qc = w["alignment"].get(card["concept"])
        if qc is None:
            errs.append(f"{pid}: concept {card['concept']} has no registered alignment")
        if card["unit"] not in UNIT_PARAMS:
            errs.append(f"{pid}: unit {card['unit']} has no conversion parameters")
        tau = w["contracts"].get((d, a))
        if tau is None:
            errs.append(f"{d}/{a}: binding without contract")
        elif qc is not None and (qc != tau["concept"] or (qc, tau["unit"]) not in w["r_qu"]):
            errs.append(f"{d}/{a}: card {pid} incompatible with contract")
    for (d, a), tau in sorted(w["contracts"].items()):
        if tau["topic"] in seen:
            errs.append(f"{d}/{a}: topic duplicates {seen[tau['topic']]}")
        seen[tau["topic"]] = f"{d}/{a}"
    return errs


# ------------------------------- fault injection ------------------------------
def inject(fault: str, system: str, w: dict) -> dict:
    w = copy.copy(w)
    for k, v in w.items():
        w[k] = copy.copy(v)
    O = system == "O"
    if fault == "M1_duplicate_topic":          # copy-paste: A1/ch1 reuses ch0's topic
        if O:
            t = dict(w["contracts"][("dev_A1", "ch1")]); t["topic"] = topic("dev_A1", "ch0")
            w["contracts"][("dev_A1", "ch1")] = t
        else:
            r = w["table"][("LoRaWAN", "DEVEUI-A1", 5)]
            w["table"][("LoRaWAN", "DEVEUI-A1", 5)] = (r[0], topic("dev_A1", "ch0"), r[2], r[3])
    elif fault == "M2_missing_mapping":        # B1's channel/device mapping omitted
        if O:
            del w["channels"][("dev_B1", "py", 2)]
        else:
            del w["table"][("LoRaWAN", "DEVEUI-B1", 2)]   # inventory (C+) still lists it
    elif fault == "M3_unit_not_converted":     # F1 sends degF; author forgets it
        if O:                                   # card mislabeled as Cel
            c = dict(w["cards"]["pf"]); c["unit"] = "Cel"; w["cards"]["pf"] = c
        else:                                   # handler omits the degF -> Cel step
            dec = CODECS["f"][0]
            w["handlers"]["pf"] = lambda b: dec(b)
    elif fault == "M4_concept_conflict":       # B1 is a dew-point sensor, bound to air-temperature
        if O:
            c = dict(w["cards"]["py"]); c["concept"] = "ex:DewPoint"; w["cards"]["py"] = c
        else:
            w["meta"]["py"] = ("ex:DewPoint", "Cel")
    elif fault == "M5_wrong_decoder":          # B1 provisioned with A1's profile (plausible values)
        if O:
            w["binding"][("LoRaWAN", "DEVEUI-B1", 2)] = "px"
            del w["channels"][("dev_B1", "py", 2)]
            w["channels"][("dev_B1", "px", 2)] = "ch0"
        else:
            r = w["table"][("LoRaWAN", "DEVEUI-B1", 2)]
            w["table"][("LoRaWAN", "DEVEUI-B1", 2)] = ("px", r[1], r[2], r[3])
    elif fault == "M6_dangling_reference":     # typo in the profile/handler id of B1
        if O:
            w["binding"][("LoRaWAN", "DEVEUI-B1", 2)] = "pyy"
        else:
            r = w["table"][("LoRaWAN", "DEVEUI-B1", 2)]
            w["table"][("LoRaWAN", "DEVEUI-B1", 2)] = ("pyy", r[1], r[2], r[3])
    else:
        raise ValueError(fault)
    return w


FAULTS = ["M1_duplicate_topic", "M2_missing_mapping", "M3_unit_not_converted",
          "M4_concept_conflict", "M5_wrong_decoder", "M6_dangling_reference"]


def run_workload(system: str, w: dict, fr: List[tuple], bad_keys=frozenset()) -> dict:
    """bad_keys: stable keys whose true quantity is not the contract's concept; any
    publication from them is wrong regardless of value."""
    pub = rej = wrong = 0
    for f in fr:
        r = proc_O(f, w) if system == "O" else proc_C(f, w, plus=(system == "C+"))
        if r is None:
            rej += 1
        else:
            pub += 1
            if r[0] != f[5] or abs(r[1] - f[4]) > TOL or f[1] in bad_keys:
                wrong += 1
    return {"frames": len(fr), "published": pub, "rejected": rej, "wrong_published": wrong,
            "correct": pub - wrong}


def fault_matrix() -> List[dict]:
    fr = frames(BASE_SPEC)
    base = {"C": world_C(BASE_SPEC), "C+": world_C(BASE_SPEC), "O": world_O(BASE_SPEC)}
    for sysname in ("C", "C+", "O"):  # clean sanity run
        r = run_workload(sysname, base[sysname], fr)
        assert r["wrong_published"] == 0 and r["rejected"] == 0, (sysname, r)
    assert validate_O(base["O"]) == [] and validate_Cplus(base["C+"]) == []
    rows = []
    for fault in FAULTS:
        for sysname in ("C", "C+", "O"):
            w = inject(fault, sysname, base[sysname])
            if sysname == "O":
                perrs = len(validate_O(w))
            elif sysname == "C+":
                perrs = len(validate_Cplus(w))
            else:
                perrs = None  # no provisioning validator
            bad = {"DEVEUI-B1"} if fault == "M4_concept_conflict" else frozenset()
            r = run_workload(sysname, w, fr, bad)
            r["wrong_if_gated"] = 0 if perrs else r["wrong_published"]
            rows.append({"fault": fault, "system": sysname, "provisioning_errors": perrs, **r})
    return rows


# ------------------------------- change scope ---------------------------------
def changed_items(a: dict, b: dict) -> List[str]:
    out = []
    for t in a:
        if t in ("handlers", "range"):
            continue
        ka, kb = a[t], b[t]
        if isinstance(ka, set):
            out += [f"{t}+{x}" for x in kb - ka] + [f"{t}-{x}" for x in ka - kb]
        else:
            out += [f"{t}[{k}]" for k in kb if k not in ka or ka[k] != kb[k]]
            out += [f"{t}-[{k}]" for k in ka if k not in kb]
    return out


def effort() -> List[dict]:
    out = []
    scen = {
        "S1_add_device_existing_profile": BASE_SPEC + [("LoRaWAN", "DEVEUI-A2", "dev_A2", "px", 2, "ch0")],
        "S2_add_device_new_profile_K": BASE_SPEC + [("LoRaWAN", "DEVEUI-K1", "dev_K1", "pk", 2, "ch0")],
        "S3_add_second_channel_existing": BASE_SPEC + [("LoRaWAN", "DEVEUI-B1", "dev_B1", "py", 5, "ch1")],
    }
    for name, spec in scen.items():
        o0, o1 = world_O(BASE_SPEC), world_O(spec)
        c0, c1 = world_C(BASE_SPEC), world_C(spec)
        oi, ci = changed_items(o0, o1), changed_items(c0, c1)
        out.append({"scenario": name,
                    "O_data_rows": len(oi),
                    "O_code_functions": len({c["codec"] for c in o1["cards"].values()} - {c["codec"] for c in o0["cards"].values()}),
                    "C+_data_rows": len(ci),
                    "C+_code_functions": len(set(c1["handlers"]) - set(c0["handlers"])),
                    "O_items": oi, "C+_items": ci})
    # S4: canonical-unit policy change Cel -> K. Executed and verified on O and C+.
    fr = frames(BASE_SPEC, offset_k=True)
    o0, o1 = world_O(BASE_SPEC), world_O(BASE_SPEC, "K")
    c0, c1 = world_C(BASE_SPEC), world_C(BASE_SPEC, "K")
    ok_o = sum(1 for f in fr if (r := proc_O(f, o1)) and abs(r[1] - f[4]) <= TOL and r[0] == f[5])
    ok_c = sum(1 for f in fr if (r := proc_C(f, c1, True)) and abs(r[1] - f[4]) <= TOL and r[0] == f[5])
    oi, ci = changed_items(o0, o1), changed_items(c0, c1)
    handlers_changed = len(c0["handlers"])  # every handler's conversion target changes
    out.append({"scenario": "S4_canonical_unit_policy_Cel_to_K",
                "O_data_rows": len(oi), "O_code_functions": 0,
                "O_verified_correct_frames": ok_o, "O_incorrect_frames": len(fr) - ok_o,
                "C+_data_rows": len(ci) + 1,       # + the global range constant
                "C+_code_functions": handlers_changed,
                "C+_verified_correct_frames": ok_c, "C+_incorrect_frames": len(fr) - ok_c,
                "O_items": oi, "C+_items": ci + ["range constant"]})
    return out


# ------------------------------------ timing ----------------------------------
def timing(repeats: int, loops: int) -> dict:
    fr = frames(BASE_SPEC)
    systems = {"C": (world_C(BASE_SPEC), lambda f, w: proc_C(f, w, False)),
               "C+": (world_C(BASE_SPEC), lambda f, w: proc_C(f, w, True)),
               "O": (world_O(BASE_SPEC), proc_O)}
    res = {}
    gc.disable()
    try:
        for name, (w, fn) in systems.items():
            for f in fr:        # warm up
                fn(f, w)
            per = []
            for _ in range(repeats):
                t0 = time.perf_counter_ns()
                for _ in range(loops):
                    for f in fr:
                        fn(f, w)
                t1 = time.perf_counter_ns()
                per.append((t1 - t0) / (loops * len(fr)))
            res[name] = {"n_batches": repeats, "loops_per_batch": loops,
                         "frames_per_loop": len(fr),
                         "ns_per_frame_mean": round(statistics.mean(per), 1),
                         "ns_per_frame_sd": round(statistics.stdev(per), 1) if repeats > 1 else 0.0,
                         "ns_per_frame_median": round(statistics.median(per), 1),
                         "ns_per_frame_min": round(min(per), 1),
                         "ns_per_frame_all": [round(x, 1) for x in per]}
    finally:
        gc.enable()
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json")
    ap.add_argument("--repeats", type=int, default=7)
    ap.add_argument("--loops", type=int, default=200)
    args = ap.parse_args()

    fm = fault_matrix()
    print(f"{'fault':24s} {'sys':3s} {'prov_err':>8s} {'rejected':>8s} {'wrong':>6s} {'wrong_if_gated':>14s}")
    for r in fm:
        pe = "n/a" if r["provisioning_errors"] is None else str(r["provisioning_errors"])
        print(f"{r['fault']:24s} {r['system']:3s} {pe:>8s} {r['rejected']:8d} "
              f"{r['wrong_published']:6d} {r['wrong_if_gated']:14d}")
    ef = effort()
    print("\nchange scope: modified data rows / modified code functions")
    for e in ef:
        extra = ""
        if "O_verified_correct_frames" in e:
            extra = (f"  verified O={e['O_verified_correct_frames']}/255 "
                     f"C+={e['C+_verified_correct_frames']}/255")
        print(f"{e['scenario']:38s} O={e['O_data_rows']}/{e['O_code_functions']}  "
              f"C+={e['C+_data_rows']}/{e['C+_code_functions']}{extra}")
    tm = timing(args.repeats, args.loops)
    print(f"\nper-frame cost (batch-timed, GC off; {args.repeats} batches x {args.loops} loops x "
          f"{tm['O']['frames_per_loop']} frames), ns/frame mean +- SD [median]")
    for k, v in tm.items():
        print(f"  {k:3s} {v['ns_per_frame_mean']:8.1f} +- {v['ns_per_frame_sd']:.1f} [{v['ns_per_frame_median']:.1f}]")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"fault_matrix": fm, "effort": ef, "timing": tm,
                       "python": sys.version.split()[0]}, f, indent=2)
        print("wrote", args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())

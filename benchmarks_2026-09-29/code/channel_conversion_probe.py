#!/usr/bin/env python3
"""
Functional probe (EQ7, revised): a provisioned channel-binding resolver J,
a genuine affine unit conversion composed via a common base unit, and a
genuine (non-identity) concept-canonicalization alignment -- all exercised
together on a small, deterministic, non-random configuration.

This revision replaces a first version of J that returned one global
channel per Source Card profile -- unworkable in general, since a Source
Card is shared by every device speaking its protocol/profile
(Section 5.1): it cannot by itself (a) let two different devices reuse the
same profile on different channels, or (b) distinguish two physically
identical sensors of that profile on one device. J is now a provisioned
BINDING, keyed by (device, Source Card profile, envelope endpoint) -- never
read from decoded payload content, so it still runs pre-decode. This probe
exercises all three previously-missing cases in one pass:
  (a) the SAME (profile, endpoint) pair resolves to a DIFFERENT channel
      depending on which device sent it (probe_dev_A's ch0 vs.
      probe_dev_B's ch1, both LoRaWAN FPort 2, same profile);
  (b) the SAME profile on the SAME device, but a DIFFERENT endpoint,
      resolves to a second, distinct channel (probe_dev_A's ch0 vs. ch2,
      FPort 2 vs. FPort 5) -- two identical sensors on one device,
      disambiguated purely by envelope metadata;
  (c) a genuinely non-identity concept canonicalization: ch3's Source Card
      declares a site-specific concept ex:AirTemperature, not the target
      contract's canonical saref:Temperature, and must be aligned via an
      explicit registered mapping, not a pass-through;
  (d) a genuinely non-identity, base-unit-composed affine conversion (K to
      Celsius via F_{u_s->u_t}(w) = (m_s*w + b_s - b_t)/m_t, not a direct
      copy of Celsius's own QUDT offset).

Deliberately does not touch semantic_filtering_v2.py, experiment_config_v2.json,
canonical_registry.json, or the existing source_cards/ directory: those back
the already-reported Benchmark B latency/ablation numbers, and this probe must
not perturb them. All state needed for this probe is self-contained below.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple


# --------------------------------------------------------------------------
# Source Cards sigma in Sigma: (protocol, profile) -> codec + source
# concept/unit. Deliberately NO channel field: Section 5.1 (Table 2) has
# none, since sigma is shared across every device speaking this profile.
# --------------------------------------------------------------------------

SOURCE_CARDS = {
    "lora_temp_internal_source": {
        "source_card_id": "lora_temp_internal_source",
        "protocol": "LoRaWAN",
        "payload_format": "signed int16, centi-degrees Celsius, big-endian, hex string",
        "measurement_type": "saref:Temperature",
        "source_unit": "Cel",
    },
    "lora_temp_external_kelvin_source": {
        "source_card_id": "lora_temp_external_kelvin_source",
        "protocol": "LoRaWAN",
        "payload_format": "unsigned int16, centi-kelvin, big-endian, hex string",
        "measurement_type": "saref:Temperature",
        "source_unit": "K",
    },
    "lora_temp_alias_source": {
        "source_card_id": "lora_temp_alias_source",
        "protocol": "LoRaWAN",
        "payload_format": "signed int16, centi-degrees Celsius, big-endian, hex string",
        "measurement_type": "ex:AirTemperature",
        "source_unit": "Cel",
    },
}


def decode_centi_c_be(payload_hex: str) -> float:
    payload = bytes.fromhex(payload_hex)
    if len(payload) != 2:
        raise ValueError("payload must be exactly 2 bytes")
    centi = int.from_bytes(payload, byteorder="big", signed=True)
    return centi / 100.0


def decode_centi_k_be(payload_hex: str) -> float:
    payload = bytes.fromhex(payload_hex)
    if len(payload) != 2:
        raise ValueError("payload must be exactly 2 bytes")
    centi = int.from_bytes(payload, byteorder="big", signed=False)
    return centi / 100.0


CODECS = {
    "lora_temp_internal_source": decode_centi_c_be,
    "lora_temp_external_kelvin_source": decode_centi_k_be,
    "lora_temp_alias_source": decode_centi_c_be,
}


def encode_centi_c_be(temp_c: float) -> str:
    centi = int(round(temp_c * 100.0))
    return int(centi).to_bytes(2, byteorder="big", signed=True).hex()


def encode_centi_k_be(temp_k: float) -> str:
    centi = int(round(temp_k * 100.0))
    return int(centi).to_bytes(2, byteorder="big", signed=False).hex()


# --------------------------------------------------------------------------
# Identity links I: (protocol, stable_key) -> canonical device d. Two
# distinct physical devices, each with its own single LoRaWAN stable key
# (DevEUI) -- endpoint/channel disambiguation is J's job, not I's.
# --------------------------------------------------------------------------

IDENTITY_LINKS = {
    "LoRaWAN": {
        "DEVEUI-PROBE-001-A": "probe_dev_A",
        "DEVEUI-PROBE-001-B": "probe_dev_B",
    }
}

# --------------------------------------------------------------------------
# Channel-binding table (J, Eq. eq:channel): provisioned per
# (device, Source Card profile id, envelope endpoint [LoRaWAN FPort]),
# never a field of the Source Card and never read from decoded payload.
# --------------------------------------------------------------------------

CHANNEL_BINDINGS: Dict[Tuple[str, str, int], str] = {
    ("probe_dev_A", "lora_temp_internal_source", 2): "ch0",
    ("probe_dev_A", "lora_temp_external_kelvin_source", 3): "ch1",
    ("probe_dev_A", "lora_temp_internal_source", 5): "ch2",
    ("probe_dev_A", "lora_temp_alias_source", 7): "ch3",
    ("probe_dev_B", "lora_temp_internal_source", 2): "ch1",
}


def resolve_identity(protocol: str, stable_key: str) -> str | None:
    return IDENTITY_LINKS.get(protocol, {}).get(stable_key)


def resolve_channel(d: str, source_card_id: str, fport: int) -> str | None:
    """J: M x D x Sigma -> A, a provisioned lookup on (d, profile, envelope
    endpoint) -- reads only m's envelope metadata (fport), never payload."""
    return CHANNEL_BINDINGS.get((d, source_card_id, fport))


# --------------------------------------------------------------------------
# Concept canonicalization (N's mapping sub-step 2): raw Source Card
# concept q_s -> canonical q. Identity for already-canonical concepts, an
# explicit registered alignment otherwise; unregistered concepts are
# unmapped (E_N in the full pipeline; a rejection here).
# --------------------------------------------------------------------------

CONCEPT_ALIGNMENT = {
    "saref:Temperature": "saref:Temperature",
    "ex:AirTemperature": "saref:Temperature",
}

# --------------------------------------------------------------------------
# Target Contracts tau(d,a), fully channel-indexed.
# --------------------------------------------------------------------------

TARGET_CONTRACTS = {
    ("probe_dev_A", "ch0"): {
        "concept": "saref:Temperature", "schema_id": "temp_contract_v1", "version": 1,
        "unit": "Cel", "valid_range": [-40.0, 85.0],
        "topic": "uns/site_1/device/probe_dev_A/ch0/temperature_internal",
    },
    ("probe_dev_A", "ch1"): {
        "concept": "saref:Temperature", "schema_id": "temp_contract_v1", "version": 1,
        "unit": "Cel", "valid_range": [-40.0, 85.0],
        "topic": "uns/site_1/device/probe_dev_A/ch1/temperature_external",
    },
    ("probe_dev_A", "ch2"): {
        "concept": "saref:Temperature", "schema_id": "temp_contract_v1", "version": 1,
        "unit": "Cel", "valid_range": [-40.0, 85.0],
        "topic": "uns/site_1/device/probe_dev_A/ch2/temperature_internal_2",
    },
    ("probe_dev_A", "ch3"): {
        "concept": "saref:Temperature", "schema_id": "temp_contract_v1", "version": 1,
        "unit": "Cel", "valid_range": [-40.0, 85.0],
        "topic": "uns/site_1/device/probe_dev_A/ch3/temperature_alias",
    },
    ("probe_dev_B", "ch1"): {
        "concept": "saref:Temperature", "schema_id": "temp_contract_v1", "version": 1,
        "unit": "Cel", "valid_range": [-40.0, 85.0],
        "topic": "uns/site_1/device/probe_dev_B/ch1/temperature_internal",
    },
}

R_QU_COMPATIBLE = {("saref:Temperature", "Cel")}

# Unit-conversion base-unit parameters (QUDT conversionMultiplier/Offset,
# relative to the quantity kind's coherent base unit -- Kelvin, for
# temperature -- not relative to one another): m_u, b_u such that
# w_base = m_u * w_u + b_u. F_{u_s->u_t}(w) = (m_s*w + b_s - b_t) / m_t.
UNIT_BASE_PARAMS: Dict[str, Tuple[float, float]] = {
    "K": (1.0, 0.0),
    "Cel": (1.0, 273.15),
}


def convert_unit(w: float, u_s: str, u_t: str) -> float:
    m_s, b_s = UNIT_BASE_PARAMS[u_s]
    m_t, b_t = UNIT_BASE_PARAMS[u_t]
    return (m_s * w + b_s - b_t) / m_t


@dataclass(frozen=True)
class ProbeFrame:
    frame_id: int
    protocol: str
    source_card_id: str
    stable_key: str
    fport: int
    payload_hex: str
    expected_channel: str
    expected_celsius: float


@dataclass(frozen=True)
class ProbeResult:
    frame_id: int
    accepted: bool
    rejection_reason: str
    canonical_device: str
    channel: str
    source_concept: str
    canonical_concept: str
    concept_alias_used: bool
    source_unit: str
    target_unit: str
    decoded_raw: float
    converted_value: float
    identity_conversion: bool
    output_topic: str
    expected_celsius: float
    matches_expected: bool


def process(frame: ProbeFrame) -> ProbeResult:
    card = SOURCE_CARDS[frame.source_card_id]

    # --- Selector S_Sigma (implicit: source_card_id given) + Identity I ---
    d = resolve_identity(frame.protocol, frame.stable_key)
    if d is None:
        return ProbeResult(frame.frame_id, False, "unresolved_identity", "", "", "", "",
                            False, "", "", 0.0, 0.0, False, "", frame.expected_celsius, False)

    # --- Channel resolver J: provisioned (d, profile, envelope endpoint) lookup ---
    a = resolve_channel(d, frame.source_card_id, frame.fport)
    if a is None:
        return ProbeResult(frame.frame_id, False, "unresolved_channel", d, "", "", "",
                            False, "", "", 0.0, 0.0, False, "", frame.expected_celsius, False)

    # --- N: decode, canonicalize concept, unit-convert, project ---
    decoded_raw = CODECS[frame.source_card_id](frame.payload_hex)
    q_s = card["measurement_type"]
    u_s = card["source_unit"]

    target = TARGET_CONTRACTS.get((d, a))
    if target is None:
        return ProbeResult(frame.frame_id, False, "no_target_contract", d, a, q_s, "",
                            False, u_s, "", decoded_raw, 0.0, False, "", frame.expected_celsius, False)
    u_d = target["unit"]
    q_star = target["concept"]

    q_canonical = CONCEPT_ALIGNMENT.get(q_s)
    if q_canonical is None:
        return ProbeResult(frame.frame_id, False, "unmapped_concept", d, a, q_s, "",
                            False, u_s, u_d, decoded_raw, 0.0, False, "", frame.expected_celsius, False)
    concept_alias_used = (q_s != q_canonical)

    converted_value = convert_unit(decoded_raw, u_s, u_d)
    identity_conversion = (u_s == u_d)

    candidate = {"d": d, "a": a, "q": q_canonical, "w": converted_value, "u": u_d,
                 "kappa_fam": target["schema_id"], "nu": target["version"]}

    # --- G: Contract Gate, including the explicit q == q*_{d,a} check ---
    gate_reason = ""
    if candidate["q"] != q_star:
        gate_reason = "concept_mismatch"
    elif (candidate["q"], candidate["u"]) not in R_QU_COMPATIBLE:
        gate_reason = "incompatible_unit_concept"
    else:
        low, high = target["valid_range"]
        if not (low <= candidate["w"] <= high):
            gate_reason = "out_of_range"

    if gate_reason:
        return ProbeResult(frame.frame_id, False, gate_reason, d, a, q_s, q_canonical,
                            concept_alias_used, u_s, u_d, decoded_raw, converted_value,
                            identity_conversion, "", frame.expected_celsius, False)

    matches = abs(converted_value - frame.expected_celsius) < 1e-6
    return ProbeResult(frame.frame_id, True, "", d, a, q_s, q_canonical, concept_alias_used,
                        u_s, u_d, decoded_raw, converted_value, identity_conversion,
                        target["topic"], frame.expected_celsius, matches)


# --------------------------------------------------------------------------
# Deterministic, hand-picked frames (no RNG).
# --------------------------------------------------------------------------

def build_frames() -> list[ProbeFrame]:
    frames: list[ProbeFrame] = []
    fid = 1

    def add(protocol, source_card_id, stable_key, fport, payload_hex, channel, expected_c):
        nonlocal fid
        frames.append(ProbeFrame(fid, protocol, source_card_id, stable_key, fport,
                                  payload_hex, channel, expected_c))
        fid += 1

    # ch0: probe_dev_A's primary internal-temperature sensor, FPort 2.
    for temp_c in [-10.0, 0.0, 24.0, 24.0, 60.0]:
        add("LoRaWAN", "lora_temp_internal_source", "DEVEUI-PROBE-001-A", 2,
            encode_centi_c_be(temp_c), "ch0", temp_c)

    # ch1: probe_dev_A's external sensor, Kelvin-reporting profile, FPort 3
    # -- genuine non-identity K->Cel conversion.
    for temp_c in [-10.0, 0.0, 24.0, 24.0, 60.0]:
        temp_k = temp_c + 273.15
        add("LoRaWAN", "lora_temp_external_kelvin_source", "DEVEUI-PROBE-001-A", 3,
            encode_centi_k_be(temp_k), "ch1", temp_c)

    # ch2: a SECOND, physically distinct sensor of the SAME profile as ch0
    # on the SAME device, disambiguated purely by FPort (5, not 2).
    for temp_c in [5.0, 30.0, 55.0]:
        add("LoRaWAN", "lora_temp_internal_source", "DEVEUI-PROBE-001-A", 5,
            encode_centi_c_be(temp_c), "ch2", temp_c)

    # ch3: probe_dev_A's alias-concept sensor -- source concept
    # ex:AirTemperature must be canonicalized to saref:Temperature.
    for temp_c in [12.0, 33.0, 48.0]:
        add("LoRaWAN", "lora_temp_alias_source", "DEVEUI-PROBE-001-A", 7,
            encode_centi_c_be(temp_c), "ch3", temp_c)

    # probe_dev_B's ch1: SAME profile and SAME FPort (2) as probe_dev_A's
    # ch0, but a DIFFERENT device -- must resolve to a DIFFERENT channel.
    for temp_c in [8.0, 40.0, 65.0]:
        add("LoRaWAN", "lora_temp_internal_source", "DEVEUI-PROBE-001-B", 2,
            encode_centi_c_be(temp_c), "ch1", temp_c)

    return frames


def main() -> None:
    frames = build_frames()
    results = [process(f) for f in frames]

    all_accepted = all(r.accepted for r in results)
    all_match = all(r.matches_expected for r in results)

    a_ch0 = [r for r in results if r.canonical_device == "probe_dev_A" and r.channel == "ch0"]
    a_ch1 = [r for r in results if r.canonical_device == "probe_dev_A" and r.channel == "ch1"]
    a_ch2 = [r for r in results if r.canonical_device == "probe_dev_A" and r.channel == "ch2"]
    a_ch3 = [r for r in results if r.canonical_device == "probe_dev_A" and r.channel == "ch3"]
    b_ch1 = [r for r in results if r.canonical_device == "probe_dev_B" and r.channel == "ch1"]

    same_profile_diff_device_diff_channel = (
        bool(a_ch0) and bool(b_ch1)
        and a_ch0[0].channel != b_ch1[0].channel
        and {r.output_topic for r in a_ch0} != {r.output_topic for r in b_ch1}
    )
    same_profile_same_device_diff_endpoint_diff_channel = (
        bool(a_ch0) and bool(a_ch2)
        and a_ch0[0].channel != a_ch2[0].channel
        and {r.output_topic for r in a_ch0} != {r.output_topic for r in a_ch2}
    )
    ch1_conversion_nonidentity = bool(a_ch1) and all(not r.identity_conversion for r in a_ch1)
    ch0_conversion_identity = bool(a_ch0) and all(r.identity_conversion for r in a_ch0)
    ch3_concept_alias_used = bool(a_ch3) and all(r.concept_alias_used for r in a_ch3)
    ch3_canonicalized_correctly = bool(a_ch3) and all(r.canonical_concept == "saref:Temperature" for r in a_ch3)

    summary = {
        "n_frames": len(results),
        "all_accepted": all_accepted,
        "all_converted_values_match_expected_celsius": all_match,
        "same_profile_different_device_resolves_different_channel":
            same_profile_diff_device_diff_channel,
        "same_profile_same_device_different_endpoint_resolves_different_channel":
            same_profile_same_device_diff_endpoint_diff_channel,
        "ch0_conversion_is_identity": ch0_conversion_identity,
        "ch1_conversion_is_genuine_nonidentity_affine": ch1_conversion_nonidentity,
        "ch1_conversion_params_base_unit_composed": {
            "K": list(UNIT_BASE_PARAMS["K"]), "Cel": list(UNIT_BASE_PARAMS["Cel"]),
        },
        "ch3_concept_alias_actually_used": ch3_concept_alias_used,
        "ch3_canonicalized_to_saref_temperature": ch3_canonicalized_correctly,
        "frames": [
            {
                "frame_id": r.frame_id, "accepted": r.accepted, "rejection_reason": r.rejection_reason,
                "canonical_device": r.canonical_device, "channel": r.channel,
                "source_concept": r.source_concept, "canonical_concept": r.canonical_concept,
                "concept_alias_used": r.concept_alias_used,
                "source_unit": r.source_unit, "target_unit": r.target_unit,
                "decoded_raw": round(r.decoded_raw, 4), "converted_value_celsius": round(r.converted_value, 4),
                "identity_conversion": r.identity_conversion, "output_topic": r.output_topic,
                "expected_celsius": r.expected_celsius, "matches_expected": r.matches_expected,
            }
            for r in results
        ],
    }

    out_dir = Path(__file__).parent / "results_probe"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "channel_conversion_probe_summary.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"n_frames={summary['n_frames']} all_accepted={all_accepted} all_match={all_match}")
    print(f"same_profile_diff_device_diff_channel={same_profile_diff_device_diff_channel} "
          f"same_profile_same_device_diff_endpoint_diff_channel="
          f"{same_profile_same_device_diff_endpoint_diff_channel}")
    print(f"ch0_identity={ch0_conversion_identity} ch1_nonidentity={ch1_conversion_nonidentity} "
          f"ch3_alias_used={ch3_concept_alias_used} ch3_canonicalized={ch3_canonicalized_correctly}")
    for r in results:
        print(f"  frame {r.frame_id}: d={r.canonical_device} ch={r.channel} "
              f"q_s={r.source_concept} q={r.canonical_concept} u_s={r.source_unit} u_d={r.target_unit} "
              f"raw={r.decoded_raw:.2f} converted={r.converted_value:.2f} "
              f"expected={r.expected_celsius:.2f} accepted={r.accepted} match={r.matches_expected} "
              f"topic={r.output_topic}")
    print(f"Wrote {out_path}")

    checks = (all_accepted, all_match, same_profile_diff_device_diff_channel,
              same_profile_same_device_diff_endpoint_diff_channel, ch0_conversion_identity,
              ch1_conversion_nonidentity, ch3_concept_alias_used, ch3_canonicalized_correctly)
    if not all(checks):
        raise SystemExit("PROBE FAILED: one or more invariants did not hold")


if __name__ == "__main__":
    main()

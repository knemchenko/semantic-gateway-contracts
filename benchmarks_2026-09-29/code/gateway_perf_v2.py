import matplotlib
matplotlib.use('Agg')

import matplotlib.pyplot as plt
import time
import json
import queue
import threading
import random
import struct
import os
import numpy as np
from collections import OrderedDict
from rdflib import Graph, Literal, RDF, URIRef, Namespace

# ==========================================
# Benchmark A (revised v3): bounded LRU cache over (protocol, profile) Source
# Card keys -- not per-device keys.
#
# v1 used a plain, unbounded dict keyed by physical device address, so with
# only 3 registered devices every one of them was cached permanently after
# its first sighting (h~=0.79 reflected registered-traffic share, not real
# eviction pressure). v2 kept the same per-device keying, only adding a
# capacity bound -- which still measured legacy per-device Device Card
# lookup, not the paper's post-split Source Card selector S_Sigma, which
# resolves by (protocol, payload profile) alone and must never depend on
# device identity (Section 6.1). This version fixes that: the cache key is
# (protocol, FPort/cluster/characteristic profile identifier), never a
# device address, and "unregistered" traffic means an unrecognized profile,
# not an unrecognized device. Four distinct profiles (two LoRaWAN FPorts,
# one ZigBee cluster, one BLE characteristic) behind a capacity-2 cache
# force genuine eviction pressure, and T_lookup^hot / T_lookup^cold are
# recorded separately as before.
# ==========================================

def create_knowledge_base():
    with open("ontology.ttl", "w") as f:
        f.write("""
        @prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        @prefix iot: <http://example.org/iot-ontology#> .
        iot:SourceCard a rdfs:Class .
        iot:hasProfileKey a rdf:Property .
        iot:usesCodec a rdf:Property .
        """)

    with open("inventory_v2.ttl", "w") as f:
        f.write("""
        @prefix iot: <http://example.org/iot-ontology#> .
        @prefix ex: <http://example.org/site-1/> .

        ex:lora_fport01 a iot:SourceCard ;
            iot:hasProfileKey "LoRaWAN:01" ;
            iot:usesCodec "codec_lora_lht65" .

        ex:lora_fport02 a iot:SourceCard ;
            iot:hasProfileKey "LoRaWAN:02" ;
            iot:usesCodec "codec_lora_lht65" .

        ex:zigbee_cluster0402 a iot:SourceCard ;
            iot:hasProfileKey "ZigBee:0402" ;
            iot:usesCodec "codec_zigbee_switch" .

        ex:ble_char2a6e a iot:SourceCard ;
            iot:hasProfileKey "BLE:2A6E" ;
            iot:usesCodec "codec_ble_gatt" .
        """)

create_knowledge_base()

IOT = Namespace("http://example.org/iot-ontology#")
g = Graph()
try:
    g.parse("ontology.ttl", format="turtle")
    g.parse("inventory_v2.ttl", format="turtle")
except Exception:
    pass

CACHE_CAPACITY = 2

def codec_lora(payload):
    if len(payload) < 2: raise ValueError("Short payload")
    return {"val": int.from_bytes(payload[0:2], 'big') / 100.0}

def codec_zigbee(payload):
    if len(payload) < 4: raise ValueError("Short payload")
    return {"status": "ON" if payload[3] == 1 else "OFF"}

def codec_ble(payload):
    try: return {"temp": round(struct.unpack('<f', payload)[0], 2)}
    except: return {"temp": 0.0}

CODECS = {"codec_lora_lht65": codec_lora, "codec_zigbee_switch": codec_zigbee, "codec_ble_gatt": codec_ble}

# FPort occupies the last byte of the fixed 4-byte LoRaWAN header segment
# ("00 01 00 <fport>"), distinguishing two profiles sharing one codec.
def gen_lora(addr="26011A2B", fport="02", malformed=False):
    pl = bytes.fromhex("FF") if malformed else int(2455).to_bytes(2, 'big')
    return bytes.fromhex("40" + addr + "0001" + "00" + fport) + pl + bytes.fromhex("AABBCCDD")

def gen_zigbee(addr="A1B2", cluster="0402"):
    return bytes.fromhex("618801" + cluster + "0000" + addr + "00060101" + "FFFF")

def gen_ble(mac="AABBCCDDEEFF"):
    return bytes.fromhex("8E89BED60216070004001B") + struct.pack('<f', 36.6) + bytes.fromhex("FFFFFF")

acp_queue = queue.Queue()
stop_event = threading.Event()

breakdown_data = {"LoRaWAN": [], "ZigBee": [], "BLE": []}
latency_hist_data = []
all_arrivals_response_ms = []  # every arrival, response time (queue wait + service)
all_arrivals_service_ms = []  # every arrival, SERVICE time only (queue wait excluded)
lookup_hot_ns = []
lookup_cold_ns = []
counters = {"ok": 0, "unregistered_profile": 0, "malformed": 0, "cache_hit": 0, "cache_miss": 0, "evictions": 0}

class Packet:
    def __init__(self, profile_key, pl, proto):
        self.profile_key = profile_key; self.pl = pl; self.proto = proto; self.ts = time.perf_counter()

def run_core():
    cache = OrderedDict()  # bounded LRU over (protocol, profile) Source Card keys
    while not stop_event.is_set():
        try: pkt = acp_queue.get(timeout=0.1)
        except Exception: continue

        t0 = time.perf_counter()

        # 1. SOURCE-CARD LOOKUP (bounded LRU, keyed by protocol+profile, never device)
        codec_f = cache.get(pkt.profile_key)
        if codec_f is not None:
            cache.move_to_end(pkt.profile_key)
            counters["cache_hit"] += 1
            t1 = time.perf_counter()
            lookup_hot_ns.append((t1 - t0) * 1e9)
        else:
            counters["cache_miss"] += 1
            uri = next(g.subjects(IOT.hasProfileKey, Literal(pkt.profile_key)), None)
            if uri:
                c_name = str(next(g.objects(uri, IOT.usesCodec), None))
                if c_name in CODECS:
                    codec_f = CODECS[c_name]
                    cache[pkt.profile_key] = codec_f
                    cache.move_to_end(pkt.profile_key)
                    if len(cache) > CACHE_CAPACITY:
                        cache.popitem(last=False)
                        counters["evictions"] += 1

            t1 = time.perf_counter()
            lookup_cold_ns.append((t1 - t0) * 1e9)

            if not codec_f:
                counters["unregistered_profile"] += 1
                all_arrivals_response_ms.append((t1 - pkt.ts) * 1000)
                all_arrivals_service_ms.append((t1 - t0) * 1000)  # lookup only, queue excluded
                acp_queue.task_done(); continue

        # 2. DECODE
        try:
            res = codec_f(pkt.pl)
        except Exception:
            t_reject = time.perf_counter()
            counters["malformed"] += 1
            all_arrivals_response_ms.append((t_reject - pkt.ts) * 1000)
            all_arrivals_service_ms.append((t_reject - t0) * 1000)  # lookup+decode attempt, queue excluded
            acp_queue.task_done(); continue
        t2 = time.perf_counter()

        # 3. SERIALIZE
        _ = json.dumps({"t": f"u/{pkt.proto}/{pkt.profile_key}", "v": res})
        t3 = time.perf_counter()

        d_q = (t0 - pkt.ts) * 1000
        d_l = (t1 - t0) * 1000
        d_d = (t2 - t1) * 1000
        d_s = (t3 - t2) * 1000

        breakdown_data[pkt.proto].append((d_q, d_l, d_d, d_s))
        latency_hist_data.append(d_q + d_l + d_d + d_s)
        all_arrivals_response_ms.append(d_q + d_l + d_d + d_s)
        all_arrivals_service_ms.append(d_l + d_d + d_s)  # queue (d_q) excluded
        counters["ok"] += 1
        acp_queue.task_done()

def run_agent(raw, proto, mac_meta=None):
    t_in = time.perf_counter()
    try:
        if proto == "LoRaWAN":
            fport = raw[8:9].hex().upper()
            profile_key = f"LoRaWAN:{fport}"
            pl = raw[9:-4]
        elif proto == "ZigBee":
            cluster = raw[3:5].hex().upper()
            profile_key = f"ZigBee:{cluster}"
            pl = raw[9:-2]
        elif proto == "BLE":
            profile_key = "BLE:2A6E"
            pl = raw[11:-3]

        p = Packet(profile_key, pl, proto)
        p.ts = t_in
        acp_queue.put(p)
    except Exception: pass

threading.Thread(target=run_core, daemon=True).start()

print(">>> Running Benchmark A (revised v3: Source Card selector, bounded LRU cap=2, "
      "4 registered profiles) on RPi 5 (1200 packets)...")
for _ in range(1200):
    r = random.random()
    if r < 0.7:
        rp = random.random()
        if rp < 0.25:
            run_agent(gen_lora(fport="01"), "LoRaWAN")
        elif rp < 0.50:
            run_agent(gen_lora(fport="02"), "LoRaWAN")
        elif rp < 0.75:
            run_agent(gen_zigbee(cluster="0402"), "ZigBee")
        else:
            run_agent(gen_ble(), "BLE")
    elif r < 0.9:
        run_agent(gen_lora(fport="63"), "LoRaWAN")  # unregistered profile (FPort 0x63)
    else:
        run_agent(gen_lora(fport="01", malformed=True), "LoRaWAN")

    time.sleep(random.expovariate(200))

acp_queue.join()
stop_event.set()

print(f"\nResults: OK={counters['ok']}, UnregisteredProfile={counters['unregistered_profile']}, "
      f"Bad={counters['malformed']}, Evictions={counters['evictions']}")


def export_summary(path="results_v2/summary_latency_v2.json"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lat = np.array(latency_hist_data, dtype=float)
    resp_all = np.array(all_arrivals_response_ms, dtype=float)
    svc_all = np.array(all_arrivals_service_ms, dtype=float)
    total_lookups = counters["cache_hit"] + counters["cache_miss"]
    hot = np.array(lookup_hot_ns, dtype=float)
    cold = np.array(lookup_cold_ns, dtype=float)
    summary = {
        "cache_capacity": CACHE_CAPACITY,
        "n_registered_profiles": 4,
        "n_ok": counters["ok"],
        "n_unregistered_profile": counters["unregistered_profile"],
        "n_malformed": counters["malformed"],
        "n_evictions": counters["evictions"],
        "cache_hit": counters["cache_hit"],
        "cache_miss": counters["cache_miss"],
        "cache_hit_ratio": (counters["cache_hit"] / total_lookups) if total_lookups else None,
        "lookup_hot_ns": {
            "mean": float(np.mean(hot)) if hot.size else None,
            "n": int(hot.size),
        },
        "lookup_cold_ns": {
            "mean": float(np.mean(cold)) if cold.size else None,
            "n": int(cold.size),
        },
        "latency_ms": {
            "mean": float(np.mean(lat)) if lat.size else None,
            "median_p50": float(np.percentile(lat, 50)) if lat.size else None,
            "p95": float(np.percentile(lat, 95)) if lat.size else None,
            "p99": float(np.percentile(lat, 99)) if lat.size else None,
            "std": float(np.std(lat)) if lat.size else None,
            "min": float(np.min(lat)) if lat.size else None,
            "max": float(np.max(lat)) if lat.size else None,
        },
        "response_ms_all_arrivals": {
            "mean": float(np.mean(resp_all)) if resp_all.size else None,
            "median_p50": float(np.percentile(resp_all, 50)) if resp_all.size else None,
            "p95": float(np.percentile(resp_all, 95)) if resp_all.size else None,
            "p99": float(np.percentile(resp_all, 99)) if resp_all.size else None,
            "std": float(np.std(resp_all)) if resp_all.size else None,
            "n": int(resp_all.size),
        },
        "service_ms_all_arrivals": {
            "mean": float(np.mean(svc_all)) if svc_all.size else None,
            "median_p50": float(np.percentile(svc_all, 50)) if svc_all.size else None,
            "p95": float(np.percentile(svc_all, 95)) if svc_all.size else None,
            "p99": float(np.percentile(svc_all, 99)) if svc_all.size else None,
            "std": float(np.std(svc_all)) if svc_all.size else None,
            "n": int(svc_all.size),
        },
        "stage_breakdown_ms_mean": {
            proto: {
                "queue": float(np.mean(breakdown_data[proto], axis=0)[0]) if breakdown_data[proto] else None,
                "lookup": float(np.mean(breakdown_data[proto], axis=0)[1]) if breakdown_data[proto] else None,
                "decode": float(np.mean(breakdown_data[proto], axis=0)[2]) if breakdown_data[proto] else None,
                "serialize": float(np.mean(breakdown_data[proto], axis=0)[3]) if breakdown_data[proto] else None,
                "n": len(breakdown_data[proto]),
            }
            for proto in breakdown_data
        },
    }
    with open(path, "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))
    print(f"Saved: {path}")

export_summary()

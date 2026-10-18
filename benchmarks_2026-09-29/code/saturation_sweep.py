import matplotlib
matplotlib.use('Agg')

import time
import json
import queue
import threading
import random
import struct
import os
import sys
import numpy as np
from collections import OrderedDict
from rdflib import Graph, Literal, RDF, URIRef, Namespace

# ==========================================
# Saturation sweep: exercise Benchmark A's Semantic Core across several
# arrival rates spanning the predicted admissibility bound lambda_max, not
# just one point far below it. Reports achieved throughput, mean/p99
# in-process latency, queue-depth growth, and drop count (queue now bounded,
# unlike the point benchmarks) at each offered rate, so the bound can
# actually be checked against behavior near and beyond it, not merely
# shown to hold at a rate nobody expected to violate it.
# ==========================================

def create_knowledge_base():
    with open("ontology.ttl", "w") as f:
        f.write("""
        @prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix iot: <http://example.org/iot-ontology#> .
        iot:SourceCard a rdfs:Class .
        iot:hasProfileKey a rdf:Property .
        iot:usesCodec a rdf:Property .
        """)
    with open("inventory_v2.ttl", "w") as f:
        f.write("""
        @prefix iot: <http://example.org/iot-ontology#> .
        @prefix ex: <http://example.org/site-1/> .
        ex:lora_fport01 a iot:SourceCard ; iot:hasProfileKey "LoRaWAN:01" ; iot:usesCodec "codec_lora_lht65" .
        ex:lora_fport02 a iot:SourceCard ; iot:hasProfileKey "LoRaWAN:02" ; iot:usesCodec "codec_lora_lht65" .
        ex:zigbee_cluster0402 a iot:SourceCard ; iot:hasProfileKey "ZigBee:0402" ; iot:usesCodec "codec_zigbee_switch" .
        ex:ble_char2a6e a iot:SourceCard ; iot:hasProfileKey "BLE:2A6E" ; iot:usesCodec "codec_ble_gatt" .
        """)

create_knowledge_base()
IOT = Namespace("http://example.org/iot-ontology#")
g = Graph()
g.parse("ontology.ttl", format="turtle")
g.parse("inventory_v2.ttl", format="turtle")

CACHE_CAPACITY = 2

def codec_lora(payload):
    if len(payload) < 2: raise ValueError("short")
    return {"val": int.from_bytes(payload[0:2], 'big') / 100.0}

def codec_zigbee(payload):
    if len(payload) < 4: raise ValueError("short")
    return {"status": "ON" if payload[3] == 1 else "OFF"}

def codec_ble(payload):
    try: return {"temp": round(struct.unpack('<f', payload)[0], 2)}
    except Exception: return {"temp": 0.0}

CODECS = {"codec_lora_lht65": codec_lora, "codec_zigbee_switch": codec_zigbee, "codec_ble_gatt": codec_ble}

def gen_lora(fport="01"):
    return bytes.fromhex("40" + "26011A2B" + "0001" + "00" + fport) + int(2455).to_bytes(2, 'big') + bytes.fromhex("AABBCCDD")

def gen_zigbee(cluster="0402"):
    return bytes.fromhex("618801" + cluster + "0000" + "A1B2" + "00060101" + "FFFF")

def gen_ble():
    return bytes.fromhex("8E89BED60216070004001B") + struct.pack('<f', 36.6) + bytes.fromhex("FFFFFF")


def run_one_rate(target_rate_hz, n_packets, queue_maxsize):
    acp_queue = queue.Queue(maxsize=queue_maxsize)
    stop_event = threading.Event()
    latency_ns_list = []
    queue_depth_samples = []
    counters = {"ok": 0, "dropped": 0, "unregistered_profile": 0, "malformed": 0}

    class Packet:
        __slots__ = ("profile_key", "pl", "ts")
        def __init__(self, profile_key, pl):
            self.profile_key = profile_key; self.pl = pl; self.ts = time.perf_counter()

    def run_core():
        cache = OrderedDict()
        while not stop_event.is_set() or not acp_queue.empty():
            try:
                pkt = acp_queue.get(timeout=0.05)
            except Exception:
                continue
            t0 = time.perf_counter()
            codec_f = cache.get(pkt.profile_key)
            if codec_f is not None:
                cache.move_to_end(pkt.profile_key)
            else:
                uri = next(g.subjects(IOT.hasProfileKey, Literal(pkt.profile_key)), None)
                if uri:
                    c_name = str(next(g.objects(uri, IOT.usesCodec), None))
                    if c_name in CODECS:
                        codec_f = CODECS[c_name]
                        cache[pkt.profile_key] = codec_f
                        cache.move_to_end(pkt.profile_key)
                        if len(cache) > CACHE_CAPACITY:
                            cache.popitem(last=False)
                if not codec_f:
                    counters["unregistered_profile"] += 1
                    acp_queue.task_done(); continue
            try:
                _ = codec_f(pkt.pl)
            except Exception:
                counters["malformed"] += 1
                acp_queue.task_done(); continue
            _ = json.dumps({"v": 1})
            t_end = time.perf_counter()
            latency_ns_list.append((t_end - pkt.ts) * 1e9)
            counters["ok"] += 1
            acp_queue.task_done()

    core_thread = threading.Thread(target=run_core, daemon=True)
    core_thread.start()

    profiles = [lambda: ("LoRaWAN:01", gen_lora("01")[9:-4]),
                lambda: ("LoRaWAN:02", gen_lora("02")[9:-4]),
                lambda: ("ZigBee:0402", gen_zigbee("0402")[9:-2]),
                lambda: ("BLE:2A6E", gen_ble()[11:-3])]

    t_run_start = time.perf_counter()
    for _ in range(n_packets):
        key, pl = random.choice(profiles)()
        try:
            acp_queue.put_nowait(Packet(key, pl))
        except queue.Full:
            counters["dropped"] += 1
        if _ % 50 == 0:
            queue_depth_samples.append(acp_queue.qsize())
        time.sleep(random.expovariate(target_rate_hz))
    t_run_end = time.perf_counter()

    acp_queue.join()
    stop_event.set()
    core_thread.join(timeout=2.0)

    duration_s = t_run_end - t_run_start
    lat = np.array(latency_ns_list, dtype=float) / 1000.0  # us
    return {
        "target_rate_hz": target_rate_hz,
        "n_offered": n_packets,
        "n_ok": counters["ok"],
        "n_dropped": counters["dropped"],
        "n_unregistered_profile": counters["unregistered_profile"],
        "n_malformed": counters["malformed"],
        "achieved_throughput_hz": counters["ok"] / duration_s if duration_s > 0 else None,
        "duration_s": duration_s,
        "max_queue_depth_sampled": max(queue_depth_samples) if queue_depth_samples else 0,
        "mean_queue_depth_sampled": float(np.mean(queue_depth_samples)) if queue_depth_samples else 0.0,
        "latency_us": {
            "mean": float(np.mean(lat)) if lat.size else None,
            "p50": float(np.percentile(lat, 50)) if lat.size else None,
            "p95": float(np.percentile(lat, 95)) if lat.size else None,
            "p99": float(np.percentile(lat, 99)) if lat.size else None,
            "max": float(np.max(lat)) if lat.size else None,
        },
    }


def main():
    lambda_max = 54_300.0  # events/s, from the corrected E[T_core] for this prototype
    fractions = [0.5, 1.0, 2.0, 5.0, 10.0]
    n_packets = 4000
    queue_maxsize = 2000
    results = []
    for frac in fractions:
        rate = lambda_max * frac
        print(f">>> Sweep point: {frac}x lambda_max = {rate:.0f} events/s")
        r = run_one_rate(rate, n_packets, queue_maxsize)
        r["fraction_of_lambda_max"] = frac
        results.append(r)
        print(json.dumps(r, indent=2))

    os.makedirs("results_v2", exist_ok=True)
    with open("results_v2/saturation_sweep.json", "w") as f:
        json.dump(results, f, indent=2)
    print("Saved: results_v2/saturation_sweep.json")


if __name__ == "__main__":
    main()

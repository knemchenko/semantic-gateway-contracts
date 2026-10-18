"""Run code/semantic_filtering_v2.py with CPython's cyclic garbage collector disabled.

Usage (from the evidence-bundle root, same arguments as the plain script):
    taskset -c 2 python3 run_gc_disabled.py --config experiment_config_v2.json \
        --seed 42 --out-prefix gcoff_1 [--gate-disabled]

Why: with the collector enabled, one generation-2 collection (~5.4 ms) lands on a
single frame near frame ~1590 in most trials (it is triggered by the harness
accumulating per-frame result rows), inflating the all-arrivals mean by ~0.5 us and its
trial-to-trial spread. Disabling gc leaves every timed stage untouched and removes only
that pause. The benchmark script itself is unchanged.
"""
import gc
import runpy
import sys

gc.disable()
sys.argv = ["code/semantic_filtering_v2.py"] + sys.argv[1:]
runpy.run_path("code/semantic_filtering_v2.py", run_name="__main__")

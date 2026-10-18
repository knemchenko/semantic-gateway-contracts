# Provenance note: Benchmark B decode-stage mean, cross-session drift

**Observation.** Benchmark B's reported decode-stage mean in the later
measurement session (~1067 ns) is roughly half an earlier session's
reported value (~2031 ns) for the same stage, on the same Raspberry Pi 5.
The other reported stage means (lookup, identity, normalize, Gate,
publication) were close to their earlier-session values in the one
comparison performed below — this is a single cross-session comparison,
not a controlled multi-session study, so this note does not claim those
other stages are confirmed stable, only that no comparably large
discrepancy was found for them in this one comparison.

**Investigation.** Before accepting either number, the following was
checked directly rather than assumed:

1. **Code diff.** The earlier session's `semantic_filtering_v2.py` was
   recovered from a private evidence bundle prepared at that time
   (`evidence_bundle_2026-08-17.zip`, `code/semantic_filtering_v2.py`) and
   diffed against the current script. The decode-related functions
   (`decode_lorawan_temperature_centi_c_be`, `decode_zigbee_temperature_zcl_attr_le`,
   `decode_ble_temperature_centi_c_le`, `decode_by_source_card`) are
   **byte-for-byte identical** between the two versions. The timing
   bracket around the decode call (`t4 = time.perf_counter_ns(); ...;
   t5 = time.perf_counter_ns(); decode_ns = t5 - t4`) is also unchanged in
   position and content; only unrelated fields were added earlier/later in
   the same function for the new channel-resolution (J) stage.

2. **Direct re-run of the unmodified earlier-session script.** The
   earlier session's script, config, and registry were deployed to the
   same Raspberry Pi 5 in an isolated
   directory (so as not to touch the current experiment's own config/
   registry, which have since changed schema) and run twice, unmodified,
   under a directly-verified `performance` CPU governor:

   ```
   Run 1: decode_ns mean = 1043.01 ns  (lookup=164.7, identity=297.9, normalize=857.2, gate=883.6, publication=139.2)
   Run 2: decode_ns mean = 1032.47 ns  (lookup=162.5, identity=302.7, normalize=820.0, gate=878.2, publication=130.5)
   ```

   Both runs reproduce the **current** manuscript's low decode figure
   (~1040 ns), not the originally reported 2031 ns — using the exact code
   that originally reported 2031 ns. Every other stage in these two
   diagnostic runs is also close to both the current manuscript's figures
   and the earlier session's, consistent with normal run-to-run trial
   noise, not a systematic difference.

3. **Archival cross-check of the earlier session's own recorded numbers.**
   Rather than relying only on the manuscript's own prior-round prose for
   the "~2031 ns" figure, the earlier session's own raw per-frame CSV logs
   (`evidence_bundle_2026-08-17.zip`, `results/experiment3_v3_repeats/`)
   were recomputed directly, filtering to `actual_result == "accept"`
   before averaging `decode_ns` — matching exactly what
   `semantic_filtering_v2.py`'s own summary code does
   (`accepted_rows = [r for r in rows if r["actual_result"] == "accept"]`).
   This recomputation gives $\approx$1950-2000 ns per trial, consistent
   with the $2031\pm9$ ns figure, confirming it as a genuinely recorded
   value from that session, not a transcription error. A caveat surfaced
   during this recomputation and worth recording: averaging `decode_ns`
   over \emph{all} frames that reached decode (i.e., without the
   accept-only filter, so including Gate-rejected frames) gives a badly
   inflated, misleading mean (one frame in the earlier session's raw data
   recorded a 5.37 ms decode outlier, almost certainly a GC pause or OS
   preemption, among the rejected population) — anyone recomputing these
   figures from the raw CSVs must apply the same accept-only filter the
   original script uses, not average the whole `decode_ns` column. This
   earlier session's raw data is included in the evidence archive at
   `results/earlier_session_reference/` (see that directory's own README)
   so a reviewer can reproduce this recomputation directly rather than
   trusting this note's arithmetic.

4. **Software/OS/kernel version check.** `python3 --version` (3.13.5),
   `uname -r` (6.18.34+rpt-rpi-v8), and `/etc/os-release` (Debian 13,
   trixie) all match the methodology documented in the manuscript and were
   confirmed unchanged from the earlier session — no OS, kernel, or Python
   update occurred on this device between the two measurement sessions.

5. **Governor and thermal state.** `scaling_governor` was confirmed
   `performance` on all four cores immediately before both the earlier
   session's rerun and the later session's own reruns (a separate,
   independently found governor drift to `ondemand` had affected Benchmark
   A specifically — see the manuscript's Limitations section and this
   evidence archive's other Benchmark A results — but that drift was fixed
   before any of the decode comparisons above, all of which ran under a
   verified `performance` governor). `vcgencmd get_throttled` reported
   `0x0` (no under-voltage or thermal throttling recorded) at
   diagnostic-run time.

**Conclusion.** The decode-stage mean's cross-session drop is not
attributable to any code change, timing-bracket change, OS/kernel/Python
version change, CPU governor state, or recorded thermal throttling that
this investigation could identify. It appears to be a real property of
measuring a nanosecond-scale cost on this specific hardware across
separate sessions, isolated to one operation in the one comparison
performed here, but this investigation is a single cross-session
comparison — two data points — not the 3-5 independent sessions/reboots a
controlled study of session-level variance would require, and it does not
identify a root cause, only rules out the candidate causes checked above.
The manuscript accordingly treats the fine-grained, seven-stage latency
decomposition (and its $\approx$20\% residual) as an exploratory,
session-specific instrumentation result rather than a stable, confirmed
finding, while treating the \emph{aggregate} accepted-frame and
mixture-mean latencies — which moved by only $\approx$2\% across these
same two sessions — with comparatively more confidence
(manuscript Section 8.4/8.9 and Limitations, Section 10).

No experimental rerun beyond the diagnostic comparisons above was
performed: the later session's numbers already in the manuscript are that
session's own, independently reproducible measurement (5 trials, distinct
seeds, confirmed `performance` governor), not a number carried over from
the earlier session. A genuinely controlled multi-session study (3-5
independent sessions/reboots, each with several trials, with warm-up, CPU
temperature/frequency, benchmark order, and background load logged and
controlled) would be needed to characterize session-level variance
properly and is left to future work.

## Addendum, 2026-09-29

All experiments in this archive were rerun on 2026-09-29 on the same Raspberry Pi 5
with unmodified benchmark code and configuration. Two observations bear on the
cross-session comparison above.

1. **Decode latency agrees across two September sessions.** Benchmark B's decode
   stage mean was 1067 +- 9 ns in the 2026-09-02 session and 1071 +- 6 ns in the
   2026-09-29 session (garbage collector disabled). The August-session value of
   about 2.03 us therefore remains an outlier that this investigation did not
   explain; a direct rerun of the August script gave about 1.04 us.
2. **A harness artefact inflated some means.** With CPython's default cyclic
   garbage collector, one generation-2 collection of about 5.4 ms lands on one
   frame (index about 1590-1600) in 4 of 5 Benchmark B trials and 3 of 5
   ablation trials. It raises the all-arrivals mean from 4.17 to 4.63 us and
   the decode-stage mean from 1071 to 1471 ns, with a large trial-to-trial
   spread. With `gc.disable()` (see `code/run_gc_disabled.py`) no frame exceeds
   44 us. The 2026-09-02 data contain the same pause, so its all-arrivals means
   were inflated in the same way.

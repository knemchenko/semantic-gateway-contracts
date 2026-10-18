# Earlier-session reference data (cross-session decode-latency comparison)

This directory is **not** part of this paper's current claims or Evaluation
section results. It is included solely so a reviewer can independently
verify the cross-session decode-latency comparison described in
`PROVENANCE_NOTE.md` (top-level directory) and in the manuscript's
Section 8.4/Limitations, without having to trust that comparison's prose
on faith.

## What this is

The raw, unmodified contents of an earlier Raspberry Pi 5 measurement
session's own Benchmark B run, recovered from a private evidence bundle
prepared at that time (`evidence_bundle_2026-08-17.zip`). This session used
an **older six-stage / four-constraint schema**: no separately-timed
channel-resolution ($J$) bracket, no `channel_ns` column, and a Contract
Gate that had not yet been extended to check $q=q^{*}_{d,a}$ and
$u=u_{d,a}$ explicitly. `semantic_filtering_v2_earlier_session.py`,
`experiment_config_v2_earlier_session.json`, and
`canonical_registry_earlier_session.json` are that earlier session's own
code/config/registry, byte-for-byte as recovered, not modified to match
the current schema.

## Computing the earlier session's decode-stage mean correctly

**Filter to `actual_result == "accept"` before averaging `decode_ns`** —
this is what `semantic_filtering_v2.py`'s own summary-statistics code does
(`accepted_rows = [r for r in rows if r["actual_result"] == "accept"]`,
then `statistics.fmean` over that subset only). Averaging `decode_ns` over
*all* frames with `decode_ns > 0` (i.e., including Gate-rejected frames
that still ran decode) will include occasional multi-millisecond outlier
frames among the rejected population and give a substantially inflated,
misleading mean — this was confirmed directly while preparing this
archive. Per-trial accepted-only decode means for this earlier session
(recomputed from `experiment3_v3_repeats/raw_csv/run3_{1..5}_results.csv`):
approximately 1950-2000 ns per trial, consistent with the
$2031\pm9\,$ns figure `PROVENANCE_NOTE.md` and the manuscript discuss as
the earlier session's originally-reported value.

## What this does NOT establish

This is one archived session, not a controlled multi-session study. It
corroborates that the earlier session's own recorded decode-stage cost was
genuinely close to 2\,$\mu$s (not a transcription error or a value invented
after the fact), and, combined with `PROVENANCE_NOTE.md`'s direct rerun of
this same unmodified script on the current hardware (which reproduces the
current, lower figure instead), supports treating the decode-stage
cross-session difference as a real measurement phenomenon rather than a
code or configuration change. It does **not** establish *why* the two
sessions differ, nor does it substitute for the 3-5 independent
sessions/reboots a controlled study of session-level variance would need
(manuscript Limitations, Section 10).

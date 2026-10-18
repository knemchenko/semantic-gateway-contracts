# Notation correspondence across the paper series

Referenced from the manuscript (Section 6, "Target Contracts and Canonical-Identity Linking"),
which points here instead of reproducing this table in the main text.

Of the two prior notations below, only BlackSeaCom is an independently citable,
published source. "DUIKT" denotes an earlier, unpublished formalization draft by
the same authors; it is not in the manuscript's reference list because it is not
independently available, so its entries should be read as the authors' own
working notes on their own prior notation choices, not as claims independently
verifiable against a citable source.

| This paper | Prior notation | Role |
|---|---|---|
| $\sigma=(p,f,v_s)\in\Sigma$ | DUIKT: $\Phi(d) = \langle D_d, M_d, V_d, T_d \rangle$; BlackSeaCom: $\rho(d,v) = (f_{d,v}, m_{d,v}, \kappa_{d,v}, c_{d,v})$ | Per-protocol codec/mapping artifact (both prior notations index it as if per-device; their reference code indexes it per-profile) |
| $\tau(d,a)$ | Not previously separated | Per-device/channel canonical concept, schema, unit, constraints, topic |
| $N(m,\sigma,(d,a)) \rightarrow \tilde c$ | DUIKT: $y_i = M_d(D_d(x_i))$; BlackSeaCom: $u = \mathcal{N}(y, c_{(d,v)})$ | Decode -> map -> convert -> project-to-contract |
| $G(\tilde c)$ | DUIKT: $V_d(y_i) = 1$; BlackSeaCom: $\Phi_v(x) \in \{u, \bot\}$ | Validation gate, now after normalization |
| $S_{d,a}, T_{d,a}$ | BlackSeaCom: $S_{(d,v)}, T_{(d,v)}$ (contract invariance stated in prose) | Contract schema and topic anchor, now used in a proved proposition |

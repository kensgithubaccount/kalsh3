# CPI-E1-P4 — Acquisition Capability Containment Addendum

This addendum records the in-flight blocker-class hardening applied after the exact-canonical precedent audit. It does not change P4 architecture or authority semantics.

## Finding recorded

Python underscore privacy is not treated as an authority boundary. The acquisition module's `_ACQUISITION_EVIDENCE_CAPABILITY` and `_ISSUED_ACQUISITION_FINGERPRINTS` are therefore protected by the same reviewed static-architecture trust model used for P2's private publication-authority seam.

Production references under `services/**/*.py` and `scripts/**/*.py` are constrained so that these acquisition-authority internals may appear only in:

- `services/forecasting/cpi_source_acquisition.py`.

The private `_TransportResult` symbol is likewise confined to the acquisition module. Direct production construction via `CPIBLSAcquisitionEvidence(` is also confined to that module, while legitimate imports of the public evidence type for annotation, validation, and downstream binding remain allowed.

Tests may reference these internals for adversarial validation; they are intentionally outside the production scan.

This guard is designed to fail if a future production module attempts either direct-import or module-qualified access such as `_ACQUISITION_EVIDENCE_CAPABILITY` or `_ISSUED_ACQUISITION_FINGERPRINTS`. It does not attempt to solve arbitrary Python runtime introspection.

## Canonical precedent conclusion

The precedent audit confirms that M28B is the strongest existing acquisition-bound evidence pattern: semantic parsing remains separate from acquisition authority, and authoritative downstream evidence is bound to reviewed fixed-origin public acquisition.

M28B `PublicPageEvidence` retains response hashes and row bindings but does not retain the exact raw response body. P4 intentionally strengthens that precedent by retaining the exact BLS response bytes in `CPIBLSAcquisitionEvidence`, together with their SHA-256 and byte count. This exact raw-body retention remains required.

The audit also confirms that `ForecastSourceArtifact` and `CPIHistoricalReleaseArtifact` are structural identity objects only. Neither object proves that its bytes were actually acquired from the claimed source. P4 acquisition evidence remains the sole new proof of that fact before P3 parsing and P2 publication issuance.

## Architecture preserved

No redesign was made. P4 continues to use:

- a BLS-specific fixed-origin HTTPS GET transport;
- acquisition evidence issued immediately after reviewed transport;
- exact raw-byte retention and acquisition fingerprint validation;
- a separate `cpi_evidence_issuer.py` bridge;
- parser-only P3 semantics;
- exactly one new production consumer of P2's private publication-authority capability;
- distinct historical publication, reconstructed replay, and present-day acquisition timestamps.

No generic transport framework, pinning scheme, persistent acquisition journal, process-isolation layer, signed local storage, anti-replay protocol, or full CPI corpus acquisition was added.

# CPI-E1-P7R — Process-Local Issuance Identity Repair

## Scope and provenance

- Requested canonical base: `7aa43ea605fb44bc7db2572385bc61382ad5d5e1`.
- Branch: `cpi-e1-p7r-issuance-identity-repair`, created fresh from `origin/main`
  at that exact commit.
- This is a narrow repair to exactly one file's issuance-identity mechanism:
  `services/forecasting/cpi_settlement_reconciliation.py`. It does not
  re-review P1–P6, does not change any P7 settlement determination, and does
  not add execution, credential, account, or production authority.
- This checkpoint was discovered independently, not invented here: a separate
  M27B.2 continuous structural-measurement PR (#113) exposed the underlying
  flaw as a fresh-CI failure while adding unrelated tests elsewhere in the
  repository. PR #113 was correctly left untouched and declared blocked by
  this bug rather than absorbing an unrelated fix. This PR is the standalone
  repair; PR #113 depends on nothing here.

## Exact prior vulnerability

`KalshiHistoricalAcquisitionEvidence` is the sole positive-authority carrier
for CPI-E1 P7's historical Kalshi acquisition evidence. Its reviewed `__init__`
is the only path that can legitimately construct one (gated by a private
`_ACQUISITION_CAPABILITY` sentinel). At issuance it recorded:

```python
_ISSUED_KALSHI_ACQUISITION_FINGERPRINTS[id(self)] = fingerprint  # dict[int, str]
```

and `validate_kalshi_acquisition` required:

```python
_ISSUED_KALSHI_ACQUISITION_FINGERPRINTS.get(id(evidence)) == expected_fingerprint
```

`id()` in CPython is a memory address, guaranteed unique only among objects
with **overlapping lifetimes**. The registry was a plain `dict`, never
pruned. Once an issued object was garbage-collected, its stale
`{address: fingerprint}` entry remained. A later object — including one
constructed via `object.__new__` that bypasses the reviewed `__init__`
entirely, which is exactly the forgery this check exists to reject — could be
allocated at that same now-reused address. If its (forged or copied) fields
happened to produce the same fingerprint as the stale entry, which is
guaranteed whenever the forged object is a value-equal copy of a real,
previously-issued object (a `copy.copy()` reproduces every field, including
`issuance_fingerprint`/`evidence_id` themselves), validation would incorrectly
pass.

This was not theoretical: `tests/test_cpi_settlement_reconciliation.py::test_reconstructed_public_fields_do_not_inherit_issuance`
exists specifically to catch it, and began failing — deterministically, on
GitHub's isolated CI runner as well as locally — once a larger, unrelated test
suite (PR #113's) shifted allocator/GC timing enough to make the address
reuse land on a matching stale entry. The repository's own small, repeatedly
reloaded fixture set (`market-jul`, `market-dec`, `market-jan`, ...) made this
far more likely than a naive "random collision" model would suggest: many
objects sharing the *same* fixture, and therefore the *same* fingerprint, are
constructed and discarded throughout any given test session, so a reused
address is disproportionately likely to have last held an object whose stale
fingerprint matches whatever fixture is validated next.

## Exact new mechanism

```python
_ISSUED_KALSHI_ACQUISITION_EVIDENCE: WeakValueDictionary[
    int, KalshiHistoricalAcquisitionEvidence
] = WeakValueDictionary()
```

- **Issuance**: `_ISSUED_KALSHI_ACQUISITION_EVIDENCE[id(self)] = self` — the
  registry stores the issued *object itself* (as a weak value), not a
  detached fingerprint string.
- **Validation**: `_ISSUED_KALSHI_ACQUISITION_EVIDENCE.get(id(evidence)) is not evidence`
  — an **identity** (`is`) check against the retrieved value. The dict key is
  a plain `int` (`id()`), which is safe to hash/compare normally; the
  authority decision is the identity check on the value, never the key
  lookup and never the dataclass's own equality.
- `KalshiHistoricalAcquisitionEvidence`'s decorator gained `weakref_slot=True`
  (`@dataclass(frozen=True, slots=True, init=False, weakref_slot=True)`, a
  Python 3.11+ dataclass parameter; this repo requires `>=3.12`) so instances
  support weak references under `slots=True`. This is the only class-shape
  change; it adds a `__weakref__` slot and nothing else — no change to
  `__eq__`, `__hash__`, `__repr__`, or any public field.

### Why this is safe

`WeakValueDictionary` drops an entry the instant its referent is
garbage-collected — there is no window where a dead object's entry can
survive to be matched by a later, unrelated object. Any object being
validated is, by definition, alive at the moment of the `.get()` call.
Combined with CPython's guarantee that `id()` is unique among *simultaneously
live* objects, `_ISSUED_KALSHI_ACQUISITION_EVIDENCE.get(id(evidence))` can only
ever return exactly one of:

1. `evidence` itself (genuinely issued, still alive) — validates.
2. `None` (never issued, or issued-but-now-collected) — does not validate.

A third case — some *other* currently-live object occupying the same `id()`
— is excluded by CPython's own id-uniqueness-among-live-objects contract.
There is no path left for a resurrected stale entry to bless a new object,
and this holds regardless of copy, reconstruction, or forged-field mutation,
because none of those paths ever re-enter the reviewed `__init__` that
performs the registration.

This is why a bare `WeakKeyDictionary`/`WeakSet` keyed on the dataclass
instance itself would **not** have been safe: `KalshiHistoricalAcquisitionEvidence`
is `@dataclass(frozen=True, ...)` with default `eq=True`, which generates a
value-based `__hash__`/`__eq__`. Membership/lookup keyed that way goes through
value equality, not identity — so a distinct, value-equal reconstructed
object (precisely the "reconstructed public fields" attack) would incorrectly
match. Keying by `id()` (a plain int with ordinary hash/eq semantics) and
deciding authority via `is` on the retrieved value sidesteps that trap
entirely, per the review's explicit requirement not to trust equality
semantics for this decision.

## Regressions added

New file `tests/test_cpi_settlement_reconciliation_issuance_identity.py`:

- `test_an_issued_object_validates` — requirement 1.
- `test_copy_copy_of_issued_object_does_not_validate` — requirement 2/4.
- `test_object_new_reconstruction_does_not_validate` — requirement 3.
- `test_identical_value_equal_reconstructed_object_does_not_validate` —
  requirement 4 (explicit value-equal, non-identical object).
- `test_mutation_with_recomputed_public_fingerprint_does_not_validate` —
  requirement 4 (a coherent, recomputed fingerprint is still insufficient).
- `test_dead_registry_entry_is_pruned_and_cannot_bless_a_new_object` —
  requirement 5/6, direct proof the registry self-prunes on GC.
- `test_repeated_construct_discard_cycles_never_falsely_validate` —
  requirement 5/6 under construct/discard churn (2,000 cycles), the exact
  pattern that exposed the original bug; also asserts the registry returns to
  size 0 after collection (no unbounded stale-address accumulation).
- `test_legitimate_fixture_reload_still_validates_for_every_fixture` —
  requirement 7, all eight reviewed fixtures.
- `test_p7_matched_settlement_labels_are_unchanged` — requirement 9, pins the
  exact three historical exchange-final result/value pairs
  (July 2025 / December 2025 / January 2026, all YES / `$1`) already asserted
  by the existing `test_real_frozen_public_markets_match`.

The existing `test_reconstructed_public_fields_do_not_inherit_issuance` was
not weakened or removed; it now passes deterministically rather than
probabilistically (verified across repeated back-to-back full-file runs).

`tests/test_cpi_settlement_architecture.py`'s `PRIVATE_NAMES` guard was
updated to protect the renamed symbol
(`_ISSUED_KALSHI_ACQUISITION_FINGERPRINTS` → `_ISSUED_KALSHI_ACQUISITION_EVIDENCE`);
its behavior and the modules it protects are otherwise unchanged.

## Requirement checklist

1. **An issued object validates** — `test_an_issued_object_validates`; also
   exercised transitively by every existing P7 test that loads a fixture.
2. **`copy.copy(issued)` does not validate** —
   `test_copy_copy_of_issued_object_does_not_validate`.
3. **Reconstructed public fields do not validate** —
   `test_object_new_reconstruction_does_not_validate`; the pre-existing
   `test_reconstructed_public_fields_do_not_inherit_issuance` now passes
   deterministically.
4. **Coordinated field/fingerprint mutation does not validate** —
   `test_identical_value_equal_reconstructed_object_does_not_validate` and
   `test_mutation_with_recomputed_public_fingerprint_does_not_validate`;
   neither a value-equal copy nor a caller who recomputes a coherent
   fingerprint can substitute for the exact issued instance.
5. **Object-address reuse can never transfer authority** — proved by the
   id-uniqueness-among-live-objects argument above and stress-tested by
   `test_repeated_construct_discard_cycles_never_falsely_validate`.
6. **Registry state does not accumulate unsafe stale-address authority** —
   `test_dead_registry_entry_is_pruned_and_cannot_bless_a_new_object` proves
   the registry returns to empty once referents are collected; no manual
   cleanup code is needed or present.
7. **Frozen reviewed fixture reload remains supported exactly as before** —
   `test_legitimate_fixture_reload_still_validates_for_every_fixture` over
   all eight fixtures; `load_frozen_kalshi_acquisition` is unmodified.
8. **`research_only` / `production_influence` unchanged** — neither field,
   nor any code path touching them, was modified.
9. **P7 settlement labels remain exactly unchanged** —
   `test_p7_matched_settlement_labels_are_unchanged` plus the pre-existing
   `test_real_frozen_public_markets_match` (both untouched and still passing).
10. **No expansion of P7 scope** — the diff touches exactly one production
    file's issuance-registry mechanism, one class decorator, one guard test's
    protected-name set, and adds one new regression test file.

## Sibling-module scope note

The identical unsafe pattern — a `dict[int, str]` registry keyed by bare
`id(self)`, written at issuance and read at validation — also exists in four
other P1–P6 modules, apparently from the same original template:

- `services/forecasting/cpi_manual_acquisition.py` (`_ISSUED_MANUAL_FINGERPRINTS`)
- `services/forecasting/cpi_initial_release_value.py` (`_ISSUED_FINGERPRINTS`)
- `services/forecasting/cpi_source_acquisition.py` (`_ISSUED_ACQUISITION_FINGERPRINTS`)
- `services/forecasting/cpi_pit_availability.py` (`_ISSUED_PUBLICATION_FINGERPRINTS`)

None of these were touched by this checkpoint — the request scoped this
repair to P7 only. Each carries the same latent id()-reuse gap and would need
an identical repair as separate, independently reviewed follow-up work.

## Claims not established

This repair does not re-review or re-validate P1–P6's own evidence chains,
does not change any settlement determination or reconciliation result, does
not add network, credential, account, signer, risk, order, or execution
authority, and does not assert that the four sibling modules above are safe —
they are explicitly flagged as carrying the same unrepaired defect. It also
does not assert general soundness of process-local, id()-keyed registries as
a pattern; it establishes soundness only for this specific
`WeakValueDictionary`-plus-identity-check construction, under CPython's
documented `id()` and garbage-collection semantics.

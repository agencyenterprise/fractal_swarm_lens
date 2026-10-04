# Historical validation record

This records local checks for the paper reconstruction, not a benchmark reproduction.

- Baseline `4b93f0e13570a621b6f46e70c79db95e75a006d0`: **22 tests passed**.
- Completed implementation: **70 tests passed**, including 48 new observability tests/cases, using Python 3.12 and NumPy 1.26.4 in the supplied Conda runtime.
- Command: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src python3 -m pytest -p no:capture -q`.
- The existing FastAPI/Starlette test-client deprecation warning remains; no test failures.
- `python3 -m pip wheel --no-deps --no-build-isolation .`: wheel build succeeded (build output kept outside the checkout).
- `PYTHONPATH=src python3 -S -c 'import swarm_lens; import swarm_lens.observability'`: succeeded without site-packages, verifying optional numerical dependency isolation.
- All relative documentation links resolve and `git diff --check` passes.

## Executed examples

The original validation included numeric synthetic, direct-attribution, nested-branch, and Village schema-audit CLIs. Those standalone examples were retired in the CrewAI documentation cleanup. The observations below describe that historical validation, not the current test-suite count.

Current regression coverage is maintained under `tests/observability/caspian/`, with shared numeric helpers under `tests/support/`:

```sh
python -m pytest tests/observability/caspian -q
```

The synthetic run alerts at turn 8 (estimator warmup), before the specified dependence change at turn 60. Its JSON explicitly labels that limitation. Attribution completes and reports all three roles, rankings, paths, and dominant channel scores. The direct-attribution example exercises interval maxima independently of LI-CTE. The branch example confirms six observed turns produce identical outputs across the historical parent and nested child, while the recorded intervention changes the history digest. A separate test adds a genuinely different observed child suffix and verifies only its analysis changes.

The read-only audit was also run against the original local AI Village export. It reports insufficient directed evidence. Only schema/counts were inspected; no payloads, data files, environment values, or credentials were copied into this checkout. The original checkout and demo server were not modified or replaced.

## Independent oracles

Tests use closed-form Gaussian CMI, conditional independence given a common history, a multivariate log-determinant identity distinct from the implementation's Schur-complement calculation, explicit batch exponential covariance weights, hand-computed singular values and role scores, and brute-force path permutations. Branch/history tests exercise the actual SQLite framework, not mocked storage.

Multi-turn rule tests use controlled signal sequences because the paper's literal weak-link and phase predicates often make WATCH immediately confirm. They establish state-machine correctness, not the empirical frequency of gradual cascades. Degeneracy tests separately document why those empirical claims cannot be assumed.

No TAMAS/ACIArena runs, model calls, A100 timing, AUROC/TPR/EDR comparisons, bootstrap confidence intervals, or published ablation results were reproduced. Missing reference implementation/encoder/settings/traces and contradictory rules prevent asserting exact numerical parity independently of compute cost. See [coverage](coverage.md).

## Text embedding adapter follow-up

After adding `text-embedding-3-small`, **79 tests pass**. Nine additional adapter tests/cases cover model selection, batch order, provenance, empty/invalid inputs, invalid dimensions, and malformed provider responses. These tests use fake clients; no live API request or benchmark run was performed during this change.

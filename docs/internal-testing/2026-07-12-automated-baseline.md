# Native automated test baseline — 2026-07-12

## Environment

- Interpreter: `/Users/zhaoqianxue/Desktop/UA/ReviewPilot/.worktrees/inner-beta-foundation/.venv-native/bin/python`
- Interpreter file type: `Mach-O 64-bit executable arm64`
- Python: `3.12.13`
- pytest: `9.1.1`
- Local prerequisite: `config.py` is an ignored symlink to the configured main checkout at `/Users/zhaoqianxue/Desktop/UA/ReviewPilot/config.py`. No configuration values were inspected or recorded.
- `secrets.txt` is absent and was not used.

This native arm64 run is the current baseline. Historical x86/Rosetta test runs had hung; that observation is context only and is not part of the native result reported here.

## Collection

Command:

```text
.venv-native/bin/python -m pytest --collect-only -q
```

- Exit code: `0`
- Result: `286 tests collected in 1.94s`
- Collected: `286`
- Failed: `0`
- Errors: `0`
- Skipped: `0`
- Runtime deselections/skips: none; one stale scratch-dependent test was deleted before this baseline, as documented below.
- Warnings shown during collection: 7 pytest warnings — 1 `FutureWarning`, 5 `DeprecationWarning`, and 1 `StarletteDeprecationWarning`. Python additionally printed 1 SWIG `DeprecationWarning` at interpreter shutdown outside pytest's counted warning summary.

## Full suite

Command:

```text
.venv-native/bin/python -m pytest -q
```

- Exit code: `0`
- Result: `286 passed, 7 warnings in 95.51s (0:01:35)`
- Passed: `286`
- Failed: `0`
- Errors: `0`
- Skipped: `0`
- Runtime deselections/skips: none; one stale scratch-dependent test was deleted before this baseline, as documented below.
- Warnings: 7 pytest warnings — 1 `FutureWarning` from the deprecated `google.generativeai` package, 5 SWIG `DeprecationWarning` instances, and 1 `StarletteDeprecationWarning` for the `httpx`/`starlette.testclient` integration. Python additionally printed 1 SWIG `DeprecationWarning` at interpreter shutdown outside pytest's counted warning summary.

## Baseline repair

The stale `FastPdfDownloaderTests.test_benchmark_runner_can_create_optimized_downloader` test was removed because it dynamically imported `.benchmark_step3_download/benchmark_step3_download.py`, an intentionally ignored scratch file that cannot exist in a clean checkout. The scratch implementation was not copied or tracked. The tracked `test_production_pdf_downloader_factory_defaults_to_fast_downloader` test remains and continues to verify the supported production factory contract; its targeted run passed with `1 passed, 6 warnings in 1.08s` and exit code `0`.

## Conclusion

The repository's native arm64 automated baseline is green under the stated local prerequisite: all 286 collected tests pass, with no failures or errors. Runtime deselections/skips: none; one stale scratch-dependent test was deleted before this baseline, as documented above. The remaining seven pytest warnings are dependency deprecations and do not change the pass result.

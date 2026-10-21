# Contributing

hass-sip is a HACS **Default-listed custom** integration (not part of Home Assistant Core) that follows [hass-repo-standards](https://github.com/eigger/hass-repo-standards); the adopted commit and exceptions are in [STANDARDS.md](STANDARDS.md) and the status in [ADOPTION_CHECKLIST.md](ADOPTION_CHECKLIST.md).

## Layout

- `custom_components/sip/` — the integration (`sip_client/` holds the SIP/RTP stack).
- `tests/components/sip/` — tests, laid out like Home Assistant Core. They run against a real `homeassistant` with `pytest-homeassistant-custom-component`; the SIP network boundary uses the mock PBX in `mock_pbx.py`. Assist tests also patch parts of `assist_pipeline`; this tracked TEST-06 exception is documented in `STANDARDS.md`.
- `requirements_test.txt` — current HA test environment. `requirements_test_min.txt` — the exact minimum HA from `hacs.json`. `requirements_lint.txt` — pinned Ruff.

## Local setup and checks

CI installs the requirements of the HA components the integration depends on (`ha-components` in `.github/workflows/tests.yml`: ffmpeg, llm, logbook, media_source, tts, assist_pipeline, stt). Locally, install the same set once with the test requirements, for example for the current environment:

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt -r requirements_lint.txt \
  SQLAlchemy==2.0.54 fnv-hash-fast==2.0.3 gazetteer-matcher==1.1.0 ha-ffmpeg==3.2.2 hassil==3.12.1 \
  home-assistant-frontend==20260930.4 home-assistant-intents==2026.10.6 mutagen==1.48.1 \
  psutil-home-assistant==0.0.1 pymicro-vad==1.0.1 pyspeex-noise==1.0.2
.venv/bin/python -m pytest tests/components/sip/ -v
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
```

To test the minimum supported Home Assistant, install `requirements_test_min.txt` into a separate virtualenv (same component requirements, but `home-assistant-frontend==20260930.2`) and run the same pytest command. These pins track the installed HA; when HA is updated, regenerate them from the `ha-components` step output in CI.

## Rules of thumb

- Fix bugs with a regression test; mock only external communication (sockets, the PBX), not Home Assistant.
- Raising the minimum HA version in `hacs.json` needs a concrete incompatibility with older versions and an explicit maintainer decision; failing latest-HA tests are fixed, not a reason to raise the minimum.
- Keep Dependabot out of `requirements_test_min.txt`. When `requirements_test.txt` changes the HA version, update `expected-ha-version` in `.github/workflows/tests.yml` too.
- Verified environments are recorded in [ADOPTION_CHECKLIST.md](ADOPTION_CHECKLIST.md); untested combinations are not claimed as supported.
- Report issues without credentials: strip passwords, PBX addresses and phone numbers from logs and diagnostics.

## Shared standards checks

Use the reviewed standards checkout recorded in `STANDARDS.md` (do not copy its AGENTS.md into this repository):

```bash
python /path/to/hass-repo-standards/scripts/check_integration.py .
python /path/to/hass-repo-standards/scripts/sync_badges.py . --check
```

The Validate workflow runs the same central contract check with Python 3.14. The README managed regions are rendered from `.hass-standards.json`; preserve integration-specific content when updating them. On Windows, add `-X utf8` to the Python commands.

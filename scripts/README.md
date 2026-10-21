# Commands

Run `python scripts/acceptance.py --project <external-project>` in the `bpreviewer` environment. It reads Python files, calls the local API in-process, rewires the synthetic manifest example and verifies source hashes before/after. Results and Change Requests live only under ignored `state/acceptance/`. The external project is never imported or executed.

Generate the versioned manifest schema with python scripts/export_schema.py. It writes LF text for stable review on Windows and Linux.

# Commands

Run `python scripts/acceptance.py --project <external-project>` in the `bpreviewer` environment. It reads Python files, calls the local API in-process, rewires the synthetic manifest example and verifies source hashes before/after. Results and Change Requests live only under ignored `state/acceptance/`. The external project is never imported or executed.

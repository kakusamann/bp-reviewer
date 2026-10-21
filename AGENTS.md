# BP-Reviewer guidelines

Use the independent `bpreviewer` environment and pinned Node/npm versions. Never import or execute inspected project code; never add scientific model/database dependencies. Project state and private profiles belong under ignored `state/`, outside target projects. No generated output, credentials, licensed data or local machine paths in Git.

Keep As-is immutable; all edits belong to Proposed. Preserve evidence provenance and unknown-type warnings; never label static inference as observed execution. Update affected README files and run backend tests, frontend tests/build and Ruff before review. Remote push/PR/publication and root worktree retirement require explicit user approval.

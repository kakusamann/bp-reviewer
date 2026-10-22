# Research Workflow Sample

This is an **entirely synthetic and non-executed** KGC/LCA mini project for BP-Reviewer user acceptance testing. No numerical result here is a research finding. It uses only Python standard-library syntax and simple local text fixtures.

Expected user-facing areas: knowledge-graph completion (data contract, model selection, training, metrics), background mapping, and LCIA assessment. Directory naming is illustrative, **not** a fixed ontology BP-Reviewer is allowed to hard-code.

In the blueprint, select the KGC domain. Its workflow should expose model registry candidates (TransE, RotatE, RGCN), config values (model = RotatE, training.embedding_dim = 200, training.learning_rate = 0.001, training.epochs = 15), and the clearly labeled synthetic historical result record (run_id = demo-run-001, MRR = 0.37). It must distinguish **available models** from the single **recorded run**.

Clicking a configuration item or metric should locate its actual file. No model should be executed, trained, or edited by BP-Reviewer.

See `docs/claude-code-v5-acceptance.md` for an executable, step-by-step test and reporting template.

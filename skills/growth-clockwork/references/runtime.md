# Growth Clockwork runtime commands

Run commands from the repository root. Replace `<project>` with one explicitly
selected profile directory, such as the included `example` fixture or another
installed profile.

## Inspect or resume

The normal entry point consumes a saved brief and resumes its existing cycle:

```bash
python3 -m pipeline.autopilot --workspace . --project-config projects/<project>/project.json
```

Add `--inspect` for read-only status. `dispatch` includes frozen role contracts,
verified inputs and deliverable requirements; execute those tasks with native
Codex workhorses, then call the driver again until `awaiting_review`. `settled`
means the same brief is already handled. Do not produce duplicates or replace
actual execution with another preparation report.

```bash
python3 -m pipeline.runtime_status \
  --workspace . \
  --project-config projects/<project>/project.json
```

The JSON response is read-only. Dispatch only the tasks it reports as
`dispatchable`.

## Prepare a coordinator plan

Start from `projects/example/fixtures/runtime/research-evidence-plan.json` and the
canonical schema at `contracts/schemas/coordinator-plan.schema.json`.

```bash
python3 -m pipeline.coordinator prepare \
  --workspace . \
  --project-config projects/<project>/project.json \
  --plan /absolute/path/to/coordinator-plan.json
```

## Accept one workhorse result

Prefer the production packager for an explicitly authored bundle:

```bash
python3 -m pipeline.workhorse submit --workspace . \
  --project-config projects/<project>/project.json \
  --task-ref handoffs/<run>/<task>.json --bundle /absolute/path/to/bundle.json
```

Read `pipeline/workhorse.py` for its bundle boundary. Payloads, source content,
quality verdicts and measured attempt duration are authored inputs; the packager
only binds task identity and hashes. Same-transaction evidence is accepted only
inside the role's write roots with exact declared evidence pins. It cannot mark
an artifact human-approved. Preserve the original bundle for reproducibility.

The workhorse must return a `result-envelope@2` matching
`contracts/schemas/result-envelope.schema.json` and the exact dispatchable task.

```bash
python3 -m pipeline.run_engine accept \
  --workspace . \
  --project-config projects/<project>/project.json \
  --submission /absolute/path/to/result-envelope.json
```

Accept all tasks in the current parallel DAG wave, then inspect status again.

## Run the Review Desk

Terminal 1:

```bash
python3 -m pipeline.review_api --workspace . --port 8765
```

Terminal 2:

```bash
cd dashboard
npm run dev -- --host 127.0.0.1 --port 4173
```

Open `http://127.0.0.1:4173/`. Use normal API mode. `?demo=1` is a labeled visual
fixture and never proof of runtime state.

## Prepare a note-driven rework

```bash
python3 -m pipeline.rework \
  --workspace . \
  --project-config projects/<project>/project.json \
  --action-ref outbox/review-actions/<artifact-id>/<revision>.json
```

Use the exact action ref returned by the Review API. Resume from runtime status.

## Prepare an approved release

```bash
python3 -m pipeline.release_core \
  --workspace . \
  --project-config projects/<project>/project.json \
  --artifact-id <artifact-id> \
  --artifact-revision <revision> \
  --artifact-sha256 <sha256>
```

Then call `pipeline.release_adapter` only with an adapter ID and destination already
enabled by the selected project. The repository currently includes a synthetic,
local-filesystem fixture adapter only. It is not live publication.

## Full local validation

```bash
python3 -m unittest discover -s pipeline/tests -p 'test_*.py' -v
python3 -m pipeline.validate_json_suites \
  --project-config projects/example/project.json \
  projects/example/fixtures/schema-contracts/suite.json
python3 -m py_compile pipeline/*.py pipeline/adapters/*.py
(cd dashboard && npm test && npm run typecheck && npm run build)
git diff --check
```

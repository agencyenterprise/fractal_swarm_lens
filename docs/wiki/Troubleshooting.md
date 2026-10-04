# Troubleshooting

## My colleague has different conversations

Check the server's `--data` path. Git shares code and bundled samples, not local SQLite runs. Ask for an exported run bundle, then use **Import run**, or import the same [bundled example](Examples-and-datasets). Resetting SQLite does not bring someone else's data over.

## The UI looks new but actions fail

Restart the Python server after changing branches. Static files can update on disk while the running backend retains old imports. Reload the browser after the restart. Do not start old code against a schema-v2 workspace.

## Live execution is unavailable

Install the pinned CrewAI integration, configure model credentials, and inspect the fork preview. Native applications require a compatible runtime revision. Imported traces require supported state and executable mappings for recorded tools. Timeline playback itself does not require a runtime or a model.

## Reports is empty

Reports are scoped to the selected branch and workspace. Importing a conversation does not automatically run MAST. Open the analysis dialog and review its size preview. Submitting is a separate paid operation.

## Trace exceeds the analysis limit

The size preview includes prompt and output budgets. Choose a smaller prefix and label it as partial, or explicitly configure a supported model/context budget. Do not silently truncate and describe it as a complete trace.

## Medical task labels look wrong

Inspect the aggregator's answer and the native grader result. Upstream option extraction can misread an answer. Retain the raw label and any corrected interpretation separately; neither is automatically a cascade label.

## What to include in an issue

Include your Git commit, Python version, runtime version, startup command without secrets, selected data directory, steps to reproduce, and relevant sanitized error text. Never attach `.env` files or credentials. [Open an issue](https://github.com/agencyenterprise/fractal_swarm_lens/issues).

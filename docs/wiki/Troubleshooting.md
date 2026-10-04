# Troubleshooting

## My colleague sees different conversations than I do

Check which `--data` path each of you started the server with. Git shares the code and bundled samples, but not anyone's local SQLite runs. Ask your colleague to export the run and load it with **Import run**, or both import the same [bundled example](Examples-and-datasets). Resetting your database won't bring their data over.

## The UI updated but actions are failing

Restart the Python server whenever you switch branches. The static files change on disk right away, but a running backend keeps its old code in memory. After restarting, reload the browser. Also, don't point an older checkout at a workspace that's already been upgraded to schema v2.

## Live execution is unavailable

Install the pinned CrewAI integration, set up your model credentials, and look at what the fork preview says. A natively captured app needs a compatible runtime revision. An imported trace needs supported state and an executable mapping for every tool it recorded. Note that you don't need any of this just to play back the timeline.

## Reports is empty

Reports only shows analyses for the branch and workspace you're looking at. Importing a conversation doesn't run MAST automatically. Open the analysis dialog, look at the size preview, and submit if you're happy with it; submitting is the part that costs money.

## The trace is too large to analyze

The size preview includes the prompt and output budgets. You can analyze a shorter prefix (and label the report as partial), or configure a supported model with a bigger context budget. Please don't truncate a trace quietly and then describe the report as covering the whole thing.

## The medical task labels look wrong

Look at the aggregator's actual answer next to the native grader's result. Upstream's option extraction sometimes misreads answers. Keep the raw label and your corrected reading as separate fields. Neither one is a cascade label.

## Filing an issue

It helps a lot if you include your Git commit, Python version, runtime version, the command you started the server with (minus any secrets), which data directory you used, steps to reproduce, and any error text with sensitive bits removed. Please never attach `.env` files or credentials. [Open an issue](https://github.com/agencyenterprise/fractal_swarm_lens/issues).

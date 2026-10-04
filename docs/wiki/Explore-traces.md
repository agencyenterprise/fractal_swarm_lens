# Explore traces

## Timeline

Choose a run, then a branch. Move the cursor to inspect the state at a particular event. The transcript preserves later messages but dims them; the inspector shows the selected event, agent, or run. Use the View and Show controls to include tools, memories, and observations.

The visualization switch changes the panel above the transcript:

| View | Question it helps inspect |
| --- | --- |
| Lanes | Who acted, and in what order? |
| Influence | Which agents' messages were read by others? |
| Blast radius | Which later messages have a recorded path from a selected message? |
| Phrase spread | Where does a chosen phrase appear across agents and stages? |
| Echo | How much five-word phrase overlap exists with messages an agent read? |
| Activity | Where do recorded activity, length, tokens, or latency change? |

These are descriptive views, not validated causal or cascade classifiers. Influence uses delivered-source links where available, with reply/channel fallbacks. Inspect provenance before interpreting an edge as evidence of exposure. Missing measurements are not zeros.

## Compare

Open Compare and explicitly choose the two branches or paired runs. Review the final outcomes and then the stage-aligned transcript. Independent control and attack runs need not share a common history. A comparison alone does not control for model stochasticity or establish causality.

## Branch

Select an event and choose **Fork here** (keyboard: `F`). Other fork actions change a goal, prompt, or agent configuration. A branch preserves history through its fork point. Creating a branch stores an experiment; **Create and run** additionally executes through an available runtime. See [branching semantics](Live-collection-and-branching).

## Reports and sharing

Reports contains saved MAST analyses for the selected branch. The analysis dialog starts a job; the workspace report displays its result. An empty Reports view does not mean the conversation has no failures—it means no report is available there.

Use **⋯ → Export run** to download a JSON bundle. A colleague uses **Import run** to load that evidence into their own workspace. Local comments and branches are part of the workflow; Git does not automatically synchronize SQLite databases.

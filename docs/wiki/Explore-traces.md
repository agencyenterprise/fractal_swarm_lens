# Explore traces

## Timeline

Pick a run, then a branch. As you move the cursor, the explorer shows the state of the conversation at that event: later messages stay visible but dimmed, and the inspector shows details for whatever you've selected (an event, an agent, or the whole run). Use the View and Show controls to bring tools, memories, and observations into the transcript.

The panel above the transcript has several views:

| View | Good for answering |
| --- | --- |
| Lanes | Who acted, and in what order? |
| Influence | Whose messages did other agents actually read? |
| Blast radius | Which later messages can be traced back to this one? |
| Phrase spread | Where does a given phrase show up, across agents and stages? |
| Echo | How much does an agent's message overlap (in five-word phrases) with what it read? |
| Activity | Where do activity, message length, tokens, or latency shift? |

These views describe the trace. They don't classify anything as causal or as a cascade. Influence uses the recorded delivery links when they exist and falls back to replies and channels when they don't, so check where an edge came from before reading it as proof that one agent saw another's message. A missing measurement is shown as missing, never as zero.

## Compare

Open Compare and pick the two branches or paired runs you want side by side. Start with the final outcomes, then read the transcript aligned by stage. A control run and an attack run can be completely independent; they don't need a shared history. Keep in mind that a single comparison can't separate the effect of your change from ordinary model randomness.

## Branch

Select an event and choose **Fork here** (or press `F`). The other fork actions let you change a goal, a prompt, or an agent's configuration. The new branch keeps everything up to the fork point. **Create branch** saves it as an experiment; **Create and run** also executes it, if a runtime is available. See [how branching works](Live-collection-and-branching).

## Reports and sharing

Reports lists the saved MAST analyses for the branch you're on. Starting an analysis from the dialog creates a job, and the result shows up here when it finishes. An empty Reports view only means nobody has run an analysis on this branch. It says nothing about whether the conversation has problems.

To share a run, use **⋯ → Export run** to download a JSON bundle. Your colleague loads it with **Import run** into their own workspace. Comments and branches stay local, and Git won't sync SQLite databases for you.

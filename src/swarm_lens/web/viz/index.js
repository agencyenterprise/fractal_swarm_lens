// Built-in visualizations shown next to Lanes and Influence. Each module exports one
// visualization: { id, title, mount(root, actions) -> { update(context), destroy() } }.
import { blastRadius } from "./blast-radius.js";
import { phraseSpread } from "./phrase-spread.js";
import { echoScore } from "./echo-score.js";
import { activityStrip } from "./activity-strip.js";

export const builtinVisualizations = [blastRadius, phraseSpread, echoScore, activityStrip];

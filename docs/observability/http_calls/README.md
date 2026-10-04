# HTTP calls per website

A model-free saved-trace plugin. It counts HTTP calls per website across the session, so a burst of calls to one website stands out. It makes no model calls, and its results are counts, not a judgment that a burst is harmful.

- Analysis: `swarm_lens.observability.http_calls` (`HttpCallsPlugin`, id `http_calls`; `analyze_http_calls(history, config)` works without a server).
- Web extension: `swarm_lens.web.http_calls`. `GET /api/plugins/http_calls/capabilities` returns the manifest (`ui.renderer: "http_calls"`, modes `["saved_trace"]`, config schema). `POST /api/plugins/http_calls/analyses` with `{branch_id, cursor, config}` runs synchronously through `Framework.analyze`, which stores the record with its input digest.
- UI: `http-calls.js` adds "HTTP calls per website…" to the Plugins menu. The modal shows a line chart (the five busiest or spiking websites; the rest as "other"), spike markers, and a table. A spike or a table row opens the explorer at the first call behind it.

## Use

Agent CLI or web interface, up to you: open **Plugins → Network activity → HTTP calls per website…** in the explorer, or have an agent or script call the API:

```sh
curl -X POST localhost:8765/api/plugins/http_calls/analyses \
  -H 'Content-Type: application/json' \
  -d '{"branch_id": "BRANCH_ID", "cursor": 200, "config": {}}'
```

## What counts as a call

Only network-like calls count. A URL that is only mentioned does not count.

| Source | Counted URLs |
|---|---|
| Browser, navigation or fetch tool: the tool name contains `navigat`, `brows`, `fetch`, `http`, `visit`, `goto`, `open_url`, `scrap`, `crawl`, `download`, `curl` or `wget` | every URL in its arguments |
| Execution tool: the name contains `bash`, `shell`, `exec`, `command`, `terminal`, `python`, `jupyter`, `script` or `code_run` | URLs in an argument text (command or code) that makes a request: `curl`, `wget`, `aria2c`, `httpie`, `http GET …`, `requests.get/post/…`, `httpx.`, `urllib.request`, `urlopen(`, `aiohttp`, `fetch(`, `git clone/fetch/pull/ls-remote` |
| Any tool | an explicit `url`, `uri` or `href` argument field, at any depth |
| `observation.recorded` | its `url` field (or `metadata.url`) |

URLs in file tools (`open_file`, `edit_file`, patches), in messages, and in tool results or errors do not count. `git clone` over https is counted because it makes HTTPS requests to that host; `pip install` is not, because its target host is not in the command.

Each website counts once per call. A call is identified by its tool call `id`, so started and completed events count once, at the first recorded event. A call that names two websites counts once for each. Reported (reconstructed) calls are counted and also totaled separately as `reported`.

Website = the URL host, in lowercase, without port, credentials, or a leading `www.`. No public-suffix list is used: `api.github.com` and `github.com` are different websites.

## Binning

The session runs from the first to the last event of the selected history prefix. When every event timestamp (`occurred_at`) is readable, never goes backwards, and the timestamps are not all equal, the session is split into `bins` equal time bins (default 40). Otherwise, it is split by event position, with at most one bin per position. `axis` and `axis_reason` in the output say which axis was used and why. Timestamps that a source synthesized at a fixed step (such as the MAST haystack) pass these checks, so time bins then equal position bins in practice.

## Spike rule

For each website, take its call counts in all bins, including empty bins. A bin is a spike bin when

```
count ≥ max(spike_min_calls, median + spike_mads × max(MAD, √median, 1))    defaults: spike_min_calls = 3, spike_mads = 4
```

MAD is the median absolute deviation from the median. The median and the MAD do not move much when a few bins are large, so a burst cannot raise its own threshold. For sparse traffic the MAD is often 0; then `√median` (the Poisson spread of a count) or `1` sets the scale, so chance clusters of a few calls do not count. Example: a website with median 0 needs 4 calls in a bin; a website with a steady 2 calls per bin needs 8.

Consecutive spike bins of one website are one spike, so a burst that crosses a bin edge is reported once. Each spike has its bin range, its total and peak count, the threshold, and the positions and event ids of up to 50 calls behind it.

## Limitations

- Tool names and request patterns are matched with fixed patterns. A network tool with an unusual name (for example `get_page`) is not found unless it has a `url` argument. An execution tool that makes requests through another client (`socket`, a custom SDK) is not found.
- A URL that is built at run time (`base + path`), shortened, or only in a screenshot is not found.
- When a command or code makes a request, every URL in that same argument counts, including a URL that is only in a comment.
- A browser click or form submission without a URL argument is not counted, even when it loads a page.
- A recorded tool call is not proof that a request went out. Reconstructed calls are reports by the agent.
- One website's spike is relative to its own baseline. A website that is busy all session does not spike, even when it receives most of the calls.

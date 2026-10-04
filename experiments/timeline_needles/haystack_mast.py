"""Benign haystack from real MAST multi-agent traces, stitched as consecutive team episodes.

Source: HyperAgent (SWE-bench) traces in vendor/mast/traces/HyperAgent/*.json. Each trace is one
team (Planner, Navigator, Editor, Executor) resolving one GitHub issue. Only traces whose MAST human
annotation marks none of the misalignment-like failure modes are used. HyperAgent logs carry no
timestamps, so timestamps are synthetic: one fixed step per fact.

Size is measured as o200k_base tokens of the text the timeline plugin reads: rendered event lines
joined by newlines, exactly as `render_history` produces them after ingestion.

TOOL_EVENT_SCHEMA. HyperAgent logs only model outputs; tool use lives in message text as
"Action:" followed by a code fence (Python calling `tool._run(...)`, other Python, or bash).
These are REPORTED actions: the agent wrote them, and the log never shows that they ran. Right
after a message.created (content unchanged), each parsed action adds:
- tool.started: id "<message id>-t<n>", agent_id = speaker, tool_name, arguments, status "running".
- tool.completed: same id, status "completed" or "failed" (observation shows a nonzero exitcode,
  "execution failed", "Error executing command" or a traceback), result = observation text or None.
  The reducer needs a final status; the renderer shows only the started event of a reconstructed call.
tool_name: open_file, edit_file, code_search, list_folder, find_file, list_symbols, find_references,
go_to_definition, execute_command (bash; arguments.command), execute_python (other Python;
arguments.code); calls to tools HyperAgent lacks keep their raw name. Renamed tools keep
arguments.raw_tool. metadata on both events: reconstructed (always true), source_message_id, observed
(an observation exists in the log), team, source, parse_rule, conditional (the call sits inside a
branch or loop of its code), runs_tests (execute_command or execute_python whose command runs
pytest/unittest/runtests.py/manage.py test/tox); tool.started adds reported_observation. The
observation is never invented: it is the text after an "Observation"/"exitcode:" marker in the same
message, written there by the agent or its report, else None.
"""
import ast
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import json
from pathlib import Path
import random
import re
import tempfile

import tiktoken

from swarm_lens import Fact, Framework, State
from swarm_lens.adapters.sqlite import SQLiteHistory
from swarm_lens.observability.timeline.render import render_event, render_history

TRACE_DIR = Path("vendor/mast/traces/HyperAgent")
SYSTEM = "HyperAgent"
TOKENIZER = "o200k_base"
BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
FACT_STEP = timedelta(seconds=5)
# One episode may overshoot the target; capping episodes keeps small haystacks near their target.
MAX_EPISODE_TOKENS = 80_000

# MAST human labels that resemble misalignment. A trace with "yes" on any of these is excluded.
# Benign failures (step repetition, restarts, no verification, ...) are allowed.
MISALIGNMENT_LIKE_MODES = frozenset({
    "Withholding relevant information",
    "Misalignment between internal thoughts and response message",
    "Claiming that a task is done while it is not true.",
    "Derailing from task objectives",
    "Invented content",
    "Ignoring good suggestions from other agent",
})

LOG_HEADER = re.compile(r"^HyperAgent_\S+ - [A-Z]+ - (?P<body>.*)$", re.S)
RESPONSE = re.compile(r"^(?:Inner-(?P<inner>\w+)-Assistant|(?P<planner>Planner))'s Response: ?(?P<text>.*)$", re.S)
REPORT = re.compile(r"^(?P<reporter>\w+)->Planner: ?(?P<text>.*)$", re.S)


class TraceParseError(ValueError):
    pass


@dataclass(frozen=True)
class Turn:
    role: str
    kind: str  # "response" (an agent's own step) or "report" (an intern's summary to the Planner)
    content: str


@dataclass(frozen=True)
class Trace:
    source: str
    instance: str
    task: str
    labels: dict
    turns: tuple[Turn, ...]

    @property
    def roles(self):
        return list(dict.fromkeys(turn.role for turn in self.turns))


@dataclass(frozen=True)
class Inventory:
    files: int
    unannotated: list[str]
    flagged: list[str]
    unparseable: dict[str, str]
    oversized: list[str]
    benign: list[Path]


def turn_start(body):
    """(role, kind, first line) for a log header that opens a turn; None for other log lines."""
    if response := RESPONSE.match(body):
        return response["inner"] or response["planner"], "response", response["text"]
    if report := REPORT.match(body):
        return report["reporter"], "report", report["text"]
    return None


def parse_turns(lines):
    """Group log lines into turns. Continuation lines belong to the latest turn header."""
    turns, current = [], None
    for line in lines:
        header = LOG_HEADER.match(line)
        if header is None:
            if current is not None:
                current[2].append(line)
            continue
        turns.append(current)
        start = turn_start(header["body"])
        current = None if start is None else (start[0], start[1], [start[2]])
    turns.append(current)
    return [Turn(role, kind, content) for role, kind, body in filter(None, turns)
            if (content := "\n".join(body).strip())]


def load_trace(path):
    data = json.loads(Path(path).read_text())
    trajectory = data.get("trajectory")
    if not isinstance(trajectory, list) or not all(isinstance(line, str) for line in trajectory):
        raise TraceParseError("trajectory is not a list of log lines")
    turns = parse_turns(trajectory)
    if len({turn.role for turn in turns}) < 2:
        raise TraceParseError(f"fewer than two agents speak ({len(turns)} turns)")
    return Trace(str(path), data["instance_id"], "\n".join(data["problem_statement"]).strip(),
                 (data.get("note") or {}).get("options") or {}, tuple(turns))


def is_benign(labels):
    return not any(labels.get(mode) == "yes" for mode in MISALIGNMENT_LIKE_MODES)


# --- Tool actions written inside message text -------------------------------------------------------

# A closing fence carries no info string, so "```python" never closes a block.
FENCE = re.compile(r"```(?P<lang>[^\s`]*)[^\n`]*\n(?P<code>.*?)(?P<close>```(?=[ \t]*(?:\n|\Z))|\Z)", re.S)
ACTION_MARKER = re.compile(r"^[ \t#*>-]*(?:[Nn]ext[ \t]+[Aa]ction|Action)\b(?P<rest>[^\n]*)$", re.M)
TEMPLATE_ECHO = re.compile(r"^\W*the action as block of code")
REGION_STOP = re.compile(r"^[ \t#*]*(?:Observation\b|Thought\b|Final Answer\b)|^#{1,6}[ \t]", re.M)
OBSERVATION_STOP = re.compile(r"^[ \t#*]*(?:Thought\b|Final Answer\b)|^#{1,6}[ \t]", re.M)
OBSERVATION_START = re.compile(r"\s*(?:[#*]*[ \t]*Observation\b[*:]*|(?=exitcode:[ \t]*-?\d))", re.S)
FINAL_ANSWER = re.compile(r"^[ \t#*]*Final Answer\b", re.M)
SUBGOAL = re.compile(r"^[ \t#*]*Subgoal\b", re.M)
FAILURE = re.compile(r"exitcode:[ \t]*-?[1-9]|execution failed|Error executing command|Traceback \(most recent call last\)")
RUN_CALL = re.compile(r"\b(?P<tool>\w+)\._run\(")
TEST_RUN = re.compile(r"\bpytest\b|\bruntests\.py\b|\bunittest\b|manage\.py test\b|\btox\b|\bbin/test\b")
TOOL_STUB = re.compile(r"\bdef _run\(")
SHELL_LANGS = frozenset({"bash", "sh", "shell", "console"})
TOOL_NAMES = {
    "open_file": "open_file", "open_file_gen": "open_file", "editor": "edit_file",
    "code_search": "code_search", "CodeSearchTool": "code_search", "get_folder_structure": "list_folder",
    "find_file": "find_file", "find_files": "find_file", "get_all_symbols": "list_symbols",
    "find_all_refs": "find_references", "go_to_def": "go_to_definition",
    "executor": "execute_command", "run_command": "execute_command", "execute_command": "execute_command",
}
TOOL_ROLES = frozenset({"Navigator", "Editor", "Executor"})
EXECUTION_TOOLS = frozenset({"execute_command", "execute_python"})


@dataclass(frozen=True)
class ToolAction:
    tool_name: str
    arguments: dict
    observation: str | None  # text after an "Observation"/"exitcode:" marker in the same message, if any
    parse_rule: str
    conditional: bool = False  # the call sits inside a branch or loop, so it may not have been reached

    @property
    def failed(self):
        return self.observation is not None and FAILURE.search(self.observation) is not None

    @property
    def command(self):
        """The command or code an execution tool runs, from keyword or positional arguments."""
        named = self.arguments.get("command") or self.arguments.get("code")
        return named if isinstance(named, str) else " ".join(map(str, self.arguments.get("args") or []))

    @property
    def runs_tests(self):
        return self.tool_name in EXECUTION_TOOLS and TEST_RUN.search(self.command) is not None

    @property
    def key(self):
        return self.tool_name, json.dumps(self.arguments, sort_keys=True, default=str)


@dataclass(frozen=True)
class TurnActions:
    status: str  # "has_actions_parsed" | "no_actions" | "unparsed_candidate"
    reason: str
    actions: tuple[ToolAction, ...] = ()


class MessageScan:
    """Code fences, Action regions and section starts of one message. Markers inside code are ignored."""

    def __init__(self, text):
        self.text = text
        self.fences = list(FENCE.finditer(text))
        self.markers = [marker for marker in ACTION_MARKER.finditer(text)
                        if self.outside_code(marker.start()) and not TEMPLATE_ECHO.match(marker["rest"])]
        self.regions = [(marker.start(), self.region_end(marker)) for marker in self.markers]
        self.final_answer = self.first_outside_code(FINAL_ANSWER)
        self.subgoal = self.first_outside_code(SUBGOAL)

    def outside_code(self, position):
        return not any(fence.start() <= position < fence.end() for fence in self.fences)

    def first_outside_code(self, pattern):
        return next((match.start() for match in pattern.finditer(self.text) if self.outside_code(match.start())),
                    len(self.text))

    def region_end(self, marker):
        """An Action region ends at the next Observation/Thought/Final Answer line, heading, or Action marker."""
        stops = [stop.start() for stop in REGION_STOP.finditer(self.text, marker.end() + 1)
                 if self.outside_code(stop.start())]
        stops += [other.start() for other in ACTION_MARKER.finditer(self.text, marker.end() + 1)
                  if self.outside_code(other.start())]
        return min(stops, default=len(self.text))

    def in_region(self, fence):
        return any(start <= fence.start() < end for start, end in self.regions)

    def followed_by_observation(self, fence):
        return bool(fence["close"]) and OBSERVATION_START.match(self.text, fence.end()) is not None \
            and bool(self.text[fence.end():].strip())

    def fence_verdict(self, fence):
        """(rule, None) for an action fence, (None, reason) for a candidate that is not an action,
        (None, None) for code that is merely quoted."""
        if self.in_region(fence):
            rule = "action_marker"
        elif self.followed_by_observation(fence):
            rule = "fence_then_observation"
        elif RUN_CALL.search(fence["code"]):
            rule = "run_call_fence"
        else:
            return None, None
        lang, code = fence["lang"].lower(), fence["code"]
        if rule != "action_marker" and fence.start() > self.final_answer:
            return None, "code_in_final_answer"
        if rule != "action_marker" and fence.start() > self.subgoal:
            return None, "code_in_delegated_subgoal"
        if lang not in SHELL_LANGS | {"python", ""}:
            return None, "non_executable_fence_language"
        if lang == "" and not RUN_CALL.search(code):
            return None, "untyped_fence_without_tool_call"
        if TOOL_STUB.search(code):
            return None, "defines_tool_stub"
        return rule, None

    def markers_without_code(self):
        """Action markers whose region holds no code fence at all."""
        return [start for start, end in self.regions
                if not any(start <= fence.start() < end for fence in self.fences)]


def observation_after(text, start, end):
    """Observation text between an action fence and the next action or stop line; None if absent or empty."""
    stop = OBSERVATION_STOP.search(text, start, end)
    window = text[start:stop.start() if stop else end]
    marker = OBSERVATION_START.match(window)
    if marker is None:
        return None
    return window[marker.end():].strip() or None


def literal_argument(node, assignments):
    try:
        return ast.literal_eval(node)
    except ValueError:
        if isinstance(node, ast.Name) and node.id in assignments:
            return assignments[node.id]
        return ast.unparse(node)


UNBOUND = object()


class RunCallCollector(ast.NodeVisitor):
    """Walks code in execution order. A name resolves only to a constant assigned before the call;
    function bodies never run; a constant-false branch is skipped; calls in other branches, loops and
    exception handlers are conditional."""

    def __init__(self):
        self.assignments, self.calls, self.depth = {}, [], 0

    def skip(self, node):
        pass

    visit_FunctionDef = visit_AsyncFunctionDef = visit_Lambda = skip

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Store):
            self.assignments.pop(node.id, None)

    def visit_Assign(self, node):
        self.visit(node.value)
        for target in node.targets:
            self.visit(target)
        if isinstance(node.value, ast.Constant):
            self.assignments.update({target.id: node.value.value for target in node.targets
                                     if isinstance(target, ast.Name)})

    def visit_If(self, node):
        self.visit(node.test)
        if isinstance(node.test, ast.Constant):
            self.visit_all(node.body if node.test.value else node.orelse)
        else:
            self.visit_branches(node.body, node.orelse)

    def visit_IfExp(self, node):
        self.visit_If(node)

    def visit_While(self, node):
        self.visit(node.test)
        if not (isinstance(node.test, ast.Constant) and not node.test.value):
            self.visit_branches(node.body, node.orelse)

    def visit_For(self, node):
        self.visit(node.iter)
        self.visit(node.target)
        self.visit_branches(node.body, node.orelse)

    visit_AsyncFor = visit_For

    def visit_Try(self, node):
        self.visit_all(node.body)
        self.visit_branches(*node.handlers, node.orelse)
        self.visit_all(node.finalbody)

    visit_TryStar = visit_Try

    def visit_all(self, nodes):
        for node in nodes if isinstance(nodes, list) else [nodes]:
            self.visit(node)

    def visit_branches(self, *branches):
        """Visit each branch from the same known names; afterwards, names any branch rebinds are unknown."""
        before, rebound = dict(self.assignments), set()
        self.depth += 1
        for branch in branches:
            self.assignments = dict(before)
            self.visit_all(branch)
            rebound |= {name for name in before if self.assignments.get(name, UNBOUND) != before[name]}
        self.depth -= 1
        self.assignments = {name: value for name, value in before.items() if name not in rebound}

    def visit_Call(self, node):
        self.generic_visit(node)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "_run" and isinstance(node.func.value, ast.Name):
            arguments = {keyword.arg: literal_argument(keyword.value, self.assignments)
                         for keyword in node.keywords if keyword.arg}
            if node.args:
                arguments["args"] = [literal_argument(argument, self.assignments) for argument in node.args]
            self.calls.append((node.func.value.id, arguments, self.depth > 0))


def run_calls(code):
    """(raw tool name, arguments, conditional) for every `tool._run(...)` call that the code can reach, in
    execution order; None when the code is not valid Python."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    collector = RunCallCollector()
    collector.visit(tree)
    return collector.calls


def tool_action(raw_tool, arguments, observation, rule, conditional=False):
    name = TOOL_NAMES.get(raw_tool, raw_tool)
    return ToolAction(name, arguments if name == raw_tool else {**arguments, "raw_tool": raw_tool}, observation, rule,
                      conditional)


def fence_actions(lang, code, observation, rule):
    """Tool actions in one action fence. Python fences run in the agent's kernel: each `tool._run(...)`
    is a tool call (a shared observation goes to the last call); other Python is one execute_python call."""
    if lang.lower() in SHELL_LANGS:
        return [ToolAction("execute_command", {"command": code.strip()}, observation, f"{rule}:shell")]
    calls = run_calls(code)
    if calls is None:
        return [tool_action(match["tool"], {"code": code.strip()}, observation, f"{rule}:run_call_invalid_python")
                for match in RUN_CALL.finditer(code)] or \
               [ToolAction("execute_python", {"code": code.strip()}, observation, f"{rule}:invalid_python")]
    if not calls:
        return [ToolAction("execute_python", {"code": code.strip()}, observation, f"{rule}:python")]
    return [tool_action(tool, arguments, observation if index == len(calls) - 1 else None, f"{rule}:run_call",
                        conditional)
            for index, (tool, arguments, conditional) in enumerate(calls)]


def has_action_signal(text):
    """Detector of text that looks like a tool action, independent of the parser, to audit it."""
    return (re.search(r"(?m)^[ \t#*>-]*(?:[Nn]ext[ \t]+[Aa]ction|Action)\b", text) is not None
            or RUN_CALL.search(text) is not None or "exitcode:" in text
            or re.search(r"```[ \t]*\n\s*[#*]*[ \t]*Observation\b", text) is not None)


def scan_actions(text):
    """(actions, reasons): parsed tool actions, and the named reason for every action signal that is not one."""
    scan = MessageScan(text)
    verdicts = [(fence, *scan.fence_verdict(fence)) for fence in scan.fences]
    selected = [(fence, rule) for fence, rule, _ in verdicts if rule]
    reasons = {reason for _, _, reason in verdicts if reason}
    actions = []
    for position, (fence, rule) in enumerate(selected):
        next_start = selected[position + 1][0].start() if position + 1 < len(selected) else len(text)
        actions += fence_actions(fence["lang"], fence["code"], observation_after(text, fence.end(), next_start), rule)
    if scan.markers_without_code():
        reasons.add("action_marker_without_code")
    if any(scan.outside_code(match.start()) for match in RUN_CALL.finditer(text)):
        reasons.add("run_call_in_prose")
    if not actions and "exitcode:" in text:
        reasons.add("observation_without_action")
    if any(TEMPLATE_ECHO.match(marker["rest"]) for marker in ACTION_MARKER.finditer(text)):
        reasons.add("prompt_format_echo")
    if any(not scan.outside_code(marker.start()) for marker in ACTION_MARKER.finditer(text)):
        reasons.add("action_marker_quoted_in_code")
    return actions, reasons


def classify_turns(trace):
    """TurnActions per turn. Only Navigator, Editor and Executor hold tools. A message repeated verbatim
    right after itself is a duplicated log line; a report action already run in the same delegation is a recap."""
    results, delegation, previous = [], set(), None
    for turn in trace.turns:
        if turn.role == "Planner":
            delegation = set()
        results.append(turn_actions(turn, previous, delegation))
        delegation |= {action.key for action in results[-1].actions}
        previous = turn
    return results


def turn_actions(turn, previous, delegation):
    if not has_action_signal(turn.content):
        return TurnActions("no_actions", "plain_reasoning")
    if turn.role not in TOOL_ROLES:
        return TurnActions("no_actions", "planner_has_no_tools")
    if previous == turn:
        return TurnActions("no_actions", "repeated_log_line")
    actions, reasons = scan_actions(turn.content)
    if turn.kind == "report":
        fresh = [action for action in actions if action.key not in delegation]
        if actions and not fresh:
            reasons.add("report_recap")
        actions = fresh
    if actions:
        return TurnActions("has_actions_parsed", "parsed", tuple(actions))
    if reasons:
        return TurnActions("no_actions", "+".join(sorted(reasons)))
    return TurnActions("unparsed_candidate", "unparsed")


@lru_cache(maxsize=1)
def inventory(trace_dir=TRACE_DIR):
    """Classify every top-level HyperAgent trace. raw_trajs/ holds unannotated logs and is not used."""
    paths = sorted(Path(trace_dir).glob("*.json"))
    unannotated, flagged, unparseable, oversized, benign = [], [], {}, [], []
    for path in paths:
        try:
            trace = load_trace(path)
        except (TraceParseError, KeyError, json.JSONDecodeError) as error:
            unparseable[path.name] = str(error)
            continue
        if not trace.labels:
            unannotated.append(path.name)
        elif not is_benign(trace.labels):
            flagged.append(path.name)
        elif episode_tokens(trace) > MAX_EPISODE_TOKENS:
            oversized.append(path.name)
        else:
            benign.append(path)
    return Inventory(len(paths), unannotated, flagged, unparseable, oversized, benign)


def team_id(episode):
    return f"team{episode:03d}"


def agent_id(team, role):
    return f"{team}.{role.lower()}"


def trace_facts(trace, episode):
    """One trace as one team episode: task, agents, the team channel, then every turn in order."""
    team = team_id(episode)
    agents = [agent_id(team, role) for role in trace.roles]
    setup = [Fact("environment.updated", {
        "task": trace.task, "goal": f"Team {team} ({SYSTEM}): resolve GitHub issue {trace.instance}."}, "")]
    setup += [Fact("agent.added", {"id": agent, "name": f"{role} ({team})", "metadata": {"team": team}}, "")
              for agent, role in zip(agents, trace.roles)]
    setup.append(Fact("channel.created", {"id": team, "name": f"{team} {trace.instance}", "members": agents}, ""))
    turns = []
    for index, (turn, found) in enumerate(zip(trace.turns, classify_turns(trace))):
        message_id, sender = f"{team}-m{index}", agent_id(team, turn.role)
        turns.append(Fact("message.created", {
            "id": message_id, "channel_id": team, "sender_id": sender, "content": turn.content,
            "metadata": {"team": team, "system": SYSTEM, "source": trace.source, "index": index, "kind": turn.kind}},
            ""))
        turns += reported_tool_facts(found, message_id, sender, team, trace.source)
    return setup + turns


def message_tool_facts(role, content, message_id, sender, team, source, occurred_at=""):
    """Reported tool facts for one agent response that was not read from a trace (e.g. a planted message),
    through the same parser as trace turns; facts take `occurred_at`."""
    found = turn_actions(Turn(role, "response", content), None, set())
    facts = reported_tool_facts(found, message_id, sender, team, source)
    return [Fact(fact.kind, fact.data, occurred_at) for fact in facts]


def reported_tool_facts(found, message_id, sender, team, source):
    """tool.started then tool.completed for every action parsed from one message, in order."""
    metadata = {"team": team, "source": source, "source_message_id": message_id}
    return [fact for number, action in enumerate(found.actions)
            for fact in tool_facts(action, f"{message_id}-t{number}", sender, metadata)]


def tool_facts(action, call_id, sender, metadata):
    """tool.started then tool.completed for one reported action; the result is only ever observation text
    from the log."""
    metadata = {**metadata, "reconstructed": True, "observed": action.observation is not None,
                "parse_rule": action.parse_rule, "conditional": action.conditional, "runs_tests": action.runs_tests}
    started = Fact("tool.started", {
        "id": call_id, "agent_id": sender, "tool_name": action.tool_name, "arguments": action.arguments,
        "status": "running", "metadata": {**metadata, "reported_observation": action.observation}}, "")
    completed = Fact("tool.completed", {
        "id": call_id, "status": "failed" if action.failed else "completed", "result": action.observation,
        "metadata": metadata}, "")
    return [started, completed]


def with_timestamps(facts, offset):
    return [Fact(fact.kind, fact.data, (BASE_TIME + (offset + index) * FACT_STEP).isoformat())
            for index, fact in enumerate(facts)]


@lru_cache(maxsize=1)
def encoder():
    return tiktoken.get_encoding(TOKENIZER)


class RenderMeter:
    """Applies facts to a private state (the reducer validates them) and counts rendered tokens."""

    def __init__(self):
        self.state, self.lines, self.tokens = State("measure"), 0, 0

    def add(self, facts):
        for fact in facts:
            line = render_event(Framework._event(self.state, fact))
            if line is not None:
                self.tokens += len(encoder().encode(line, disallowed_special=())) + (1 if self.lines else 0)
                self.lines += 1


def episode_tokens(trace):
    meter = RenderMeter()
    meter.add(with_timestamps(trace_facts(trace, 0), 0))
    return meter.tokens


def build(target_tokens: int, seed: int) -> list[Fact]:
    """Stitch distinct benign traces, in seeded order, until the rendered size reaches target_tokens."""
    candidates = list(inventory().benign)
    random.Random(f"mast|{seed}").shuffle(candidates)
    facts, meter = [], RenderMeter()
    for episode, path in enumerate(candidates):
        if meter.tokens >= target_tokens:
            return facts
        episode_facts = with_timestamps(trace_facts(load_trace(path), episode), len(facts))
        meter.add(episode_facts)
        facts += episode_facts
    if meter.tokens < target_tokens:
        raise ValueError(f"All {len(candidates)} benign traces render to {meter.tokens} tokens < {target_tokens}")
    return facts


class FactList:
    def __init__(self, facts):
        self.items = facts

    def facts(self):
        yield from self.items


def ingested_history(facts, directory):
    """Ingest facts into a fresh SQLite history under `directory` (the reducer validates them)."""
    framework = Framework(SQLiteHistory(Path(directory) / "haystack.sqlite"))
    branch = framework.create_run("haystack", {})
    framework.ingest(branch.id, FactList(facts))
    return framework.history(branch.id)


def rendered_text(facts):
    """The exact text the plugin reads, via a real SQLite ingestion."""
    with tempfile.TemporaryDirectory() as scratch:
        return "\n".join(line for _, line in render_history(ingested_history(facts, scratch)))


def rendered_tokens(facts):
    return len(encoder().encode(rendered_text(facts), disallowed_special=()))

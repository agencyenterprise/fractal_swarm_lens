"""Package the pinned MAST notebook's prompt without executing notebook code."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "vendor/mast"
REVISION = "a70542e541b2104ef8fcd785778179e173fb8d70"


def build():
    revision = subprocess.check_output(["git", "-C", str(UPSTREAM), "rev-parse", "HEAD"], text=True).strip()
    if revision != REVISION:
        raise ValueError("Inspect upstream changes before updating the pinned MAST assets")
    files = {name: (UPSTREAM / name).read_bytes() for name in (
        "llm_judge_pipeline.ipynb", "taxonomy_definitions_examples/definitions.txt",
        "taxonomy_definitions_examples/examples.txt",
    )}
    notebook = json.loads(files["llm_judge_pipeline.ipynb"])
    tree = ast.parse("".join(notebook["cells"][0]["source"]))
    evaluator = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "openai_evaluator")
    prompt = next(n.value for n in evaluator.body if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == "prompt" for t in n.targets))
    parts = []
    for item in prompt.values:
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            parts.append({"text": item.value})
        elif isinstance(item, ast.FormattedValue) and isinstance(item.value, ast.Name) and item.value.id in {
            "trace", "definitions", "examples",
        }:
            parts.append({"slot": item.value.id})
        else:
            raise ValueError("Unexpected upstream prompt expression")
    definitions = files["taxonomy_definitions_examples/definitions.txt"].decode()
    examples = files["taxonomy_definitions_examples/examples.txt"].decode()
    headings = list(re.finditer(r"^(\d\.\d) ([^\n:]+):[ \t]*", definitions, re.M))
    question_text = "".join(part.get("text", "") for part in parts)
    categories = []
    groups = {"1": "Specification issues", "2": "Inter-agent misalignment", "3": "Task verification"}
    for i, heading in enumerate(headings):
        code, definition_label = heading.groups()
        question = re.search(re.escape(code) + r" ([^:]+): <yes or no>", question_text)
        if not question:
            raise ValueError(f"Missing question for {code}")
        end = headings[i + 1].start() if i + 1 < len(headings) else len(definitions)
        categories.append({"code": code, "label": question.group(1), "definition_label": definition_label,
                           "group": groups[code[0]], "definition": definitions[heading.end():end].strip()})
    if len(categories) != 14:
        raise ValueError("Expected MAST's 14 categories")
    return {"repository": "https://github.com/multi-agent-systems-failure-taxonomy/MAST",
            "revision": revision, "prompt_parts": parts, "definitions": definitions, "examples": examples,
            "categories": categories, "file_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
            "upstream_notes": [
                "The notebook asks 3.2 No or Incorrect Verification and 3.3 Weak Verification; definitions.txt assigns these names to 3.3 and 3.2 respectively. Results retain the notebook's question codes without remapping.",
                "The notebook's example answer contains extra codes 1.6 and 2.7, outside its 14-category question list. They are not scored.",
            ]}


if __name__ == "__main__":
    target = ROOT / "src/swarm_lens/observability/mast/assets.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n")
    print(target)

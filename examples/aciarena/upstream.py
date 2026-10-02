"""Load the pinned upstream implementation without editing the submodule."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

REVISION = '3f226a40cddf01a08b9f700cbef0031dfab2ff64'
ROOT = Path(__file__).resolve().parents[2] / 'vendor' / 'aciarena'


def verify_checkout(root=ROOT):
    if not (root / 'aciarena').is_dir():
        raise ValueError('Initialize the benchmark with git submodule update --init vendor/aciarena')
    revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=no'], text=True)
    if revision != REVISION or dirty:
        raise ValueError('Benchmark requires its pinned, unmodified upstream revision')
    return revision


def load_tasks(count, root=ROOT):
    path = root / 'aciarena/evaluation/datasets/aciarena_math.json'
    raw = path.read_bytes()
    tasks = json.loads(raw)
    if not 1 <= count <= len(tasks):
        raise ValueError(f'--tasks must be in [1, {len(tasks)}]')
    return [{'id': f'math-{i:03d}', **task} for i, task in enumerate(tasks[:count])], hashlib.sha256(raw).hexdigest()


def disclosure_information(root=ROOT):
    # Reuse the benchmark's exact planted context without constructing its concurrent suite.
    tree = ast.parse((root / 'aciarena/evaluation/evaluation_suite.py').read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == 'USER_INFORMATION'
                                                for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('Pinned benchmark disclosure context was not found')


def load_components():
    verify_checkout()
    sys.path.insert(0, str(ROOT))
    from aciarena.mas.llm_debate.llm_debate_mas import LLMDebate
    from aciarena.attacks.disclosure_attack import MathNameLeakInstruction
    from aciarena.evaluation.task.math_task import MathTask
    return LLMDebate, MathNameLeakInstruction, MathTask

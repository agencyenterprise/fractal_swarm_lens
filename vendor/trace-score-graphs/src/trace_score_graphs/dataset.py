"""Pinned download, label inventory, and disjoint multilabel cohort allocation."""
import collections
import random
import urllib.request
from pathlib import Path
from .common import digest, file_sha, read, save, text_sha

REPO = 'mcemri/MAST-Data'
REVISION = '95118ac951421753cf1deb87ddea3b01e693c41b'
SHA256 = 'd636ac63dfc1c6af2d312e862f4b7d383b62d3898d431ccd0e7a79d21d85406f'
LABELS = [f'{i}.{j}' for i, n in [(1, 5), (2, 6), (3, 3)] for j in range(1, n + 1)]


def download(root):
    raw = Path(root) / 'raw'
    raw.mkdir(parents=True, exist_ok=True)
    for name in ('README.md', 'MAD_full_dataset.json'):
        path = raw / name
        url = f'https://huggingface.co/datasets/{REPO}/resolve/{REVISION}/{name}'
        if not path.exists():
            temp = path.with_suffix(path.suffix + '.part')
            with urllib.request.urlopen(url, timeout=120) as src, temp.open('wb') as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
            temp.replace(path)
        if name.endswith('.json') and file_sha(path) != SHA256:
            raise ValueError('Pinned dataset checksum mismatch')
    save(raw / 'download.json', {'repository': REPO, 'revision': REVISION,
         'sha256': SHA256, 'license': 'CC-BY-4.0'})
    return raw / 'MAD_full_dataset.json'


def identity(row):
    return text_sha(row['trace']['trajectory'])


def sample(root, count=20, seed=20261004, include_disputed=False):
    path = download(root)
    rows = read(path)
    unique, conflicts, duplicates, originals = {}, set(), {}, {}
    for row_number, row in enumerate(rows):
        key = identity(row)
        duplicates.setdefault(key, []).append({'key': row['trace']['key'], 'index': row['trace']['index'],
                                              'trace_id': row['trace_id'], 'dataset_row': row_number,
                                              'labels': row['mast_annotation']})
        originals.setdefault(key, []).append((row_number, row))
        if key in unique and unique[key]['mast_annotation'] != row['mast_annotation']:
            conflicts.add(key)
        unique.setdefault(key, row)
    # Quarantine conflicting annotations, not choose whichever label suits a cohort.
    if not include_disputed:
        for key in conflicts:
            unique.pop(key)
    save(Path(root) / 'duplicate-audit.json', [{'trajectory_sha256': h,
         'conflicting_labels': h in conflicts, 'records': sources}
         for h, sources in duplicates.items() if len(sources) > 1])
    groups = {'no_issue': [h for h in unique
                           if all(r['mast_annotation'].get(c) == 0
                                  for _, r in originals[h] for c in LABELS)]}
    groups.update({c: [h for h in unique if any(r['mast_annotation'].get(c) == 1
                                               for _, r in originals[h])]
                   for c in LABELS})
    inventory = {'rows': len(rows), 'unique_trajectories': len(unique),
                 'eligible': {g: len(hs) for g, hs in groups.items()},
                 'conflicting_trajectories_found': len(conflicts),
                 'conflicting_trajectories_excluded': 0 if include_disputed else len(conflicts),
                 'unknown_label_rows': sum(any(r['mast_annotation'].get(c) is None
                                               for c in LABELS) for r in rows),
                 'frameworks': dict(collections.Counter(r['mas_name'] for r in rows))}
    save(Path(root) / 'inventory.json', inventory)
    # Reproducible framework interleaving; rare labels allocated first. Augmenting
    # paths solve the disjoint assignment, rather than silently reusing a trace.
    rng = random.Random(seed)
    ranked = {}
    for g, hs in sorted(groups.items()):
        bins = collections.defaultdict(list)
        for h in sorted(hs):
            bins[unique[h]['mas_name']].append(h)
        for values in bins.values():
            rng.shuffle(values)
            values.sort(key=lambda h: h in conflicts)  # Prefer undisputed within each framework.
        order = sorted(bins)
        rng.shuffle(order)
        ranked[g] = [bins[f][i] for i in range(max(map(len, bins.values()), default=0))
                     for f in order if i < len(bins[f])]
    assigned, slots = {}, {}

    def allocate(slot, seen):
        g = slot[0]
        for h in ranked[g]:
            if h in seen:
                continue
            seen.add(h)
            if h not in assigned or allocate(assigned[h], seen):
                assigned[h] = slot
                slots[slot] = h
                return True
        return False

    for g in sorted(groups, key=lambda g: (len(groups[g]), g)):
        for i in range(count):
            if not allocate((g, i), set()):
                raise ValueError(f'Cannot allocate {count} disjoint examples for {g}')
    manifest = {'dataset': {'repository': REPO, 'revision': REVISION, 'sha256': SHA256},
                'seed': seed, 'per_group': count, 'policy': 'disjoint_exact_trajectory_hash',
                'include_disputed': include_disputed,
                'note': 'Issue eligibility means at least one original record labels that issue positive. '
                        'No-issue eligibility requires all annotations on every duplicate to be zero. '
                        'Group assignment is not an exclusive or adjudicated label; all original labels retained.',
                'conversations': []}
    for (group, slot), h in sorted(slots.items()):
        eligible_rows = [(i, r) for i, r in originals[h] if group == 'no_issue'
                         or r['mast_annotation'].get(group) == 1]
        row_number, row = eligible_rows[0]
        cid = 'trace-' + h[:20]
        save(Path(root) / 'selected' / (cid + '.json'), row)
        manifest['conversations'].append({'conversation_id': cid, 'trajectory_sha256': h,
            'cohort': group, 'slot': slot, 'source_trace_id': row['trace_id'],
            'source_key': row['trace']['key'], 'source_index': row['trace']['index'],
            'framework': row['mas_name'], 'benchmark': row['benchmark_name'],
            'labels': row['mast_annotation'], 'source_dataset_row': row_number,
            'disputed_annotations': h in conflicts, 'all_source_annotations': duplicates[h]})
    if len({x['conversation_id'] for x in manifest['conversations']}) != count * 15:
        raise ValueError('Cohorts are not disjoint')
    save(Path(root) / 'selection.json', manifest)
    return manifest

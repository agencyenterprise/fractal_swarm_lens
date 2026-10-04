import argparse
import json
from .client import MODEL
from .common import load_env
from .dataset import download, sample
from .pipeline import prepare, score, export


def main():
    p = argparse.ArgumentParser(description='Independent MAST to Jev multilayer graphs experiment')
    p.add_argument('--data', default='data')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('download')
    s = sub.add_parser('sample')
    s.add_argument('--per-group', type=int, default=20)
    s.add_argument('--seed', type=int, default=20261004)
    s.add_argument('--include-disputed', action='store_true')
    sub.add_parser('prepare')
    s = sub.add_parser('score')
    s.add_argument('--env-file')
    s.add_argument('--model', default=MODEL)
    s.add_argument('--workers', type=int, default=8)
    s.add_argument('--max-requests', type=int, default=50000)
    s.add_argument('--max-cost', type=float, default=25)
    s.add_argument('--limit', type=int)
    sub.add_parser('export')
    sub.add_parser('audit')
    args = p.parse_args()
    if args.command == 'download':
        print(download(args.data))
    elif args.command == 'sample':
        m = sample(args.data, args.per_group, args.seed, args.include_disputed)
        print(json.dumps({'conversations': len(m['conversations']), 'seed': m['seed']}))
    elif args.command == 'prepare':
        run, result = prepare(args.data)
        print(json.dumps({'run': str(run), **result}, indent=2))
    elif args.command == 'score':
        load_env(args.env_file)
        result = score(args.data, args.model, args.workers, args.max_requests, args.max_cost, args.limit)
        print(json.dumps(result, indent=2))
        if result['failures']:
            raise SystemExit(1)
    elif args.command == 'audit':
        from .validation import audit
        result = audit(args.data)
        print(json.dumps(result, indent=2))
        if not result['passed']:
            raise SystemExit(1)
    else:
        print(json.dumps(export(args.data), indent=2))


if __name__ == '__main__':
    main()

"""python -m collector.pipeline --help"""
import argparse
from pathlib import Path
import subprocess
import sys
from . import experiences
from .analyzer import analyze
from .transport import export_bundle, import_bundle


def main():
    parser = argparse.ArgumentParser(description='采集证据 → 离线迁移 → 本地分析 → 经历草稿 → 训练数据')
    sub = parser.add_subparsers(dest='command', required=True)
    exp = sub.add_parser('pack', help='导出可离线迁移的数据包')
    exp.add_argument('--data', default='data')
    exp.add_argument('--out', required=True)
    imp = sub.add_parser('unpack', help='校验并导入独立目录')
    imp.add_argument('bundle')
    imp.add_argument('--data', required=True)
    for name in ('analyze', 'live'):
        command = sub.add_parser(name)
        command.add_argument('--data', default='data')
        command.add_argument('--model', default='collector-qwen:16k')
        command.add_argument('--endpoint', default='http://127.0.0.1:11434/api/chat')
        if name == 'analyze':
            command.add_argument('--event-id', type=int)
            command.add_argument('--limit', type=int, default=0)
            command.add_argument('--watch', action='store_true')
    build = sub.add_parser('build')
    build.add_argument('--data', default='data')
    build.add_argument('--out', default='data/experiences')
    review = sub.add_parser('review')
    review.add_argument('episode_id')
    review.add_argument('--directory', default='data/experiences')
    review.add_argument('--text-file', type=Path)
    review.add_argument('--reviewer', required=True)
    review.add_argument('--reject', action='store_true')
    train = sub.add_parser('train-export')
    train.add_argument('--directory', default='data/experiences')
    train.add_argument('--out', required=True)
    args = parser.parse_args()
    if args.command == 'pack':
        print(export_bundle(args.data, args.out))
    elif args.command == 'unpack':
        print(import_bundle(args.bundle, args.data))
    elif args.command == 'analyze':
        if args.limit < 0:
            parser.error('--limit must be nonnegative')
        analyze(args.data, args.model, args.endpoint, args.event_id, args.limit, args.watch)
    elif args.command == 'build':
        print('Experience drafts:', len(experiences.build(args.data, args.out)))
    elif args.command == 'review':
        text = args.text_file.read_text(encoding='utf-8-sig') if args.text_file else ''
        print(experiences.review(args.directory, args.episode_id, text, args.reviewer, args.reject))
    elif args.command == 'train-export':
        print('Training examples:', experiences.export_training(args.directory, args.out))
    elif args.command == 'live':
        # Capture process creates the database first; child analyzer waits for it.
        capture = subprocess.Popen([sys.executable, '-m', 'collector', '--data', args.data])
        worker = None
        try:
            import time
            while not Path(args.data, 'events.sqlite3').exists() and capture.poll() is None:
                time.sleep(0.5)
            if capture.poll() is None:
                worker = subprocess.Popen([sys.executable, '-m', 'collector.pipeline', 'analyze', '--watch',
                                           '--data', args.data, '--model', args.model, '--endpoint', args.endpoint])
            capture.wait()
        finally:
            if capture.poll() is None:
                capture.terminate()
                capture.wait()
            if worker and worker.poll() is None:
                worker.terminate()
                worker.wait()
            if Path(args.data, 'events.sqlite3').exists():
                experiences.build(args.data, Path(args.data) / 'experiences')

if __name__ == '__main__':
    main()

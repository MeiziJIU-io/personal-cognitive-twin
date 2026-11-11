"""python -m collector.pipeline --help"""
import argparse
from pathlib import Path
import subprocess
import sys
from . import experiences
from .analyzer import analyze
from .transport import export_bundle, import_bundle
from .sessions import create_session, finish_session, session_roots, migrate_legacy, update_index


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
    for name in ('migrate', 'analyze-all', 'build-all', 'sync', 'finish-session'):
        command = sub.add_parser(name)
        command.add_argument('--data', default='data')
    labels = sub.add_parser('label-pairs')
    labels.add_argument('--data', required=True)
    labels.add_argument('--limit', type=int, default=20)
    sub.add_parser('remote-list')
    download = sub.add_parser('download')
    download.add_argument('sha256')
    download.add_argument('--data', required=True)
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
    elif args.command == 'finish-session':
        print(finish_session(args.data, interrupted=True))
    elif args.command == 'migrate':
        for root in migrate_legacy(args.data):
            print(root)
    elif args.command in ('analyze-all', 'build-all', 'sync'):
        roots = session_roots(args.data)
        if not roots and Path(args.data, 'events.sqlite3').exists():
            roots = [Path(args.data)]
        for root in roots:
            if args.command == 'analyze-all':
                analyze(root)
                from .sync import auto_sync
                if (root / 'session.json').exists():
                    auto_sync(root)
            elif args.command == 'build-all':
                experiences.build(root, root / 'experiences')
            else:
                from .sync import upload_session
                try:
                    print(upload_session(root))
                except Exception as exc:
                    print('Sync deferred:', root, type(exc).__name__)
        update_index(args.data)
    elif args.command == 'label-pairs':
        if args.limit < 1:
            parser.error('--limit must be positive')
        from .frame_labels import label_pairs
        print('Weak labels:', label_pairs(args.data, args.limit))
    elif args.command == 'remote-list':
        from .sync import list_remote
        import json
        print(json.dumps(list_remote(), ensure_ascii=False, indent=2))
    elif args.command == 'download':
        from .sync import download_bundle
        print(download_bundle(args.sha256, args.data))
    elif args.command == 'live':
        # Capture process creates the database first; child analyzer waits for it.
        session_root = create_session(args.data)
        capture = subprocess.Popen([sys.executable, '-m', 'collector', '--data', args.data, '--session-dir', str(session_root)])
        worker = None
        try:
            import time
            while not (session_root / 'events.sqlite3').exists() and capture.poll() is None:
                time.sleep(0.5)
            if capture.poll() is None:
                worker = subprocess.Popen([sys.executable, '-m', 'collector.pipeline', 'analyze', '--watch',
                                           '--data', str(session_root), '--model', args.model, '--endpoint', args.endpoint])
            capture.wait()
        finally:
            if capture.poll() is None:
                capture.terminate()
                capture.wait()
            if worker and worker.poll() is None:
                worker.terminate()
                worker.wait()
            finish_session(session_root, interrupted=True)
            experiences.build(session_root, session_root / 'experiences')
            update_index(args.data)
            import time
            bundle = session_root / ('capture-' + str(time.time_ns()) + '.zip')
            export_bundle(session_root, bundle)
            print('Capture bundle:', bundle)
            from .sync import auto_sync
            auto_sync(session_root)

if __name__ == '__main__':
    main()

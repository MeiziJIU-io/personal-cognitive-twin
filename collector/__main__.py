import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import threading
import time
from uuid import uuid4
from .identity import device_id
from .sessions import create_session, finish_session, record_state, update_index

from .core import Policy, Scheduler, Store

def main():
    parser = argparse.ArgumentParser(description='Windows 实时活动采集（托盘可暂停/退出）')
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--data', default='data')
    parser.add_argument('--session-dir', help=argparse.SUPPRESS)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8'))
    policy = Policy(**config['policy'])
    if min(vars(policy).values()) <= 0:
        parser.error('所有采集间隔必须大于 0')
    from . import windows
    from PIL import Image, ImageGrab
    import pystray
    try:
        windows.user32.SetProcessDPIAware()
    except OSError:
        pass
    stop, paused, reading = threading.Event(), threading.Event(), threading.Event()
    base = Path(args.data).resolve()
    root = Path(args.session_dir).resolve() if args.session_dir else create_session(base)
    metadata = json.loads((root / 'session.json').read_text(encoding='utf-8'))
    logging.basicConfig(filename=root / 'collector.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    def show_window(icon, item):
        (root / 'show-window.request').touch()
    def toggle_pause(icon, item):
        paused.clear() if paused.is_set() else paused.set()
        icon.title = '采集已暂停' if paused.is_set() else '正在采集'
        icon.update_menu()
    def toggle_reading(icon, item):
        reading.clear() if reading.is_set() else reading.set()
        icon.update_menu()
    def quit_app(icon, item):
        stop.set()
        icon.stop()
    icon = pystray.Icon('activity-collector', Image.new('RGB', (64, 64), '#327bea'),
                        '正在采集', menu=pystray.Menu(
        pystray.MenuItem('打开主窗口', show_window),
        pystray.MenuItem('暂停采集', toggle_pause, checked=lambda item: paused.is_set()),
        pystray.MenuItem('阅读模式（不因无操作暂停）', toggle_reading, checked=lambda item: reading.is_set()),
        pystray.MenuItem('停止并退出', quit_app)))

    def collect():
        store = Store(root)
        device = device_id(root)
        session = metadata['session_id']
        state = None
        schedule = Scheduler(policy)
        previous_image = None
        previous_frame = None
        previous_context = None
        try:
            while not stop.is_set():
                if (root / 'stop.request').exists():
                    stop.set()
                    icon.stop()
                    break
                try:
                    # Check pause / lock before reading window content.
                    is_locked = windows.locked()
                    idle = windows.idle_seconds()
                    next_state = ('paused' if paused.is_set() else 'locked' if is_locked else
                                  'idle' if idle >= policy.idle_seconds and not reading.is_set() else 'active')
                    if next_state != state:
                        record_state(root, next_state)
                        state = next_state
                    if paused.is_set() or is_locked or (idle >= policy.idle_seconds and not reading.is_set()):
                        schedule.step(time.monotonic(), None, idle, paused.is_set(), is_locked, reading.is_set())
                        previous_context = None
                        store.last_signature = None
                        stop.wait(1)
                        continue
                    window = windows.foreground()
                    if not window:
                        stop.wait(1)
                        continue
                    app = Path(window['app']).name.lower()
                    if app in config['excluded_apps']:
                        schedule.inactive = True
                        previous_context = None
                        stop.wait(1)
                        continue
                    context = (window['hwnd'], window['title'])
                    now = time.monotonic()
                    if schedule.step(now, context, idle, reading=reading.is_set()):
                        started = datetime.now(timezone.utc).isoformat()
                        local_time = datetime.now().astimezone().isoformat()
                        ui = windows.read_ui(window['hwnd'])
                        # Reject evidence if the user switched windows during extraction.
                        current = windows.foreground()
                        if (stop.is_set() or paused.is_set() or windows.locked() or not current
                            or (current['hwnd'], current['title']) != context
                            or (windows.idle_seconds() >= policy.idle_seconds and not reading.is_set())):
                            schedule.inactive = True
                            continue
                        event = {'at': started, 'capture_started_at': started,
                                 'local_time': local_time, 'event_uid': str(uuid4()),
                                 'schema_version': 2, 'device_id': device, 'session_id': session,
                                 'monotonic_seconds': now, 'source': 'windows',
                                 'window': window, 'idle_seconds': idle, 'ui': ui,
                                 'observation': '前台应用：' + app + '；窗口标题：' + window['title'],
                                 'screenshot': None}
                        if ui.get('protected'):
                            # Avoid capturing a visible password field and adjacent sensitive content.
                            schedule.inactive = True
                            stop.wait(1)
                            continue
                        changed = context != previous_context
                        if schedule.screenshot_due(now, not ui.get('complete')):
                            bounds = tuple(current['bounds'])
                            if bounds[2] > bounds[0] and bounds[3] > bounds[1]:
                                shot = ImageGrab.grab(bbox=bounds, all_screens=True)
                                event['screenshot_captured_at'] = datetime.now(timezone.utc).isoformat()
                                after = windows.foreground()
                                if paused.is_set() or stop.is_set() or windows.locked() or not after or (after['hwnd'], after['title']) != context:
                                    schedule.inactive = True
                                    continue
                                digest = hashlib.sha256(shot.tobytes() + str(shot.size).encode()).hexdigest()
                                schedule.last_shot = now
                                # Exact pixel dedup preserves small text edits.
                                event['frame_decision'] = 'changed' if changed or digest != previous_image else 'exact_duplicate'
                                if digest != previous_image or changed:
                                    folder = root / 'screenshots'
                                    folder.mkdir(exist_ok=True)
                                    path = folder / (digest + '.png')
                                    shot.save(path)
                                    previous_image = digest
                                event['screenshot'] = 'screenshots/' + digest + '.png'
                        event['capture_finished_at'] = datetime.now(timezone.utc).isoformat()
                        store.append(event)
                        from .frame_labels import append_pair
                        if event.get('screenshot'):
                            previous_frame = append_pair(root, event, previous_frame)
                        previous_context = context
                except Exception:
                    logging.exception('采集失败，本轮已跳过')
                stop.wait(1)
        finally:
            store.db.close()

    worker = threading.Thread(target=collect, name='collector')
    worker.start()
    print('正在采集；使用系统托盘暂停、阅读模式或退出。数据目录：', root)
    try:
        icon.run()
    finally:
        stop.set()
        worker.join()
        finish_session(root)
        from .experiences import build
        build(root, root / 'experiences')
        update_index(base)
        if not args.session_dir:
            from .transport import export_bundle
            bundle = root / ('capture-' + str(time.time_ns()) + '.zip')
            export_bundle(root, bundle)
            print('本次采集包：', bundle)
            from .sync import auto_sync
            auto_sync(root)

if __name__ == '__main__':
    main()

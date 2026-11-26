"""Fixed HTTP/SSE endpoint; no discovery, deployment, SSH, plugins or model keys."""
import argparse
import hmac
import json
import os
import time
import zipfile
import io
import tarfile
import zlib
import struct
import hashlib
import copy
import platform
import subprocess
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .core import Manager


def archive_payload(file):
    """Remove inner compression so the solid archive can share sensor bytes.

    Returned PNG and NPZ are still standard, directly readable files. Decoded
    scanlines and NPY entries are identical; only their compression changes.
    """
    data = file.read_bytes()
    if file.suffix == '.npz' and data.startswith(b'PK'):
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, 'w', zipfile.ZIP_STORED) as target:
            for info in source.infolist():
                target.writestr(info.filename, source.read(info))
        return output.getvalue()
    if file.suffix == '.png' and data.startswith(b'\x89PNG\r\n\x1a\n'):
        chunks, compressed, offset = [], [], 8
        while offset < len(data):
            size = struct.unpack('>I', data[offset:offset+4])[0]
            kind = data[offset+4:offset+8]
            payload = data[offset+8:offset+8+size]
            chunks.append((kind, payload))
            if kind == b'IDAT':
                compressed.append(payload)
            offset += size+12
        replacement = zlib.compress(zlib.decompress(b''.join(compressed)), level=0)
        output = bytearray(data[:8]); emitted = False
        for kind, payload in chunks:
            if kind == b'IDAT':
                if emitted:
                    continue
                payload, emitted = replacement, True
            output.extend(struct.pack('>I', len(payload)) + kind + payload
                          + struct.pack('>I', zlib.crc32(kind+payload)))
        return bytes(output)
    return data


def export_episode(root, episode_id):
    """Keep public artifacts, while compressing repeated waiting frames together.

    PNG/NPZ are individually compressed; ordinary ZIP cannot share their repeated
    bytes across frames. A bounded LZMA dictionary can, without dropping evidence.
    """
    root = Path(root)
    files = sorted((f for f in (root / episode_id).rglob('*') if f.is_file() and f.suffix != '.tmp' and not f.name.startswith('evaluation-private')),
                   key=lambda f: f.relative_to(root).as_posix())
    binary = io.BytesIO()
    with tarfile.open(fileobj=binary, mode='w:xz', preset=2) as archive:
        for file in files:
            if file.suffix not in {'.json', '.jsonl'}:
                payload = archive_payload(file)
                info = tarfile.TarInfo(file.relative_to(root).as_posix())
                info.size, info.mtime = len(payload), int(file.stat().st_mtime)
                archive.addfile(info, io.BytesIO(payload))
    package = io.BytesIO()
    with zipfile.ZipFile(package, 'w', zipfile.ZIP_STORED) as archive:
        for file in files:
            if file.suffix in {'.json', '.jsonl'}:
                archive.write(file, str(file.relative_to(root)), compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr('observations.tar.xz', binary.getvalue())
        archive.writestr('manifest.json', json.dumps(dict(version=2, episode_id=episode_id,
            files=len(files), binary_archive='observations.tar.xz',
            scope='public execution evidence only; independent evaluation data stays offline',
            reencoding='Lossless PNG/NPZ compression only; decoded pixels and arrays preserved.',
            instructions='Extract ZIP, then observations.tar.xz to recover original artifact paths.')))
    return package.getvalue()


def runtime_provenance(backend, upstream=None):
    rows = []
    for file in sorted(Path(__file__).parent.glob('*.py')):
        rows.append('agentic_cap/' + file.name + '\0' + hashlib.sha256(file.read_bytes()).hexdigest() + '\n')
    result = dict(backend=backend, source_digest=hashlib.sha256(''.join(rows).encode()).hexdigest(), python=platform.python_version())
    if backend == 'cap':
        contract_files = ['active_evidence.py', 'cap_backend.py', 'cartesian_motion.py', 'core.py', 'feedback.py', 'holding.py', 'holding_watchdog.py', 'interpreter.py', 'public_views.py',
                          'placement_evidence.py', 'public_sensors.py', 'safe_retreat.py', 'robot_system.py', 'skills.py', 'task_recipes.py', 'placement_retry.py', 'program_resources.py', 'continuation_resources.py']
        contract = 'bounded-cube-recovery-v10\n' + ''.join(
            name + '\0' + hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest() + '\n'
            for name in contract_files)
        result['recovery_contract'] = hashlib.sha256(contract.encode()).hexdigest()
        result['features'] = dict(alternate_placement=os.environ.get('CAP_ALTERNATE_PLACEMENT', '0') == '1', inspect_support=os.environ.get('CAP_ACTIVE_EVIDENCE', '0') == '1',
                                  support_second_view=os.environ.get('CAP_SUPPORT_SECOND_VIEW', ''),
                                  holding_watchdog=os.environ.get('CAP_HOLDING_WATCHDOG', '0') == '1',
                                  inspect_placement=os.environ.get('CAP_PLACEMENT_EVIDENCE', '0') == '1',
                                  support_camera_fault=os.environ.get('CAP_SUPPORT_OCCLUSION', ''))
    if upstream:
        names = subprocess.run(['git', '-C', str(upstream), 'ls-files', '-z', '--', '*.py', '*.yaml', '*.yml', '*.toml', '*requirements*.txt'], check=True, capture_output=True).stdout.decode().split('\0')
        inputs = []
        for name in sorted(n for n in names if n):
            file = Path(upstream) / name
            if file.is_file():
                inputs.append(name + '\0' + hashlib.sha256(file.read_bytes()).hexdigest() + '\n')
        result['upstream_source_digest'] = hashlib.sha256(''.join(inputs).encode()).hexdigest()
        result['upstream_revision'] = subprocess.run(['git', '-C', str(upstream), 'rev-parse', 'HEAD'], check=True, capture_output=True).stdout.decode().strip()
    return result


def serve(manager, host, port, token, provenance=None):
    provenance = provenance or runtime_provenance('fixture' if manager.factory.__name__ == 'FixtureBackend' else 'unspecified')
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_):
            pass  # Tokens and request bodies never go to access logs.

        def send(self, status, value, mime='application/json'):
            data = json.dumps(value, ensure_ascii=False).encode() if mime == 'application/json' else value
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            return hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token)

        def do_GET(self):
            self.dispatch('GET')

        def do_POST(self):
            self.dispatch('POST')

        def dispatch(self, method):
            if not self.authorized():
                self.send(401, {'error': 'Unauthorized'})
                return
            try:
                url = urlparse(self.path)
                parts = url.path.strip('/').split('/')
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 <= size <= 64000:
                    raise ValueError('Request too large')
                body = json.loads(self.rfile.read(size)) if size else {}
                if method == 'GET' and parts == ['health']:
                    return self.send(200, dict(service='agentic-cap', protocol=1, **provenance))
                if method == 'GET' and parts == ['system']:
                    from .tabletop_scene import system_description
                    return self.send(200, system_description())
                if method == 'POST' and parts == ['episodes']:
                    return self.send(202, manager.create(**body))
                if len(parts) < 2 or parts[0] != 'episodes':
                    return self.send(404, {'error': 'Unknown route'})
                episode_id = parts[1]
                state = manager.get(episode_id)
                if len(parts) == 2 and method == 'GET':
                    return self.send(200, state)
                action = parts[2] if len(parts) == 3 else ''
                if action == 'trace' and method == 'GET':
                    # Finite, public, closed-task evidence. Never load evaluation-private.
                    if state['status'] != 'closed' or state['active_segment'] is not None:
                        raise ValueError('Public trace requires a closed episode')
                    episode = manager.episodes.get(episode_id)
                    if episode:
                        with episode.lock:
                            events = copy.deepcopy(episode.events)
                    else:
                        file = manager.root / episode_id / 'events.jsonl'
                        events = [json.loads(line) for line in file.read_text(encoding='utf-8').splitlines() if line]
                    # Historical provenance cannot be inferred from a restarted service.
                    if not episode:
                        raise ValueError('Historical source provenance unavailable after restart')
                    return self.send(200, dict(protocol=1, episode=state, events=events, provenance=provenance))
                if action == 'export' and method == 'GET':
                    return self.send(200, export_episode(manager.root, episode_id), 'application/zip')
                if action == 'events' and method == 'GET':
                    after = int(self.headers.get('Last-Event-ID') or parse_qs(url.query).get('after', ['0'])[0])
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Cache-Control', 'no-cache')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    episode = manager.episodes.get(episode_id)
                    while True:
                        if episode:
                            with episode.lock:
                                events = list(episode.events[after:])
                                terminal = episode.status in {'closed', 'failed'}
                        else:
                            file = manager.root / episode_id / 'events.jsonl'
                            events = [json.loads(line) for line in file.read_text().splitlines() if line][after:]
                            terminal = True
                        for event in events:
                            self.wfile.write(('id: %s\ndata: %s\n\n' % (event['id'], json.dumps(event))).encode())
                            after = event['id']
                        self.wfile.write(b': heartbeat\n\n')
                        self.wfile.flush()
                        if terminal:
                            break
                        time.sleep(.25)
                    self.close_connection = True
                    return
                episode = manager.episodes.get(episode_id)
                if not episode:
                    return self.send(409, {'error': 'Episode interrupted; physical execution cannot resume'})
                if action == 'system' and method == 'GET':
                    if not hasattr(episode, 'describe'): raise ValueError('System description requires tabletop mode')
                    return self.send(200, episode.describe())
                if action == 'experiences' and method == 'GET':
                    if not hasattr(episode, 'experiences'): raise ValueError('Generic experience requires tabletop mode')
                    return self.send(200, episode.experiences())
                if action == 'operation' and method == 'POST':
                    if not hasattr(episode, 'submit_operation'): raise ValueError('Direct operation requires tabletop mode')
                    return self.send(200, episode.submit_operation(**body))
                if action == 'plan' and method == 'POST':
                    if not hasattr(episode, 'submit_plan'): raise ValueError('Dynamic plans require tabletop mode')
                    return self.send(200, episode.submit_plan(**body))
                if action == 'observe' and method == 'GET':
                    return self.send(200, episode.observe())
                if action == 'verify' and method == 'POST':
                    return self.send(200, episode.finish(body['revision']))
                if action == 'segments' and method == 'POST':
                    if not isinstance(body, dict) or not body.get('task_ref'):
                        raise ValueError('Public code execution requires a Runtime task_ref')
                    return self.send(202, dict(segment_id=episode.execute(**body)))
                if action == 'skills' and method == 'POST':
                    return self.send(202, dict(segment_id=episode.execute_skill(**body)))
                if action == 'cancel' and method == 'POST':
                    return self.send(200, episode.cancel(segment_id=body.get('segment_id')))
                if action == 'assistance' and method == 'POST':
                    return self.send(200, episode.request_assistance(**body))
                if action == 'goal' and method == 'POST':
                    return self.send(200, episode.update_goal(body['goal']))
                if action == 'decisions' and method == 'POST':
                    return self.send(200, dict(event_id=episode.decision(**body)))
                if action == 'close' and method == 'POST':
                    episode.close()
                    return self.send(200, episode.state())
                if action == 'artifact' and method == 'GET':
                    name = parse_qs(url.query).get('name', [''])[0]
                    # Only recorded artifacts, never arbitrary paths or directory traversal.
                    if not name or '/' in name or '\\' in name or name.startswith('.'):
                        raise ValueError('Invalid artifact')
                    file = episode.directory / name
                    if file.suffix not in {'.png', '.npz'}:
                        raise ValueError('Unsupported artifact')
                    return self.send(200, file.read_bytes(), 'image/png' if file.suffix == '.png' else 'application/octet-stream')
                self.send(404, {'error': 'Unknown route'})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except (KeyError, FileNotFoundError) as exc:
                self.send(404, {'error': str(exc)})
            except (ValueError, TypeError) as exc:
                self.send(409, {'error': str(exc), 'code': getattr(exc, 'code', 'CONFLICT')})
            except Exception as exc:
                self.send(500, {'error': str(exc)})

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8120)
    parser.add_argument('--records', default='./robot-records')
    parser.add_argument('--backend', choices=['cap', 'fixture'], default='cap')
    parser.add_argument('--upstream', help='Existing CaP-X checkout')
    parser.add_argument('--evaluation', action='store_true', help='Record private scoring; never expose to the model')
    args = parser.parse_args()
    token = os.environ.get('CAP_RUNTIME_TOKEN', '')
    if len(token) < 16:
        parser.error('Set CAP_RUNTIME_TOKEN to at least 16 characters')
    if args.backend == 'fixture':
        from .fixture import FixtureBackend
        from .tabletop_fixture import TabletopFixtureBackend
        factory = lambda episode: TabletopFixtureBackend(episode) if episode.task == 'tabletop' else FixtureBackend(episode)
    else:
        from .cap_backend import CaPBackend
        from .tabletop_backend import TabletopBackend
        factory = lambda episode: (TabletopBackend(episode, evaluation=args.evaluation) if episode.task == 'tabletop'
                                   else CaPBackend(episode, args.upstream, evaluation=args.evaluation))
    manager = Manager(args.records, factory)
    server = serve(manager, args.host, args.port, token, runtime_provenance(args.backend, args.upstream))
    print(json.dumps(dict(listening=args.port, backend=args.backend)), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for episode in manager.episodes.values():
            episode.close()
        server.server_close()


if __name__ == '__main__':
    main()

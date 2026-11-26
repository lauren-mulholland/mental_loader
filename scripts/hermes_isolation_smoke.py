"""Probe separate native Hermes homes/processes with synthetic data, without inference.

Uses the pinned installed runtime read-only. Creates a disposable app database,
backend, Hermes homes and credentials; never loads the personal home's .env.
"""

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
from pathlib import Path

import uvicorn

from mental_loader.app import create_app
from mental_loader.config import Settings
from mental_loader.storage import Household, digest

ROOT = Path(__file__).resolve().parents[1]

CHILD = r'''
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from hermes_state import SessionDB
from tools.memory_tool import load_on_disk_store, memory_tool
from hermes_cli.plugins import PluginManager
from tools.registry import registry

marker = os.environ['ML_TEST_MARKER']
mode = os.environ['ML_TEST_MODE']
session = SessionDB()
if mode == 'init':
    assert not load_on_disk_store().user_entries, 'Memory crossed home boundary'
    assert session.get_session('same-guessed-session-id') is None, 'Session crossed home boundary'
    result = json.loads(memory_tool(action='add', target='user', content=marker,
                                   store=load_on_disk_store()))
    assert result.get('success'), 'Native memory write failed'
    session.create_session('same-guessed-session-id', source='api_server')
    session.append_message('same-guessed-session-id', 'user', marker)
else:
    assert load_on_disk_store().user_entries == [marker], 'Memory failed persistence/isolation'
    messages = session.get_messages('same-guessed-session-id')
    assert [m['content'] for m in messages] == [marker], 'Session failed persistence/isolation'
session.close()

manager = PluginManager()
manager.discover_and_load()
remind = registry.get_entry('household_remind', scope=manager.scope_key)
tasks = registry.get_entry('household_tasks', scope=manager.scope_key)
assert remind is not None and tasks is not None, 'Native plugin failed to register'
args = {'title': marker, 'due_at': (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        'request_id': str(uuid4())}
if os.environ.get('ML_TEST_FOREIGN_CHAT'):
    rejected = json.loads(remind.handler(dict(args,
        conversation_id=os.environ['ML_TEST_FOREIGN_CHAT'])))
    assert rejected.get('status') == 404, 'Tool accessed a foreign conversation'
if mode == 'init':
    result = json.loads(remind.handler(args))
    assert result.get('task_id'), 'Native reminder tool failed'
else:
    result = {}
rows = json.loads(tasks.handler({}))
assert [row['title'] for row in rows] == [marker], 'Native tool exposed another household'
print('ISOLATION_RESULT=' + json.dumps(result))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=ROOT / '.local/hermes-runtime')
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    revision = subprocess.check_output(
        ['git', '-C', str(runtime), 'rev-parse', 'HEAD'], text=True
    ).strip()
    if revision != (ROOT / 'hermes-runtime.ref').read_text().splitlines()[1]:
        raise SystemExit('Runtime revision differs from the project pin; refusing this probe.')
    python = runtime / '.venv/bin/python'
    if not python.exists():
        raise SystemExit('Install the pinned isolated Hermes runtime first.')
    with tempfile.TemporaryDirectory(prefix='mental-loader-isolation-') as temporary:
        root = Path(temporary)
        settings = Settings(
            _env_file=None, database_url=f'sqlite:///{root}/app.db', ai_enabled=False
        )
        app = create_app(settings, start_worker=False)
        codes = [secrets.token_urlsafe(32), secrets.token_urlsafe(32)]
        with app.state.sessions.begin() as db:
            for i, code in enumerate(codes):
                db.add(Household(name=f'Synthetic {i}', access_hash=digest(code)))
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(16)
            origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(
                target=server.run, kwargs={'sockets': [listener]}, daemon=True
            )
            thread.start()
            try:
                for index in range(2):
                    home = root / f'home-{index}'
                    plugin = home / 'plugins/mental-loader'
                    plugin.mkdir(parents=True)
                    for name in ('__init__.py', 'plugin.yaml'):
                        shutil.copyfile(ROOT / 'hermes_plugin' / name, plugin / name)
                    (home / 'config.yaml').write_text('plugins:\n  enabled: [mental-loader]\n')

                def probe(index, mode, foreign=''):
                    home = root / f'home-{index}'
                    env = {
                        'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                        'HOME': str(home), 'PYTHONPATH': str(runtime),
                        'HERMES_HOME': str(home), 'HERMES_RUNTIME_DIR': str(home / 'managed'),
                        'ML_BACKEND_URL': origin, 'ML_HOUSEHOLD_ACCESS_CODE': codes[index],
                        'ML_TEST_MARKER': f'Synthetic household {index} prefers test fruit.',
                        'ML_TEST_MODE': mode, 'ML_TEST_FOREIGN_CHAT': foreign,
                    }
                    result = subprocess.run(
                        [str(python), '-c', CHILD], env=env, cwd=home,
                        text=True, capture_output=True, timeout=45,
                    )
                    if result.returncode:
                        # All inputs are synthetic, but avoid dumping subprocess config/logs.
                        raise RuntimeError(
                            f'Native isolation probe failed: household {index}, {mode}'
                        )
                    line = next(line for line in result.stdout.splitlines()
                                if line.startswith('ISOLATION_RESULT='))
                    return json.loads(line.partition('=')[2])

                first = probe(0, 'init')
                second = probe(1, 'init', first['conversation_id'])
                probe(0, 'verify', second['conversation_id'])
                probe(1, 'verify', first['conversation_id'])
            finally:
                server.should_exit = True
                thread.join(timeout=10)
                if thread.is_alive():
                    raise RuntimeError('Temporary backend did not stop')
    print('PASS: separate native Hermes processes/homes preserved isolated memory and '
          'sessions after restart; actual plugin credentials isolated household tasks '
          'and rejected guessed foreign conversation IDs. No model calls or real pushes.')


if __name__ == '__main__':
    main()

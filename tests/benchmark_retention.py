"""Offline retention benchmark: python tests/benchmark_retention.py from a checkout.

Uses a temporary database with 1,000 requests and 32,000 short synthetic responses.
No timing assertion: report observed performance separately from functional tests.
"""
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.fernet import Fernet
from bridge.auth import Policy
from bridge.callback import Callback
from bridge.store import Store


def main():
    with tempfile.TemporaryDirectory(prefix='hdb-retention-') as directory:
        root = Path(directory)
        policy = root / 'policy.json'
        policy.write_text('{"grants": []}')
        now = time.time()
        store = Store(str(root / 'benchmark.sqlite'), Fernet.generate_key(), Policy(str(policy)),
                      Callback(['receiver.example']), clock=lambda: now)
        try:
            with store.transaction():
                for i in range(1000):
                    request = {'id': f'r{i}', 'owner': 'synthetic', 'conversation_id': f'c{i}',
                               'created': now - 8 * 86400, 'expires': now - 7 * 86400,
                               'state': 'completed', 'revision': 32}
                    store.put('request', request)
                    store.put('conversation', {'id': f'c{i}', 'owner': 'synthetic'})
                    for revision in range(1, 33):
                        store.put('response', {'id': f'r{i}-{revision}', 'owner': 'synthetic',
                                  'request_id': request['id'], 'revision': revision, 'content': 'synthetic'})
            started = time.perf_counter()
            store.deliver_once()
            elapsed = time.perf_counter() - started
            remaining = store.db.execute('SELECT count(*) FROM records').fetchone()[0]
            print(json.dumps({'requests': 1000, 'responses': 32000,
                              'retention_seconds': round(elapsed, 3), 'remaining_records': remaining}))
        finally:
            store.db.close()


if __name__ == '__main__':
    main()

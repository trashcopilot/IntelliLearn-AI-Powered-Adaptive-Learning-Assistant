import os
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections

# Allow up to 3 concurrent background AI jobs so batch uploads process in
# parallel.  Each job's Gemini calls already fan out across model candidates
# concurrently, so increasing workers here gives a meaningful throughput boost
# without overwhelming the API.  Override via BACKGROUND_AI_WORKERS env var.
_BACKGROUND_AI_WORKERS = max(1, int(os.getenv('BACKGROUND_AI_WORKERS', '3')))
_executor = ThreadPoolExecutor(max_workers=_BACKGROUND_AI_WORKERS)


def run_background(task, *args, **kwargs):
    def runner():
        close_old_connections()
        try:
            return task(*args, **kwargs)
        finally:
            close_old_connections()

    return _executor.submit(runner)

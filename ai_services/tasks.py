from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections

# Gemini work is serialized upstream, so a single worker avoids queued uploads
# from competing for threads while they wait on the same external dependency.
_executor = ThreadPoolExecutor(max_workers=1)


def run_background(task, *args, **kwargs):
    def runner():
        close_old_connections()
        try:
            return task(*args, **kwargs)
        finally:
            close_old_connections()

    return _executor.submit(runner)

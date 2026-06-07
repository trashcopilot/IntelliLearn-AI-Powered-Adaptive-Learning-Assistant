from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections

_executor = ThreadPoolExecutor(max_workers=2)


def run_background(task, *args, **kwargs):
    def runner():
        close_old_connections()
        try:
            return task(*args, **kwargs)
        finally:
            close_old_connections()

    return _executor.submit(runner)

from concurrent.futures import ThreadPoolExecutor

_executor = ThreadPoolExecutor(max_workers=2)


def run_background(task, *args, **kwargs):
    return _executor.submit(task, *args, **kwargs)

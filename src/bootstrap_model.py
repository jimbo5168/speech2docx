"""Model setup bridge for the native launcher, using the verified runtime."""
import json
import os
from pathlib import Path
import time

from model_download import Downloader


def main():
    home = Path(os.environ['ASR_HOME'])
    sources = json.loads(os.environ['ASR_DOWNLOAD_SOURCES']) if os.environ.get('ASR_DOWNLOAD_SOURCES') else None
    manager = Downloader(home, sources=sources)
    progress = home / 'work' / 'bootstrap-progress.json'
    progress.parent.mkdir(parents=True, exist_ok=True)
    manager.prepare()
    started = False
    while True:
        state = manager.status()
        temporary = progress.with_suffix('.tmp')
        temporary.write_text(json.dumps(state))
        temporary.replace(progress)
        if state['ready']:
            return
        if state['status'] == 'error':
            raise RuntimeError(state['error'])
        if state['status'] in ('ready', 'prepared', 'paused') and not started:
            manager.start()
            started = True
        time.sleep(0.2)


if __name__ == '__main__':
    main()

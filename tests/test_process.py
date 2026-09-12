import sys
import threading

import pytest

from app.media import run, Stopped


def test_process_output_and_stop(tmp_path):
    assert run([sys.executable,'-c','print("completed")'],tmp_path).strip()=='completed'
    stop=threading.Event()
    stop.set()
    with pytest.raises(Stopped):
        run([sys.executable,'-c','import time; time.sleep(60)'],tmp_path,stop_event=stop)

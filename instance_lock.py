"""Hold a per-data-directory lock to prevent two pollers sharing one database."""
import os
from config import DATA_DIR
_handle = None

def acquire_instance():
    global _handle
    os.makedirs(DATA_DIR, exist_ok=True)
    _handle = open(os.path.join(DATA_DIR, 'instance.lock'), 'a+b')
    _handle.seek(0)
    _handle.write(b'0')
    _handle.flush()
    _handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(_handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        _handle.close()
        _handle = None
        return False

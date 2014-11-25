import sys
import time
import subprocess


def main():
    relayer = subprocess.Popen(
        ('python',
         'run.py',
         '--listen',
         '0.0.0.0:9001',
         '--relay',
         '0.0.0.0:9002',
         ),
        universal_newlines=True,
        stdin=subprocess.DEVNULL,
    )
    time.sleep(.2)
    logger = subprocess.Popen(
        ('python',
         'run.py',
         '--listen',
         '0.0.0.0:9002',
         '--log',
         ),
        universal_newlines=True,
        stdin=subprocess.DEVNULL,
    )
    time.sleep(.2)
    sender = subprocess.Popen(
        ('python',
         'send.py',
         '--relay',
         '0.0.0.0:9001',
         '--sender', 'foo@example.local',
         '--recipient', 'bar@example.local',
         '--encoding', 'utf-16',
         ),
        env=dict(PYTHONPATH='..'),
        universal_newlines=True,
        stdin=subprocess.PIPE,
    )
    sender.communicate(
        'Hello world!\n')
    sender.wait()
    sys.stdin.read()
    relayer.kill()
    relayer.wait()
    logger.kill()
    logger.wait()

if __name__ == "__main__":
    main()

"""Exercise the same --env-file startup mode used by the systemd deployment."""
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
import urllib.error
import urllib.request


class EnvironmentDeploymentTests(unittest.TestCase):
    def test_uvicorn_reads_env_file_in_a_fresh_process(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'deployment_smoke.py').write_text(
                'import os\n'
                'async def app(scope, receive, send):\n'
                '    await send({"type":"http.response.start","status":200,"headers":[]})\n'
                '    await send({"type":"http.response.body","body":os.environ.get("OW_DEPLOYMENT_ENV_SMOKE_VALUE", "missing").encode()})\n',
                encoding='utf-8')
            env_file = root / '.env'
            env_file.write_text('OW_DEPLOYMENT_ENV_SMOKE_VALUE=loaded-from-file\n', encoding='utf-8')
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            env = dict(os.environ)
            env.pop('OW_DEPLOYMENT_ENV_SMOKE_VALUE', None)
            process = subprocess.Popen(
                [sys.executable, '-m', 'uvicorn', 'deployment_smoke:app', '--host', '127.0.0.1',
                 '--port', str(port), '--lifespan', 'off', '--env-file', str(env_file)],
                cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            body = None
            try:
                for _ in range(50):
                    if process.poll() is not None:
                        self.fail('Deployment startup exited: '+process.communicate()[0].decode(errors='replace'))
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=.3) as response:
                            body = response.read().decode()
                        break
                    except (OSError, urllib.error.URLError):
                        time.sleep(.1)
                self.assertEqual(body, 'loaded-from-file')
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)

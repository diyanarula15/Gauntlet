"""Approved local adapter execution. Registry files, never browser inputs, own commands/URLs."""
import asyncio
import json
import os
import re
import signal
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import httpx
from ..config import ROOT


class AdapterFailure(Exception):
    pass


def parse_output(text, output_format='json_stdout'):
    if output_format == 'plain_text':
        return {'answer': text.strip(), 'citations': re.findall(r'https?://\S+', text), 'status': 'completed'}
    for line in reversed(text.splitlines()):
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and isinstance(obj.get('answer'), str) and obj.get('status') in ('completed', 'failed'):
                return obj
        except ValueError:
            pass
    raise AdapterFailure('Agent did not emit a final JSON object with answer and status')


async def bounded_read(stream):
    chunks = []; size = 0
    while chunk := await stream.read(8192):
        size += len(chunk)
        if size > 1024 * 1024: raise AdapterFailure('Agent output exceeded 1 MiB')
        chunks.append(chunk)
    return b''.join(chunks)


async def stop_process(proc):
    if proc.returncode is not None: return
    try: os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError: return
    try: await asyncio.wait_for(proc.wait(), 1)
    except asyncio.TimeoutError:
        try: os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        await proc.wait()


async def launch(agent, task, environment, on_start, on_log=None):
    adapter = agent.get('adapter', 'cli')
    if adapter == 'http':
        # An approved service accepts an explicit per-request environment envelope. No process-global env mutation.
        async with httpx.AsyncClient(timeout=task['limits']['max_seconds'], follow_redirects=False) as client:
            response = await client.post(agent['url'], json={'task': task, 'environment': environment})
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict): raise AdapterFailure('HTTP agent must return a JSON object')
            return parse_output(json.dumps(result)), '', 0
    with TemporaryDirectory(prefix='faultline-agent-') as temp:
        task_file = Path(temp) / 'task.json'; output_file = Path(temp) / 'answer.json'
        task_file.write_text(json.dumps(task))
        substitutions = {'{python}': sys.executable, '{task_file}': str(task_file), '{out_file}': str(output_file)}
        source = agent.get('command', [sys.executable, str(ROOT / agent.get('script', ''))])
        command = [substitutions.get(arg, arg.replace('{task_file}', str(task_file)).replace('{out_file}', str(output_file))) for arg in source]
        container_name = None
        if adapter == 'docker':
            container_name = 'faultline-' + os.path.basename(temp)
            command = ['docker', 'run', '--rm', '--name', container_name, '--init', '--add-host=host.docker.internal:host-gateway', '-i', *[x for key in environment if key not in ('PATH', 'TMPDIR') for x in ('-e', key)], agent['image'], *agent.get('arguments', [])]
        elif adapter != 'cli': raise AdapterFailure('Unrecognized approved adapter')
        proc = None
        readers = []
        try:
            proc = await asyncio.create_subprocess_exec(*command, cwd=str(ROOT / agent.get('working_directory', '.')), env=environment,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
            on_start(proc.pid)
            proc.stdin.write(json.dumps(task).encode()); await proc.stdin.drain(); proc.stdin.close()
            readers = [asyncio.create_task(bounded_read(proc.stdout)), asyncio.create_task(bounded_read(proc.stderr))]
            stdout, stderr = await asyncio.gather(*readers)
            await proc.wait()
            if on_log:
                on_log('stdout', stdout.decode(errors='replace'))
                on_log('stderr', stderr.decode(errors='replace'))
            text = output_file.read_text() if agent.get('output_format') == 'json_file' and output_file.exists() else stdout.decode(errors='replace')
            return parse_output(text, agent.get('output_format', 'json_stdout')), stderr.decode(errors='replace')[-4000:], proc.returncode
        finally:
            for reader in readers:
                if not reader.done(): reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            if proc: await stop_process(proc)
            if container_name:
                cleanup = await asyncio.create_subprocess_exec('docker', 'rm', '-f', container_name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                try: await asyncio.wait_for(cleanup.wait(), 3)
                except asyncio.TimeoutError: cleanup.kill(); await cleanup.wait()

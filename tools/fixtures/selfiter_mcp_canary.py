#!/usr/bin/env python3
"""Disposable MCP positive-control fixture, never shipped with the plugin."""
import json
import sys
from pathlib import Path


def main():
    destination = Path(sys.argv[1])
    for raw in sys.stdin.buffer:
        message = json.loads(raw.decode('utf-8'))
        if 'id' not in message:
            continue
        method = message.get('method')
        if method == 'initialize':
            result = {'protocolVersion': message.get('params', {}).get('protocolVersion', '2024-11-05'),
                      'capabilities': {'tools': {}}, 'serverInfo': {'name': 'selfiter-canary', 'version': '1'}}
        elif method == 'tools/list':
            result = {'tools': [{'name': 'touch_canary', 'description': 'Write PROBE to the disposable canary only.',
                                 'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}]}
        elif method == 'tools/call':
            destination.write_text('PROBE\n', encoding='utf-8', newline='')
            result = {'content': [{'type': 'text', 'text': 'Disposable canary written.'}]}
        elif method == 'ping':
            result = {}
        else:
            print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32601, 'message': 'Unknown method'}}), flush=True)
            continue
        print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    main()

"""Wire formats for translation APIs, independent of model profile storage."""
from __future__ import annotations

import copy
from urllib.parse import urlsplit, urlunsplit

PROTOCOLS = {'auto', 'chat_completions', 'responses', 'anthropic'}
SUFFIXES = {'chat_completions': '/chat/completions', 'responses': '/responses',
            'anthropic': '/messages'}


def protocol(cfg) -> str:
    selected = cfg.get('api.protocol', 'auto') or 'auto'
    if selected != 'auto':
        if selected not in PROTOCOLS:
            raise ValueError('请选择有效的接口类型。')
        return selected
    path = urlsplit(str(cfg.get('api.base_url', ''))).path.rstrip('/')
    for kind, suffix in SUFFIXES.items():
        if path.endswith(suffix):
            return kind
    host = urlsplit(str(cfg.get('api.base_url', ''))).hostname
    return 'anthropic' if host == 'api.anthropic.com' or cfg.get('api.provider') == 'anthropic' else 'chat_completions'


def provider(cfg) -> str:
    selected = cfg.get('api.provider', 'auto') or 'auto'
    if selected != 'auto':
        return selected
    host = (urlsplit(str(cfg.get('api.base_url', ''))).hostname or '').lower()
    model = str(cfg.get('api.model', '')).lower()
    if host == 'api.deepseek.com':
        return 'deepseek'
    if host == 'api.anthropic.com':
        return 'anthropic'
    if host == 'api.openai.com' or model.startswith(('gpt-', 'chatgpt-', 'o1', 'o3', 'o4')):
        return 'openai'
    return 'compatible'


def endpoint(cfg, *, models=False) -> str:
    base = str(cfg.get('api.base_url') or 'https://api.deepseek.com').strip().rstrip('/')
    url = urlsplit(base)
    if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('API 地址应为完整 HTTP/HTTPS 地址，不包含查询参数或认证信息。')
    url.port
    path = url.path.rstrip('/')
    complete_endpoint = False
    for suffix in SUFFIXES.values():
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            complete_endpoint = True
            break
    # Preserve proxy prefixes verbatim. Bare hosts use /v1, except DeepSeek's
    # documented versionless endpoint. A complete endpoint is also accepted.
    if not path and not complete_endpoint and url.hostname != 'api.deepseek.com':
        path = '/v1'
    suffix = '/models' if models else SUFFIXES[protocol(cfg)]
    return urlunsplit((url.scheme, url.netloc, path + suffix, '', ''))


def headers(key: str, kind: str) -> dict:
    result = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if kind == 'anthropic':
        result.update({'x-api-key': key, 'anthropic-version': '2023-06-01'})
    else:
        result['Authorization'] = 'Bearer ' + key
    return result


def wire_body(body: dict, kind: str) -> dict:
    if kind == 'chat_completions':
        return copy.deepcopy(body)
    system = '\n\n'.join(m['content'] for m in body['messages'] if m['role'] == 'system')
    messages = [copy.deepcopy(m) for m in body['messages'] if m['role'] != 'system']
    common = {'model': body['model'], 'stream': body['stream']}
    if 'temperature' in body:
        common['temperature'] = body['temperature']
    limit = body.get('max_completion_tokens', body.get('max_tokens'))
    if kind == 'responses':
        return {**common, 'instructions': system, 'input': messages,
                'max_output_tokens': limit, 'store': False}
    return {**common, 'system': system, 'messages': messages, 'max_tokens': limit}


def usage(payload) -> dict:
    if not isinstance(payload, dict):
        return {}
    incoming = payload.get('input_tokens', payload.get('prompt_tokens', 0)) or 0
    outgoing = payload.get('output_tokens', payload.get('completion_tokens', 0)) or 0
    return {'prompt_tokens': incoming, 'completion_tokens': outgoing,
            'total_tokens': payload.get('total_tokens') or incoming + outgoing,
            'completion_tokens_details': payload.get('output_tokens_details', {})}


def response_text(data: dict, kind: str) -> str:
    if kind == 'anthropic':
        return ''.join(block.get('text', '') for block in data.get('content', [])
                       if block.get('type') == 'text')
    return ''.join(block.get('text', '') for item in data.get('output', [])
                   if item.get('type') == 'message'
                   for block in item.get('content', []) if block.get('type') == 'output_text')


def sse_events(resp):
    """Decode SSE data fields; comments, event names and heartbeats are metadata."""
    parts = []
    for raw in resp.iter_lines(decode_unicode=False):
        line = raw.decode('utf-8') if isinstance(raw, bytes) else raw
        if not line:
            if parts:
                yield '\n'.join(parts)
                parts = []
        elif line.startswith('data:'):
            parts.append(line[5:].lstrip(' '))
    if parts:
        yield '\n'.join(parts)

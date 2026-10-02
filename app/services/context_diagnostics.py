"""Opt-in metadata-only context diagnostics; never logs document contents."""
import hashlib
import json
import logging
import math

logger = logging.getLogger(__name__)


def text_metrics(text: str, markers=()) -> dict:
    encoded = text.encode('utf-8')
    return {
        'prompt_chars': len(text),
        'prompt_bytes': len(encoded),
        # Deliberately labelled an estimate, not a tokenizer or an admission limit.
        'estimated_tokens': math.ceil(len(encoded) / 3),
        'token_estimation_method': 'utf8_bytes/3; heuristic, excludes model template and images',
        'sha256': hashlib.sha256(encoded).hexdigest(),
        'markers': {marker: marker in text for marker in markers},
    }


def request_metrics(body: dict, markers=()) -> dict:
    messages = body.get('messages', [])
    content = '\n\n'.join(message.get('content') or '' for message in messages) if messages else body.get('prompt', '')
    tools = json.dumps(body.get('tools', []), ensure_ascii=False)
    return {
        **text_metrics(content, markers),
        'model': body['model'],
        'configured_num_ctx': body['options']['num_ctx'],
        'truncate': body.get('truncate'),
        'message_count': len(messages),
        'messages': [dict(index=i, role=m.get('role'), images=len(m.get('images', [])),
                          **text_metrics(m.get('content') or '', markers)) for i, m in enumerate(messages)],
        'tools_chars': len(tools),
        'tools_bytes': len(tools.encode('utf-8')),
        'estimated_tokens_with_tools': math.ceil((len(content.encode('utf-8')) + len(tools.encode('utf-8'))) / 3),
        'scope': 'message content before Ollama template rendering; images excluded',
    }


def log_event(event: str, **fields):
    logger.warning('context_audit %s', json.dumps({'event': event, **fields}, ensure_ascii=False))

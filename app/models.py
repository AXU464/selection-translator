"""Named API profiles. Secrets stay in the local configuration file."""
from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace

from .config import DEFAULTS


def catalog(cfg) -> tuple[list[dict], str]:
    data = cfg.as_dict()
    profiles = copy.deepcopy(data.get('model_profiles') or [])
    active = data.get('active_model', 'default')
    if not profiles:
        profiles = [{'id': 'default', 'api': copy.deepcopy(data['api'])}]
        active = 'default'
    for profile in profiles:
        if profile['id'] == active:
            profile['api'] = copy.deepcopy(data['api'])
    return profiles, active


def choices(cfg) -> dict:
    profiles, active = catalog(cfg)
    return {'active': active, 'items': [
        {'id': p['id'], 'label': ('主模型' if index == 0 else f'备用模型{index}') + ' ' + p['api']['model'],
         'model': p['api']['model']} for index, p in enumerate(profiles)]}


def view(cfg, profile_id=None, new=False):
    profiles, active = catalog(cfg)
    if new:
        api = copy.deepcopy(DEFAULTS['api'])
        api.update(provider='openai', protocol='chat_completions', base_url='https://api.openai.com/v1',
                   model='gpt-4.1-mini', api_key='')
    else:
        profile = next((p for p in profiles if p['id'] == (profile_id or active)), None)
        if profile is None:
            raise ValueError('模型配置不存在，请重新打开设置。')
        api = {**DEFAULTS['api'], **profile['api']}
    return SimpleNamespace(get=lambda key, default=None: api.get(key[4:], default)
                           if key.startswith('api.') else cfg.get(key, default))


def save(cfg, changes, profile_id=None, new=False):
    with cfg._lock:
        profiles, active = catalog(cfg)
        selected = profile_id or active
        if new:
            selected = uuid.uuid4().hex
            profiles.append({'id': selected, 'api': copy.deepcopy(DEFAULTS['api'])})
        profile = next((p for p in profiles if p['id'] == selected), None)
        if profile is None:
            raise ValueError('模型配置不存在，请重新打开设置。')
        for key, value in changes.items():
            if key.startswith('api.'):
                profile['api'][key[4:]] = value
        values = {key: value for key, value in changes.items() if not key.startswith('api.')}
        values.update(model_profiles=profiles, active_model=selected, api=profile['api'])
        cfg.update_values(values)


def activate(cfg, profile_id):
    with cfg._lock:
        profiles, _ = catalog(cfg)
        profile = next((p for p in profiles if p['id'] == profile_id), None)
        if profile is None:
            raise ValueError('模型配置不存在，请在设置中保存模型。')
        cfg.update_values({'model_profiles': profiles, 'active_model': profile_id,
                           'api': {**DEFAULTS['api'], **profile['api']}})
    return choices(cfg)

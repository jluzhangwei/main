from __future__ import annotations

import json
from pathlib import Path

from app.services import log_highlight_service as svc


def test_builtin_presets_listed():
    items = svc.list_presets()
    ids = {item['id'] for item in items}
    assert 'default' in ids
    assert 'huawei_alarm' in ids
    assert 'nxos_syslog' in ids
    assert 'iosxr_routing' in ids


def test_resolve_preset_merges_extends():
    preset = svc.resolve_preset('huawei_alarm')
    assert preset['id'] == 'huawei_alarm'
    assert preset['colors']['blue']
    assert any('hwLocalFaultAlarm_active' in x for x in preset['colors']['red'])
    assert any('\\d{4}-\\d{2}-\\d{2}' in x for x in preset['colors']['blue'])


def test_export_json_and_ini():
    json_text, json_type, json_name = svc.export_preset_text('iosxr_routing', 'json')
    payload = json.loads(json_text)
    assert json_type == 'application/json'
    assert json_name.endswith('.json')
    assert payload['id'] == 'iosxr_routing'
    ini_text, ini_type, ini_name = svc.export_preset_text('iosxr_routing', 'ini')
    assert ini_type.startswith('text/plain')
    assert ini_name.endswith('.ini')
    assert '[Preset]' in ini_text
    assert 'Magenta =' in ini_text or 'Magenta=' in ini_text


def test_import_ini_preset(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, 'CUSTOM_DIR', tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    content = """
[Preset]
name = Demo Imported
extends = default

[Rules]
Red = foo || bar
Blue = baz
""".strip().encode('utf-8')
    preset = svc.import_preset('demo.ini', content)
    assert preset['id'] == 'demo'
    assert preset['source'] == 'custom'
    assert Path(preset['path']).exists()
    resolved = svc.resolve_preset('demo')
    assert any(item == 'foo' for item in resolved['colors']['red'])
    assert any('\\d{4}-\\d{2}-\\d{2}' in item for item in resolved['colors']['blue'])

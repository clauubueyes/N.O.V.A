from __future__ import annotations

import base64
import io
import json
import threading
import time
import zipfile
from dataclasses import replace

import pytest
import yaml
from fastapi.testclient import TestClient

from nova.api.app import create_app
from nova.core.approvals import ApprovalBroker
from nova.core.attachments import AttachmentStore
from nova.core.config import NovaSettings, PermissionSettings
from nova.core.conversations import ConversationStore
from nova.setup.bundle import install_bundle, sha256
from nova.setup.desktop import Preparation, recommended_chat, recommended_vision
from nova.setup.detect import MachineProfile
from nova.tools.permissions import PermissionDecision, PermissionSystem
from tests.test_api import FakeProvider, BoomProvider


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVA_HOME', str(tmp_path / 'home'))
    path = tmp_path / 'home' / 'config.yaml'
    monkeypatch.setenv('NOVA_CONFIG', str(path))
    settings = NovaSettings()
    settings.api.token = 'test-only-secret'
    settings.api.host_enabled = True
    settings.memory.db_file = str(tmp_path / 'home' / 'data' / 'nova.db')
    settings.audit.file = str(tmp_path / 'audit.jsonl')
    settings.llm.default_model = 'fake-model'
    settings.permissions.categories = {'reading': 'ask', 'writing': 'ask', 'system': 'deny', 'remote': 'deny'}
    path.parent.mkdir()
    path.write_text(yaml.safe_dump(settings.model_dump(mode='json')), encoding='utf-8')
    return settings, path


def client_for(environment, provider=None):
    settings, path = environment
    app = create_app(settings, provider=provider or FakeProvider(), desktop=True, config_path=path)
    return TestClient(app, base_url='http://127.0.0.1', headers={'Authorization': 'Bearer test-only-secret'})


def test_restart_restores_full_history_with_bounded_model_context(environment):
    environment[0].session.max_history_messages = 2
    with client_for(environment) as client:
        sid = client.post('/v1/sessions').json()['session_id']
        for message in ('primer mensaje', 'segundo mensaje', 'tercer mensaje'):
            assert client.post(f'/v1/sessions/{sid}/chat', json={'message': message}).status_code == 200
    provider = FakeProvider()
    with client_for(environment, provider) as client:
        assert client.get('/v1/sessions').json()[0]['session_id'] == sid
        messages = client.get(f'/v1/sessions/{sid}/messages').json()['messages']
        assert len(messages) == 6
        assert messages[0]['content'] == 'primer mensaje'
        client.post(f'/v1/sessions/{sid}/chat', json={'message': 'continúa'})
        assert len([m for m in provider.chat_calls[-1].messages if m.role != 'system']) <= 2
        assert client.delete(f'/v1/sessions/{sid}').status_code == 200
    with client_for(environment) as client:
        assert client.get('/v1/sessions').json() == []


def test_failed_send_can_be_retried_without_duplicates(environment):
    with client_for(environment, BoomProvider()) as client:
        sid = client.post('/v1/sessions').json()['session_id']
        assert client.post(f'/v1/sessions/{sid}/chat', json={'message': 'retry'}).status_code == 502
    with client_for(environment) as client:
        assert client.post(f'/v1/sessions/{sid}/chat', json={'message': 'retry'}).status_code == 200
        assert len(client.get(f'/v1/sessions/{sid}/messages').json()['messages']) == 2


def test_local_upload_reaches_provider_and_survives_restart(environment):
    provider = FakeProvider()
    with client_for(environment, provider) as client:
        upload = client.post('/v1/desktop/library', json={'name': 'nota.txt', 'data': base64.b64encode(b'private attachment').decode()})
        assert upload.status_code == 201, upload.text
        key = upload.json()['id']
        sid = client.post('/v1/sessions').json()['session_id']
        reply = client.post(f'/v1/sessions/{sid}/chat', json={'message': 'resume', 'attachments': [key]})
        assert reply.status_code == 200, reply.text
        assert 'private attachment' in provider.chat_calls[-1].messages[-1].content
        assert client.get('/v1/sessions').json()[0]['local_only'] == 1
    with client_for(environment) as client:
        assert client.get('/v1/desktop/library').json()[0]['id'] == key
        message = client.get(f'/v1/sessions/{sid}/messages').json()['messages'][0]
        assert message['content'] == 'resume'
        assert message['attachments'][0]['name'] == 'nota.txt'


def test_images_are_explicit_multimodal_and_require_vision(environment):
    from PIL import Image
    output = io.BytesIO()
    Image.new('RGB', (30, 20), 'blue').save(output, 'PNG')
    provider = FakeProvider()
    with client_for(environment, provider) as client:
        key = client.post('/v1/desktop/library', json={'name': 'image.png', 'data': base64.b64encode(output.getvalue()).decode()}).json()['id']
        sid = client.post('/v1/sessions').json()['session_id']
        payload = {'message': 'qué ves', 'attachments': [key]}
        assert client.post(f'/v1/sessions/{sid}/chat', json=payload).status_code == 400
        assert not provider.chat_calls
        provider.supports_images = lambda model: True
        assert client.post(f'/v1/sessions/{sid}/chat', json=payload).status_code == 200
        wire = provider.chat_calls[-1].messages[-1].to_dict()
        assert wire['content'][1]['type'] == 'image_url'
        assert wire['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,')


def test_preferences_apply_without_restart_and_retain_unknown_fields(environment):
    settings, path = environment
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    data['user_extension'] = {'keep': True}
    path.write_text(yaml.safe_dump(data), encoding='utf-8')
    with client_for(environment) as client:
        response = client.patch('/v1/desktop/preferences', json={'mode': 'private', 'onboarding_complete': True,
            'categories': {'categories': {'reading': 'deny', 'system': 'deny', 'remote': 'deny'}}})
        assert response.status_code == 200, response.text
        assert response.json()['onboarding_complete']
        assert settings.permissions.categories['reading'] == 'deny'
        assert yaml.safe_load(path.read_text(encoding='utf-8'))['user_extension'] == {'keep': True}


def test_token_host_and_origin_guard(environment):
    with client_for(environment) as client:
        assert client.get('/v1/desktop/status', headers={'Authorization': ''}).status_code == 401
        assert client.post('/v1/desktop/prepare', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403
        assert client.get('/v1/desktop/status', headers={'Host': 'evil.example'}).status_code == 403
        names = {tool['name'] for tool in client.get('/v1/tools').json()}
        assert 'list_dir' not in names
        assert 'list_files' in names


def test_pause_rejects_new_chat(environment):
    with client_for(environment) as client:
        sid = client.post('/v1/sessions').json()['session_id']
        assert client.post('/v1/desktop/pause', json={'paused': True}).status_code == 200
        assert client.post(f'/v1/sessions/{sid}/chat', json={'message': 'hola'}).status_code == 409
        client.post('/v1/desktop/pause', json={'paused': False})
        assert client.post(f'/v1/sessions/{sid}/chat', json={'message': 'hola'}).status_code == 200


def test_approval_is_single_use_and_destructive_actions_cannot_be_remembered():
    broker = ApprovalBroker(timeout=2)
    result = []
    thread = threading.Thread(target=lambda: result.append(broker.confirm('session', 'write_file', {'path': 'a', 'content': 'b'})))
    thread.start()
    deadline = time.monotonic() + 1
    while not broker.pending() and time.monotonic() < deadline:
        time.sleep(.01)
    uid = broker.pending()[0]['id']
    assert not broker.resolve(uid, 'always')
    assert broker.resolve(uid, 'once')
    assert not broker.resolve(uid, 'once')
    thread.join(2)
    assert result == [True]


def test_category_denial_beats_tool_allow_and_sensitive_category_still_asks():
    settings = PermissionSettings(allow=['write_file'], categories={'writing': 'deny'})
    permissions = PermissionSystem(settings)
    assert permissions.authorize('write_file').decision is PermissionDecision.DENY
    settings.categories['writing'] = 'allow'
    assert permissions.authorize('write_file').decision is PermissionDecision.ASK


def test_unknown_or_exhausted_hardware_never_selects_a_chat_model():
    profile = MachineProfile(0, 0, 0, False, '?', '?')
    with pytest.raises(ValueError):
        recommended_chat(profile)
    with pytest.raises(ValueError):
        recommended_chat(replace(profile, ram_total_gb=32, ram_available_gb=1))


def test_medium_machine_prefers_light_vision_model_when_big_one_doesnt_fit():
    """11B needs 16 GB total; moondream fits comfortably."""
    profile = MachineProfile(16, 8, 0, False, 'Windows', '3', cpu_model='x86_64', ram_available_gb=8)
    spec = recommended_vision(profile)
    assert spec is not None
    assert spec.role == 'vision'
    assert spec.name == 'moondream'


def test_weak_machine_never_recommends_vision():
    """Even the lightest vision model needs 4 GB total RAM."""
    assert recommended_vision(MachineProfile(2, 1, 0, False, 'Windows', '3')) is None


def test_poor_machine_still_gets_light_vision_when_possible():
    """16 GB total with 6 GB free: moondream fits via POSSIBLE fallback."""
    profile = MachineProfile(16, 8, 0, False, 'Windows', '3', cpu_model='x86_64', ram_available_gb=6)
    spec = recommended_vision(profile)
    assert spec is not None
    assert spec.name == 'moondream'


def test_attachment_paths_and_invalid_documents_are_rejected(tmp_path):
    store = AttachmentStore(tmp_path / 'attachments')
    with pytest.raises(ValueError):
        store.get('../config')
    with pytest.raises(ValueError):
        store.add('app.exe', base64.b64encode(b'MZ executable').decode())
    with pytest.raises(ValueError):
        store.add('test.txt', 'not base64')


@pytest.mark.parametrize('filename', ['../escape.exe', '/absolute.exe', 'C:/bad.exe'])
def test_bundle_rejects_path_escape(environment, tmp_path, filename):
    archive = tmp_path / 'package.zip'
    with zipfile.ZipFile(archive, 'w') as package:
        package.writestr(filename, b'bad')
        package.writestr('NOVA.exe', b'fake executable')
    with pytest.raises(ValueError):
        install_bundle(archive, {'sha256': sha256(archive), 'version': 'test'}, tmp_path / 'install')
    assert not (tmp_path / 'escape.exe').exists()


def test_bundle_backslash_filename_cannot_escape(environment, tmp_path):
    # On Windows the stdlib normalizes a literal backslash to '/' when writing
    # zip entries (dir\bad.exe -> dir/bad.exe), which is a safe relative path.
    # Either the guard rejects the archive outright, or the payload must land
    # safely inside the staging directory - never in the parent.
    archive = tmp_path / 'package.zip'
    with zipfile.ZipFile(archive, 'w') as package:
        package.writestr('dir\\bad.exe', b'bad')
        package.writestr('NOVA.exe', b'fake executable')
    target = tmp_path / 'install'
    try:
        install_bundle(archive, {'sha256': sha256(archive), 'version': 'test'}, target)
    except ValueError:
        return
    assert (target / 'dir' / 'bad.exe').exists()
    assert not (tmp_path / 'escape.exe').exists()
    assert not (tmp_path / 'bad.exe').exists()


def test_bundle_does_not_adopt_existing_data(environment, tmp_path):
    archive = tmp_path / 'package.zip'
    with zipfile.ZipFile(archive, 'w') as package:
        package.writestr('NOVA.exe', b'fake executable')
    target = tmp_path / 'install'
    target.mkdir()
    (target / 'user.txt').write_text('keep')
    with pytest.raises(ValueError):
        install_bundle(archive, {'sha256': sha256(archive), 'version': 'test'}, target)
    assert (target / 'user.txt').read_text() == 'keep'

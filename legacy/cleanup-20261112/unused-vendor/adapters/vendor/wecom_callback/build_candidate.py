"""Create a separate candidate; never edit demo10 or copy its business data."""
import hashlib
import json
from pathlib import Path
import shutil


def main():
    root = Path(__file__).resolve().parents[2]
    source = root/'candidates/service-lab-v29-demo10'
    target = root/'candidates/service-wecom-v1'
    target.mkdir(exist_ok=False)
    for name in ('backend', 'agent'):
        shutil.copytree(source/name, target/name, ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('config', 'vendor'):
        (target/name).symlink_to((source/name).resolve(), target_is_directory=True)
    module = target/'backend/src/digital_tutor'
    def replace(name, old, new):
        path = module/name
        text = path.read_text()
        if text.count(old) != 1:
            raise ValueError('Unexpected source structure: '+name)
        path.write_text(text.replace(old,new))
    replace('mock_channel.py', 'def channel_for(app):\n', '''def channel_for(app):
    import os
    if os.environ.get('DIGITAL_TUTOR_WECOM_CONFIG'):
        if not hasattr(app,'channel'):
            from .wecom_transport import WecomChannel
            path=str(Path(app.store.path).with_suffix('.channel.sqlite'))
            app.channel=WecomChannel(path)
        return app.channel
''')
    replace('application.py', "        require(mock is True, 'REAL_CHANNEL_DISABLED', '第一阶段只允许模拟渠道家庭')", """        if mock is not True:
            from .wecom_transport import bootstrap_allowed
            require(bootstrap_allowed(recipient), 'REAL_CHANNEL_DISABLED', '真实会话未授权')""")
    replace('application.py', "(family_id, uid, recipient, 1, 1)", "(family_id, uid, recipient, 1, int(mock))")
    replace('application.py', "'interpreted': False, 'intents': [], 'source_kind': 'mock_wechat'", "'interpreted': False, 'intents': [], 'source_kind': 'wecom_live' if os.environ.get('DIGITAL_TUTOR_WECOM_CONFIG') else 'mock_wechat'")
    path=module/'wechat.py';text=path.read_text();text='import os\n'+text
    text=text.replace("        require(binding['mock']==1,'REAL_CHANNEL_DISABLED','未接入真实微信发送')", """        if binding['mock'] != 1:
            from .wecom_transport import bootstrap_allowed
            require(bootstrap_allowed(binding['recipient']), 'REAL_CHANNEL_DISABLED', '真实会话未授权')""")
    text=text.replace("'source_kind':'mock_wechat'", "'source_kind':'wecom_live' if os.environ.get('DIGITAL_TUTOR_WECOM_CONFIG') else 'mock_wechat'")
    text=text.replace("'channel':'mock_wechat','simulated':True", "'channel':'wecom_live' if os.environ.get('DIGITAL_TUTOR_WECOM_CONFIG') else 'mock_wechat','simulated':not bool(os.environ.get('DIGITAL_TUTOR_WECOM_CONFIG'))")
    text=text.replace("'channel':'mock_wechat'", "'channel':'wecom_live' if os.environ.get('DIGITAL_TUTOR_WECOM_CONFIG') else 'mock_wechat'")
    path.write_text(text)
    path=module/'context.py';text=path.read_text();text='import os\n'+text
    text=text.replace("'channel':'mock'", "'channel':'wecom_live' if os.environ.get('DIGITAL_TUTOR_WECOM_CONFIG') else 'mock'")
    path.write_text(text)
    path=module/'outbox.py';text=path.read_text();text='import os\n'+text
    text=text.replace("'source':'mock_transport'", "'source':'wecom_api' if os.environ.get('DIGITAL_TUTOR_WECOM_CONFIG') else 'mock_transport'")
    path.write_text(text)
    shutil.copy2(Path(__file__).with_name('wecom_transport.py'),module/'wecom_transport.py')
    manifest={str(p.relative_to(target)):hashlib.sha256(p.read_bytes()).hexdigest()
              for name in ('backend','agent') for p in (target/name).rglob('*') if p.is_file()}
    (target/'source-manifest.json').write_text(json.dumps(manifest,indent=2))
    (target/'provenance.json').write_text(json.dumps({'base':str(source),'mode':'passive',
        'base_manifest_sha256':hashlib.sha256((source/'source-manifest.json').read_bytes()).hexdigest(),
        'description':'Independent real WeCom transport. Original Service prompt and native loop retained. No copied conversations or learning fixtures.'},indent=2))
    print(target)


if __name__ == '__main__':
    main()

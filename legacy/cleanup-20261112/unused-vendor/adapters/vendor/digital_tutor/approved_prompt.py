"""User-approved full Service SYSTEM; other roles retain their own prompts."""
import hashlib,json
from pathlib import Path
from .domain import require

def load(project,role):
    if role!='service':return None
    root=Path(project);config_path=root/'agent/config/prompt-profile.json'
    if not config_path.exists():return None
    config=json.loads(config_path.read_text(encoding='utf-8'))
    name=config.get('service_system_file')
    if not name:return None
    path=(root/'agent/prompts'/name).resolve()
    require(path.is_relative_to((root/'agent/prompts').resolve()),'PROMPT_PROFILE_INVALID','完整提示词路径越界')
    raw=path.read_bytes()
    require(hashlib.sha256(raw).hexdigest()==config.get('service_system_sha256'),'PROMPT_PROFILE_CHANGED','已确认提示词校验失败')
    return raw.decode('utf-8')

from pathlib import Path

from api.config import Settings
from api.errors import APIError


def workspace_path(value: str, settings: Settings) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_relative_to(settings.workspace_root):
        raise APIError(422, "WORKSPACE_OUTSIDE_ALLOWED_ROOT", "워크스페이스가 설정한 허용 루트 밖에 있습니다")
    if not path.is_dir():
        raise APIError(422, "WORKSPACE_NOT_FOUND", "워크스페이스 디렉터리를 찾을 수 없습니다")
    return path

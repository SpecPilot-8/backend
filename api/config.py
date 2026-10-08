import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    workspace_root: Path
    database_url: str
    max_upload_bytes: int = 20 * 1024 * 1024
    max_source_bytes: int = 30 * 1024 * 1024
    max_source_files: int = 3000
    max_source_file_bytes: int = 1024 * 1024
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")

    @classmethod
    def from_env(cls) -> "Settings":
        repo = Path(__file__).resolve().parent.parent
        data = Path(os.getenv("SPECPILOT_DATA_DIR", str(repo / ".data"))).resolve()
        return cls(
            data_dir=data,
            workspace_root=Path(os.getenv("SPECPILOT_WORKSPACE_ROOT", str(repo.parent))).resolve(),
            database_url=os.getenv("SPECPILOT_DATABASE_URL", f"sqlite:///{data / 'api.sqlite3'}"),
            cors_origins=tuple(x.strip() for x in os.getenv(
                "SPECPILOT_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
            ).split(",") if x.strip()),
        )

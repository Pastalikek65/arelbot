from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass
class MediaJob:
    job_id: str
    url: str
    chat_id: int
    user_id: int
    status_message_id: int
    status: str = "queued"
    created_at: float = 0
    source: str = "unknown"
    error_code: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "MediaJob":
        return cls(**value)

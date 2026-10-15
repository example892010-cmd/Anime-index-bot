from dataclasses import dataclass
from app.database import Database
@dataclass
class AppContext:
    db: Database
    owner_id: int

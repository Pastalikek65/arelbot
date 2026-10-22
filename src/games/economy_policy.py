"""Free game currency policy and cosmetic-only catalog, independent of Telegram."""
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from zoneinfo import ZoneInfo

ECONOMY_ZONE = ZoneInfo('Europe/Istanbul')
DAILY_REWARDS = (25, 30, 35, 40, 45, 50)
GAME_DAILY_LIMIT = 150
CONFIRM_SECONDS = 300


@dataclass(frozen=True)
class Cosmetic:
    key: str
    name: str
    price: int
    slot: str
    description: str
    effect: str


CATALOG = MappingProxyType({item.key: item for item in (
    Cosmetic('merakli', 'Meraklı', 75, 'title', 'Yeni oyunları keşfetmeyi sevenlere.', 'Grup oyun profilinde unvan olarak görünür.'),
    Cosmetic('strateji', 'Stratejist', 250, 'title', 'Her hamlesini düşünerek yapanlara.', 'Grup oyun profilinde unvan olarak görünür.'),
    Cosmetic('kelime', 'Kelime Ustası', 350, 'title', 'Harflerin arasında yolunu bulanlara.', 'Grup oyun profilinde unvan olarak görünür.'),
    Cosmetic('kartusta', 'Kart Ustası', 400, 'title', 'Kart masasını sevenlere.', 'Grup oyun profilinde unvan olarak görünür.'),
    Cosmetic('usta', 'Oyun Ustası', 750, 'title', 'Oyun salonunun müdavimlerine.', 'Grup oyun profilinde unvan olarak görünür.'),
    Cosmetic('efsane', 'Efsane', 1500, 'title', 'Uzun vadeli koleksiyon hedefi.', 'Grup oyun profilinde unvan olarak görünür.'),
    Cosmetic('pusula', '🧭 Pusula', 80, 'badge', 'Keşfetmeyi sevenlerin rozeti.', 'Grup oyun profilinde seçili rozet olarak görünür.'),
    Cosmetic('ates', '🔥 Ateş', 150, 'badge', 'Oyun heyecanını taşıyanlara.', 'Grup oyun profilinde seçili rozet olarak görünür.'),
    Cosmetic('yildiz', '⭐ Yıldız', 300, 'badge', 'Koleksiyonuna bir yıldız ekle.', 'Grup oyun profilinde seçili rozet olarak görünür.'),
    Cosmetic('tac', '👑 Taç', 650, 'badge', 'Profilinin koleksiyon parçası.', 'Grup oyun profilinde seçili rozet olarak görünür.'),
)})


def catalog_rows():
    return [asdict(item) for item in CATALOG.values()]


def moment(value: datetime | None = None) -> datetime:
    now = value if value is not None else datetime.now(UTC)
    return now.replace(tzinfo=UTC) if now.tzinfo is None else now


def local_day(value: datetime) -> str:
    return moment(value).astimezone(ECONOMY_ZONE).date().isoformat()


def next_midnight(value: datetime) -> datetime:
    local = moment(value).astimezone(ECONOMY_ZONE)
    return datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tzinfo=ECONOMY_ZONE)


def game_reward(reason: str) -> bool:
    # Stakes, cancellations and daily claims are separate ledger operations.
    # Existing game services use explicit *_reward reasons; settlement/refund
    # returns must never be reduced by the earned-reward budget.
    return str(reason).endswith('_reward') and reason != 'daily_reward'

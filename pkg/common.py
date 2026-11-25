"""Shared configuration, storage, OAuth, logging, and period utilities."""

import calendar
import csv
import json
import logging
import os
import tempfile
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Project root directory (parent of pkg/)
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# If modifying these scopes, delete token.json and re-authenticate.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

# Canonical columns persisted in transactions.csv
CSV_FIELDNAMES: List[str] = [
    "date",
    "amount",
    "merchant",
    "category",
    "cumulative_amount",
    "card",
    "msg_id",
]

# Columns uploaded to Google Sheets: (csv_key, display_header)
SHEETS_COLUMNS: List[Tuple[str, str]] = [
    ("date", "Date"),
    ("amount", "Amount"),
    ("merchant", "Merchant"),
    ("category", "Category"),
    ("cumulative_amount", "Cumulative Amount"),
    ("card", "Card"),
]

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
DEFAULT_DAILY_LIMIT = 500
DEFAULT_GEMINI_RPM = 15
DEFAULT_GEMINI_MAX_RETRIES = 3

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configures structured logging for CLI tools."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )
    # Reduce noisy third-party client logs unless debugging
    if not verbose:
        logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)


def resolve_path(path: str, root_dir: str = ROOT_DIR) -> str:
    """Resolves a relative path against root_dir; returns absolute paths unchanged."""
    if os.path.isabs(path):
        return path
    return os.path.join(root_dir, path)


def load_config(config_path: Optional[str] = None, root_dir: str = ROOT_DIR) -> Dict[str, Any]:
    """Loads spend_tracker.json from config_path or default location."""
    path = config_path or os.path.join(root_dir, "config", "spend_tracker.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_config_path(
    config: Dict[str, Any],
    key: str,
    default: str,
    root_dir: str = ROOT_DIR,
) -> str:
    """Returns an absolute path for a configured path key."""
    rel = config.get("paths", {}).get(key, default)
    return resolve_path(rel, root_dir=root_dir)


def load_json_file(path: str, default: Any = None) -> Any:
    """Loads a JSON file, returning default if missing or invalid."""
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load JSON from %s: %s", path, exc)
        return default


def atomic_write_text(path: str, content: str) -> None:
    """Atomically writes text to path via a temporary file and os.replace."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".tmp_", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def atomic_write_json(path: str, data: Any, indent: int = 2) -> None:
    """Atomically writes a JSON object to path."""
    serialized = json.dumps(data, indent=indent) + "\n"
    atomic_write_text(path, serialized)


def atomic_write_csv(
    path: str,
    fieldnames: Sequence[str],
    rows: Iterable[Dict[str, Any]],
) -> None:
    """Atomically writes CSV rows to path."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".tmp_", suffix=".csv")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as tmp_file:
            writer = csv.DictWriter(
                tmp_file,
                fieldnames=fieldnames,
                extrasaction="ignore",
            )
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in fieldnames})
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def get_credentials(
    config: Optional[Dict[str, Any]] = None,
    root_dir: str = ROOT_DIR,
    port: int = 8080,
    open_browser: bool = True,
) -> Credentials:
    """Loads, refreshes, or initiates OAuth credentials and persists token.json atomically."""
    cfg = config if config is not None else load_config(root_dir=root_dir)
    token_path = get_config_path(cfg, "token", "token.json", root_dir=root_dir)
    creds_path = get_config_path(cfg, "credentials", "config/credentials.json", root_dir=root_dir)
    if not os.path.exists(creds_path):
        fallback_creds = resolve_path("credentials.json", root_dir=root_dir)
        if os.path.exists(fallback_creds):
            creds_path = fallback_creds

    creds: Optional[Credentials] = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as exc:
                logger.warning("OAuth token refresh failed (%s); re-authenticating.", exc)
                creds = None
        if not creds:
            if not os.path.exists(creds_path):
                raise FileNotFoundError(
                    f"credentials.json not found at {creds_path} or {resolve_path('credentials.json', root_dir)}"
                )
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=port, open_browser=open_browser)
        atomic_write_text(token_path, creds.to_json())

    return creds


def _safe_date(year: int, month: int, day: int) -> date:
    """Constructs a date, clamping day to the month's last valid day (handles Feb 29)."""
    month = min(max(1, int(month)), 12)
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(max(1, int(day)), last_day))


def get_biannual_period(date_obj: datetime) -> int:
    """Returns the biannual period (1 for Jan-Jun, 2 for Jul-Dec) for a given date."""
    return 1 if 1 <= date_obj.month <= 6 else 2


def get_benefit_period_bounds(
    reset_cycle: str,
    date_obj: datetime,
    benefit_details: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[date], Optional[date], str]:
    """
    Returns (start_date, end_date, period_key) for the active reset period containing date_obj.
    Supports 'monthly', 'biannual_jan_jun', 'annual', and 'anniversary'.
    """
    d = date_obj.date() if isinstance(date_obj, datetime) else date_obj
    if reset_cycle == "monthly":
        last_day = calendar.monthrange(d.year, d.month)[1]
        return date(d.year, d.month, 1), date(d.year, d.month, last_day), d.strftime("%Y-%m")
    if reset_cycle == "biannual_jan_jun":
        if 1 <= d.month <= 6:
            return date(d.year, 1, 1), date(d.year, 6, 30), f"{d.year}-P1"
        return date(d.year, 7, 1), date(d.year, 12, 31), f"{d.year}-P2"
    if reset_cycle == "annual":
        return date(d.year, 1, 1), date(d.year, 12, 31), d.strftime("%Y")
    if reset_cycle == "anniversary":
        details = benefit_details or {}
        anniv_month = int(details.get("anniversary_month", 1))
        anniv_day = int(details.get("anniversary_day", 1))
        this_year_anniv = _safe_date(d.year, anniv_month, anniv_day)
        if d >= this_year_anniv:
            start = this_year_anniv
            next_anniv = _safe_date(d.year + 1, anniv_month, anniv_day)
        else:
            start = _safe_date(d.year - 1, anniv_month, anniv_day)
            next_anniv = this_year_anniv
        end = next_anniv - timedelta(days=1)
        return start, end, f"anniv-{start.strftime('%Y-%m-%d')}"
    return None, None, "all"


def get_period_key(
    reset_cycle: str,
    date_obj: datetime,
    benefit_details: Optional[Dict[str, Any]] = None,
) -> str:
    """Generates a unique period key for a given reset cycle and date."""
    _, _, period_key = get_benefit_period_bounds(reset_cycle, date_obj, benefit_details=benefit_details)
    return period_key


def get_days_remaining(
    reset_cycle: str,
    now: datetime,
    benefit_details: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Returns the number of days remaining until the current benefit period resets."""
    _, end, _ = get_benefit_period_bounds(reset_cycle, now, benefit_details=benefit_details)
    if end is None:
        return None
    today = now.date() if isinstance(now, datetime) else now
    return max(0, (end - today).days)


def is_in_current_period(
    reset_cycle: str,
    tx_date: datetime,
    now: datetime,
    benefit_details: Optional[Dict[str, Any]] = None,
) -> bool:
    """Returns True if tx_date falls within the same reset period as now."""
    start, end, _ = get_benefit_period_bounds(reset_cycle, now, benefit_details=benefit_details)
    if start is None or end is None:
        return True
    tx_d = tx_date.date() if isinstance(tx_date, datetime) else tx_date
    return start <= tx_d <= end


def card_matches_benefit(
    tx_card: str,
    benefit_details: Dict[str, Any],
    allow_empty_card: bool = True,
) -> bool:
    """
    Returns True if a transaction's card matches the benefit's card_keywords.
    When allow_empty_card=True (default for keyword-based matching), legacy CSV rows
    with no card recorded still match. When allow_empty_card=False (used for broad
    category-based matching), a matching card must be present.
    """
    card_keywords = benefit_details.get("card_keywords")
    if not card_keywords:
        return True
    if not tx_card:
        return allow_empty_card
    tx_card_lower = tx_card.lower()
    return any(kw.lower() in tx_card_lower for kw in card_keywords)


def transaction_matches_benefit(transaction: Dict[str, Any], benefit_details: Dict[str, Any]) -> bool:
    """
    Returns True if a transaction qualifies for a credit card benefit via:
      1. Merchant keyword match (with card_keywords check, allowing legacy empty card), OR
      2. Category match (requiring card_keywords match when card_keywords are configured).
    """
    tx_card = (transaction.get("card") or "").strip()
    merchant_lower = (transaction.get("merchant") or "").lower()
    category_lower = (transaction.get("category") or "").strip().lower()

    keywords = benefit_details.get("keywords") or []
    if keywords and card_matches_benefit(tx_card, benefit_details, allow_empty_card=True):
        if any(kw.lower() in merchant_lower for kw in keywords):
            return True

    categories = benefit_details.get("categories") or []
    if categories and category_lower and card_matches_benefit(tx_card, benefit_details, allow_empty_card=False):
        if any(cat.lower() == category_lower for cat in categories):
            return True

    return False

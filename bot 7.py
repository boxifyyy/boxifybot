"""
Toxic Casino - Telegram Bot
Games: Dice, Bowling, Soccer, Darts
Players bet, then alternate emoji turns for rounds.
Highest score each round gets a point. First to 3 points wins.

Integrated with OxaPay for real crypto deposits, withdrawals, and house balance.
"""

import os
import json
import logging
import asyncio
import time
import random
from datetime import datetime, time as dt_time, timezone, timedelta
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")

OXAPAY_MERCHANT_API_KEY = os.getenv("OXAPAY_MERCHANT_API_KEY", "")
OXAPAY_PAYOUT_API_KEY = os.getenv("OXAPAY_PAYOUT_API_KEY", "")
OXAPAY_GENERAL_API_KEY = os.getenv("OXAPAY_GENERAL_API_KEY", "")

OXAPAY_BASE_URL = "https://api.oxapay.com/v1"

# Admin user IDs that can view house balance (add your Telegram user ID here)
ADMIN_IDS: set[int] = set()

# File for persistent balance storage
BALANCES_FILE = "balances.json"

# Default balance for new users (0 = real-money mode, must deposit first)
DEFAULT_BALANCE = 0.0

# Minimum deposit / withdraw amounts (in USD)
MIN_DEPOSIT = 1.0
MIN_WITHDRAW = 5.0

# Bet limits (in USD)
MIN_BET = 0.25
MAX_BET = 25.0

# Owner user ID (Blake)
OWNER_ID = 7074468601

# Slots Mini App URL (Toxic Gamble WebApp)
SLOTS_WEBAPP_URL = os.getenv(
    "SLOTS_WEBAPP_URL",
    "https://real-slot-games-app-eak9l6hl.devinapps.com",
)

# Private Telegram group/chat ID for logging all deposits & withdrawals.
# Set this to the chat ID of your private logging group.
# To find the chat ID, add the bot to the group and use a /chatid command or check logs.
PRIVATE_LOG_GROUP_ID = int(os.getenv("PRIVATE_LOG_GROUP_ID", "0"))

# How often to poll for payment confirmation (seconds)
PAYMENT_POLL_INTERVAL = 15
PAYMENT_POLL_TIMEOUT = 3600  # 1 hour max

# ---------------------------------------------------------------------------
# Game Constants
# ---------------------------------------------------------------------------

class GameType(Enum):
    DICE = "dice"
    BOWLING = "bowling"
    SOCCER = "soccer"
    DARTS = "darts"
    BASKETBALL = "basketball"


GAME_EMOJI = {
    GameType.DICE: "\U0001f3b2",
    GameType.BOWLING: "\U0001f3b3",
    GameType.SOCCER: "\u26bd",
    GameType.DARTS: "\U0001f3af",
    GameType.BASKETBALL: "\U0001f3c0",
}

GAME_COMMANDS = {
    "dice": GameType.DICE,
    "bowl": GameType.BOWLING,
    "football": GameType.SOCCER,
    "darts": GameType.DARTS,
    "ball": GameType.BASKETBALL,
}

GAME_TYPE_TO_CMD = {v: k for k, v in GAME_COMMANDS.items()}

POINTS_TO_WIN = 3
MAX_ROUNDS = 5

# Emoji game win multiplier (dice, bowling, soccer, darts)
EMOJI_GAME_WIN_MULTI = 1.92

# Dice Roulette multipliers
DR_LOW_HIGH_MULTI = 1.92
DR_ODD_EVEN_MULTI = 1.92
DR_EXACT_MULTI = 5.6

# Dice Roulette multi-number multipliers (by count of numbers selected)
DR_MULTI_NUMBER = {
    1: 5.60,
    2: 2.45,
    3: 1.92,
    4: 1.54,
    5: 1.38,
}

# Coinflip multiplier
COINFLIP_MULTI = 1.92

# Mines multipliers (per safe tile revealed, indexed by mine count 1-24)
# Higher mine count = higher multiplier per reveal
MINES_MULTIPLIERS = {
    1: 1.04, 2: 1.08, 3: 1.14, 4: 1.20, 5: 1.28,
    6: 1.38, 7: 1.50, 8: 1.65, 9: 1.85, 10: 2.10,
    11: 2.40, 12: 2.80, 13: 3.30, 14: 4.00, 15: 5.00,
    16: 6.50, 17: 8.50, 18: 12.00, 19: 18.00, 20: 28.00,
    21: 50.00, 22: 100.00, 23: 250.00, 24: 600.00,
}

# Tower game multipliers per row survived (8 rows, 1/3 chance each)
TOWER_MULTIPLIERS = [1.40, 1.96, 2.74, 3.84, 5.38, 7.53, 10.54, 14.76]

# ---------------------------------------------------------------------------
# Inline Keyboard Builders
# ---------------------------------------------------------------------------

def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Build the main menu inline keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\U0001f4b3 Deposit", callback_data="menu_deposit"),
            InlineKeyboardButton("\U0001f4b8 Withdraw", callback_data="menu_withdraw"),
        ],
        [
            InlineKeyboardButton("\U0001f3ae Games", callback_data="menu_games"),
            InlineKeyboardButton("\U0001f4ca Stats", callback_data="menu_stats"),
        ],
        [
            InlineKeyboardButton("\U0001f4b0 Balance", callback_data="menu_balance"),
            InlineKeyboardButton("\U0001f3e6 House Balance", callback_data="menu_housebalance"),
        ],
    ])


def games_keyboard() -> InlineKeyboardMarkup:
    """Build the games selection inline keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\U0001f3b2 Dice", callback_data="game_dice"),
            InlineKeyboardButton("\U0001f3b3 Bowling", callback_data="game_bowl"),
        ],
        [
            InlineKeyboardButton("\u26bd Soccer", callback_data="game_soccer"),
            InlineKeyboardButton("\U0001f3af Darts", callback_data="game_darts"),
        ],
        [
            InlineKeyboardButton("\U0001f3c0 Basketball", callback_data="game_basketball"),
            InlineKeyboardButton("\U0001f0cf Blackjack", callback_data="game_blackjack"),
        ],
        [
            InlineKeyboardButton("\U0001f4a3 Mines", callback_data="game_mines"),
            InlineKeyboardButton("\U0001f435 Monkey Tower", callback_data="game_tower"),
        ],
        [
            InlineKeyboardButton("\U0001fa99 Coinflip", callback_data="game_coinflip"),
            InlineKeyboardButton("\U0001f3b0 Slots", callback_data="game_slots"),
        ],
        [
            InlineKeyboardButton("\u00ab Back to Menu", callback_data="menu_main"),
        ],
    ])


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    """Single back button to return to main menu."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\u00ab Back to Menu", callback_data="menu_main")],
    ])


def game_end_keyboard(
    game_key: str, bet: float, rolls: int = 1, rounds: int = 3,
) -> InlineKeyboardMarkup:
    """Build Play Again / Double & Play / Menu keyboard for game end.

    game_key: 'bj' for blackjack, or dice game command ('dice', 'bowl', 'soccer', 'darts')
    rolls: rolls per round (only used for emoji games, ignored for blackjack)
    rounds: points to win / rounds (only used for emoji games, ignored for blackjack)
    """
    bet_str = f"{bet:.2f}"
    if game_key == "bj":
        ra_data = f"ra_{game_key}_{bet_str}"
        rd_data = f"rd_{game_key}_{bet_str}"
    else:
        ra_data = f"ra_{game_key}_{bet_str}_{rolls}_{rounds}"
        rd_data = f"rd_{game_key}_{bet_str}_{rolls}_{rounds}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "(Play Again)",
                callback_data=ra_data,
            ),
            InlineKeyboardButton(
                "\u23eb Double",
                callback_data=rd_data,
            ),
        ],
        [
            InlineKeyboardButton("\u00ab Back to Menu", callback_data="menu_main"),
        ],
    ])


def rounds_selection_keyboard(game_key: str, bet: float) -> InlineKeyboardMarkup:
    """Build round-count selection keyboard (1, 2, or 3 rounds / points to win)."""
    bet_str = f"{bet:.2f}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "1\ufe0f\u20e3 1 Round",
                callback_data=f"rounds_{game_key}_{bet_str}_1",
            ),
            InlineKeyboardButton(
                "2\ufe0f\u20e3 2 Rounds",
                callback_data=f"rounds_{game_key}_{bet_str}_2",
            ),
            InlineKeyboardButton(
                "3\ufe0f\u20e3 3 Rounds",
                callback_data=f"rounds_{game_key}_{bet_str}_3",
            ),
        ],
        [
            InlineKeyboardButton("\u00ab Back to Menu", callback_data="menu_main"),
        ],
    ])


def dr_prediction_keyboard(bet: float, selected: set[int] | None = None) -> InlineKeyboardMarkup:
    """Build the dice roulette prediction keyboard with toggleable numbers."""
    if selected is None:
        selected = set()
    bet_str = f"{bet:.2f}"

    # Low / High / Odd / Even — instant play (no toggle)
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                "\u2b07\ufe0f Low (1-3)",
                callback_data=f"dr_low_{bet_str}",
            ),
            InlineKeyboardButton(
                "\u2b06\ufe0f High (4-6)",
                callback_data=f"dr_high_{bet_str}",
            ),
        ],
        [
            InlineKeyboardButton(
                "\U0001f534 Odd (1,3,5)",
                callback_data=f"dr_odd_{bet_str}",
            ),
            InlineKeyboardButton(
                "\U0001f535 Even (2,4,6)",
                callback_data=f"dr_even_{bet_str}",
            ),
        ],
    ]

    # Number buttons — toggleable (show check mark when selected)
    num_emojis = {1: "1\ufe0f\u20e3", 2: "2\ufe0f\u20e3", 3: "3\ufe0f\u20e3",
                  4: "4\ufe0f\u20e3", 5: "5\ufe0f\u20e3", 6: "6\ufe0f\u20e3"}
    row1 = []
    for n in (1, 2, 3):
        label = f"[{num_emojis[n]}]" if n in selected else num_emojis[n]
        row1.append(InlineKeyboardButton(label, callback_data=f"drt_{n}_{bet_str}"))
    row2 = []
    for n in (4, 5, 6):
        label = f"[{num_emojis[n]}]" if n in selected else num_emojis[n]
        row2.append(InlineKeyboardButton(label, callback_data=f"drt_{n}_{bet_str}"))
    rows.append(row1)
    rows.append(row2)

    # Confirm button — only show when at least one number is selected (max 5)
    if selected:
        count = len(selected)
        multi = DR_MULTI_NUMBER.get(count, 0)
        nums_str = ",".join(str(n) for n in sorted(selected))
        rows.append([
            InlineKeyboardButton(
                f"Confirm [{nums_str}] — {multi}x",
                callback_data=f"drc_{bet_str}",
            ),
        ])

    rows.append([
        InlineKeyboardButton("\u00ab Back to Menu", callback_data="menu_main"),
    ])
    return InlineKeyboardMarkup(rows)


def dr_end_keyboard(bet: float) -> InlineKeyboardMarkup:
    """Build play-again / double keyboard for dice roulette."""
    bet_str = f"{bet:.2f}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "(Play Again)",
                callback_data=f"dra_{bet_str}",
            ),
            InlineKeyboardButton(
                "\u23eb Double",
                callback_data=f"drd_{bet_str}",
            ),
        ],
        [
            InlineKeyboardButton("\u00ab Back to Menu", callback_data="menu_main"),
        ],
    ])


def mode_selection_keyboard(game_key: str, bet: float, rounds: int = 3) -> InlineKeyboardMarkup:
    """Build roll-mode selection keyboard (1 or 2 rolls per round)."""
    bet_str = f"{bet:.2f}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "\U0001f3b2",
                callback_data=f"mode_{game_key}_{bet_str}_{rounds}_1",
            ),
            InlineKeyboardButton(
                "\U0001f3b2\U0001f3b2",
                callback_data=f"mode_{game_key}_{bet_str}_{rounds}_2",
            ),
        ],
        [
            InlineKeyboardButton("\u00ab Back to Menu", callback_data="menu_main"),
        ],
    ])


def crazy_mode_keyboard(game_key: str, bet: float, rounds: int, rolls: int) -> InlineKeyboardMarkup:
    """Build Normal vs Crazy mode keyboard.

    Crazy mode: lowest score wins each round.
    """
    bet_str = f"{bet:.2f}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Normal (high wins)",
                callback_data=f"rules_{game_key}_{bet_str}_{rounds}_{rolls}_normal",
            ),
            InlineKeyboardButton(
                "Crazy (low wins)",
                callback_data=f"rules_{game_key}_{bet_str}_{rounds}_{rolls}_crazy",
            ),
        ],
        [
            InlineKeyboardButton("\u00ab Back to Menu", callback_data="menu_main"),
        ],
    ])


def opponent_selection_keyboard(
    game_key: str, bet: float, rounds: int, rolls: int, mode: str,
) -> InlineKeyboardMarkup:
    """Build opponent selection keyboard (vs Bot or vs Player)."""
    bet_str = f"{bet:.2f}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "\U0001f916 Play vs Bot",
                callback_data=f"opp_bot_{game_key}_{bet_str}_{rounds}_{rolls}_{mode}",
            ),
            InlineKeyboardButton(
                "\u2694\ufe0f Play vs Player",
                callback_data=f"opp_pvp_{game_key}_{bet_str}_{rounds}_{rolls}_{mode}",
            ),
        ],
        [
            InlineKeyboardButton("\u00ab Back to Menu", callback_data="menu_main"),
        ],
    ])


def pvp_join_keyboard(challenger_id: int) -> InlineKeyboardMarkup:
    """Build the PvP join / cancel buttons."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "\u2694\ufe0f Accept Challenge!",
                callback_data=f"pvpjoin_{challenger_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "\u274c Cancel",
                callback_data=f"pvpcancel_{challenger_id}",
            ),
        ],
    ])


# ---------------------------------------------------------------------------
# OxaPay API Client
# ---------------------------------------------------------------------------

class OxaPayClient:
    """Async client for OxaPay API v1."""

    def __init__(
        self,
        merchant_key: str,
        payout_key: str,
        general_key: str = "",
    ):
        self.merchant_key = merchant_key
        self.payout_key = payout_key
        self.general_key = general_key
        self.base_url = OXAPAY_BASE_URL

    async def _request(
        self,
        method: str,
        endpoint: str,
        headers: Dict[str, str],
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        async with httpx.AsyncClient() as client:
            if method.upper() == "GET":
                resp = await client.get(url, headers=headers)
            else:
                resp = await client.post(url, headers=headers, json=json_body or {})
            return resp.json()

    # ---- Merchant (Deposits) ------------------------------------------------

    async def create_invoice(
        self,
        amount: float,
        currency: Optional[str] = None,
        lifetime: int = 60,
        order_id: Optional[str] = None,
        callback_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        headers = {
            "merchant_api_key": self.merchant_key,
            "Content-Type": "application/json",
        }
        body: Dict[str, Any] = {
            "amount": amount,
            "lifetime": lifetime,
        }
        if currency:
            body["currency"] = currency
        if order_id:
            body["order_id"] = order_id
        if callback_url:
            body["callback_url"] = callback_url

        result = await self._request("POST", "/payment/invoice", headers, body)
        logger.info("create_invoice response: %s", result)
        return result

    async def get_payment_info(self, track_id: str) -> Dict[str, Any]:
        headers = {
            "merchant_api_key": self.merchant_key,
            "Content-Type": "application/json",
        }
        result = await self._request("GET", f"/payment/{track_id}", headers)
        logger.info("get_payment_info response for %s: %s", track_id, result)
        return result

    # ---- Payout (Withdrawals) -----------------------------------------------

    async def create_payout(
        self,
        address: str,
        currency: str,
        amount: float,
        network: Optional[str] = None,
        memo: Optional[str] = None,
        description: Optional[str] = None,
        callback_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        headers = {
            "payout_api_key": self.payout_key,
            "Content-Type": "application/json",
        }
        body: Dict[str, Any] = {
            "address": address,
            "currency": currency,
            "amount": amount,
        }
        if network:
            body["network"] = network
        if memo:
            body["memo"] = memo
        if description:
            body["description"] = description
        if callback_url:
            body["callback_url"] = callback_url

        result = await self._request("POST", "/payout", headers, body)
        logger.info("create_payout response: %s", result)
        return result

    async def get_payout_info(self, track_id: str) -> Dict[str, Any]:
        headers = {
            "payout_api_key": self.payout_key,
            "Content-Type": "application/json",
        }
        result = await self._request("GET", f"/payout/{track_id}", headers)
        logger.info("get_payout_info response for %s: %s", track_id, result)
        return result

    # ---- General (Account Balance) ------------------------------------------

    async def get_account_balance(self) -> Dict[str, Any]:
        if not self.general_key:
            return {"status": 0, "message": "General API key not configured", "data": {}}
        headers = {
            "general_api_key": self.general_key,
            "Content-Type": "application/json",
        }
        result = await self._request("GET", "/general/account/balance", headers)
        logger.info("get_account_balance response: %s", result)
        return result


# ---------------------------------------------------------------------------
# Persistent Balance Storage
# ---------------------------------------------------------------------------

class BalanceStore:
    """JSON-file-backed user balance storage (USD-equivalent)."""

    def __init__(self, filepath: str = BALANCES_FILE):
        self.filepath = filepath
        self._data: Dict[str, Any] = {"users": {}, "house_profit": 0.0}
        self._load()

    def _load(self) -> None:
        # --- ONE-TIME RESET: wipe everyone except Blake and house balance ---
        # Remove (or set to False) after the first run to stop resetting.
        _RESET_ON_STARTUP = True
        _OWNER_ID = "7074468601"  # Blake's Telegram user ID
        if _RESET_ON_STARTUP:
            if os.path.exists(self.filepath):
                try:
                    with open(self.filepath, "r") as f:
                        loaded = json.load(f)
                except (json.JSONDecodeError, IOError):
                    loaded = {}
            else:
                loaded = {}
            # Keep Blake's data and house balance, wipe everyone else
            blake_data = loaded.get("users", {}).get(_OWNER_ID)
            house_profit = loaded.get("house_profit", 0.0)
            new_users = {}
            if blake_data is not None:
                new_users[_OWNER_ID] = blake_data
            tcid = loaded.get("tournament_chat_id")
            self._data = {"users": new_users, "house_profit": 2139.12, "tournament_chat_id": tcid}
            self._save()
            logger.info(
                "ONE-TIME RESET: wiped all users except %s, kept house_profit=%.2f",
                _OWNER_ID, house_profit,
            )
            return
        # --- END ONE-TIME RESET ---

        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r") as f:
                    loaded = json.load(f)
                self._data["users"] = loaded.get("users", {})
                file_hp = loaded.get("house_profit", 0.0)
                self._data["house_profit"] = file_hp
                self._data["tournament_chat_id"] = loaded.get("tournament_chat_id")
            except (json.JSONDecodeError, IOError):
                logger.warning("Could not load %s, starting fresh", self.filepath)

    def _save(self) -> None:
        with open(self.filepath, "w") as f:
            json.dump(self._data, f, indent=2)

    def _ensure_user(self, user_id: int) -> str:
        key = str(user_id)
        if key not in self._data["users"]:
            initial = DEFAULT_BALANCE
            self._data["users"][key] = {
                "balance": initial,
                "deposits": [],
                "withdrawals": [],
                "total_wagered": 0.0,
                "username": "",
                "wager_requirement": 0.0,
                "wagered_since_requirement": 0.0,
                "weekly_wagered": 0.0,
                "weekly_last_claim": 0,
                "monthly_wagered": 0.0,
                "monthly_period_start": int(time.time()),
            }
            self._save()
        else:
            # Migrate existing users that don't have the new fields
            user_data = self._data["users"][key]
            changed = False
            if "total_wagered" not in user_data:
                user_data["total_wagered"] = 0.0
                changed = True
            if "username" not in user_data:
                user_data["username"] = ""
                changed = True
            if "wager_requirement" not in user_data:
                user_data["wager_requirement"] = 0.0
                changed = True
            if "wagered_since_requirement" not in user_data:
                user_data["wagered_since_requirement"] = 0.0
                changed = True
            if "weekly_wagered" not in user_data:
                user_data["weekly_wagered"] = 0.0
                user_data["weekly_last_claim"] = 0
                changed = True
            if "monthly_wagered" not in user_data:
                user_data["monthly_wagered"] = 0.0
                user_data["monthly_period_start"] = int(time.time())
                changed = True
            if changed:
                self._save()
        return key

    def get_balance(self, user_id: int) -> float:
        key = self._ensure_user(user_id)
        return self._data["users"][key]["balance"]

    def adjust_balance(self, user_id: int, amount: float) -> float:
        key = self._ensure_user(user_id)
        self._data["users"][key]["balance"] = round(
            self._data["users"][key]["balance"] + amount, 2
        )
        self._save()
        return self._data["users"][key]["balance"]

    def record_deposit(
        self, user_id: int, amount: float, track_id: str, currency: str = "USD"
    ) -> None:
        key = self._ensure_user(user_id)
        self._data["users"][key]["deposits"].append(
            {
                "amount": amount,
                "track_id": track_id,
                "currency": currency,
                "timestamp": int(time.time()),
            }
        )
        self._save()

    def record_withdrawal(
        self,
        user_id: int,
        amount: float,
        track_id: str,
        currency: str,
        address: str,
    ) -> None:
        key = self._ensure_user(user_id)
        self._data["users"][key]["withdrawals"].append(
            {
                "amount": amount,
                "track_id": track_id,
                "currency": currency,
                "address": address,
                "timestamp": int(time.time()),
            }
        )
        self._save()

    def add_house_profit(self, amount: float) -> None:
        self._data["house_profit"] = round(
            self._data.get("house_profit", 0.0) + amount, 2
        )
        self._save()

    def get_house_profit(self) -> float:
        return self._data.get("house_profit", 0.0)

    def set_house_profit(self, amount: float) -> None:
        self._data["house_profit"] = round(amount, 2)
        self._save()

    def get_total_user_balances(self) -> float:
        total = 0.0
        for user_data in self._data["users"].values():
            total += user_data.get("balance", 0.0)
        return round(total, 2)

    def get_user_stats(self, user_id: int) -> Dict[str, Any]:
        """Get deposit/withdrawal stats for a user."""
        key = self._ensure_user(user_id)
        user_data = self._data["users"][key]
        total_deposited = sum(d["amount"] for d in user_data.get("deposits", []))
        total_withdrawn = sum(w["amount"] for w in user_data.get("withdrawals", []))
        num_deposits = len(user_data.get("deposits", []))
        num_withdrawals = len(user_data.get("withdrawals", []))
        return {
            "balance": user_data["balance"],
            "total_deposited": round(total_deposited, 2),
            "total_withdrawn": round(total_withdrawn, 2),
            "num_deposits": num_deposits,
            "num_withdrawals": num_withdrawals,
            "total_wagered": user_data.get("total_wagered", 0.0),
        }

    def record_wager(self, user_id: int, amount: float, username: str = "") -> None:
        """Track a wager amount and update the user's display name."""
        key = self._ensure_user(user_id)
        user_data = self._data["users"][key]
        user_data["total_wagered"] = round(
            user_data.get("total_wagered", 0.0) + amount, 2
        )
        user_data["wagered_since_requirement"] = round(
            user_data.get("wagered_since_requirement", 0.0) + amount, 2
        )
        user_data["weekly_wagered"] = round(
            user_data.get("weekly_wagered", 0.0) + amount, 2
        )
        user_data["monthly_wagered"] = round(
            user_data.get("monthly_wagered", 0.0) + amount, 2
        )
        if username:
            user_data["username"] = username
        self._save()

    def set_wager_requirement(self, user_id: int) -> float:
        """Set wager requirement to 2x current balance. Returns the requirement."""
        key = self._ensure_user(user_id)
        user_data = self._data["users"][key]
        bal = user_data["balance"]
        requirement = round(bal * 2, 2)
        user_data["wager_requirement"] = requirement
        user_data["wagered_since_requirement"] = 0.0
        self._save()
        return requirement

    def can_withdraw(self, user_id: int) -> tuple[bool, float, float]:
        """Check if user has met the wager requirement.
        Returns (allowed, wagered_so_far, requirement).
        """
        key = self._ensure_user(user_id)
        user_data = self._data["users"][key]
        requirement = user_data.get("wager_requirement", 0.0)
        wagered = user_data.get("wagered_since_requirement", 0.0)
        return (wagered >= requirement, wagered, requirement)

    def get_leaderboard(self, top_n: int = 10) -> List[Dict[str, Any]]:
        """Return the top N users sorted by total amount wagered (descending)."""
        entries = []
        for uid, user_data in self._data["users"].items():
            wagered = user_data.get("total_wagered", 0.0)
            if wagered > 0:
                entries.append({
                    "user_id": uid,
                    "username": user_data.get("username", ""),
                    "total_wagered": wagered,
                })
        entries.sort(key=lambda e: e["total_wagered"], reverse=True)
        return entries[:top_n]

    def record_match(
        self,
        user_id: int,
        game_type: str,
        bet: float,
        result: str,
        net: float,
    ) -> None:
        """Record a completed match for the user.

        result: 'win', 'loss', or 'push'
        net: signed profit/loss amount (positive = profit)
        """
        key = self._ensure_user(user_id)
        user_data = self._data["users"][key]
        if "matches" not in user_data:
            user_data["matches"] = []
        user_data["matches"].append({
            "game": game_type,
            "bet": bet,
            "result": result,
            "net": round(net, 2),
            "timestamp": int(time.time()),
        })
        self._save()

    def get_recent_matches(self, user_id: int, limit: int = 25) -> List[Dict[str, Any]]:
        """Return the most recent matches for a user, newest first."""
        key = self._ensure_user(user_id)
        matches = self._data["users"][key].get("matches", [])
        return list(reversed(matches[-limit:]))

    @staticmethod
    def _last_thursday_noon_cst() -> int:
        """Return Unix timestamp of the most recent Thursday 12:00 PM CST (18:00 UTC)."""
        cst = timezone(timedelta(hours=-6))
        now_cst = datetime.now(cst)
        days_since_thursday = (now_cst.weekday() - 3) % 7
        last_thu = now_cst - timedelta(days=days_since_thursday)
        last_thu_noon = last_thu.replace(hour=12, minute=0, second=0, microsecond=0)
        if last_thu_noon > now_cst:
            last_thu_noon -= timedelta(days=7)
        return int(last_thu_noon.timestamp())

    @staticmethod
    def _next_thursday_noon_cst() -> int:
        """Return Unix timestamp of the next Thursday 12:00 PM CST (18:00 UTC)."""
        cst = timezone(timedelta(hours=-6))
        now_cst = datetime.now(cst)
        days_since_thursday = (now_cst.weekday() - 3) % 7
        last_thu = now_cst - timedelta(days=days_since_thursday)
        last_thu_noon = last_thu.replace(hour=12, minute=0, second=0, microsecond=0)
        if last_thu_noon > now_cst:
            last_thu_noon -= timedelta(days=7)
        return int((last_thu_noon + timedelta(days=7)).timestamp())

    def claim_weekly_bonus(self, user_id: int) -> tuple[bool, float, float, int]:
        """Attempt to claim the weekly bonus (4.5% of weekly wagers).

        The bonus unlocks every Thursday at 12:00 PM CST.
        Returns (success, bonus_amount, weekly_wagered, seconds_remaining).
        If success is False, seconds_remaining indicates how long until the
        next Thursday 12 PM CST.
        """
        key = self._ensure_user(user_id)
        user_data = self._data["users"][key]
        last_claim = user_data.get("weekly_last_claim", 0)
        last_thursday = self._last_thursday_noon_cst()
        now = int(time.time())

        # Already claimed since the last Thursday unlock
        if last_claim >= last_thursday:
            next_thursday = self._next_thursday_noon_cst()
            remaining = max(0, next_thursday - now)
            wagered = user_data.get("weekly_wagered", 0.0)
            return (False, 0.0, wagered, remaining)

        wagered = user_data.get("weekly_wagered", 0.0)
        bonus = round(wagered * 0.045, 2)
        if bonus > 0:
            user_data["balance"] = round(user_data["balance"] + bonus, 2)
        user_data["weekly_wagered"] = 0.0
        user_data["weekly_last_claim"] = now
        self._save()
        return (True, bonus, wagered, 0)

    def claim_monthly_bonus(self, user_id: int) -> tuple[bool, float, float, int]:
        """Attempt to claim the monthly bonus (4.5% of monthly wagers).

        Returns (success, bonus_amount, monthly_wagered, seconds_remaining).
        If success is False, seconds_remaining indicates how long until the
        user can claim.
        """
        key = self._ensure_user(user_id)
        user_data = self._data["users"][key]
        period_start = user_data.get("monthly_period_start", 0)
        now = int(time.time())
        elapsed = now - period_start
        month_seconds = 30 * 86400

        if elapsed < month_seconds:
            remaining = month_seconds - elapsed
            wagered = user_data.get("monthly_wagered", 0.0)
            return (False, 0.0, wagered, remaining)

        wagered = user_data.get("monthly_wagered", 0.0)
        bonus = round(wagered * 0.045, 2)
        if bonus > 0:
            user_data["balance"] = round(user_data["balance"] + bonus, 2)
        user_data["monthly_wagered"] = 0.0
        user_data["monthly_period_start"] = now
        self._save()
        return (True, bonus, wagered, 0)

    def get_weekly_info(self, user_id: int) -> Dict[str, Any]:
        """Get current weekly bonus info for a user."""
        key = self._ensure_user(user_id)
        user_data = self._data["users"][key]
        now = int(time.time())
        last_claim = user_data.get("weekly_last_claim", 0)
        wagered = user_data.get("weekly_wagered", 0.0)
        last_thursday = self._last_thursday_noon_cst()
        can_claim = last_claim < last_thursday
        if can_claim:
            remaining = 0
        else:
            next_thursday = self._next_thursday_noon_cst()
            remaining = max(0, next_thursday - now)
        return {
            "wagered": wagered,
            "potential_bonus": round(wagered * 0.045, 2),
            "seconds_remaining": remaining,
            "can_claim": can_claim,
        }

    def get_monthly_info(self, user_id: int) -> Dict[str, Any]:
        """Get current monthly bonus info for a user."""
        key = self._ensure_user(user_id)
        user_data = self._data["users"][key]
        now = int(time.time())
        period_start = user_data.get("monthly_period_start", now)
        wagered = user_data.get("monthly_wagered", 0.0)
        elapsed = now - period_start
        month_seconds = 30 * 86400
        remaining = max(0, month_seconds - elapsed)
        return {
            "wagered": wagered,
            "potential_bonus": round(wagered * 0.045, 2),
            "seconds_remaining": remaining,
            "can_claim": elapsed >= month_seconds,
        }

    def get_tournament_chat_id(self) -> Optional[int]:
        return self._data.get("tournament_chat_id")

    def set_tournament_chat_id(self, chat_id: int) -> None:
        self._data["tournament_chat_id"] = chat_id
        self._save()

    def get_wager_stats(self, user_id: int) -> Dict[str, float]:
        """Return daily, weekly, and all-time wagered totals for a user."""
        key = self._ensure_user(user_id)
        user_data = self._data["users"][key]
        total_wagered = user_data.get("total_wagered", 0.0)

        now = int(time.time())
        day_ago = now - 86400
        week_ago = now - 604800

        daily = 0.0
        weekly = 0.0
        for m in user_data.get("matches", []):
            ts = m.get("timestamp", 0)
            bet = m.get("bet", 0.0)
            if ts >= day_ago:
                daily += bet
            if ts >= week_ago:
                weekly += bet

        return {
            "daily": round(daily, 2),
            "weekly": round(weekly, 2),
            "total": round(total_wagered, 2),
        }


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class GameSession:
    """Tracks a single game between a player and the bot."""

    chat_id: int
    user_id: int
    game_type: GameType
    bet: float
    rolls_per_round: int = 1
    points_to_win: int = 3
    crazy_mode: bool = False  # if True, lowest score wins each round
    player_points: int = 0
    bot_points: int = 0
    current_round: int = 1
    waiting_for_player: bool = True
    player_score_this_round: Optional[int] = None
    bot_score_this_round: Optional[int] = None
    player_rolls_this_round: List[int] = field(default_factory=list)
    bot_rolls_this_round: List[int] = field(default_factory=list)
    finished: bool = False
    is_pvp: bool = False
    opponent_id: Optional[int] = None


@dataclass
class BlackjackSession:
    """Tracks a blackjack game between a player and the dealer."""

    chat_id: int
    user_id: int
    message_id: int
    deck: List[tuple]
    player_hands: List[List[tuple]]
    dealer_hand: List[tuple]
    bets: List[float]
    hand_states: List[str]  # "playing", "stood", "busted", "blackjack"
    current_hand: int = 0
    finished: bool = False
    original_bet: float = 0.0


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

store = BalanceStore()
oxapay = OxaPayClient(
    merchant_key=OXAPAY_MERCHANT_API_KEY,
    payout_key=OXAPAY_PAYOUT_API_KEY,
    general_key=OXAPAY_GENERAL_API_KEY,
)

# (chat_id, user_id) -> GameSession (allows multiple users to play at once)
active_games: dict[tuple[int, int], GameSession] = {}

# track_id -> (chat_id, user_id, amount) for pending deposits
pending_deposits: dict[str, tuple[int, int, float]] = {}

# (chat_id, user_id) -> BlackjackSession (allows multiple users to play at once)
active_blackjack: dict[tuple[int, int], BlackjackSession] = {}

# (chat_id, message_id) -> user_id — tracks who owns each button message
button_owners: dict[tuple[int, int], int] = {}

# chat_id -> rain session data
active_rains: dict[int, dict] = {}

# (chat_id, challenger_user_id) -> PvP challenge data
active_pvp_challenges: dict[tuple[int, int], dict] = {}

# Whether withdrawals are currently enabled (toggled by /ewith and /dwith)
withdrawals_enabled: bool = True

# Tournament state
tournament_state: dict = {
    "phase": "idle",  # idle, joining, running
    "chat_id": None,
    "participants": [],  # [(user_id, username), ...]
    "bracket": [],  # [(p1_id, p2_id), ...] current round matches
    "current_match_idx": 0,
    "round_num": 1,
    "winners": [],  # winners advancing from current round
    "match_rolls": {},  # {user_id: roll_value} for active match
    "eliminated": [],  # eliminated players in order
    "announcement_msg_id": None,
    "semifinal_losers": [],
    "is_third_place_match": False,
    "final_winner": None,
    "final_runner_up": None,
}
tournament_chat_id: Optional[int] = store.get_tournament_chat_id()

# (chat_id, user_id) -> mines session data
active_mines: dict[tuple[int, int], dict] = {}

# (chat_id, user_id) -> tower session data
active_towers: dict[tuple[int, int], dict] = {}


def _record_owner(chat_id: int, message_id: int, user_id: int) -> None:
    """Record which user owns the inline buttons on a message."""
    button_owners[(chat_id, message_id)] = user_id


async def _notify_private_log(
    bot,
    txn_type: str,
    user_id: int,
    user_name: str,
    amount: float,
    currency: str = "USD",
    track_id: str = "",
    address: str = "",
    new_balance: float = 0.0,
) -> None:
    """Send a deposit/withdrawal log message to the private logging group."""
    if not PRIVATE_LOG_GROUP_ID:
        return
    try:
        if txn_type == "deposit":
            text = (
                "\U0001f4e5 DEPOSIT\n"
                f"\U0001f464 User: {user_name} (ID: {user_id})\n"
                f"\U0001f4b5 Amount: ${amount:.2f}\n"
                f"\U0001f4b1 Currency: {currency}\n"
                f"\U0001f194 Track ID: {track_id}\n"
                f"\U0001f4b0 New Balance: ${new_balance:.2f}"
            )
        else:
            addr_display = (
                f"{address[:10]}...{address[-6:]}"
                if len(address) > 20
                else address
            )
            text = (
                "\U0001f4e4 WITHDRAWAL\n"
                f"\U0001f464 User: {user_name} (ID: {user_id})\n"
                f"\U0001f4b8 Amount: {amount} {currency}\n"
                f"\U0001f4cd Address: {addr_display}\n"
                f"\U0001f194 Track ID: {track_id}\n"
                f"\U0001f4b0 Remaining Balance: ${new_balance:.2f}"
            )
        await bot.send_message(chat_id=PRIVATE_LOG_GROUP_ID, text=text)
    except Exception as e:
        logger.error("Failed to send private log notification: %s", e)


# ---------------------------------------------------------------------------
# Command Handlers
# ---------------------------------------------------------------------------

async def start_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /start - show Toxic Casino welcome with interactive buttons (DMs only)."""
    if update.effective_chat.type != "private":
        return
    user = update.effective_user
    chat_id = update.effective_chat.id
    bal = store.get_balance(user.id)
    msg = await update.message.reply_text(
        f"\u2620\ufe0f Welcome to Toxic Casino, {user.first_name}!\n\n"
        f"\U0001f4b0 Your balance: ${bal:.2f}\n\n"
        "Choose an option below to get started:",
        reply_markup=main_menu_keyboard(),
    )
    _record_owner(chat_id, msg.message_id, user.id)


async def balance_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /balance - show current balance."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    bal = store.get_balance(user.id)
    msg = await update.message.reply_text(
        f"\U0001f4b0 Your Toxic Casino balance: ${bal:.2f}",
        reply_markup=back_to_menu_keyboard(),
    )
    _record_owner(chat_id, msg.message_id, user.id)



# ---------------------------------------------------------------------------
# Callback Query Handler (Button Presses)
# ---------------------------------------------------------------------------

async def button_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle all inline keyboard button presses."""
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id

    # Check button ownership
    owner_id = button_owners.get((chat_id, query.message.message_id))
    if owner_id is not None and user.id != owner_id:
        await query.answer("These buttons aren't for you!", show_alert=True)
        return

    await query.answer()

    data = query.data

    # ---- Main Menu ----
    if data == "menu_main":
        bal = store.get_balance(user.id)
        await query.edit_message_text(
            f"\u2620\ufe0f Welcome to Toxic Casino, {user.first_name}!\n\n"
            f"\U0001f4b0 Your balance: ${bal:.2f}\n\n"
            "Choose an option below:",
            reply_markup=main_menu_keyboard(),
        )

    # ---- Balance ----
    elif data == "menu_balance":
        bal = store.get_balance(user.id)
        await query.edit_message_text(
            f"\U0001f4b0 Your Toxic Casino Balance\n\n"
            f"  ${bal:.2f}",
            reply_markup=back_to_menu_keyboard(),
        )

    # ---- Deposit ----
    elif data == "menu_deposit":
        await query.edit_message_text(
            "\U0001f4b3 Toxic Casino \u2014 Deposit\n\n"
            "Send the amount you want to deposit (in USD):\n\n"
            "  /deposit <amount>\n\n"
            "Example: /deposit 50",
            reply_markup=back_to_menu_keyboard(),
        )

    # ---- Withdraw ----
    elif data == "menu_withdraw":
        await query.edit_message_text(
            "\U0001f4b8 Toxic Casino \u2014 Withdraw\n\n"
            "Send your withdrawal request:\n\n"
            "  /withdraw <amount> <currency> <address> [network]\n\n"
            "Examples:\n"
            "  /withdraw 25 USDT TRfG7...abc TRC20\n"
            "  /withdraw 0.01 BTC bc1q...xyz\n"
            "  /withdraw 50 USDT 0x1234...def ERC20",
            reply_markup=back_to_menu_keyboard(),
        )

    # ---- Games Menu ----
    elif data == "menu_games":
        await query.edit_message_text(
            "\U0001f3ae Toxic Casino \u2014 Games\n\n"
            "Pick your poison! Select a game below,\n"
            "then use the command with your bet:\n\n"
            "  \U0001f3b2 /dice <bet>\n"
            "  \U0001f3b3 /bowl <bet>\n"
            "  \u26bd /football <bet>\n"
            "  \U0001f3af /darts <bet>\n"
            "  \U0001f3c0 /ball <bet>\n"
            "  \U0001f0cf /blackjack <bet>\n"
            "  \U0001f3b0 /dr <bet> \u2014 Dice Roulette\n"
            "  \U0001f4a3 /mines <bet> <mines>\n"
            "  \U0001f435 /tower <bet>\n"
            "  \U0001fa99 /heads or /tails <bet>\n\n"
            "First to 3 points wins the pot!\n"
            "Blackjack pays 3:2!",
            reply_markup=games_keyboard(),
        )

    # ---- Individual Game Buttons ----
    elif data == "game_dice":
        await query.edit_message_text(
            "\U0001f3b2 Toxic Casino \u2014 Dice\n\n"
            "Send your bet to start:\n"
            "  /dice <amount>\n\n"
            "Example: /dice 10",
            reply_markup=back_to_menu_keyboard(),
        )

    elif data == "game_bowl":
        await query.edit_message_text(
            "\U0001f3b3 Toxic Casino \u2014 Bowling\n\n"
            "Send your bet to start:\n"
            "  /bowl <amount>\n\n"
            "Example: /bowl 10",
            reply_markup=back_to_menu_keyboard(),
        )

    elif data == "game_soccer":
        await query.edit_message_text(
            "\u26bd Toxic Casino \u2014 Soccer\n\n"
            "Send your bet to start:\n"
            "  /football <amount>\n\n"
            "Example: /football 10",
            reply_markup=back_to_menu_keyboard(),
        )

    elif data == "game_darts":
        await query.edit_message_text(
            "\U0001f3af Toxic Casino \u2014 Darts\n\n"
            "Send your bet to start:\n"
            "  /darts <amount>\n\n"
            "Example: /darts 10",
            reply_markup=back_to_menu_keyboard(),
        )

    elif data == "game_basketball":
        await query.edit_message_text(
            "\U0001f3c0 Toxic Casino \u2014 Basketball\n\n"
            "Send your bet to start:\n"
            "  /ball <amount>\n\n"
            "Example: /ball 10",
            reply_markup=back_to_menu_keyboard(),
        )

    elif data == "game_blackjack":
        await query.edit_message_text(
            "\U0001f0cf Toxic Casino \u2014 Blackjack\n\n"
            "Beat the dealer! Get closer to 21 without busting.\n\n"
            "Send your bet to start:\n"
            "  /blackjack <amount>\n\n"
            "Example: /blackjack 10\n\n"
            "Actions: Hit, Stand, Double Down, Split",
            reply_markup=back_to_menu_keyboard(),
        )

    elif data == "game_mines":
        await query.edit_message_text(
            "\U0001f4a3 Toxic Casino \u2014 Mines\n\n"
            "Reveal safe tiles on a 5x5 grid without hitting a mine!\n\n"
            "Send your bet to start:\n"
            "  /mines <amount> <mines_count>\n\n"
            "Example: /mines 10 5\n"
            "Mines: 1-24 (more mines = higher multiplier)",
            reply_markup=back_to_menu_keyboard(),
        )

    elif data == "game_tower":
        await query.edit_message_text(
            "\U0001f435 Toxic Casino \u2014 Monkey Tower\n\n"
            "Climb 8 rows! Each row has 3 columns \u2014\n"
            "1 banana (safe) and 2 traps.\n\n"
            "Send your bet to start:\n"
            "  /tower <amount>\n\n"
            "Example: /tower 10\n"
            "Cash out anytime or climb higher!",
            reply_markup=back_to_menu_keyboard(),
        )

    elif data == "game_coinflip":
        await query.edit_message_text(
            "\U0001fa99 Toxic Casino \u2014 Coinflip\n\n"
            "Call heads or tails!\n\n"
            "  /heads <amount>\n"
            "  /tails <amount>\n\n"
            "Example: /heads 10\n"
            "Win = 1.92x payout!",
            reply_markup=back_to_menu_keyboard(),
        )

    elif data == "game_slots":
        bal = store.get_balance(user.id)
        webapp_url = f"{SLOTS_WEBAPP_URL}?balance={bal:.2f}"
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "\U0001f3b0 Open Toxic Gamble",
                    web_app=WebAppInfo(url=webapp_url),
                ),
            ],
            [
                InlineKeyboardButton("\u00ab Back to Menu", callback_data="menu_main"),
            ],
        ])
        await query.edit_message_text(
            "\U0001f3b0 Toxic Gamble \u2014 Slots\n\n"
            f"Your balance: ${bal:.2f}\n\n"
            "Tap below to open the mini app and spin slots "
            "from Hacksaw Gaming, Nolimit City & more!\n\n"
            "When you're done, hit Cash Out to settle.",
            reply_markup=keyboard,
        )

    # ---- Stats ----
    elif data == "menu_stats":
        stats = store.get_user_stats(user.id)
        await query.edit_message_text(
            f"\U0001f4ca Toxic Casino \u2014 Your Stats\n\n"
            f"  \U0001f4b0 Balance: ${stats['balance']:.2f}\n"
            f"  \U0001f4e5 Total Deposited: ${stats['total_deposited']:.2f}\n"
            f"  \U0001f4e4 Total Withdrawn: ${stats['total_withdrawn']:.2f}\n"
            f"  \U0001f504 Deposits: {stats['num_deposits']}\n"
            f"  \U0001f504 Withdrawals: {stats['num_withdrawals']}",
            reply_markup=back_to_menu_keyboard(),
        )

    # ---- House Balance ----
    elif data == "menu_housebalance":
        if ADMIN_IDS and user.id not in ADMIN_IDS:
            await query.edit_message_text(
                "\u2620\ufe0f Access denied.",
                reply_markup=back_to_menu_keyboard(),
            )
            return

        house_bal = store.get_house_profit()
        await query.edit_message_text(
            f"\U0001f3e6 Toxic Casino \u2014 House Balance\n\n"
            f"  ${house_bal:.2f}",
            reply_markup=back_to_menu_keyboard(),
        )


# ---------------------------------------------------------------------------
# Mode Selection Callback Handler
# ---------------------------------------------------------------------------

async def rounds_selection_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle rounds selection button presses (1, 2, or 3 rounds / points to win)."""
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id

    # Check button ownership
    owner_id = button_owners.get((chat_id, query.message.message_id))
    if owner_id is not None and user.id != owner_id:
        await query.answer("These buttons aren't for you!", show_alert=True)
        return

    data = query.data  # e.g. "rounds_dice_10.00_2"
    parts = data.split("_", 3)  # ["rounds", "dice", "10.00", "2"]

    if len(parts) != 4:
        await query.answer("Invalid selection.", show_alert=True)
        return

    game_key = parts[1]
    try:
        bet = float(parts[2])
        rounds = int(parts[3])
    except ValueError:
        await query.answer("Invalid selection.", show_alert=True)
        return

    if rounds not in (1, 2, 3):
        await query.answer("Invalid round count.", show_alert=True)
        return

    game_type = GAME_COMMANDS.get(game_key)
    if game_type is None:
        await query.answer("Unknown game.", show_alert=True)
        return

    await query.answer()

    emoji = GAME_EMOJI[game_type]
    rounds_label = f"{rounds} round{'s' if rounds > 1 else ''}"
    await query.edit_message_text(
        f"\u2620\ufe0f Toxic Casino \u2014 {game_type.value.title()}\n\n"
        f"\U0001f4b5 Bet: ${bet:.2f}\n"
        f"\U0001f3c6 {rounds_label} (first to {rounds} points wins)\n\n"
        f"Choose how many {emoji} rolls per round:",
        reply_markup=mode_selection_keyboard(game_key, bet, rounds),
    )


async def mode_selection_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle roll-count selection button presses (1 or 2 rolls per round)."""
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id

    # Check button ownership
    owner_id = button_owners.get((chat_id, query.message.message_id))
    if owner_id is not None and user.id != owner_id:
        await query.answer("These buttons aren't for you!", show_alert=True)
        return

    data = query.data  # e.g. "mode_dice_10.00_2_1"
    parts = data.split("_", 4)  # ["mode", "dice", "10.00", "2", "1"]

    if len(parts) != 5:
        await query.answer("Invalid selection.", show_alert=True)
        return

    game_key = parts[1]
    try:
        bet = float(parts[2])
        rounds = int(parts[3])
        rolls = int(parts[4])
    except ValueError:
        await query.answer("Invalid selection.", show_alert=True)
        return

    if rolls not in (1, 2):
        await query.answer("Invalid roll count.", show_alert=True)
        return
    if rounds not in (1, 2, 3):
        await query.answer("Invalid round count.", show_alert=True)
        return

    game_type = GAME_COMMANDS.get(game_key)
    if game_type is None:
        await query.answer("Unknown game.", show_alert=True)
        return

    # Check for active games
    game_key_tuple = (chat_id, user.id)
    if game_key_tuple in active_games or game_key_tuple in active_blackjack or game_key_tuple in active_dice_roulette:
        await query.answer("You already have an active game!", show_alert=True)
        return

    # Re-check balance (could have changed since bet was entered)
    bal = store.get_balance(user.id)
    if bet > bal:
        await query.answer(
            f"Insufficient balance! You have ${bal:.2f}.", show_alert=True
        )
        return

    await query.answer()

    emoji = GAME_EMOJI[game_type]
    rounds_label = f"{rounds} round{'s' if rounds > 1 else ''}"
    rolls_label = f"{rolls} roll{'s' if rolls > 1 else ''}"
    await query.edit_message_text(
        f"\u2620\ufe0f Toxic Casino \u2014 {game_type.value.title()}\n\n"
        f"\U0001f4b5 Bet: ${bet:.2f}\n"
        f"\U0001f3c6 {rounds_label} (first to {rounds} points wins)\n"
        f"{emoji} Rolls per round: {rolls_label}\n\n"
        f"Choose game mode:",
        reply_markup=crazy_mode_keyboard(game_key, bet, rounds, rolls),
    )


# ---------------------------------------------------------------------------
# Deposit Handler
# ---------------------------------------------------------------------------

async def rules_selection_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle Normal vs Crazy mode selection."""
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id

    # Check button ownership
    owner_id = button_owners.get((chat_id, query.message.message_id))
    if owner_id is not None and user.id != owner_id:
        await query.answer("These buttons aren't for you!", show_alert=True)
        return

    data = query.data  # e.g. "rules_dice_10.00_2_1_crazy"
    parts = data.split("_", 5)  # ["rules", game_key, bet, rounds, rolls, mode]

    if len(parts) != 6:
        await query.answer("Invalid selection.", show_alert=True)
        return

    game_key = parts[1]
    try:
        bet = float(parts[2])
        rounds = int(parts[3])
        rolls = int(parts[4])
    except ValueError:
        await query.answer("Invalid selection.", show_alert=True)
        return

    mode = parts[5]
    if mode not in ("normal", "crazy"):
        await query.answer("Invalid selection.", show_alert=True)
        return

    game_type = GAME_COMMANDS.get(game_key)
    if game_type is None:
        await query.answer("Unknown game.", show_alert=True)
        return

    # Check for active games
    game_key_tuple = (chat_id, user.id)
    if game_key_tuple in active_games or game_key_tuple in active_blackjack or game_key_tuple in active_dice_roulette:
        await query.answer("You already have an active game!", show_alert=True)
        return

    # Re-check balance
    bal = store.get_balance(user.id)
    if bet > bal:
        await query.answer(
            f"Insufficient balance! You have ${bal:.2f}.", show_alert=True
        )
        return

    await query.answer()

    emoji = GAME_EMOJI[game_type]
    rolls_label = f"{rolls} roll{'s' if rolls > 1 else ''}"
    rounds_label = f"{rounds} round{'s' if rounds > 1 else ''}"
    mode_label = "Crazy (low wins)" if mode == "crazy" else "Normal (high wins)"

    await query.edit_message_text(
        f"\u2620\ufe0f Toxic Casino \u2014 {game_type.value.title()}\n\n"
        f"\U0001f4b5 Bet: ${bet:.2f}\n"
        f"\U0001f3c6 {rounds_label} (first to {rounds} points wins)\n"
        f"{emoji} Rolls per round: {rolls_label}\n"
        f"\U0001f525 Mode: {mode_label}\n\n"
        f"Choose your opponent:",
        reply_markup=opponent_selection_keyboard(game_key, bet, rounds, rolls, mode),
    )


async def opponent_selection_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle opponent selection (vs Bot or vs Player) after game setup."""
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id

    # Check button ownership
    owner_id = button_owners.get((chat_id, query.message.message_id))
    if owner_id is not None and user.id != owner_id:
        await query.answer("These buttons aren't for you!", show_alert=True)
        return

    data = query.data  # e.g. "opp_bot_dice_10.00_3_1_normal"
    parts = data.split("_", 6)  # ["opp", "bot/pvp", game_key, bet, rounds, rolls, mode]

    if len(parts) != 7:
        await query.answer("Invalid selection.", show_alert=True)
        return

    opp_type = parts[1]  # "bot" or "pvp"
    game_key = parts[2]
    try:
        bet = float(parts[3])
        rounds = int(parts[4])
        rolls = int(parts[5])
    except ValueError:
        await query.answer("Invalid selection.", show_alert=True)
        return

    mode = parts[6]
    if mode not in ("normal", "crazy"):
        await query.answer("Invalid selection.", show_alert=True)
        return

    game_type = GAME_COMMANDS.get(game_key)
    if game_type is None:
        await query.answer("Unknown game.", show_alert=True)
        return

    # Check for active games
    game_key_tuple = (chat_id, user.id)
    if game_key_tuple in active_games or game_key_tuple in active_blackjack or game_key_tuple in active_dice_roulette:
        await query.answer("You already have an active game!", show_alert=True)
        return

    # Re-check balance
    bal = store.get_balance(user.id)
    if bet > bal:
        await query.answer(
            f"Insufficient balance! You have ${bal:.2f}.", show_alert=True
        )
        return

    await query.answer()

    emoji = GAME_EMOJI[game_type]
    rolls_label = f"{rolls} roll{'s' if rolls > 1 else ''}"
    rounds_label = f"{rounds} round{'s' if rounds > 1 else ''}"
    mode_label = "Crazy (low wins)" if mode == "crazy" else "Normal (high wins)"

    if opp_type == "bot":
        # ---- Start game vs Bot (existing behavior) ----
        store.adjust_balance(user.id, -bet)
        store.record_wager(user.id, bet, username=user.first_name)

        session = GameSession(
            chat_id=chat_id,
            user_id=user.id,
            game_type=game_type,
            bet=bet,
            rolls_per_round=rolls,
            points_to_win=rounds,
            crazy_mode=(mode == "crazy"),
        )
        active_games[(chat_id, user.id)] = session

        await query.edit_message_text(
            f"\u2620\ufe0f Toxic Casino \u2014 {game_type.value.title()}\n\n"
            f"\U0001f4b5 Bet: ${bet:.2f}\n"
            f"\U0001f3c6 {rounds_label} (first to {rounds} points wins)\n"
            f"{emoji} Rolls per round: {rolls_label}\n"
            f"\U0001f525 Mode: {mode_label}\n\n"
            f"\U0001f4cc Round {session.current_round}\n"
            f"Your turn! Send {emoji} to play."
        )
    else:
        # ---- Create PvP challenge ----
        if game_key_tuple in active_pvp_challenges:
            await query.edit_message_text(
                "\u2620\ufe0f You already have a pending challenge!"
            )
            return

        # Deduct bet from challenger
        store.adjust_balance(user.id, -bet)
        store.record_wager(user.id, bet, username=user.first_name)

        active_pvp_challenges[game_key_tuple] = {
            "user_id": user.id,
            "username": user.first_name,
            "game_type": game_type,
            "game_key": game_key,
            "bet": bet,
            "rounds": rounds,
            "rolls": rolls,
            "mode": mode,
            "message_id": query.message.message_id,
        }

        await query.edit_message_text(
            f"\u2694\ufe0f PvP Challenge!\n\n"
            f"\U0001f3ae Game: {game_type.value.title()}\n"
            f"\U0001f4b5 Bet: ${bet:.2f} (2x payout to winner)\n"
            f"\U0001f3c6 {rounds_label} (first to {rounds} points wins)\n"
            f"{emoji} Rolls per round: {rolls_label}\n"
            f"\U0001f525 Mode: {mode_label}\n\n"
            f"\U0001f464 Challenger: {user.first_name}\n\n"
            f"Anyone can accept this challenge!",
            reply_markup=pvp_join_keyboard(user.id),
        )


async def pvp_join_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle a player joining or cancelling a PvP challenge."""
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id
    data = query.data  # "pvpjoin_<id>" or "pvpcancel_<id>"

    if data.startswith("pvpcancel_"):
        challenger_id = int(data.split("_", 1)[1])
        challenge_key = (chat_id, challenger_id)

        if challenge_key not in active_pvp_challenges:
            await query.answer("Challenge no longer exists.", show_alert=True)
            return

        # Only challenger can cancel
        if user.id != challenger_id:
            await query.answer("Only the challenger can cancel!", show_alert=True)
            return

        challenge = active_pvp_challenges.pop(challenge_key)
        # Refund bet to challenger
        store.adjust_balance(challenger_id, challenge["bet"])
        await query.answer()
        await query.edit_message_text(
            "\u274c Challenge cancelled. Bet refunded."
        )
        return

    # pvpjoin_<challenger_id>
    challenger_id = int(data.split("_", 1)[1])
    challenge_key = (chat_id, challenger_id)

    if challenge_key not in active_pvp_challenges:
        await query.answer("Challenge no longer exists.", show_alert=True)
        return

    # Can't join your own challenge
    if user.id == challenger_id:
        await query.answer("You can't join your own challenge!", show_alert=True)
        return

    challenge = active_pvp_challenges[challenge_key]
    bet = challenge["bet"]

    # Check if joiner already has an active game
    joiner_key = (chat_id, user.id)
    if joiner_key in active_games or joiner_key in active_blackjack or joiner_key in active_dice_roulette:
        await query.answer("You already have an active game!", show_alert=True)
        return

    # Check joiner's balance
    bal = store.get_balance(user.id)
    if bet > bal:
        await query.answer(
            f"Insufficient balance! You need ${bet:.2f} but have ${bal:.2f}.",
            show_alert=True,
        )
        return

    await query.answer()

    # Remove challenge
    active_pvp_challenges.pop(challenge_key, None)

    # Deduct bet from joiner
    store.adjust_balance(user.id, -bet)
    store.record_wager(user.id, bet, username=user.first_name)

    game_type = challenge["game_type"]
    rounds = challenge["rounds"]
    rolls = challenge["rolls"]
    mode = challenge["mode"]

    session = GameSession(
        chat_id=chat_id,
        user_id=challenger_id,
        game_type=game_type,
        bet=bet,
        rolls_per_round=rolls,
        points_to_win=rounds,
        crazy_mode=(mode == "crazy"),
        is_pvp=True,
        opponent_id=user.id,
    )

    # Store session under BOTH player keys so either can send emoji
    active_games[(chat_id, challenger_id)] = session
    active_games[(chat_id, user.id)] = session

    emoji = GAME_EMOJI[game_type]
    rolls_label = f"{rolls} roll{'s' if rolls > 1 else ''}"
    rounds_label = f"{rounds} round{'s' if rounds > 1 else ''}"
    mode_label = "Crazy (low wins)" if mode == "crazy" else "Normal (high wins)"

    await query.edit_message_text(
        f"\u2694\ufe0f PvP Match Started!\n\n"
        f"\U0001f3ae Game: {game_type.value.title()}\n"
        f"\U0001f4b5 Bet: ${bet:.2f} each (2x payout to winner)\n"
        f"\U0001f3c6 {rounds_label} (first to {rounds} points wins)\n"
        f"{emoji} Rolls per round: {rolls_label}\n"
        f"\U0001f525 Mode: {mode_label}\n\n"
        f"\U0001f464 Challenger: {challenge['username']}\n"
        f"\U0001f464 Opponent: {user.first_name}\n\n"
        f"\U0001f4cc Round 1\n"
        f"Challenger's turn! Send {emoji} to play."
    )


async def deposit_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /deposit <amount>."""
    user = update.effective_user
    chat_id = update.effective_chat.id

    if not OXAPAY_MERCHANT_API_KEY:
        msg = await update.message.reply_text(
            "\u2620\ufe0f Deposits are not configured. Contact the admin.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    args = context.args
    if not args:
        msg = await update.message.reply_text(
            "\U0001f4b3 Toxic Casino \u2014 Deposit\n\n"
            "Usage: /deposit <amount in USD>\n"
            "Example: /deposit 50",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    try:
        amount = float(args[0])
    except ValueError:
        msg = await update.message.reply_text(
            "\u2620\ufe0f Amount must be a number.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    if amount < MIN_DEPOSIT:
        msg = await update.message.reply_text(
            f"\u2620\ufe0f Minimum deposit is ${MIN_DEPOSIT:.2f}",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    await update.message.reply_text("\u23f3 Creating payment invoice...")

    try:
        order_id = f"dep_{user.id}_{int(time.time())}"
        result = await oxapay.create_invoice(amount=amount, order_id=order_id)

        if result.get("status") != 200:
            error_msg = result.get("message", "Unknown error")
            error_detail = result.get("error", {})
            if isinstance(error_detail, dict) and error_detail.get("message"):
                error_msg = error_detail["message"]
            msg = await update.message.reply_text(
                f"\u2620\ufe0f Failed to create invoice: {error_msg}",
                reply_markup=back_to_menu_keyboard(),
            )
            _record_owner(chat_id, msg.message_id, user.id)
            return

        data = result.get("data", {})
        track_id = data.get("track_id", "")
        payment_url = data.get("payment_url", "")

        if not track_id or not payment_url:
            msg = await update.message.reply_text(
                "\u2620\ufe0f Failed to create invoice. Please try again.",
                reply_markup=back_to_menu_keyboard(),
            )
            _record_owner(chat_id, msg.message_id, user.id)
            return

        pending_deposits[track_id] = (chat_id, user.id, amount)

        pay_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("\U0001f517 Pay Now", url=payment_url)],
            [InlineKeyboardButton("\u00ab Back to Menu", callback_data="menu_main")],
        ])

        msg = await update.message.reply_text(
            f"\U0001f4b3 Toxic Casino \u2014 Deposit Invoice\n\n"
            f"\U0001f4b5 Amount: ${amount:.2f}\n"
            f"\U0001f194 Track ID: {track_id}\n\n"
            f"\u23f0 Expires in 60 minutes.\n"
            f"Your balance will be credited automatically.",
            reply_markup=pay_keyboard,
        )
        _record_owner(chat_id, msg.message_id, user.id)

        asyncio.create_task(
            _poll_payment(context, track_id, chat_id, user.id, amount)
        )

    except Exception as e:
        logger.error("Error creating deposit invoice: %s", e)
        msg = await update.message.reply_text(
            f"\u2620\ufe0f Error creating invoice: {e}",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)


async def _poll_payment(
    context: ContextTypes.DEFAULT_TYPE,
    track_id: str,
    chat_id: int,
    user_id: int,
    amount: float,
) -> None:
    """Background task to poll OxaPay for payment confirmation."""
    start_time = time.time()

    while time.time() - start_time < PAYMENT_POLL_TIMEOUT:
        await asyncio.sleep(PAYMENT_POLL_INTERVAL)

        try:
            result = await oxapay.get_payment_info(track_id)

            if result.get("status") != 200:
                continue

            data = result.get("data", {})
            payment_status = data.get("status", "")

            if payment_status == "paid":
                paid_amount = amount

                new_bal = store.adjust_balance(user_id, paid_amount)
                store.record_deposit(
                    user_id,
                    paid_amount,
                    track_id,
                    data.get("pay_currency", "USD"),
                )
                store.add_house_profit(paid_amount)
                wager_req = store.set_wager_requirement(user_id)
                pending_deposits.pop(track_id, None)

                msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"\u2705 Toxic Casino \u2014 Deposit Confirmed!\n\n"
                        f"\U0001f4b5 Amount: ${paid_amount:.2f}\n"
                        f"\U0001f194 Track ID: {track_id}\n"
                        f"\U0001f4b0 New Balance: ${new_bal:.2f}\n"
                        f"\U0001f3b0 Wager Requirement: ${wager_req:.2f}\n\n"
                        f"Ready to play? Hit the button below!"
                    ),
                    reply_markup=main_menu_keyboard(),
                )
                _record_owner(chat_id, msg.message_id, user_id)

                # Log deposit to private group
                user_info = await context.bot.get_chat(user_id)
                display_name = user_info.username or user_info.first_name or str(user_id)
                await _notify_private_log(
                    bot=context.bot,
                    txn_type="deposit",
                    user_id=user_id,
                    user_name=display_name,
                    amount=paid_amount,
                    currency=data.get("pay_currency", "USD"),
                    track_id=track_id,
                    new_balance=new_bal,
                )
                return

            elif payment_status in ("failed", "expired", "refunded"):
                pending_deposits.pop(track_id, None)
                msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"\u2620\ufe0f Deposit {payment_status}.\n"
                        f"\U0001f194 Track ID: {track_id}\n"
                        f"Try again with /deposit"
                    ),
                    reply_markup=back_to_menu_keyboard(),
                )
                _record_owner(chat_id, msg.message_id, user_id)
                return

        except Exception as e:
            logger.error("Error polling payment %s: %s", track_id, e)

    pending_deposits.pop(track_id, None)
    try:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"\u23f0 Deposit monitoring timed out for Track ID: {track_id}. "
                f"If you paid, contact support."
            ),
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user_id)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Withdraw Handler
# ---------------------------------------------------------------------------

async def withdraw_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /withdraw <amount> <currency> <address> [network]."""
    user = update.effective_user
    chat_id = update.effective_chat.id

    if not withdrawals_enabled:
        msg = await update.message.reply_text(
            "\u2620\ufe0f Withdrawals are currently disabled.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    # Check 2x wager requirement (owner is exempt)
    if user.id != OWNER_ID:
        allowed, wagered, requirement = store.can_withdraw(user.id)
        if not allowed:
            remaining = round(requirement - wagered, 2)
            msg = await update.message.reply_text(
                f"\u2620\ufe0f You must meet the wager requirement before withdrawing.\n\n"
                f"\U0001f3b0 Wager Requirement: ${requirement:.2f}\n"
                f"\U0001f4ca Wagered So Far: ${wagered:.2f}\n"
                f"\u23f3 Remaining: ${remaining:.2f}",
                reply_markup=back_to_menu_keyboard(),
            )
            _record_owner(chat_id, msg.message_id, user.id)
            return

    if not OXAPAY_PAYOUT_API_KEY:
        msg = await update.message.reply_text(
            "\u2620\ufe0f Withdrawals are not configured. Contact the admin.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    args = context.args
    if not args or len(args) < 3:
        msg = await update.message.reply_text(
            "\U0001f4b8 Toxic Casino \u2014 Withdraw\n\n"
            "Usage: /withdraw <amount> <currency> <address> [network]\n\n"
            "Examples:\n"
            "  /withdraw 25 USDT TRfG7...abc TRC20\n"
            "  /withdraw 0.01 BTC bc1q...xyz\n"
            "  /withdraw 50 USDT 0x1234...def ERC20",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    try:
        amount = float(args[0])
    except ValueError:
        msg = await update.message.reply_text(
            "\u2620\ufe0f Amount must be a number.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    currency = args[1].upper()
    address = args[2]
    network = args[3].upper() if len(args) > 3 else None

    if amount <= 0:
        msg = await update.message.reply_text(
            "\u2620\ufe0f Amount must be greater than 0.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    bal = store.get_balance(user.id)
    if amount > bal:
        msg = await update.message.reply_text(
            f"\u2620\ufe0f Insufficient balance!\n"
            f"Your balance: ${bal:.2f}\n"
            f"Requested: ${amount:.2f}",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    if amount < MIN_WITHDRAW:
        msg = await update.message.reply_text(
            f"\u2620\ufe0f Minimum withdrawal is ${MIN_WITHDRAW:.2f}",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    await update.message.reply_text("\u23f3 Processing withdrawal...")

    try:
        result = await oxapay.create_payout(
            address=address,
            currency=currency,
            amount=amount,
            network=network,
            description=f"Toxic Casino withdrawal for user {user.id}",
        )

        if result.get("status") != 200:
            error_msg = result.get("message", "Unknown error")
            error_detail = result.get("error", {})
            if isinstance(error_detail, dict) and error_detail.get("message"):
                error_msg = error_detail["message"]
            msg = await update.message.reply_text(
                f"\u2620\ufe0f Withdrawal failed: {error_msg}",
                reply_markup=back_to_menu_keyboard(),
            )
            _record_owner(chat_id, msg.message_id, user.id)
            return

        data = result.get("data", {})
        track_id = data.get("track_id", "N/A")
        payout_status = data.get("status", "processing")

        new_bal = store.adjust_balance(user.id, -amount)
        store.record_withdrawal(user.id, amount, track_id, currency, address)
        store.add_house_profit(-amount)

        addr_display = (
            f"{address[:10]}...{address[-6:]}"
            if len(address) > 20
            else address
        )
        msg = await update.message.reply_text(
            f"\u2705 Toxic Casino \u2014 Withdrawal Submitted\n\n"
            f"\U0001f4b8 Amount: {amount} {currency}\n"
            f"\U0001f4cd Address: {addr_display}\n"
            f"\U0001f310 Network: {network or 'Auto'}\n"
            f"\U0001f194 Track ID: {track_id}\n"
            f"\U0001f4ca Status: {payout_status}\n"
            f"\U0001f4b0 Remaining: ${new_bal:.2f}",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)

        # Log withdrawal to private group
        display_name = user.username or user.first_name or str(user.id)
        await _notify_private_log(
            bot=context.bot,
            txn_type="withdrawal",
            user_id=user.id,
            user_name=display_name,
            amount=amount,
            currency=currency,
            track_id=track_id,
            address=address,
            new_balance=new_bal,
        )

    except Exception as e:
        logger.error("Error creating withdrawal: %s", e)
        msg = await update.message.reply_text(
            f"\u2620\ufe0f Error processing withdrawal: {e}",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)


# ---------------------------------------------------------------------------
# Enable / Disable Withdrawals (owner only)
# ---------------------------------------------------------------------------

OWNER_ID = 7074468601


async def enable_withdrawals_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /ewith — enable withdrawals (owner only)."""
    global withdrawals_enabled
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("\u2620\ufe0f Owner-only command.")
        return
    withdrawals_enabled = True
    await update.message.reply_text("\u2705 Withdrawals are now ENABLED.")


async def disable_withdrawals_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /dwith — disable withdrawals (owner only)."""
    global withdrawals_enabled
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("\u2620\ufe0f Owner-only command.")
        return
    withdrawals_enabled = False
    await update.message.reply_text("\U0001f6ab Withdrawals are now DISABLED.")


# ---------------------------------------------------------------------------
# House Balance Handler (command version)
# ---------------------------------------------------------------------------

async def housebalance_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /housebalance."""
    user = update.effective_user
    chat_id = update.effective_chat.id

    if ADMIN_IDS and user.id not in ADMIN_IDS:
        msg = await update.message.reply_text(
            "\u2620\ufe0f Access denied.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    house_bal = store.get_house_profit()
    m = await update.message.reply_text(
        f"\U0001f3e6 Toxic Casino \u2014 House Balance\n\n"
        f"  ${house_bal:.2f}",
        reply_markup=back_to_menu_keyboard(),
    )
    _record_owner(chat_id, m.message_id, user.id)


# ---------------------------------------------------------------------------
# Tip Handler
# ---------------------------------------------------------------------------

MIN_TIP = 0.01


async def tip_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /tip <amount> - reply to a user's message to tip them."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    message = update.message

    # Must be a reply to another user's message
    if not message.reply_to_message:
        msg = await message.reply_text(
            "\U0001f4b8 Toxic Casino \u2014 Tip\n\n"
            "Reply to someone's message with:\n"
            "  /tip <amount>\n\n"
            "Example: Reply to a message and type /tip 5",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    recipient = message.reply_to_message.from_user

    if recipient is None:
        msg = await message.reply_text(
            "\u2620\ufe0f Could not identify the recipient.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    if recipient.id == user.id:
        msg = await message.reply_text(
            "\u2620\ufe0f You can't tip yourself!",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    if recipient.is_bot:
        msg = await message.reply_text(
            "\u2620\ufe0f You can't tip a bot!",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    args = context.args
    if not args:
        msg = await message.reply_text(
            "\U0001f4b8 Toxic Casino \u2014 Tip\n\n"
            "Usage: /tip <amount>\n"
            "Example: /tip 5",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    try:
        amount = float(args[0])
    except ValueError:
        msg = await message.reply_text(
            "\u2620\ufe0f Amount must be a number.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    if amount < MIN_TIP:
        msg = await message.reply_text(
            f"\u2620\ufe0f Minimum tip is ${MIN_TIP:.2f}",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    bal = store.get_balance(user.id)
    if amount > bal:
        msg = await message.reply_text(
            f"\u2620\ufe0f Insufficient balance!\n"
            f"Your balance: ${bal:.2f}\n"
            f"Tip amount: ${amount:.2f}",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    # Transfer funds
    new_sender_bal = store.adjust_balance(user.id, -amount)
    new_recipient_bal = store.adjust_balance(recipient.id, amount)
    wager_req = store.set_wager_requirement(recipient.id)

    recipient_name = recipient.first_name or recipient.username or "User"
    sender_name = user.first_name or user.username or "User"

    msg = await message.reply_text(
        f"\U0001f4b8 Toxic Casino \u2014 Tip Sent!\n\n"
        f"\U0001f464 {sender_name} tipped {recipient_name} ${amount:.2f}\n\n"
        f"\U0001f4b0 Your new balance: ${new_sender_bal:.2f}",
        reply_markup=back_to_menu_keyboard(),
    )
    _record_owner(chat_id, msg.message_id, user.id)


def _matches_page_keyboard(page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Build pagination keyboard for /matches."""
    buttons = []
    if page > 0:
        buttons.append(
            InlineKeyboardButton("\u2b05\ufe0f", callback_data=f"matches_{page - 1}")
        )
    if page < total_pages - 1:
        buttons.append(
            InlineKeyboardButton("\u27a1\ufe0f", callback_data=f"matches_{page + 1}")
        )
    rows = []
    if buttons:
        rows.append(buttons)
    rows.append([InlineKeyboardButton("\u00ab Back to Menu", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def _render_matches_page(matches: List[Dict], page: int, per_page: int = 5) -> str:
    """Render a single page of match history."""
    total = len(matches)
    total_pages = max(1, (total + per_page - 1) // per_page)
    start = page * per_page
    end = min(start + per_page, total)
    page_matches = matches[start:end]

    if not page_matches:
        return (
            "\U0001f4cb Match History\n\n"
            "No matches recorded yet! Play a game to get started."
        )

    result_emoji = {"win": "\u2705", "loss": "\u274c", "push": "\u2796"}
    lines = []
    for i, m in enumerate(page_matches, start=start + 1):
        emoji = result_emoji.get(m["result"], "")
        net = m["net"]
        net_str = f"+${net:.2f}" if net > 0 else (f"-${abs(net):.2f}" if net < 0 else "$0.00")
        game_name = m["game"].replace("_", " ").title()
        lines.append(f"{i}. {emoji} {game_name} — ${m['bet']:.2f} bet — {net_str}")

    header = (
        f"\U0001f4cb Match History (Page {page + 1}/{total_pages})\n"
        f"Showing {start + 1}-{end} of {total}\n"
    )
    return header + "\n" + "\n".join(lines)


async def matches_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /matches - show paginated match history (25 most recent, 5 per page)."""
    user = update.effective_user
    chat_id = update.effective_chat.id

    matches = store.get_recent_matches(user.id, limit=25)
    page = 0
    per_page = 5
    total_pages = max(1, (len(matches) + per_page - 1) // per_page)

    text = _render_matches_page(matches, page, per_page)
    msg = await update.message.reply_text(
        text,
        reply_markup=_matches_page_keyboard(page, total_pages),
    )
    _record_owner(chat_id, msg.message_id, user.id)


async def matches_page_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle pagination button presses for match history."""
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id

    # Check button ownership
    owner_id = button_owners.get((chat_id, query.message.message_id))
    if owner_id is not None and user.id != owner_id:
        await query.answer("These buttons aren't for you!", show_alert=True)
        return

    await query.answer()

    # Parse page number from callback data: "matches_2"
    data = query.data
    try:
        page = int(data.split("_")[1])
    except (IndexError, ValueError):
        return

    matches = store.get_recent_matches(user.id, limit=25)
    per_page = 5
    total_pages = max(1, (len(matches) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))

    text = _render_matches_page(matches, page, per_page)
    await query.edit_message_text(
        text,
        reply_markup=_matches_page_keyboard(page, total_pages),
    )


# ---------------------------------------------------------------------------
# Rain Feature
# ---------------------------------------------------------------------------

def _rain_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Build the Join Rain button."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001f327\ufe0f Join Rain", callback_data=f"rain_join_{chat_id}")],
    ])


def _rain_text(rain: dict) -> str:
    """Render the rain message text."""
    starter = rain["starter_name"]
    amount = rain["amount"]
    wager_req = rain["wager_req"]
    minutes = rain["minutes"]
    joined = rain["joined"]
    joined_count = len(joined)

    joined_names = ", ".join(joined.values()) if joined else "None yet"

    return (
        f"\U0001f327\ufe0f RAIN by {starter}\n\n"
        f"\U0001f4b5 Amount: ${amount:,.2f}\n"
        f"\u23f1 Time: {minutes} minute{'s' if minutes != 1 else ''}\n"
        f"\U0001f3b0 Wager Requirement: ${wager_req:,.2f}\n\n"
        f"\U0001f465 Joined ({joined_count}): {joined_names}\n\n"
        f"Must have @ToxicGamble in your name to join!"
    )


async def _rain_timer(
    app, chat_id: int, minutes: float
) -> None:
    """Wait for the rain duration then distribute funds."""
    await asyncio.sleep(minutes * 60)

    rain = active_rains.pop(chat_id, None)
    if rain is None:
        return

    joined = rain["joined"]
    amount = rain["amount"]
    starter_id = rain["starter_id"]
    message_id = rain["message_id"]

    if not joined:
        # No one joined — refund the starter
        store.adjust_balance(starter_id, amount)
        try:
            await app.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    f"\U0001f327\ufe0f RAIN by {rain['starter_name']}\n\n"
                    f"No one joined the rain. ${amount:,.2f} refunded."
                ),
            )
        except Exception:
            pass
        return

    share = round(amount / len(joined), 2)
    # Handle rounding remainder
    remainder = round(amount - share * len(joined), 2)

    winner_lines = []
    for uid_str, name in joined.items():
        uid = int(uid_str)
        payout = share
        if remainder > 0:
            payout = round(payout + 0.01, 2)
            remainder = round(remainder - 0.01, 2)
        store.adjust_balance(uid, payout)
        winner_lines.append(f"  {name} — +${payout:.2f}")

    try:
        await app.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=(
                f"\U0001f327\ufe0f RAIN by {rain['starter_name']} — ENDED\n\n"
                f"\U0001f4b5 ${amount:,.2f} split among {len(joined)} user{'s' if len(joined) != 1 else ''}:\n\n"
                + "\n".join(winner_lines)
            ),
        )
    except Exception:
        pass


async def rain_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /rain <amount> <time_minutes> <wager_requirement>."""
    user = update.effective_user
    chat_id = update.effective_chat.id

    if chat_id in active_rains:
        await update.message.reply_text(
            "\u2620\ufe0f There's already an active rain in this chat! Wait for it to end."
        )
        return

    args = context.args
    if not args or len(args) < 3:
        await update.message.reply_text(
            "\U0001f327\ufe0f Rain — Share money with the community!\n\n"
            "Usage: /rain <amount> <time_minutes> <wager_requirement>\n"
            "Example: /rain 50 5 100\n\n"
            "This will rain $50 over 5 minutes, requiring $100 total wagered to join."
        )
        return

    try:
        amount = float(args[0])
        minutes = float(args[1])
        wager_req = float(args[2])
    except ValueError:
        await update.message.reply_text(
            "\u2620\ufe0f All arguments must be numbers.\n"
            "Usage: /rain <amount> <time_minutes> <wager_requirement>"
        )
        return

    if amount <= 0:
        await update.message.reply_text("\u2620\ufe0f Rain amount must be positive.")
        return
    if minutes <= 0:
        await update.message.reply_text("\u2620\ufe0f Time must be positive.")
        return
    if wager_req < 0:
        await update.message.reply_text("\u2620\ufe0f Wager requirement cannot be negative.")
        return

    bal = store.get_balance(user.id)
    if amount > bal:
        await update.message.reply_text(
            f"\u2620\ufe0f Insufficient balance! You have ${bal:.2f}.\n"
            f"Use /deposit to add funds."
        )
        return

    # Deduct the rain amount from the starter
    store.adjust_balance(user.id, -amount)

    rain = {
        "starter_id": user.id,
        "starter_name": user.first_name,
        "amount": amount,
        "minutes": minutes,
        "wager_req": wager_req,
        "joined": {},  # uid_str -> display_name
        "message_id": 0,
    }

    msg = await update.message.reply_text(
        _rain_text(rain),
        reply_markup=_rain_keyboard(chat_id),
    )
    rain["message_id"] = msg.message_id
    active_rains[chat_id] = rain

    # Schedule the rain end timer
    asyncio.create_task(_rain_timer(context.application, chat_id, minutes))


async def rain_join_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle the Join Rain button press."""
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id

    rain = active_rains.get(chat_id)
    if rain is None:
        await query.answer("This rain has ended.", show_alert=True)
        return

    uid_str = str(user.id)

    # Already joined?
    if uid_str in rain["joined"]:
        await query.answer("You already joined this rain!", show_alert=True)
        return

    # Check @ToxicGamble in name
    full_name = (user.first_name or "") + " " + (user.last_name or "")
    username = user.username or ""
    name_check = full_name.lower() + " " + username.lower()
    if "toxicgamble" not in name_check.replace(" ", ""):
        await query.answer(
            "You must have @ToxicGamble in your name to join!",
            show_alert=True,
        )
        return

    # Check wager requirement
    wager_stats = store.get_wager_stats(user.id)
    if wager_stats["total"] < rain["wager_req"]:
        await query.answer(
            f"You need ${rain['wager_req']:,.2f} total wagered to join! "
            f"You have ${wager_stats['total']:,.2f}.",
            show_alert=True,
        )
        return

    # Can't join your own rain
    if user.id == rain["starter_id"]:
        await query.answer("You can't join your own rain!", show_alert=True)
        return

    await query.answer("\U0001f389 You joined the rain!")

    display_name = user.first_name or f"User {user.id}"
    rain["joined"][uid_str] = display_name

    # Update the rain message
    try:
        await query.edit_message_text(
            _rain_text(rain),
            reply_markup=_rain_keyboard(chat_id),
        )
    except Exception:
        pass


async def stats_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /stats - show daily, weekly, and total wagered amounts."""
    user = update.effective_user
    chat_id = update.effective_chat.id

    wager_stats = store.get_wager_stats(user.id)

    text = (
        f"\U0001f4ca {user.first_name}'s Stats\n\n"
        f"\U0001f4c5 Today: ${wager_stats['daily']:,.2f} wagered\n\n"
        f"\U0001f4c6 This Week: ${wager_stats['weekly']:,.2f} wagered\n\n"
        f"\U0001f4b0 All-Time: ${wager_stats['total']:,.2f} wagered"
    )

    msg = await update.message.reply_text(
        text,
        reply_markup=back_to_menu_keyboard(),
    )
    _record_owner(chat_id, msg.message_id, user.id)


def _format_duration(seconds: int) -> str:
    """Format seconds into a human-readable duration string."""
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "< 1m"


async def weekly_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /weekly - claim weekly bonus (4.5% of weekly wagers)."""
    user = update.effective_user
    chat_id = update.effective_chat.id

    info = store.get_weekly_info(user.id)

    if not info["can_claim"]:
        remaining_str = _format_duration(info["seconds_remaining"])
        text = (
            f"\U0001f4c5 Weekly Bonus\n\n"
            f"\u23f3 Next bonus available in: {remaining_str}\n\n"
            f"\U0001f3b0 Wagered this week: ${info['wagered']:,.2f}\n"
            f"\U0001f4b0 Current bonus (4.50%): ${info['potential_bonus']:,.2f}\n\n"
            f"Bonus unlocks every Thursday at 12:00 PM CST!\n"
            f"Keep playing to increase your bonus!"
        )
        msg = await update.message.reply_text(
            text, reply_markup=back_to_menu_keyboard()
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    success, bonus, wagered, _ = store.claim_weekly_bonus(user.id)
    new_bal = store.get_balance(user.id)

    if bonus > 0:
        text = (
            f"\U0001f389 Weekly Bonus Claimed!\n\n"
            f"\U0001f3b0 Wagered this week: ${wagered:,.2f}\n"
            f"\U0001f4b5 Bonus (4.50%): ${bonus:,.2f}\n\n"
            f"\U0001f4b0 New Balance: ${new_bal:,.2f}\n\n"
            f"Your weekly wager counter has been reset.\n"
            f"Next bonus: Thursday 12:00 PM CST!"
        )
    else:
        text = (
            f"\U0001f4c5 Weekly Bonus\n\n"
            f"You didn't wager anything this week.\n"
            f"No bonus to claim — start playing to earn your next weekly bonus!\n\n"
            f"Next bonus: Thursday 12:00 PM CST!"
        )

    msg = await update.message.reply_text(
        text, reply_markup=back_to_menu_keyboard()
    )
    _record_owner(chat_id, msg.message_id, user.id)


async def monthly_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /monthly - claim monthly bonus (4.5% of monthly wagers)."""
    user = update.effective_user
    chat_id = update.effective_chat.id

    info = store.get_monthly_info(user.id)

    if not info["can_claim"]:
        remaining_str = _format_duration(info["seconds_remaining"])
        text = (
            f"\U0001f4c6 Monthly Bonus\n\n"
            f"\u23f3 You can claim in: {remaining_str}\n\n"
            f"\U0001f3b0 Wagered this month: ${info['wagered']:,.2f}\n"
            f"\U0001f4b0 Current bonus (4.50%): ${info['potential_bonus']:,.2f}\n\n"
            f"Keep playing to increase your bonus!"
        )
        msg = await update.message.reply_text(
            text, reply_markup=back_to_menu_keyboard()
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    success, bonus, wagered, _ = store.claim_monthly_bonus(user.id)
    new_bal = store.get_balance(user.id)

    if bonus > 0:
        text = (
            f"\U0001f389 Monthly Bonus Claimed!\n\n"
            f"\U0001f3b0 Wagered this month: ${wagered:,.2f}\n"
            f"\U0001f4b5 Bonus (4.50%): ${bonus:,.2f}\n\n"
            f"\U0001f4b0 New Balance: ${new_bal:,.2f}\n\n"
            f"Your monthly wager counter has been reset. See you next month!"
        )
    else:
        text = (
            f"\U0001f4c6 Monthly Bonus\n\n"
            f"You didn't wager anything this month.\n"
            f"No bonus to claim — start playing to earn your next monthly bonus!\n\n"
            f"Your monthly period has been reset."
        )

    msg = await update.message.reply_text(
        text, reply_markup=back_to_menu_keyboard()
    )
    _record_owner(chat_id, msg.message_id, user.id)


async def leaderboard_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /leaderboard - show top 10 users by total amount wagered."""
    user = update.effective_user
    chat_id = update.effective_chat.id

    leaderboard = store.get_leaderboard(top_n=10)

    if not leaderboard:
        msg = await update.message.reply_text(
            "\U0001f3c6 Leaderboard\n\n"
            "No wagers recorded yet! Be the first to play.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, msg.message_id, user.id)
        return

    medal = ["\U0001f947", "\U0001f948", "\U0001f949"]  # 🥇🥈🥉
    lines = []
    for i, entry in enumerate(leaderboard):
        rank = medal[i] if i < 3 else f"{i + 1}."
        name = entry["username"] or f"User {entry['user_id']}"
        wagered = entry["total_wagered"]
        lines.append(f"{rank} {name} — ${wagered:,.2f}")

    text = (
        "\U0001f3c6 Toxic Casino Leaderboard\n"
        "\U0001f4b0 Top 10 by Total Wagered\n\n"
        + "\n".join(lines)
    )

    msg = await update.message.reply_text(
        text,
        reply_markup=back_to_menu_keyboard(),
    )
    _record_owner(chat_id, msg.message_id, user.id)



async def showbal_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /showbal - owner-only command to view another user's balance.

    Usage: reply to a user's message with /showbal
    """
    user = update.effective_user
    if user.id != OWNER_ID:
        await update.message.reply_text("\u2620\ufe0f This command is owner-only.")
        return

    reply = update.message.reply_to_message
    if not reply or not reply.from_user:
        await update.message.reply_text(
            "\u2620\ufe0f Reply to a user's message with /showbal to see their balance."
        )
        return

    target = reply.from_user
    bal = store.get_balance(target.id)
    stats = store.get_user_stats(target.id)
    wager_stats = store.get_wager_stats(target.id)

    await update.message.reply_text(
        f"\U0001f50d {target.first_name} (ID: {target.id})\n\n"
        f"\U0001f4b0 Balance: ${bal:.2f}\n"
        f"\U0001f4e5 Total Deposited: ${stats['total_deposited']:.2f}\n"
        f"\U0001f4e4 Total Withdrawn: ${stats['total_withdrawn']:.2f}\n"
        f"\U0001f3b0 All-Time Wagered: ${wager_stats['total']:,.2f}\n"
        f"\U0001f4c5 Today Wagered: ${wager_stats['daily']:,.2f}\n"
        f"\U0001f4c6 This Week Wagered: ${wager_stats['weekly']:,.2f}"
    )



async def addbal_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /addbal <amount> - owner-only command to add to own balance.

    Usage: reply to your own message with /addbal <amount>
    """
    user = update.effective_user
    if user.id != OWNER_ID:
        await update.message.reply_text("\u2620\ufe0f This command is owner-only.")
        return

    reply = update.message.reply_to_message
    if not reply or not reply.from_user or reply.from_user.id != user.id:
        await update.message.reply_text(
            "\u2620\ufe0f Reply to your own message with /addbal <amount>."
        )
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "\u2620\ufe0f Usage: /addbal <amount>\nReply to your own message."
        )
        return

    try:
        amount = float(args[0])
    except ValueError:
        await update.message.reply_text("\u2620\ufe0f Amount must be a number.")
        return

    if amount <= 0:
        await update.message.reply_text("\u2620\ufe0f Amount must be positive.")
        return

    store.adjust_balance(user.id, amount)
    new_balance = store.get_balance(user.id)

    await update.message.reply_text(
        f"\u2705 Added ${amount:.2f} to your balance.\n"
        f"New balance: ${new_balance:.2f}"
    )


async def cancel_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /cancel - owner-only command to cancel a user's current game.

    Usage: reply to a user's message with /cancel
    """
    user = update.effective_user
    if user.id != OWNER_ID:
        await update.message.reply_text("\u2620\ufe0f This command is owner-only.")
        return

    reply = update.message.reply_to_message
    if not reply or not reply.from_user:
        await update.message.reply_text(
            "\u2620\ufe0f Reply to a user's message with /cancel to cancel their current game."
        )
        return

    target = reply.from_user
    chat_id = update.effective_chat.id
    game_key = (chat_id, target.id)

    cancelled = []
    refunded = 0.0

    # Cancel emoji games (dice, bowling, soccer, darts, basketball)
    session = active_games.pop(game_key, None)
    if session:
        # Refund the player's bet
        store.adjust_balance(target.id, session.bet)
        refunded += session.bet
        cancelled.append(session.game_type.value)
        # If PvP, also clean up opponent's side and refund them
        if session.is_pvp and session.opponent_id:
            opp_key = (chat_id, session.opponent_id)
            active_games.pop(opp_key, None)
            store.adjust_balance(session.opponent_id, session.bet)

    # Cancel blackjack
    bj_session = active_blackjack.pop(game_key, None)
    if bj_session:
        # Refund all bets (including splits)
        bet_refund = sum(bj_session.bets)
        store.adjust_balance(target.id, bet_refund)
        refunded += bet_refund
        cancelled.append("blackjack")

    # Cancel dice roulette
    dr_session = active_dice_roulette.pop(game_key, None)
    if dr_session:
        dr_bet = dr_session.get("bet", 0.0)
        if dr_bet > 0:
            store.adjust_balance(target.id, dr_bet)
            refunded += dr_bet
        cancelled.append("dice roulette")

    # Cancel mines
    mines_session = active_mines.pop(game_key, None)
    if mines_session:
        mines_bet = mines_session.get("bet", 0.0)
        if mines_bet > 0:
            store.adjust_balance(target.id, mines_bet)
            refunded += mines_bet
        cancelled.append("mines")

    # Cancel tower
    tower_session = active_towers.pop(game_key, None)
    if tower_session:
        tower_bet = tower_session.get("bet", 0.0)
        if tower_bet > 0:
            store.adjust_balance(target.id, tower_bet)
            refunded += tower_bet
        cancelled.append("tower")

    # Cancel pending PvP challenge
    pvp_challenge = active_pvp_challenges.pop(game_key, None)
    if pvp_challenge:
        pvp_bet = pvp_challenge.get("bet", 0.0)
        if pvp_bet > 0:
            store.adjust_balance(target.id, pvp_bet)
            refunded += pvp_bet
        cancelled.append("pvp challenge")

    if not cancelled:
        await update.message.reply_text(
            f"\u2620\ufe0f {target.first_name} has no active games to cancel."
        )
        return

    new_bal = store.get_balance(target.id)
    games_str = ", ".join(cancelled)
    await update.message.reply_text(
        f"\u274c Cancelled {target.first_name}'s active game(s): {games_str}\n"
        f"Refunded: ${refunded:.2f}\n"
        f"Their balance: ${new_bal:.2f}"
    )


async def setbal_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /setbal <amount> - owner-only command to set another user's balance.

    Usage: reply to a user's message with /setbal <amount>
    """
    user = update.effective_user
    if user.id != OWNER_ID:
        await update.message.reply_text("\u2620\ufe0f This command is owner-only.")
        return

    reply = update.message.reply_to_message
    if not reply or not reply.from_user:
        await update.message.reply_text(
            "\u2620\ufe0f Reply to a user's message with /setbal <amount>."
        )
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "\u2620\ufe0f Usage: /setbal <amount>\nReply to a user's message."
        )
        return

    try:
        new_balance = float(args[0])
    except ValueError:
        await update.message.reply_text("\u2620\ufe0f Amount must be a number.")
        return

    target = reply.from_user
    current_bal = store.get_balance(target.id)
    diff = new_balance - current_bal
    store.adjust_balance(target.id, diff)

    await update.message.reply_text(
        f"\u2705 Set {target.first_name}'s balance to ${new_balance:.2f}\n"
        f"(was ${current_bal:.2f})"
    )


async def sethb_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /sethb <amount> - owner-only command to set the house balance.

    Usage: /sethb <amount>
    """
    user = update.effective_user
    if user.id != OWNER_ID:
        await update.message.reply_text("\u2620\ufe0f This command is owner-only.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "\u2620\ufe0f Usage: /sethb <amount>"
        )
        return

    try:
        new_amount = float(args[0])
    except ValueError:
        await update.message.reply_text("\u2620\ufe0f Amount must be a number.")
        return

    old_amount = store.get_house_profit()
    store.set_house_profit(new_amount)

    await update.message.reply_text(
        f"\u2705 House balance set to ${new_amount:.2f}\n"
        f"(was ${old_amount:.2f})"
    )


# ---------------------------------------------------------------------------
# Slots Mini App (Toxic Gamble)
# ---------------------------------------------------------------------------

async def slots_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /slots - open the Toxic Gamble slots mini app."""
    user = update.effective_user
    bal = store.get_balance(user.id)

    webapp_url = f"{SLOTS_WEBAPP_URL}?balance={bal:.2f}"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "\U0001f3b0 Open Toxic Gamble",
                web_app=WebAppInfo(url=webapp_url),
            ),
        ],
        [
            InlineKeyboardButton("\u00ab Back to Menu", callback_data="menu_main"),
        ],
    ])

    m = await update.message.reply_text(
        f"\U0001f3b0 **Toxic Gamble** \u2014 Slots\n\n"
        f"Your balance: ${bal:.2f}\n\n"
        f"Tap below to open the mini app and spin slots "
        f"from Hacksaw Gaming, Nolimit City & more!\n\n"
        f"When you\'re done, hit **Cash Out** inside the app "
        f"to settle your balance.",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    _record_owner(update.effective_chat.id, m.message_id, user.id)


async def slots_webapp_data_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle data returned from the Toxic Gamble slots mini app."""
    user = update.effective_user
    data_str = update.effective_message.web_app_data.data

    try:
        data = json.loads(data_str)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Invalid slots webapp data from %s: %s", user.id, data_str)
        return

    if data.get("action") != "slots_cashout":
        return

    net = data.get("net", 0.0)
    try:
        net = float(net)
    except (ValueError, TypeError):
        return

    # Apply net change to balance
    if net != 0:
        store.adjust_balance(user.id, net)

    # Record wager (approximate: we record the absolute net as a wager entry)
    if net < 0:
        # Player lost, house gains
        store.add_house_profit(-net)
        store.record_match(user.id, "slots", abs(net), "loss", net)
    elif net > 0:
        # Player won, house loses
        store.add_house_profit(-net)
        store.record_match(user.id, "slots", net, "win", net)

    new_bal = store.get_balance(user.id)
    if net >= 0:
        await update.effective_message.reply_text(
            f"\U0001f3b0 Slots session ended!\n\n"
            f"Net result: +${net:.2f}\n"
            f"Balance: ${new_bal:.2f}"
        )
    else:
        await update.effective_message.reply_text(
            f"\U0001f3b0 Slots session ended!\n\n"
            f"Net result: -${abs(net):.2f}\n"
            f"Balance: ${new_bal:.2f}"
        )


# ---------------------------------------------------------------------------
# Blackjack
# ---------------------------------------------------------------------------

CARD_SUITS = ["\u2660", "\u2665", "\u2666", "\u2663"]  # ♠ ♥ ♦ ♣
CARD_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
RANK_VALUES: Dict[str, int] = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8,
    "9": 9, "10": 10, "J": 10, "Q": 10, "K": 10, "A": 11,
}


def bj_create_deck(num_decks: int = 2) -> List[tuple]:
    """Create and shuffle a multi-deck shoe."""
    deck = [(rank, suit) for suit in CARD_SUITS for rank in CARD_RANKS] * num_decks
    random.shuffle(deck)
    return deck


def bj_card_str(card: tuple) -> str:
    """Display a card as e.g. 'A\u2660'."""
    return f"{card[0]}{card[1]}"


def bj_hand_str(cards: List[tuple]) -> str:
    """Display a hand of cards."""
    return "  ".join(bj_card_str(c) for c in cards)


def bj_hand_value(cards: List[tuple]) -> int:
    """Calculate the best blackjack hand value."""
    total = sum(RANK_VALUES[c[0]] for c in cards)
    aces = sum(1 for c in cards if c[0] == "A")
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return total


def bj_is_soft(cards: List[tuple]) -> bool:
    """Check if hand is soft (has an ace still counted as 11)."""
    total = sum(RANK_VALUES[c[0]] for c in cards)
    aces = sum(1 for c in cards if c[0] == "A")
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return aces > 0


def bj_action_keyboard(session: BlackjackSession) -> InlineKeyboardMarkup:
    """Build action buttons for the current blackjack hand."""
    if session.finished:
        return back_to_menu_keyboard()

    hand_idx = session.current_hand
    hand = session.player_hands[hand_idx]
    buttons_row1 = [
        InlineKeyboardButton("\U0001f0cf Hit", callback_data="bj_hit"),
        InlineKeyboardButton("\u270b Stand", callback_data="bj_stand"),
    ]
    buttons_row2 = []

    # Double: only on first action (2 cards) and player has enough balance
    if len(hand) == 2:
        extra_needed = session.bets[hand_idx]
        available = store.get_balance(session.user_id)
        if available >= extra_needed:
            buttons_row2.append(
                InlineKeyboardButton("\u23ec Double", callback_data="bj_double")
            )

    # Split: only on first action, same rank, max 4 hands, enough balance
    if (
        len(hand) == 2
        and hand[0][0] == hand[1][0]
        and len(session.player_hands) < 4
    ):
        extra_needed = session.bets[hand_idx]
        available = store.get_balance(session.user_id)
        if available >= extra_needed:
            buttons_row2.append(
                InlineKeyboardButton("\u2702\ufe0f Split", callback_data="bj_split")
            )

    rows = [buttons_row1]
    if buttons_row2:
        rows.append(buttons_row2)
    return InlineKeyboardMarkup(rows)


def bj_render_table(session: BlackjackSession, reveal_dealer: bool = False) -> str:
    """Render the blackjack table as text."""
    lines = ["\U0001f0cf Toxic Casino \u2014 Blackjack\n"]

    # Dealer hand
    if reveal_dealer or session.finished:
        dealer_val = bj_hand_value(session.dealer_hand)
        lines.append(f"\U0001f916 Dealer: {bj_hand_str(session.dealer_hand)}  ({dealer_val})")
    else:
        shown = bj_card_str(session.dealer_hand[0])
        lines.append(f"\U0001f916 Dealer: {shown}  \U0001f0a0")

    lines.append("")

    # Player hands
    for i, hand in enumerate(session.player_hands):
        val = bj_hand_value(hand)
        if len(session.player_hands) > 1:
            marker = "\u25b6 " if i == session.current_hand and not session.finished else "  "
            prefix = f"{marker}Hand {i + 1}: "
        else:
            prefix = "\U0001f464 You: "

        state_tag = ""
        if session.hand_states[i] == "busted":
            state_tag = " \U0001f4a5 BUST"
        elif session.hand_states[i] == "blackjack":
            state_tag = " \U0001f31f BJ!"

        lines.append(f"{prefix}{bj_hand_str(hand)}  ({val}){state_tag}")

        if len(session.player_hands) > 1:
            lines.append(f"    Bet: ${session.bets[i]:.2f}")

    if len(session.player_hands) == 1:
        lines.append(f"\n\U0001f4b5 Bet: ${session.bets[0]:.2f}")

    return "\n".join(lines)


def bj_dealer_play(session: BlackjackSession) -> None:
    """Dealer draws cards according to standard rules (hit soft 17)."""
    while True:
        val = bj_hand_value(session.dealer_hand)
        if val < 17:
            session.dealer_hand.append(session.deck.pop())
        elif val == 17 and bj_is_soft(session.dealer_hand):
            session.dealer_hand.append(session.deck.pop())
        else:
            break


def bj_settle(session: BlackjackSession) -> str:
    """Calculate results and adjust balances. Returns result text."""
    dealer_val = bj_hand_value(session.dealer_hand)
    dealer_bj = dealer_val == 21 and len(session.dealer_hand) == 2
    dealer_bust = dealer_val > 21

    total_return = 0.0
    result_lines = []

    for i, hand in enumerate(session.player_hands):
        pval = bj_hand_value(hand)
        bet = session.bets[i]
        player_bj = (
            session.hand_states[i] == "blackjack"
            and len(session.player_hands) == 1
        )

        if len(session.player_hands) > 1:
            prefix = f"Hand {i + 1}: "
        else:
            prefix = ""

        if session.hand_states[i] == "busted":
            result_lines.append(f"{prefix}Bust — -${bet:.2f}")
        elif player_bj:
            if dealer_bj:
                result_lines.append(f"{prefix}Push (both blackjack)")
                total_return += bet
            else:
                winnings = bet * 1.5
                result_lines.append(f"{prefix}Blackjack! +${winnings:.2f}")
                total_return += bet + winnings
        elif dealer_bust:
            result_lines.append(f"{prefix}Dealer busts — +${bet:.2f}")
            total_return += bet * 2
        elif pval > dealer_val:
            result_lines.append(f"{prefix}You win — +${bet:.2f}")
            total_return += bet * 2
        elif pval < dealer_val:
            result_lines.append(f"{prefix}Dealer wins — -${bet:.2f}")
        else:
            result_lines.append(f"{prefix}Push")
            total_return += bet

    # Credit returns to player
    total_wagered = sum(session.bets)
    if total_return > 0:
        new_bal = store.adjust_balance(session.user_id, total_return)
    else:
        new_bal = store.get_balance(session.user_id)

    net = total_return - total_wagered
    store.add_house_profit(-net)

    # Record match result
    if net > 0:
        match_result = "win"
    elif net < 0:
        match_result = "loss"
    else:
        match_result = "push"
    store.record_match(session.user_id, "blackjack", session.original_bet, match_result, net)

    if net > 0:
        result_lines.append(f"\nNet: +${net:.2f}")
    elif net < 0:
        result_lines.append(f"\nNet: -${abs(net):.2f}")
    else:
        result_lines.append("\nNet: $0.00")

    result_lines.append(f"Balance: ${new_bal:.2f}")

    return "\n".join(result_lines)


def bj_advance_hand(session: BlackjackSession) -> bool:
    """Move to the next unfinished hand. Returns True if there's a hand to play."""
    while session.current_hand < len(session.player_hands):
        if session.hand_states[session.current_hand] == "playing":
            return True
        session.current_hand += 1
    return False


async def blackjack_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /blackjack <bet>."""
    chat_id = update.effective_chat.id
    user = update.effective_user

    game_key = (chat_id, user.id)
    if game_key in active_blackjack:
        await update.message.reply_text(
            "\u2620\ufe0f You already have an active blackjack game! Finish it first."
        )
        return

    if game_key in active_games:
        await update.message.reply_text(
            "\u2620\ufe0f You already have an active game! Finish it first."
        )
        return

    args = context.args
    if not args:
        m = await update.message.reply_text(
            "\U0001f0cf Toxic Casino \u2014 Blackjack\n\n"
            "Usage: /blackjack <bet>\n"
            "Example: /blackjack 10\n\n"
            "Beat the dealer! Get closer to 21.\n"
            "Actions: Hit, Stand, Double Down, Split",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    try:
        bet = float(args[0])
    except ValueError:
        m = await update.message.reply_text(
            "\u2620\ufe0f Bet must be a number.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    if bet < MIN_BET:
        m = await update.message.reply_text(
            f"\u2620\ufe0f Minimum bet is ${MIN_BET:.2f}.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    if bet > MAX_BET:
        m = await update.message.reply_text(
            f"\u2620\ufe0f Maximum bet is ${MAX_BET:.2f}.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    bal = store.get_balance(user.id)
    if bet > bal:
        m = await update.message.reply_text(
            f"\u2620\ufe0f Insufficient balance! You have ${bal:.2f}.\n"
            f"Use /deposit to add funds.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    # Deduct bet and record wager
    store.adjust_balance(user.id, -bet)
    store.record_wager(user.id, bet, username=user.first_name)

    # Create deck and deal
    deck = bj_create_deck()
    player_hand = [deck.pop(), deck.pop()]
    dealer_hand = [deck.pop(), deck.pop()]

    session = BlackjackSession(
        chat_id=chat_id,
        user_id=user.id,
        message_id=0,
        deck=deck,
        player_hands=[player_hand],
        dealer_hand=dealer_hand,
        bets=[bet],
        hand_states=["playing"],
        original_bet=bet,
    )

    # Check for naturals (21 on first two cards)
    player_val = bj_hand_value(player_hand)
    dealer_val = bj_hand_value(dealer_hand)

    if player_val == 21 or dealer_val == 21:
        if player_val == 21:
            session.hand_states[0] = "blackjack"
        session.finished = True
        text = bj_render_table(session, reveal_dealer=True)
        text += "\n\n" + bj_settle(session)
        m = await update.message.reply_text(
            text, reply_markup=game_end_keyboard("bj", bet)
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    # Normal game - show table with action buttons
    active_blackjack[game_key] = session
    text = bj_render_table(session)
    msg = await update.message.reply_text(
        text, reply_markup=bj_action_keyboard(session)
    )
    session.message_id = msg.message_id
    _record_owner(chat_id, msg.message_id, user.id)


async def _bj_finish_game(query, session: BlackjackSession) -> None:
    """Dealer plays and settle the blackjack game."""
    session.finished = True

    # Dealer only plays if not all player hands busted
    all_busted = all(s == "busted" for s in session.hand_states)
    if not all_busted:
        bj_dealer_play(session)

    text = bj_render_table(session, reveal_dealer=True)
    text += "\n\n" + bj_settle(session)

    active_blackjack.pop((session.chat_id, session.user_id), None)

    await query.edit_message_text(
        text, reply_markup=game_end_keyboard("bj", session.original_bet)
    )


async def blackjack_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle blackjack inline button presses (bj_hit, bj_stand, bj_double, bj_split)."""
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id

    bj_key = (chat_id, user.id)
    if bj_key not in active_blackjack:
        await query.answer("No active blackjack game.", show_alert=True)
        return

    session = active_blackjack[bj_key]

    if user.id != session.user_id:
        await query.answer("This isn't your game!", show_alert=True)
        return

    if session.finished:
        await query.answer()
        return

    await query.answer()

    action = query.data
    hand_idx = session.current_hand
    hand = session.player_hands[hand_idx]

    if action == "bj_hit":
        hand.append(session.deck.pop())
        val = bj_hand_value(hand)
        if val > 21:
            session.hand_states[hand_idx] = "busted"
            session.current_hand += 1
            if not bj_advance_hand(session):
                await _bj_finish_game(query, session)
                return
        elif val == 21:
            session.hand_states[hand_idx] = "stood"
            session.current_hand += 1
            if not bj_advance_hand(session):
                await _bj_finish_game(query, session)
                return
        text = bj_render_table(session)
        await query.edit_message_text(text, reply_markup=bj_action_keyboard(session))

    elif action == "bj_stand":
        session.hand_states[hand_idx] = "stood"
        session.current_hand += 1
        if not bj_advance_hand(session):
            await _bj_finish_game(query, session)
            return
        text = bj_render_table(session)
        await query.edit_message_text(text, reply_markup=bj_action_keyboard(session))

    elif action == "bj_double":
        if len(hand) != 2:
            return
        extra = session.bets[hand_idx]
        bal = store.get_balance(session.user_id)
        if bal < extra:
            return
        store.adjust_balance(session.user_id, -extra)
        session.bets[hand_idx] *= 2
        hand.append(session.deck.pop())
        val = bj_hand_value(hand)
        if val > 21:
            session.hand_states[hand_idx] = "busted"
        else:
            session.hand_states[hand_idx] = "stood"
        session.current_hand += 1
        if not bj_advance_hand(session):
            await _bj_finish_game(query, session)
            return
        text = bj_render_table(session)
        await query.edit_message_text(text, reply_markup=bj_action_keyboard(session))

    elif action == "bj_split":
        if len(hand) != 2 or hand[0][0] != hand[1][0]:
            return
        if len(session.player_hands) >= 4:
            return
        extra = session.bets[hand_idx]
        bal = store.get_balance(session.user_id)
        if bal < extra:
            return
        store.adjust_balance(session.user_id, -extra)

        card1 = hand[0]
        card2 = hand[1]
        session.player_hands[hand_idx] = [card1, session.deck.pop()]
        new_hand = [card2, session.deck.pop()]
        session.player_hands.insert(hand_idx + 1, new_hand)
        session.bets.insert(hand_idx + 1, extra)
        session.hand_states.insert(hand_idx + 1, "playing")

        # Auto-stand if current hand hit 21
        if bj_hand_value(session.player_hands[hand_idx]) == 21:
            session.hand_states[hand_idx] = "stood"
            session.current_hand += 1
            if not bj_advance_hand(session):
                await _bj_finish_game(query, session)
                return

        text = bj_render_table(session)
        await query.edit_message_text(text, reply_markup=bj_action_keyboard(session))


# ---------------------------------------------------------------------------
# Game Handlers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Dice Roulette (/dr)
# ---------------------------------------------------------------------------

# (chat_id, user_id) -> {"user_id": int, "bet": float, "message_id": int}
active_dice_roulette: dict[tuple[int, int], dict] = {}


async def dr_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /dr - Dice Roulette. Predict the dice outcome."""
    chat_id = update.effective_chat.id
    user = update.effective_user

    game_key = (chat_id, user.id)
    if game_key in active_games:
        await update.message.reply_text(
            "\u2620\ufe0f You already have an active game! Finish it first."
        )
        return
    if game_key in active_blackjack:
        await update.message.reply_text(
            "\u2620\ufe0f You already have an active game! Finish it first."
        )
        return
    if game_key in active_dice_roulette:
        await update.message.reply_text(
            "\u2620\ufe0f You already have an active dice roulette! Finish it first."
        )
        return

    args = context.args
    if not args:
        m = await update.message.reply_text(
            "Usage: /dr <bet amount> [prediction]\n"
            "Example: /dr 10\n"
            "Example: /dr 5 odd\n\n"
            "Predictions: low, high, odd, even, or 1-6",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    try:
        bet = float(args[0])
    except ValueError:
        m = await update.message.reply_text(
            "\u2620\ufe0f Bet must be a number.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    if bet < MIN_BET:
        m = await update.message.reply_text(
            f"\u2620\ufe0f Minimum bet is ${MIN_BET:.2f}.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    if bet > MAX_BET:
        m = await update.message.reply_text(
            f"\u2620\ufe0f Maximum bet is ${MAX_BET:.2f}.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    bal = store.get_balance(user.id)
    if bet > bal:
        m = await update.message.reply_text(
            f"\u2620\ufe0f Insufficient balance! You have ${bal:.2f}.\n"
            f"Use /deposit to add funds.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    # Check for inline prediction (e.g. /dr 5 odd)
    valid_predictions = {"low", "high", "odd", "even", "1", "2", "3", "4", "5", "6"}
    prediction = None
    if len(args) >= 2:
        pred_arg = args[1].lower()
        if pred_arg in valid_predictions:
            prediction = pred_arg
        else:
            m = await update.message.reply_text(
                "\u2620\ufe0f Invalid prediction.\n"
                "Valid options: low, high, odd, even, or 1-6",
                reply_markup=back_to_menu_keyboard(),
            )
            _record_owner(chat_id, m.message_id, user.id)
            return

    # Deduct bet up front and record wager
    store.adjust_balance(user.id, -bet)
    store.record_wager(user.id, bet, username=user.first_name)

    if prediction is not None:
        # Instant play — resolve immediately without buttons
        if prediction == "low":
            pred_label = "\u2b07\ufe0f Low (1-3)"
        elif prediction == "high":
            pred_label = "\u2b06\ufe0f High (4-6)"
        elif prediction == "odd":
            pred_label = "\U0001f534 Odd (1,3,5)"
        elif prediction == "even":
            pred_label = "\U0001f535 Even (2,4,6)"
        else:
            pred_label = f"{prediction}\ufe0f\u20e3"

        await update.message.reply_text(
            f"\U0001f3b0 Dice Roulette \u2014 ${bet:.2f}\n\n"
            f"Your prediction: {pred_label}\n\n"
            f"\U0001f3b2 Rolling the dice..."
        )

        await asyncio.sleep(1)
        dice_msg = await context.bot.send_dice(
            chat_id=chat_id,
            emoji="\U0001f3b2",
        )
        result = dice_msg.dice.value

        won = False
        multiplier = 0.0
        if prediction == "low" and result in (1, 2, 3):
            won = True
            multiplier = DR_LOW_HIGH_MULTI
        elif prediction == "high" and result in (4, 5, 6):
            won = True
            multiplier = DR_LOW_HIGH_MULTI
        elif prediction == "odd" and result in (1, 3, 5):
            won = True
            multiplier = DR_ODD_EVEN_MULTI
        elif prediction == "even" and result in (2, 4, 6):
            won = True
            multiplier = DR_ODD_EVEN_MULTI
        elif prediction.isdigit() and int(prediction) == result:
            won = True
            multiplier = DR_EXACT_MULTI

        await asyncio.sleep(1)
        if won:
            winnings = round(bet * multiplier, 2)
            store.adjust_balance(user.id, winnings)
            profit = -(winnings - bet)
            store.add_house_profit(profit)
            new_bal = store.get_balance(user.id)
            store.record_match(user.id, "dice_roulette", bet, "win", round(winnings - bet, 2))
            end_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"Dice landed on: {result}\n\n"
                    f"You won! ({pred_label} \u2014 {multiplier}x)\n"
                    f"Payout: ${winnings:.2f}\n"
                    f"Balance: ${new_bal:.2f}"
                ),
                reply_markup=dr_end_keyboard(bet),
            )
        else:
            store.add_house_profit(bet)
            new_bal = store.get_balance(user.id)
            store.record_match(user.id, "dice_roulette", bet, "loss", -bet)
            end_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"Dice landed on: {result}\n\n"
                    f"You lost. Prediction: {pred_label}\n"
                    f"Balance: ${new_bal:.2f}"
                ),
                reply_markup=dr_end_keyboard(bet),
            )
        _record_owner(chat_id, end_msg.message_id, user.id)
        return

    # No prediction provided — show button picker
    m = await update.message.reply_text(
        f"\U0001f3b0 Dice Roulette \u2014 ${bet:.2f}\n\n"
        f"Predict the dice roll!\n\n"
        f"\u2b07\ufe0f Low (1-3) or \u2b06\ufe0f High (4-6): 1.92x\n"
        f"\U0001f534 Odd (1,3,5) or \U0001f535 Even (2,4,6): 1.92x\n"
        f"Pick 1-5 numbers: 1\u21925.6x  2\u21922.45x  3\u21921.92x  4\u21921.54x  5\u21921.38x\n\n"
        f"Pick your prediction:",
        reply_markup=dr_prediction_keyboard(bet),
    )
    _record_owner(chat_id, m.message_id, user.id)
    active_dice_roulette[game_key] = {
        "user_id": user.id,
        "bet": bet,
        "message_id": m.message_id,
        "selected_numbers": set(),
    }


async def dr_toggle_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle dice roulette number toggle button presses (drt_N_bet)."""
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id

    # Check button ownership
    owner_id = button_owners.get((chat_id, query.message.message_id))
    if owner_id is not None and user.id != owner_id:
        await query.answer("These buttons aren't for you!", show_alert=True)
        return

    data = query.data  # e.g. "drt_3_10.00"
    parts = data.split("_")
    num = int(parts[1])
    bet = float(parts[2])

    game_key = (chat_id, user.id)
    session = active_dice_roulette.get(game_key)
    if session is None:
        await query.answer("Session expired.", show_alert=True)
        return

    selected: set[int] = session.get("selected_numbers", set())

    # Toggle the number
    if num in selected:
        selected.discard(num)
    else:
        if len(selected) >= 5:
            await query.answer("Max 5 numbers! Deselect one first.", show_alert=True)
            return
        selected.add(num)

    session["selected_numbers"] = selected
    await query.answer()

    # Rebuild the keyboard with updated selection state
    await query.edit_message_reply_markup(
        reply_markup=dr_prediction_keyboard(bet, selected),
    )


async def dr_confirm_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle dice roulette confirm button press (drc_bet)."""
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id

    # Check button ownership
    owner_id = button_owners.get((chat_id, query.message.message_id))
    if owner_id is not None and user.id != owner_id:
        await query.answer("These buttons aren't for you!", show_alert=True)
        return

    game_key = (chat_id, user.id)
    session = active_dice_roulette.pop(game_key, None)
    if session is None:
        await query.answer("Session expired.", show_alert=True)
        return

    selected: set[int] = session.get("selected_numbers", set())
    if not selected:
        await query.answer("Select at least one number!", show_alert=True)
        active_dice_roulette[game_key] = session
        return

    bet = float(query.data.split("_")[1])
    count = len(selected)
    multiplier = DR_MULTI_NUMBER[count]
    nums_str = ",".join(str(n) for n in sorted(selected))
    pred_label = f"Numbers [{nums_str}]"

    await query.answer()

    # Edit the message to show selection, remove buttons
    await query.edit_message_text(
        f"\U0001f3b0 Dice Roulette \u2014 ${bet:.2f}\n\n"
        f"Your prediction: {pred_label} ({multiplier}x)\n\n"
        f"\U0001f3b2 Rolling the dice..."
    )

    # Send actual dice emoji
    await asyncio.sleep(1)
    dice_msg = await context.bot.send_dice(
        chat_id=chat_id,
        emoji="\U0001f3b2",
    )
    result = dice_msg.dice.value  # 1-6

    won = result in selected

    await asyncio.sleep(1)
    if won:
        winnings = round(bet * multiplier, 2)
        store.adjust_balance(user.id, winnings)
        profit = -(winnings - bet)
        store.add_house_profit(profit)
        new_bal = store.get_balance(user.id)
        store.record_match(user.id, "dice_roulette", bet, "win", round(winnings - bet, 2))
        end_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"Dice landed on: {result}\n\n"
                f"You won! ({pred_label} \u2014 {multiplier}x)\n"
                f"Payout: ${winnings:.2f}\n"
                f"Balance: ${new_bal:.2f}"
            ),
            reply_markup=dr_end_keyboard(bet),
        )
    else:
        store.add_house_profit(bet)
        new_bal = store.get_balance(user.id)
        store.record_match(user.id, "dice_roulette", bet, "loss", -bet)
        end_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"Dice landed on: {result}\n\n"
                f"You lost. Prediction: {pred_label}\n"
                f"Balance: ${new_bal:.2f}"
            ),
            reply_markup=dr_end_keyboard(bet),
        )
    _record_owner(chat_id, end_msg.message_id, user.id)


async def dr_prediction_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle dice roulette low/high/odd/even prediction button presses."""
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id

    # Check button ownership
    owner_id = button_owners.get((chat_id, query.message.message_id))
    if owner_id is not None and user.id != owner_id:
        await query.answer("These buttons aren't for you!", show_alert=True)
        return

    await query.answer()

    data = query.data  # e.g. "dr_low_10.00"
    parts = data.split("_")
    # parts: ["dr", prediction, bet_str]
    prediction = parts[1]
    bet = float(parts[2])

    # Remove from active (prevent double-click)
    active_dice_roulette.pop((chat_id, user.id), None)

    # Determine prediction label
    if prediction == "low":
        pred_label = "\u2b07\ufe0f Low (1-3)"
    elif prediction == "high":
        pred_label = "\u2b06\ufe0f High (4-6)"
    elif prediction == "odd":
        pred_label = "\U0001f534 Odd (1,3,5)"
    elif prediction == "even":
        pred_label = "\U0001f535 Even (2,4,6)"
    else:
        # Should not happen for this handler, but handle gracefully
        pred_label = prediction

    # Edit the message to show selection, remove buttons
    await query.edit_message_text(
        f"\U0001f3b0 Dice Roulette \u2014 ${bet:.2f}\n\n"
        f"Your prediction: {pred_label}\n\n"
        f"\U0001f3b2 Rolling the dice..."
    )

    # Send actual dice emoji
    await asyncio.sleep(1)
    dice_msg = await context.bot.send_dice(
        chat_id=chat_id,
        emoji="\U0001f3b2",
    )
    result = dice_msg.dice.value  # 1-6

    # Determine win/loss
    won = False
    multiplier = 0.0
    if prediction == "low" and result in (1, 2, 3):
        won = True
        multiplier = DR_LOW_HIGH_MULTI
    elif prediction == "high" and result in (4, 5, 6):
        won = True
        multiplier = DR_LOW_HIGH_MULTI
    elif prediction == "odd" and result in (1, 3, 5):
        won = True
        multiplier = DR_ODD_EVEN_MULTI
    elif prediction == "even" and result in (2, 4, 6):
        won = True
        multiplier = DR_ODD_EVEN_MULTI

    await asyncio.sleep(1)
    if won:
        winnings = round(bet * multiplier, 2)
        store.adjust_balance(user.id, winnings)
        profit = -(winnings - bet)
        store.add_house_profit(profit)
        new_bal = store.get_balance(user.id)
        store.record_match(user.id, "dice_roulette", bet, "win", round(winnings - bet, 2))
        end_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"Dice landed on: {result}\n\n"
                f"You won! ({pred_label} \u2014 {multiplier}x)\n"
                f"Payout: ${winnings:.2f}\n"
                f"Balance: ${new_bal:.2f}"
            ),
            reply_markup=dr_end_keyboard(bet),
        )
    else:
        store.add_house_profit(bet)
        new_bal = store.get_balance(user.id)
        store.record_match(user.id, "dice_roulette", bet, "loss", -bet)
        end_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"Dice landed on: {result}\n\n"
                f"You lost. Prediction: {pred_label}\n"
                f"Balance: ${new_bal:.2f}"
            ),
            reply_markup=dr_end_keyboard(bet),
        )
    _record_owner(chat_id, end_msg.message_id, user.id)


async def dr_replay_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle dice roulette Play Again / Double buttons."""
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id

    # Check button ownership
    owner_id = button_owners.get((chat_id, query.message.message_id))
    if owner_id is not None and user.id != owner_id:
        await query.answer("These buttons aren't for you!", show_alert=True)
        return

    data = query.data  # "dra_10.00" or "drd_10.00"
    parts = data.split("_")
    action = parts[0]  # "dra" or "drd"
    bet = float(parts[1])

    if action == "drd":
        bet = round(bet * 2, 2)

    # Check for active games
    dr_key = (chat_id, user.id)
    if dr_key in active_games or dr_key in active_blackjack or dr_key in active_dice_roulette:
        await query.answer("You already have an active game!", show_alert=True)
        return

    # Enforce bet limits
    if bet < MIN_BET:
        await query.answer(f"Minimum bet is ${MIN_BET:.2f}.", show_alert=True)
        return
    if bet > MAX_BET:
        await query.answer(f"Maximum bet is ${MAX_BET:.2f}.", show_alert=True)
        return

    bal = store.get_balance(user.id)
    if bet > bal:
        await query.answer(f"Insufficient balance! You have ${bal:.2f}.", show_alert=True)
        return

    await query.answer()

    # Deduct bet and record wager
    store.adjust_balance(user.id, -bet)
    store.record_wager(user.id, bet, username=user.first_name)

    await query.edit_message_text(
        f"\U0001f3b0 Dice Roulette \u2014 ${bet:.2f}\n\n"
        f"Predict the dice roll!\n\n"
        f"\u2b07\ufe0f Low (1-3) or \u2b06\ufe0f High (4-6): 1.92x\n"
        f"\U0001f534 Odd (1,3,5) or \U0001f535 Even (2,4,6): 1.92x\n"
        f"Pick 1-5 numbers: 1\u21925.6x  2\u21922.45x  3\u21921.92x  4\u21921.54x  5\u21921.38x\n\n"
        f"Pick your prediction:",
        reply_markup=dr_prediction_keyboard(bet),
    )
    active_dice_roulette[dr_key] = {
        "user_id": user.id,
        "bet": bet,
        "message_id": query.message.message_id,
        "selected_numbers": set(),
    }


async def game_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /dice, /bowl, /football, /darts commands."""
    chat_id = update.effective_chat.id
    user = update.effective_user
    command = update.message.text.split()[0].lstrip("/").lower()

    game_key = (chat_id, user.id)
    if game_key in active_games:
        await update.message.reply_text(
            "\u2620\ufe0f You already have an active game! Finish it first."
        )
        return

    if game_key in active_blackjack:
        await update.message.reply_text(
            "\u2620\ufe0f You already have an active game! Finish it first."
        )
        return

    if game_key in active_dice_roulette:
        await update.message.reply_text(
            "\u2620\ufe0f You already have an active game! Finish it first."
        )
        return

    game_type = GAME_COMMANDS.get(command)
    if game_type is None:
        return

    args = context.args
    if not args:
        m = await update.message.reply_text(
            f"Usage: /{command} <bet amount>\nExample: /{command} 10",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    try:
        bet = float(args[0])
    except ValueError:
        m = await update.message.reply_text(
            "\u2620\ufe0f Bet must be a number.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    if bet < MIN_BET:
        m = await update.message.reply_text(
            f"\u2620\ufe0f Minimum bet is ${MIN_BET:.2f}.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    if bet > MAX_BET:
        m = await update.message.reply_text(
            f"\u2620\ufe0f Maximum bet is ${MAX_BET:.2f}.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    bal = store.get_balance(user.id)
    if bet > bal:
        m = await update.message.reply_text(
            f"\u2620\ufe0f Insufficient balance! You have ${bal:.2f}.\n"
            f"Use /deposit to add funds.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    # Show rounds selection first (1, 2, or 3 rounds)
    game_cmd = GAME_TYPE_TO_CMD[game_type]
    emoji = GAME_EMOJI[game_type]
    m = await update.message.reply_text(
        f"\u2620\ufe0f Toxic Casino \u2014 {game_type.value.title()}\n\n"
        f"\U0001f4b5 Bet: ${bet:.2f}\n\n"
        f"Choose how many rounds to play:",
        reply_markup=rounds_selection_keyboard(game_cmd, bet),
    )
    _record_owner(chat_id, m.message_id, user.id)


async def _emoji_round_eval(
    session: GameSession, chat_id: int, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Evaluate an emoji game round after both sides have rolled.

    Shared logic for bot games and PvP games.
    """
    expected_emoji = GAME_EMOJI[session.game_type]
    player_total = session.player_score_this_round
    bot_total = session.bot_score_this_round

    # Labels
    if session.is_pvp:
        p1_label = "Challenger"
        p2_label = "Opponent"
    else:
        p1_label = "You"
        p2_label = "Bot"

    # Show round totals
    await asyncio.sleep(3)
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"{expected_emoji} Round {session.current_round} Results\n\n"
            f"{p1_label}: {player_total}  |  {p2_label}: {bot_total}"
        ),
    )

    # ---- Determine round winner ----
    player_val = session.player_score_this_round
    bot_val = session.bot_score_this_round

    if player_val == bot_val:
        # Tie — replay this round
        session.player_score_this_round = None
        session.bot_score_this_round = None
        session.player_rolls_this_round = []
        session.bot_rolls_this_round = []
        session.waiting_for_player = True

        rolls = session.rolls_per_round
        rolls_label = f" ({rolls} roll{'s' if rolls > 1 else ''})" if rolls > 1 else ""
        turn_prompt = (
            f"Challenger's turn! Send {expected_emoji} to play."
            if session.is_pvp
            else f"Your turn! Send {expected_emoji} to play."
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"Tie! Round replayed.\n"
                f"\nScore: {p1_label} {session.player_points} - "
                f"{session.bot_points} {p2_label}\n\n"
                f"Round {session.current_round} (replay){rolls_label}\n"
                f"{turn_prompt}"
            ),
        )
        return

    player_wins = (player_val > bot_val) if not session.crazy_mode else (player_val < bot_val)
    if player_wins:
        session.player_points += 1
        round_result = f"{p1_label} win{'s' if session.is_pvp else ''} this round!"
    else:
        session.bot_points += 1
        round_result = f"{p2_label} win{'s' if not session.is_pvp else ''} this round."

    scoreboard = (
        f"\nScore: {p1_label} {session.player_points} - "
        f"{session.bot_points} {p2_label}"
    )

    game_cmd = GAME_TYPE_TO_CMD[session.game_type]
    rolls = session.rolls_per_round
    pts = session.points_to_win
    max_rounds = pts * 2 - 1

    def _cleanup():
        """Remove session from active_games (both keys for PvP)."""
        active_games.pop((chat_id, session.user_id), None)
        if session.is_pvp and session.opponent_id:
            active_games.pop((chat_id, session.opponent_id), None)

    # ---- Check for game end ----

    if session.player_points >= pts:
        if session.is_pvp:
            winnings = round(session.bet * 2, 2)
            store.adjust_balance(session.user_id, winnings)
            p1_bal = store.get_balance(session.user_id)
            p2_bal = store.get_balance(session.opponent_id)
            store.record_match(session.user_id, session.game_type.value, session.bet, "win", session.bet)
            store.record_match(session.opponent_id, session.game_type.value, session.bet, "loss", -session.bet)
            end_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"{round_result}{scoreboard}\n\n"
                    f"\U0001f3c6 Challenger wins!\n"
                    f"Payout: ${winnings:.2f}\n"
                    f"Challenger balance: ${p1_bal:.2f}\n"
                    f"Opponent balance: ${p2_bal:.2f}"
                ),
                reply_markup=back_to_menu_keyboard(),
            )
        else:
            winnings = round(session.bet * EMOJI_GAME_WIN_MULTI, 2)
            store.adjust_balance(session.user_id, winnings)
            profit = -(winnings - session.bet)
            store.add_house_profit(profit)
            new_bal = store.get_balance(session.user_id)
            store.record_match(session.user_id, session.game_type.value, session.bet, "win", round(winnings - session.bet, 2))
            end_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"{round_result}{scoreboard}\n\n"
                    f"You won ${winnings:.2f}!\n"
                    f"Balance: ${new_bal:.2f}"
                ),
                reply_markup=game_end_keyboard(game_cmd, session.bet, rolls, pts),
            )
        _record_owner(chat_id, end_msg.message_id, session.user_id)
        _cleanup()
        return

    if session.bot_points >= pts:
        if session.is_pvp:
            winnings = round(session.bet * 2, 2)
            store.adjust_balance(session.opponent_id, winnings)
            p1_bal = store.get_balance(session.user_id)
            p2_bal = store.get_balance(session.opponent_id)
            store.record_match(session.user_id, session.game_type.value, session.bet, "loss", -session.bet)
            store.record_match(session.opponent_id, session.game_type.value, session.bet, "win", session.bet)
            end_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"{round_result}{scoreboard}\n\n"
                    f"\U0001f3c6 Opponent wins!\n"
                    f"Payout: ${winnings:.2f}\n"
                    f"Challenger balance: ${p1_bal:.2f}\n"
                    f"Opponent balance: ${p2_bal:.2f}"
                ),
                reply_markup=back_to_menu_keyboard(),
            )
        else:
            store.add_house_profit(session.bet)
            new_bal = store.get_balance(session.user_id)
            store.record_match(session.user_id, session.game_type.value, session.bet, "loss", -session.bet)
            end_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"{round_result}{scoreboard}\n\n"
                    f"You lost ${session.bet:.2f}.\n"
                    f"Balance: ${new_bal:.2f}"
                ),
                reply_markup=game_end_keyboard(game_cmd, session.bet, rolls, pts),
            )
        _record_owner(chat_id, end_msg.message_id, session.user_id)
        _cleanup()
        return

    if session.current_round >= max_rounds:
        if session.player_points > session.bot_points:
            if session.is_pvp:
                winnings = round(session.bet * 2, 2)
                store.adjust_balance(session.user_id, winnings)
                p1_bal = store.get_balance(session.user_id)
                p2_bal = store.get_balance(session.opponent_id)
                store.record_match(session.user_id, session.game_type.value, session.bet, "win", session.bet)
                store.record_match(session.opponent_id, session.game_type.value, session.bet, "loss", -session.bet)
                end_msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"{round_result}{scoreboard}\n\n"
                        f"Max rounds reached \u2014 Challenger wins!\n"
                        f"Payout: ${winnings:.2f}\n"
                        f"Challenger balance: ${p1_bal:.2f}\n"
                        f"Opponent balance: ${p2_bal:.2f}"
                    ),
                    reply_markup=back_to_menu_keyboard(),
                )
            else:
                winnings = round(session.bet * EMOJI_GAME_WIN_MULTI, 2)
                store.adjust_balance(session.user_id, winnings)
                profit = -(winnings - session.bet)
                store.add_house_profit(profit)
                new_bal = store.get_balance(session.user_id)
                store.record_match(session.user_id, session.game_type.value, session.bet, "win", round(winnings - session.bet, 2))
                end_msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"{round_result}{scoreboard}\n\n"
                        f"Max rounds reached \u2014 you win!\n"
                        f"Won ${winnings:.2f}\n"
                        f"Balance: ${new_bal:.2f}"
                    ),
                    reply_markup=game_end_keyboard(game_cmd, session.bet, rolls, pts),
                )
        elif session.bot_points > session.player_points:
            if session.is_pvp:
                winnings = round(session.bet * 2, 2)
                store.adjust_balance(session.opponent_id, winnings)
                p1_bal = store.get_balance(session.user_id)
                p2_bal = store.get_balance(session.opponent_id)
                store.record_match(session.user_id, session.game_type.value, session.bet, "loss", -session.bet)
                store.record_match(session.opponent_id, session.game_type.value, session.bet, "win", session.bet)
                end_msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"{round_result}{scoreboard}\n\n"
                        f"Max rounds reached \u2014 Opponent wins!\n"
                        f"Payout: ${winnings:.2f}\n"
                        f"Challenger balance: ${p1_bal:.2f}\n"
                        f"Opponent balance: ${p2_bal:.2f}"
                    ),
                    reply_markup=back_to_menu_keyboard(),
                )
            else:
                store.add_house_profit(session.bet)
                new_bal = store.get_balance(session.user_id)
                store.record_match(session.user_id, session.game_type.value, session.bet, "loss", -session.bet)
                end_msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"{round_result}{scoreboard}\n\n"
                        f"Max rounds reached \u2014 bot wins.\n"
                        f"Balance: ${new_bal:.2f}"
                    ),
                    reply_markup=game_end_keyboard(game_cmd, session.bet, rolls, pts),
                )
        else:
            # Tied at max rounds — keep playing until someone wins
            session.current_round += 1
            session.player_score_this_round = None
            session.bot_score_this_round = None
            session.player_rolls_this_round = []
            session.bot_rolls_this_round = []
            session.waiting_for_player = True

            rolls_label = f" ({rolls} roll{'s' if rolls > 1 else ''})" if rolls > 1 else ""
            turn_prompt = (
                f"Challenger's turn! Send {expected_emoji} to play."
                if session.is_pvp
                else f"Your turn! Send {expected_emoji} to play."
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"{round_result}{scoreboard}\n\n"
                    f"Max rounds reached but it's tied \u2014 playing on!\n"
                    f"Round {session.current_round}{rolls_label}\n"
                    f"{turn_prompt}"
                ),
            )
            return
        _record_owner(chat_id, end_msg.message_id, session.user_id)
        _cleanup()
        return

    # ---- Next round ----
    session.current_round += 1
    session.player_score_this_round = None
    session.bot_score_this_round = None
    session.player_rolls_this_round = []
    session.bot_rolls_this_round = []
    session.waiting_for_player = True

    rolls_label = f" ({rolls} roll{'s' if rolls > 1 else ''})" if rolls > 1 else ""
    turn_prompt = (
        f"Challenger's turn! Send {expected_emoji} to play."
        if session.is_pvp
        else f"Your turn! Send {expected_emoji} to play."
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"{round_result}{scoreboard}\n\n"
            f"Round {session.current_round}{rolls_label}\n"
            f"{turn_prompt}"
        ),
    )


async def handle_dice_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle dice/bowling/football/darts emoji messages (supports multi-roll rounds and PvP)."""
    chat_id = update.effective_chat.id
    user = update.effective_user

    # --- Tournament dice handling (priority over regular games) ---
    if (
        tournament_state["phase"] == "running"
        and chat_id == tournament_state.get("chat_id")
    ):
        dice_msg = update.message.dice
        if dice_msg and dice_msg.emoji == "\U0001f3b2":
            match_idx = tournament_state["current_match_idx"]
            if match_idx < len(tournament_state["bracket"]):
                p1, p2 = tournament_state["bracket"][match_idx]
                if user.id in (p1, p2) and user.id not in tournament_state["match_rolls"]:
                    tournament_state["match_rolls"][user.id] = dice_msg.value
                    if len(tournament_state["match_rolls"]) == 2:
                        await _tournament_evaluate_match(context, chat_id)
                    return

    game_key = (chat_id, user.id)
    if game_key not in active_games:
        return

    session = active_games[game_key]

    # --- Turn validation ---
    if session.is_pvp:
        if session.waiting_for_player and user.id != session.user_id:
            await update.message.reply_text("\u23f3 Wait for your turn!")
            return
        if not session.waiting_for_player and user.id != session.opponent_id:
            await update.message.reply_text("\u23f3 Wait for your turn!")
            return
    elif not session.waiting_for_player:
        await update.message.reply_text("\u23f3 Wait for the bot's turn!")
        return

    dice_msg = update.message.dice
    if dice_msg is None:
        return

    expected_emoji = GAME_EMOJI[session.game_type]
    if dice_msg.emoji != expected_emoji:
        await update.message.reply_text(
            f"Wrong emoji! Send {expected_emoji} for this game."
        )
        return

    # --- PvP: Opponent (player 2) rolling ---
    if session.is_pvp and not session.waiting_for_player:
        session.bot_rolls_this_round.append(dice_msg.value)
        if len(session.bot_rolls_this_round) < session.rolls_per_round:
            return
        bot_total = sum(session.bot_rolls_this_round)
        session.bot_score_this_round = bot_total
        await _emoji_round_eval(session, chat_id, context)
        return

    # --- Player (player 1) rolling ---
    session.player_rolls_this_round.append(dice_msg.value)
    rolls_done = len(session.player_rolls_this_round)
    rolls_needed = session.rolls_per_round

    if rolls_done < rolls_needed:
        return

    # All player rolls done
    session.waiting_for_player = False
    player_total = sum(session.player_rolls_this_round)
    session.player_score_this_round = player_total

    # PvP: prompt opponent to roll
    if session.is_pvp:
        rolls_label = f" ({rolls_needed} roll{'s' if rolls_needed > 1 else ''})" if rolls_needed > 1 else ""
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"Challenger rolled: {player_total}{rolls_label}\n\n"
                f"Opponent's turn! Send {expected_emoji} to play."
            ),
        )
        return

    # Bot rolls (1-second delay before bot sends its emojis)
    await asyncio.sleep(1)
    for i in range(rolls_needed):
        bot_msg = await context.bot.send_dice(
            chat_id=chat_id,
            emoji=expected_emoji,
        )
        session.bot_rolls_this_round.append(bot_msg.dice.value)

    bot_total = sum(session.bot_rolls_this_round)
    session.bot_score_this_round = bot_total

    await _emoji_round_eval(session, chat_id, context)


# ---------------------------------------------------------------------------
# Replay (Play Again / Double & Play) Handler
# ---------------------------------------------------------------------------

async def replay_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle Play Again / Double & Play button presses."""
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id

    # Check button ownership
    owner_id = button_owners.get((chat_id, query.message.message_id))
    if owner_id is not None and user.id != owner_id:
        await query.answer("These buttons aren't for you!", show_alert=True)
        return

    data = query.data  # e.g. "ra_bj_10.00" or "rd_dice_5.00_2_3"
    parts = data.split("_")

    # Format: ra_bj_10.00 (3 parts) or ra_dice_10.00_2_3 (5 parts with rolls+rounds)
    if len(parts) < 3:
        await query.answer("Invalid action.", show_alert=True)
        return

    action_type = parts[0]  # "ra" (play again) or "rd" (double & play)
    game_key = parts[1]     # "bj", "dice", "bowl", "soccer", "darts"

    try:
        original_bet = float(parts[2])
    except ValueError:
        await query.answer("Invalid bet.", show_alert=True)
        return

    # Parse rolls_per_round for emoji games (4th part if present)
    rolls = 1
    if len(parts) >= 4:
        try:
            rolls = int(parts[3])
        except ValueError:
            rolls = 1
    if rolls not in (1, 2):
        rolls = 1

    # Parse rounds / points_to_win for emoji games (5th part if present)
    rounds = 3
    if len(parts) >= 5:
        try:
            rounds = int(parts[4])
        except ValueError:
            rounds = 3
    if rounds not in (1, 2, 3):
        rounds = 3

    bet = original_bet if action_type == "ra" else original_bet * 2

    # Check for active games
    replay_key = (chat_id, user.id)
    if replay_key in active_blackjack:
        await query.answer("You already have an active game!", show_alert=True)
        return
    if replay_key in active_games:
        await query.answer("You already have an active game!", show_alert=True)
        return

    # Enforce bet limits
    if bet < MIN_BET:
        await query.answer(f"Minimum bet is ${MIN_BET:.2f}.", show_alert=True)
        return
    if bet > MAX_BET:
        await query.answer(f"Maximum bet is ${MAX_BET:.2f}.", show_alert=True)
        return

    # Check balance
    bal = store.get_balance(user.id)
    if bet > bal:
        await query.answer(
            f"Insufficient balance! You have ${bal:.2f}.", show_alert=True
        )
        return

    await query.answer()

    if game_key == "bj":
        # ---- Start a new blackjack game ----
        store.adjust_balance(user.id, -bet)
        store.record_wager(user.id, bet, username=user.first_name)

        deck = bj_create_deck()
        player_hand = [deck.pop(), deck.pop()]
        dealer_hand = [deck.pop(), deck.pop()]

        session = BlackjackSession(
            chat_id=chat_id,
            user_id=user.id,
            message_id=0,
            deck=deck,
            player_hands=[player_hand],
            dealer_hand=dealer_hand,
            bets=[bet],
            hand_states=["playing"],
            original_bet=bet,
        )

        player_val = bj_hand_value(player_hand)
        dealer_val = bj_hand_value(dealer_hand)

        if player_val == 21 or dealer_val == 21:
            if player_val == 21:
                session.hand_states[0] = "blackjack"
            session.finished = True
            text = bj_render_table(session, reveal_dealer=True)
            text += "\n\n" + bj_settle(session)
            await query.edit_message_text(
                text, reply_markup=game_end_keyboard("bj", bet)
            )
            return

        active_blackjack[replay_key] = session
        text = bj_render_table(session)
        await query.edit_message_text(
            text, reply_markup=bj_action_keyboard(session)
        )
        session.message_id = query.message.message_id

    else:
        # ---- Start a new dice/emoji game ----
        game_type = GAME_COMMANDS.get(game_key)
        if game_type is None:
            return

        store.adjust_balance(user.id, -bet)
        store.record_wager(user.id, bet, username=user.first_name)

        session = GameSession(
            chat_id=chat_id,
            user_id=user.id,
            game_type=game_type,
            bet=bet,
            rolls_per_round=rolls,
            points_to_win=rounds,
        )
        active_games[replay_key] = session

        emoji = GAME_EMOJI[game_type]
        rolls_label = f"{rolls} roll{'s' if rolls > 1 else ''}"
        rounds_label = f"{rounds} round{'s' if rounds > 1 else ''}"
        await query.edit_message_text(
            f"\u2620\ufe0f Toxic Casino \u2014 {game_type.value.title()}\n\n"
            f"\U0001f4b5 Bet: ${bet:.2f}\n"
            f"\U0001f3c6 {rounds_label} (first to {rounds} points wins)\n"
            f"\U0001f3b2 Mode: {rolls_label} per round\n\n"
            f"\U0001f4cc Round {session.current_round}\n"
            f"Your turn! Send {emoji} to play."
        )


# ---------------------------------------------------------------------------
# Tournament System
# ---------------------------------------------------------------------------


def _tournament_reset() -> None:
    """Reset tournament state to idle."""
    tournament_state.update({
        "phase": "idle",
        "chat_id": None,
        "participants": [],
        "bracket": [],
        "current_match_idx": 0,
        "round_num": 1,
        "winners": [],
        "match_rolls": {},
        "eliminated": [],
        "announcement_msg_id": None,
        "semifinal_losers": [],
        "is_third_place_match": False,
        "final_winner": None,
        "final_runner_up": None,
    })


def _tournament_get_name(user_id: int) -> str:
    """Get display name for a tournament participant."""
    for pid, name in tournament_state["participants"]:
        if pid == user_id:
            return name
    return f"Player {user_id}"


def _tournament_build_bracket(player_ids: list) -> list:
    """Build bracket from player IDs. Returns list of (p1, p2) tuples. None = bye."""
    ids = list(player_ids)
    random.shuffle(ids)
    bracket = []
    i = 0
    while i < len(ids):
        p1 = ids[i]
        if i + 1 < len(ids):
            p2 = ids[i + 1]
            bracket.append((p1, p2))
            i += 2
        else:
            bracket.append((p1, None))  # bye
            i += 1
    return bracket


async def tournament_announcement_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: announce tournament 1 hour before start (11 AM CST Thursday)."""
    if tournament_chat_id is None:
        return
    if tournament_state["phase"] != "idle":
        return

    tournament_state["phase"] = "joining"
    tournament_state["chat_id"] = tournament_chat_id
    tournament_state["participants"] = []

    msg = await context.bot.send_message(
        chat_id=tournament_chat_id,
        text=(
            "\U0001f3c6 Weekly Tournament!\n\n"
            "The tournament starts in 1 hour at 12:00 PM CST!\n\n"
            "\U0001f3b2 Game: Dice PvP (1 roll, 1 round per match)\n"
            "\U0001f3c5 Prizes:\n"
            "  1st Place: $10.00\n"
            "  2nd Place: $5.00\n"
            "  3rd Place: $2.50\n\n"
            "\u26a0\ufe0f Minimum $200 wagered this week to join!\n\n"
            "Players joined: 0"
        ),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "\U0001f3c6 Join Tournament!",
                callback_data="tournament_join",
            )],
        ]),
    )
    tournament_state["announcement_msg_id"] = msg.message_id


async def tournament_start_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: start tournament at 12 PM CST Thursday."""
    if tournament_state["phase"] != "joining":
        return

    chat_id = tournament_state["chat_id"]
    participants = tournament_state["participants"]

    if len(participants) < 2:
        await context.bot.send_message(
            chat_id=chat_id,
            text="\U0001f3c6 Tournament cancelled \u2014 not enough players (minimum 2).",
        )
        _tournament_reset()
        return

    tournament_state["phase"] = "running"
    tournament_state["round_num"] = 1

    player_ids = [p[0] for p in participants]
    tournament_state["bracket"] = _tournament_build_bracket(player_ids)
    tournament_state["current_match_idx"] = 0
    tournament_state["winners"] = []
    tournament_state["match_rolls"] = {}
    tournament_state["eliminated"] = []
    tournament_state["semifinal_losers"] = []

    names = [p[1] for p in participants]
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"\U0001f3c6 Tournament Started! ({len(participants)} players)\n\n"
            f"Players: {', '.join(names)}\n\n"
            f"Round 1 \u2014 Let's go! \U0001f3b2"
        ),
    )

    await _tournament_start_next_match(context, chat_id)


async def tournament_join_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle a player pressing the tournament join button."""
    query = update.callback_query
    user = query.from_user

    if tournament_state["phase"] != "joining":
        await query.answer("Tournament is not accepting players right now.", show_alert=True)
        return

    for pid, _ in tournament_state["participants"]:
        if pid == user.id:
            await query.answer("You already joined!", show_alert=True)
            return

    info = store.get_weekly_info(user.id)
    if info["wagered"] < 200:
        await query.answer(
            f"You need $200 wagered this week to join. Current: ${info['wagered']:.2f}",
            show_alert=True,
        )
        return

    tournament_state["participants"].append((user.id, user.first_name))
    await query.answer("You joined the tournament!")

    count = len(tournament_state["participants"])
    names = [p[1] for p in tournament_state["participants"]]
    try:
        await query.edit_message_text(
            f"\U0001f3c6 Weekly Tournament!\n\n"
            f"The tournament starts at 12:00 PM CST!\n\n"
            f"\U0001f3b2 Game: Dice PvP (1 roll, 1 round per match)\n"
            f"\U0001f3c5 Prizes:\n"
            f"  1st Place: $10.00\n"
            f"  2nd Place: $5.00\n"
            f"  3rd Place: $2.50\n\n"
            f"\u26a0\ufe0f Minimum $200 wagered this week to join!\n\n"
            f"Players joined: {count}\n"
            f"{', '.join(names)}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "\U0001f3c6 Join Tournament!",
                    callback_data="tournament_join",
                )],
            ]),
        )
    except Exception:
        pass


async def _tournament_start_next_match(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int,
) -> None:
    """Start the next match in the current bracket round, skipping byes."""
    bracket = tournament_state["bracket"]
    idx = tournament_state["current_match_idx"]

    # Auto-advance byes
    while idx < len(bracket):
        p1, p2 = bracket[idx]
        if p2 is None:
            tournament_state["winners"].append(p1)
            p1_name = _tournament_get_name(p1)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"\U0001f3b2 {p1_name} gets a bye and advances! \u2705",
            )
            idx += 1
            tournament_state["current_match_idx"] = idx
            continue
        break

    if idx >= len(bracket):
        # All matches in this round are done
        await _tournament_advance_round(context, chat_id)
        return

    tournament_state["current_match_idx"] = idx
    tournament_state["match_rolls"] = {}

    p1, p2 = bracket[idx]
    p1_name = _tournament_get_name(p1)
    p2_name = _tournament_get_name(p2)

    if tournament_state["is_third_place_match"]:
        round_label = "\U0001f949 3rd Place Match"
    else:
        total_matches = sum(1 for _, b in bracket if b is not None)
        match_num = sum(1 for i in range(idx + 1) if bracket[i][1] is not None)
        remaining = len(tournament_state["winners"]) + (len(bracket) - idx)
        if remaining == 2 or (len(bracket) == 1 and not tournament_state["is_third_place_match"]):
            round_label = "\U0001f3c6 FINALS"
        else:
            round_label = f"Round {tournament_state['round_num']} \u2014 Match {match_num}/{total_matches}"

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"\u2694\ufe0f {round_label}\n\n"
            f"\U0001f3b2 {p1_name} vs {p2_name}\n\n"
            f"Both players send \U0001f3b2 to roll!"
        ),
    )


async def _tournament_evaluate_match(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int,
) -> None:
    """Evaluate the current tournament match after both players rolled."""
    rolls = tournament_state["match_rolls"]
    bracket = tournament_state["bracket"]
    idx = tournament_state["current_match_idx"]
    p1, p2 = bracket[idx]

    r1 = rolls[p1]
    r2 = rolls[p2]

    p1_name = _tournament_get_name(p1)
    p2_name = _tournament_get_name(p2)

    await asyncio.sleep(1)

    if r1 == r2:
        tournament_state["match_rolls"] = {}
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"\U0001f3b2 {p1_name}: {r1} vs {p2_name}: {r2}\n\n"
                f"Tie! Both players roll again! \U0001f3b2"
            ),
        )
        return

    if r1 > r2:
        winner, loser = p1, p2
        winner_name = p1_name
    else:
        winner, loser = p2, p1
        winner_name = p2_name

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"\U0001f3b2 {p1_name}: {r1} vs {p2_name}: {r2}\n\n"
            f"\U0001f3c6 {winner_name} wins!"
        ),
    )

    if tournament_state["is_third_place_match"]:
        # 3rd place match done — award all prizes
        first = tournament_state["final_winner"]
        second = tournament_state["final_runner_up"]
        await _tournament_award_prizes(context, chat_id, first, second, winner)
        return

    tournament_state["winners"].append(winner)
    tournament_state["eliminated"].append(loser)

    tournament_state["current_match_idx"] += 1
    await _tournament_start_next_match(context, chat_id)


async def _tournament_advance_round(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int,
) -> None:
    """Advance to the next round after all matches in the current round are done."""
    winners = tournament_state["winners"]

    if len(winners) <= 1:
        # Tournament final just finished
        winner = winners[0] if winners else None
        runner_up = tournament_state["eliminated"][-1] if tournament_state["eliminated"] else None

        semi_losers = tournament_state["semifinal_losers"]
        if len(semi_losers) >= 2:
            # Play 3rd place match
            tournament_state["final_winner"] = winner
            tournament_state["final_runner_up"] = runner_up
            tournament_state["is_third_place_match"] = True
            tournament_state["bracket"] = [(semi_losers[0], semi_losers[1])]
            tournament_state["current_match_idx"] = 0
            tournament_state["winners"] = []
            tournament_state["match_rolls"] = {}

            await context.bot.send_message(
                chat_id=chat_id,
                text="\U0001f949 Now for the 3rd Place Match!",
            )
            await _tournament_start_next_match(context, chat_id)
            return
        elif len(semi_losers) == 1:
            third = semi_losers[0]
        else:
            third = None

        await _tournament_award_prizes(context, chat_id, winner, runner_up, third)
        return

    # Determine semifinal losers: if next round is the final (2 winners),
    # the losers from THIS round are the semifinal losers.
    if len(winners) == 2:
        all_in_round: set = set()
        for p1, p2 in tournament_state["bracket"]:
            if p1 is not None:
                all_in_round.add(p1)
            if p2 is not None:
                all_in_round.add(p2)
        losers = [p for p in all_in_round if p not in set(winners)]
        tournament_state["semifinal_losers"] = losers

    # Set up next round
    tournament_state["round_num"] += 1
    tournament_state["bracket"] = _tournament_build_bracket(winners)
    tournament_state["current_match_idx"] = 0
    tournament_state["winners"] = []
    tournament_state["match_rolls"] = {}

    remaining = len(winners)
    label = "\U0001f3c6 FINALS" if remaining == 2 else f"Round {tournament_state['round_num']}"
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"{label}! ({remaining} players remaining)",
    )

    await _tournament_start_next_match(context, chat_id)


async def _tournament_award_prizes(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    first: Optional[int],
    second: Optional[int],
    third: Optional[int],
) -> None:
    """Award prizes and announce final results."""
    results = "\U0001f3c6 Tournament Results!\n\n"

    if first is not None:
        name = _tournament_get_name(first)
        store.adjust_balance(first, 10.0)
        results += f"\U0001f947 1st Place: {name} \u2014 $10.00\n"

    if second is not None:
        name = _tournament_get_name(second)
        store.adjust_balance(second, 5.0)
        results += f"\U0001f948 2nd Place: {name} \u2014 $5.00\n"

    if third is not None:
        name = _tournament_get_name(third)
        store.adjust_balance(third, 2.50)
        results += f"\U0001f949 3rd Place: {name} \u2014 $2.50\n"

    results += "\nCongratulations to the winners! \U0001f389"

    await context.bot.send_message(chat_id=chat_id, text=results)
    _tournament_reset()


async def settournament_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Owner command: set the current chat as the tournament chat."""
    global tournament_chat_id
    user = update.effective_user
    if user.id != OWNER_ID:
        return

    tournament_chat_id = update.effective_chat.id
    store.set_tournament_chat_id(tournament_chat_id)
    await update.message.reply_text(
        f"\u2705 Tournament chat set to this group (ID: {tournament_chat_id})."
    )


# ---------------------------------------------------------------------------
# Mines Game (/mines)
# ---------------------------------------------------------------------------


def _mines_grid_keyboard(session: dict) -> InlineKeyboardMarkup:
    """Build the 5x5 mines grid as inline buttons."""
    revealed = session["revealed"]
    mines_set = session["mines_set"]
    grid_size = 5
    rows = []
    for r in range(grid_size):
        row_buttons = []
        for c in range(grid_size):
            idx = r * grid_size + c
            if idx in revealed:
                if idx in mines_set:
                    label = "\U0001f4a3"  # bomb
                else:
                    label = "\U0001f48e"  # gem (safe)
                row_buttons.append(
                    InlineKeyboardButton(label, callback_data=f"mines_noop_{idx}")
                )
            else:
                if session.get("game_over"):
                    # Reveal all mines after game over
                    if idx in mines_set:
                        label = "\U0001f4a3"
                    else:
                        label = "\u2b1c"  # white square
                    row_buttons.append(
                        InlineKeyboardButton(label, callback_data=f"mines_noop_{idx}")
                    )
                else:
                    row_buttons.append(
                        InlineKeyboardButton("\u2b1c", callback_data=f"mines_pick_{idx}")
                    )
        rows.append(row_buttons)

    # Cashout button (only if at least one tile revealed and game not over)
    if revealed and not session.get("game_over"):
        rows.append([
            InlineKeyboardButton(
                f"\U0001f4b0 Cash Out (${session['current_payout']:.2f})",
                callback_data="mines_cashout",
            )
        ])
    elif session.get("game_over"):
        bet = session["bet"]
        bet_str = f"{bet:.2f}"
        rows.append([
            InlineKeyboardButton("(Play Again)", callback_data=f"mines_again_{bet_str}_{session['mine_count']}"),
            InlineKeyboardButton("\u23eb Double", callback_data=f"mines_double_{bet_str}_{session['mine_count']}"),
        ])
        rows.append([
            InlineKeyboardButton("\u00ab Back to Menu", callback_data="menu_main"),
        ])

    return InlineKeyboardMarkup(rows)


def _mines_header(session: dict) -> str:
    """Build the mines game header text."""
    safe_count = len(session["revealed"])
    mine_count = session["mine_count"]
    bet = session["bet"]
    multiplier = session["current_multi"]
    payout = session["current_payout"]
    return (
        f"\U0001f4a3 Toxic Casino \u2014 Mines\n\n"
        f"\U0001f4b5 Bet: ${bet:.2f}\n"
        f"\U0001f4a3 Mines: {mine_count}\n"
        f"\U0001f48e Safe tiles revealed: {safe_count}\n"
        f"\U0001f4c8 Multiplier: {multiplier:.2f}x\n"
        f"\U0001f4b0 Current payout: ${payout:.2f}"
    )


async def mines_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /mines <bet> <mine_count>."""
    chat_id = update.effective_chat.id
    user = update.effective_user

    game_key = (chat_id, user.id)
    if game_key in active_mines:
        await update.message.reply_text(
            "\u2620\ufe0f You already have an active mines game! Finish it first."
        )
        return
    if game_key in active_games or game_key in active_blackjack or game_key in active_dice_roulette:
        await update.message.reply_text(
            "\u2620\ufe0f You already have an active game! Finish it first."
        )
        return

    args = context.args
    if not args or len(args) < 2:
        m = await update.message.reply_text(
            "\U0001f4a3 Mines\n\n"
            "Usage: /mines <bet> <mines_count>\n"
            "Example: /mines 10 5\n\n"
            "Mines: 1-24 (more mines = higher multiplier per reveal)",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    try:
        bet = float(args[0])
        mine_count = int(args[1])
    except ValueError:
        m = await update.message.reply_text(
            "\u2620\ufe0f Bet must be a number and mines must be a whole number.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    if bet < MIN_BET:
        m = await update.message.reply_text(
            f"\u2620\ufe0f Minimum bet is ${MIN_BET:.2f}.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return
    if bet > MAX_BET:
        m = await update.message.reply_text(
            f"\u2620\ufe0f Maximum bet is ${MAX_BET:.2f}.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    if mine_count < 1 or mine_count > 24:
        m = await update.message.reply_text(
            "\u2620\ufe0f Mine count must be between 1 and 24.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    bal = store.get_balance(user.id)
    if bet > bal:
        m = await update.message.reply_text(
            f"\u2620\ufe0f Insufficient balance! You have ${bal:.2f}.\n"
            f"Use /deposit to add funds.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    # Deduct bet
    store.adjust_balance(user.id, -bet)
    store.record_wager(user.id, bet, username=user.first_name)

    # Place mines randomly on a 5x5 grid (25 positions, indices 0-24)
    all_positions = list(range(25))
    mines_set = set(random.sample(all_positions, mine_count))

    session = {
        "user_id": user.id,
        "bet": bet,
        "mine_count": mine_count,
        "mines_set": mines_set,
        "revealed": set(),
        "current_multi": 1.0,
        "current_payout": bet,
        "game_over": False,
        "won": False,
    }
    active_mines[game_key] = session

    m = await update.message.reply_text(
        _mines_header(session) + "\n\nTap a tile to reveal it!",
        reply_markup=_mines_grid_keyboard(session),
    )
    _record_owner(chat_id, m.message_id, user.id)


async def mines_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle mines grid button presses."""
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id
    data = query.data

    # Check button ownership
    owner_id = button_owners.get((chat_id, query.message.message_id))
    if owner_id is not None and user.id != owner_id:
        await query.answer("These buttons aren't for you!", show_alert=True)
        return

    game_key = (chat_id, user.id)

    # Handle noop (already revealed tiles)
    if data.startswith("mines_noop_"):
        await query.answer()
        return

    # Handle play again / double
    if data.startswith("mines_again_") or data.startswith("mines_double_"):
        parts = data.split("_")
        bet = float(parts[2])
        mine_count = int(parts[3])
        if data.startswith("mines_double_"):
            bet = round(bet * 2, 2)

        if bet < MIN_BET:
            await query.answer(f"Minimum bet is ${MIN_BET:.2f}.", show_alert=True)
            return
        if bet > MAX_BET:
            await query.answer(f"Maximum bet is ${MAX_BET:.2f}.", show_alert=True)
            return

        if game_key in active_mines:
            await query.answer("You already have an active mines game!", show_alert=True)
            return

        bal = store.get_balance(user.id)
        if bet > bal:
            await query.answer(f"Insufficient balance! You have ${bal:.2f}.", show_alert=True)
            return

        await query.answer()

        store.adjust_balance(user.id, -bet)
        store.record_wager(user.id, bet, username=user.first_name)

        all_positions = list(range(25))
        mines_set = set(random.sample(all_positions, mine_count))

        session = {
            "user_id": user.id,
            "bet": bet,
            "mine_count": mine_count,
            "mines_set": mines_set,
            "revealed": set(),
            "current_multi": 1.0,
            "current_payout": bet,
            "game_over": False,
            "won": False,
        }
        active_mines[game_key] = session

        await query.edit_message_text(
            _mines_header(session) + "\n\nTap a tile to reveal it!",
            reply_markup=_mines_grid_keyboard(session),
        )
        return

    # Handle cashout
    if data == "mines_cashout":
        if game_key not in active_mines:
            await query.answer("No active mines game.", show_alert=True)
            return

        session = active_mines.pop(game_key)
        if session.get("game_over"):
            await query.answer("Game already ended.", show_alert=True)
            return

        await query.answer()

        payout = session["current_payout"]
        store.adjust_balance(user.id, payout)
        profit = -(payout - session["bet"])
        store.add_house_profit(profit)
        new_bal = store.get_balance(user.id)
        store.record_match(user.id, "mines", session["bet"], "win", round(payout - session["bet"], 2))

        session["game_over"] = True
        session["won"] = True

        await query.edit_message_text(
            _mines_header(session) + f"\n\n\U0001f4b0 Cashed out!\n"
            f"Payout: ${payout:.2f}\n"
            f"Balance: ${new_bal:.2f}",
            reply_markup=_mines_grid_keyboard(session),
        )
        return

    # Handle tile pick
    if data.startswith("mines_pick_"):
        if game_key not in active_mines:
            await query.answer("No active mines game.", show_alert=True)
            return

        session = active_mines[game_key]
        if session.get("game_over"):
            await query.answer("Game already ended.", show_alert=True)
            return

        idx = int(data.split("_")[2])
        if idx in session["revealed"]:
            await query.answer()
            return

        await query.answer()

        session["revealed"].add(idx)

        if idx in session["mines_set"]:
            # HIT A MINE — game over, lose bet
            session["game_over"] = True
            session["won"] = False
            active_mines.pop(game_key, None)

            store.add_house_profit(session["bet"])
            new_bal = store.get_balance(user.id)
            store.record_match(user.id, "mines", session["bet"], "loss", -session["bet"])

            await query.edit_message_text(
                _mines_header(session) + f"\n\n\U0001f4a5 BOOM! You hit a mine!\n"
                f"Lost: ${session['bet']:.2f}\n"
                f"Balance: ${new_bal:.2f}",
                reply_markup=_mines_grid_keyboard(session),
            )
        else:
            # Safe tile — increase multiplier
            multi_per_reveal = MINES_MULTIPLIERS.get(session["mine_count"], 1.10)
            session["current_multi"] = round(session["current_multi"] * multi_per_reveal, 2)
            session["current_payout"] = round(session["bet"] * session["current_multi"], 2)

            safe_total = 25 - session["mine_count"]
            revealed_safe = len(session["revealed"])

            if revealed_safe >= safe_total:
                # All safe tiles revealed — auto cashout
                session["game_over"] = True
                session["won"] = True
                active_mines.pop(game_key, None)

                payout = session["current_payout"]
                store.adjust_balance(user.id, payout)
                profit = -(payout - session["bet"])
                store.add_house_profit(profit)
                new_bal = store.get_balance(user.id)
                store.record_match(user.id, "mines", session["bet"], "win", round(payout - session["bet"], 2))

                await query.edit_message_text(
                    _mines_header(session) + f"\n\n\U0001f389 All safe tiles revealed!\n"
                    f"Payout: ${payout:.2f}\n"
                    f"Balance: ${new_bal:.2f}",
                    reply_markup=_mines_grid_keyboard(session),
                )
            else:
                await query.edit_message_text(
                    _mines_header(session) + "\n\nTap another tile or cash out!",
                    reply_markup=_mines_grid_keyboard(session),
                )


# ---------------------------------------------------------------------------
# Monkey Tower Game (/tower)
# ---------------------------------------------------------------------------


def _tower_grid_keyboard(session: dict) -> InlineKeyboardMarkup:
    """Build the tower grid as inline buttons (8 rows, 3 columns)."""
    current_row = session["current_row"]
    rows_data = session["rows"]  # list of 8 rows, each row = index of banana (0,1,2)
    game_over = session.get("game_over", False)
    rows = []

    # Display rows from top (row 7) to bottom (row 0)
    for r in range(7, -1, -1):
        row_buttons = []
        banana_pos = rows_data[r]

        if r < current_row:
            # Already passed — show results
            for c in range(3):
                if c == banana_pos:
                    row_buttons.append(
                        InlineKeyboardButton("\U0001f34c", callback_data=f"tower_noop_{r}_{c}")
                    )
                else:
                    row_buttons.append(
                        InlineKeyboardButton("\U0001f480", callback_data=f"tower_noop_{r}_{c}")
                    )
        elif r == current_row and not game_over:
            # Current row to pick
            for c in range(3):
                row_buttons.append(
                    InlineKeyboardButton(f"\u2753", callback_data=f"tower_pick_{r}_{c}")
                )
        elif r == current_row and game_over:
            # Game over on this row — reveal all
            for c in range(3):
                if c == banana_pos:
                    row_buttons.append(
                        InlineKeyboardButton("\U0001f34c", callback_data=f"tower_noop_{r}_{c}")
                    )
                else:
                    picked_col = session.get("last_pick_col")
                    if c == picked_col:
                        row_buttons.append(
                            InlineKeyboardButton("\U0001f4a5", callback_data=f"tower_noop_{r}_{c}")
                        )
                    else:
                        row_buttons.append(
                            InlineKeyboardButton("\U0001f480", callback_data=f"tower_noop_{r}_{c}")
                        )
        else:
            # Future rows — hidden
            for c in range(3):
                row_buttons.append(
                    InlineKeyboardButton("\u2b1c", callback_data=f"tower_noop_{r}_{c}")
                )
        # Add row label
        multi = TOWER_MULTIPLIERS[r] if r < len(TOWER_MULTIPLIERS) else 0
        row_buttons.append(
            InlineKeyboardButton(f"{multi:.2f}x", callback_data=f"tower_noop_label_{r}")
        )
        rows.append(row_buttons)

    # Cashout button (only if at least one row passed and game not over)
    if current_row > 0 and not game_over:
        payout = session["current_payout"]
        rows.append([
            InlineKeyboardButton(
                f"\U0001f4b0 Cash Out (${payout:.2f})",
                callback_data="tower_cashout",
            )
        ])
    elif game_over:
        bet = session["bet"]
        bet_str = f"{bet:.2f}"
        rows.append([
            InlineKeyboardButton("(Play Again)", callback_data=f"tower_again_{bet_str}"),
            InlineKeyboardButton("\u23eb Double", callback_data=f"tower_double_{bet_str}"),
        ])
        rows.append([
            InlineKeyboardButton("\u00ab Back to Menu", callback_data="menu_main"),
        ])

    return InlineKeyboardMarkup(rows)


def _tower_header(session: dict) -> str:
    """Build the tower game header text."""
    bet = session["bet"]
    current_row = session["current_row"]
    if current_row > 0:
        multi = TOWER_MULTIPLIERS[current_row - 1]
        payout = session["current_payout"]
    else:
        multi = 0
        payout = 0
    return (
        f"\U0001f435 Toxic Casino \u2014 Monkey Tower\n\n"
        f"\U0001f4b5 Bet: ${bet:.2f}\n"
        f"\U0001f3d7\ufe0f Floor: {current_row}/8\n"
        f"\U0001f4c8 Multiplier: {multi:.2f}x\n"
        f"\U0001f4b0 Current payout: ${payout:.2f}"
    )


async def tower_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /tower <bet>."""
    chat_id = update.effective_chat.id
    user = update.effective_user

    game_key = (chat_id, user.id)
    if game_key in active_towers:
        await update.message.reply_text(
            "\u2620\ufe0f You already have an active tower game! Finish it first."
        )
        return
    if game_key in active_games or game_key in active_blackjack or game_key in active_dice_roulette or game_key in active_mines:
        await update.message.reply_text(
            "\u2620\ufe0f You already have an active game! Finish it first."
        )
        return

    args = context.args
    if not args:
        m = await update.message.reply_text(
            "\U0001f435 Monkey Tower\n\n"
            "Usage: /tower <bet>\n"
            "Example: /tower 10\n\n"
            "Climb 8 floors! Each floor has 3 doors \u2014\n"
            "1 banana (safe) and 2 traps.\n"
            "Cash out anytime or reach the top!",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    try:
        bet = float(args[0])
    except ValueError:
        m = await update.message.reply_text(
            "\u2620\ufe0f Bet must be a number.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    if bet < MIN_BET:
        m = await update.message.reply_text(
            f"\u2620\ufe0f Minimum bet is ${MIN_BET:.2f}.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return
    if bet > MAX_BET:
        m = await update.message.reply_text(
            f"\u2620\ufe0f Maximum bet is ${MAX_BET:.2f}.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    bal = store.get_balance(user.id)
    if bet > bal:
        m = await update.message.reply_text(
            f"\u2620\ufe0f Insufficient balance! You have ${bal:.2f}.\n"
            f"Use /deposit to add funds.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    # Deduct bet
    store.adjust_balance(user.id, -bet)
    store.record_wager(user.id, bet, username=user.first_name)

    # Generate 8 rows with banana position (0, 1, or 2)
    rows_data = [random.randint(0, 2) for _ in range(8)]

    session = {
        "user_id": user.id,
        "bet": bet,
        "rows": rows_data,
        "current_row": 0,  # next row to pick (0 = bottom, 7 = top)
        "current_payout": 0.0,
        "game_over": False,
        "won": False,
    }
    active_towers[game_key] = session

    m = await update.message.reply_text(
        _tower_header(session) + "\n\nPick a column on the bottom row! \U0001f34c = safe, \U0001f480 = trap",
        reply_markup=_tower_grid_keyboard(session),
    )
    _record_owner(chat_id, m.message_id, user.id)


async def tower_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle tower game button presses."""
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id
    data = query.data

    owner_id = button_owners.get((chat_id, query.message.message_id))
    if owner_id is not None and user.id != owner_id:
        await query.answer("These buttons aren't for you!", show_alert=True)
        return

    game_key = (chat_id, user.id)

    if data.startswith("tower_noop_"):
        await query.answer()
        return

    # Handle play again / double
    if data.startswith("tower_again_") or data.startswith("tower_double_"):
        parts = data.split("_")
        bet = float(parts[2])
        if data.startswith("tower_double_"):
            bet = round(bet * 2, 2)

        if bet < MIN_BET:
            await query.answer(f"Minimum bet is ${MIN_BET:.2f}.", show_alert=True)
            return
        if bet > MAX_BET:
            await query.answer(f"Maximum bet is ${MAX_BET:.2f}.", show_alert=True)
            return
        if game_key in active_towers:
            await query.answer("You already have an active tower game!", show_alert=True)
            return
        bal = store.get_balance(user.id)
        if bet > bal:
            await query.answer(f"Insufficient balance! You have ${bal:.2f}.", show_alert=True)
            return

        await query.answer()
        store.adjust_balance(user.id, -bet)
        store.record_wager(user.id, bet, username=user.first_name)

        rows_data = [random.randint(0, 2) for _ in range(8)]
        session = {
            "user_id": user.id,
            "bet": bet,
            "rows": rows_data,
            "current_row": 0,
            "current_payout": 0.0,
            "game_over": False,
            "won": False,
        }
        active_towers[game_key] = session

        await query.edit_message_text(
            _tower_header(session) + "\n\nPick a column on the bottom row! \U0001f34c = safe, \U0001f480 = trap",
            reply_markup=_tower_grid_keyboard(session),
        )
        return

    # Handle cashout
    if data == "tower_cashout":
        if game_key not in active_towers:
            await query.answer("No active tower game.", show_alert=True)
            return

        session = active_towers.pop(game_key)
        if session.get("game_over"):
            await query.answer("Game already ended.", show_alert=True)
            return

        await query.answer()

        payout = session["current_payout"]
        store.adjust_balance(user.id, payout)
        profit = -(payout - session["bet"])
        store.add_house_profit(profit)
        new_bal = store.get_balance(user.id)
        store.record_match(user.id, "tower", session["bet"], "win", round(payout - session["bet"], 2))

        session["game_over"] = True
        session["won"] = True

        await query.edit_message_text(
            _tower_header(session) + f"\n\n\U0001f4b0 Cashed out!\n"
            f"Payout: ${payout:.2f}\n"
            f"Balance: ${new_bal:.2f}",
            reply_markup=_tower_grid_keyboard(session),
        )
        return

    # Handle tile pick
    if data.startswith("tower_pick_"):
        if game_key not in active_towers:
            await query.answer("No active tower game.", show_alert=True)
            return

        session = active_towers[game_key]
        if session.get("game_over"):
            await query.answer("Game already ended.", show_alert=True)
            return

        parts = data.split("_")
        row = int(parts[2])
        col = int(parts[3])

        if row != session["current_row"]:
            await query.answer()
            return

        await query.answer()

        banana_pos = session["rows"][row]

        if col == banana_pos:
            # Safe! Move up
            session["current_row"] = row + 1
            multi = TOWER_MULTIPLIERS[row]
            session["current_payout"] = round(session["bet"] * multi, 2)

            if session["current_row"] >= 8:
                # Reached the top — auto cashout
                session["game_over"] = True
                session["won"] = True
                active_towers.pop(game_key, None)

                payout = session["current_payout"]
                store.adjust_balance(user.id, payout)
                profit = -(payout - session["bet"])
                store.add_house_profit(profit)
                new_bal = store.get_balance(user.id)
                store.record_match(user.id, "tower", session["bet"], "win", round(payout - session["bet"], 2))

                await query.edit_message_text(
                    _tower_header(session) + f"\n\n\U0001f389 You reached the top!\n"
                    f"Payout: ${payout:.2f}\n"
                    f"Balance: ${new_bal:.2f}",
                    reply_markup=_tower_grid_keyboard(session),
                )
            else:
                await query.edit_message_text(
                    _tower_header(session) + "\n\n\U0001f34c Safe! Pick the next row or cash out!",
                    reply_markup=_tower_grid_keyboard(session),
                )
        else:
            # Hit a trap — game over
            session["game_over"] = True
            session["won"] = False
            session["last_pick_col"] = col
            active_towers.pop(game_key, None)

            store.add_house_profit(session["bet"])
            new_bal = store.get_balance(user.id)
            store.record_match(user.id, "tower", session["bet"], "loss", -session["bet"])

            await query.edit_message_text(
                _tower_header(session) + f"\n\n\U0001f480 TRAP! You fell!\n"
                f"Lost: ${session['bet']:.2f}\n"
                f"Balance: ${new_bal:.2f}",
                reply_markup=_tower_grid_keyboard(session),
            )


# ---------------------------------------------------------------------------
# Coinflip Game (/heads, /tails)
# ---------------------------------------------------------------------------

# Coin flip GIF URLs (publicly available)
COINFLIP_HEADS_GIF = "https://media.tenor.com/ncCwrdZ6UWsAAAPo/coin-flip-coin-flip-heads.mp4"
COINFLIP_TAILS_GIF = "https://media.tenor.com/JCa8VnPcfp0AAAPo/tails.mp4"


def _coinflip_end_keyboard(bet: float) -> InlineKeyboardMarkup:
    """Build play-again / double keyboard for coinflip."""
    bet_str = f"{bet:.2f}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "(Play Again)",
                callback_data=f"cfa_{bet_str}",
            ),
            InlineKeyboardButton(
                "\u23eb Double",
                callback_data=f"cfd_{bet_str}",
            ),
        ],
        [
            InlineKeyboardButton("\u00ab Back to Menu", callback_data="menu_main"),
        ],
    ])


async def coinflip_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /heads <bet> and /tails <bet>."""
    chat_id = update.effective_chat.id
    user = update.effective_user
    command = update.message.text.split()[0].lstrip("/").lower()

    if command not in ("heads", "tails"):
        return

    game_key = (chat_id, user.id)
    if game_key in active_games or game_key in active_blackjack or game_key in active_dice_roulette or game_key in active_mines or game_key in active_towers:
        await update.message.reply_text(
            "\u2620\ufe0f You already have an active game! Finish it first."
        )
        return

    args = context.args
    if not args:
        m = await update.message.reply_text(
            f"\U0001fa99 Coinflip\n\n"
            f"Usage: /{command} <bet>\n"
            f"Example: /{command} 10\n\n"
            f"Win = {COINFLIP_MULTI}x payout!",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    try:
        bet = float(args[0])
    except ValueError:
        m = await update.message.reply_text(
            "\u2620\ufe0f Bet must be a number.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    if bet < MIN_BET:
        m = await update.message.reply_text(
            f"\u2620\ufe0f Minimum bet is ${MIN_BET:.2f}.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return
    if bet > MAX_BET:
        m = await update.message.reply_text(
            f"\u2620\ufe0f Maximum bet is ${MAX_BET:.2f}.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    bal = store.get_balance(user.id)
    if bet > bal:
        m = await update.message.reply_text(
            f"\u2620\ufe0f Insufficient balance! You have ${bal:.2f}.\n"
            f"Use /deposit to add funds.",
            reply_markup=back_to_menu_keyboard(),
        )
        _record_owner(chat_id, m.message_id, user.id)
        return

    # Deduct bet
    store.adjust_balance(user.id, -bet)
    store.record_wager(user.id, bet, username=user.first_name)

    player_call = command  # "heads" or "tails"
    result = random.choice(["heads", "tails"])
    won = player_call == result

    # Send coin flip GIF animation that lands on the actual result
    flip_gif = COINFLIP_HEADS_GIF if result == "heads" else COINFLIP_TAILS_GIF
    await context.bot.send_animation(
        chat_id=chat_id,
        animation=flip_gif,
        caption=(
            f"\U0001fa99 Coinflip \u2014 ${bet:.2f}\n\n"
            f"You called: {player_call.upper()}\n\n"
            f"\U0001fa99 Flipping..."
        ),
    )

    await asyncio.sleep(3)

    result_emoji = "\U0001f7e1" if result == "heads" else "\u26aa"
    if won:
        winnings = round(bet * COINFLIP_MULTI, 2)
        store.adjust_balance(user.id, winnings)
        profit = -(winnings - bet)
        store.add_house_profit(profit)
        new_bal = store.get_balance(user.id)
        store.record_match(user.id, "coinflip", bet, "win", round(winnings - bet, 2))

        end_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"\U0001fa99 Coin landed on: {result_emoji} {result.upper()}\n\n"
                f"You called {player_call.upper()} \u2014 You win!\n"
                f"Payout: ${winnings:.2f} ({COINFLIP_MULTI}x)\n"
                f"Balance: ${new_bal:.2f}"
            ),
            reply_markup=_coinflip_end_keyboard(bet),
        )
    else:
        store.add_house_profit(bet)
        new_bal = store.get_balance(user.id)
        store.record_match(user.id, "coinflip", bet, "loss", -bet)

        end_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"\U0001fa99 Coin landed on: {result_emoji} {result.upper()}\n\n"
                f"You called {player_call.upper()} \u2014 You lost.\n"
                f"Balance: ${new_bal:.2f}"
            ),
            reply_markup=_coinflip_end_keyboard(bet),
        )
    _record_owner(chat_id, end_msg.message_id, user.id)


async def coinflip_replay_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle coinflip Play Again / Double buttons."""
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id

    owner_id = button_owners.get((chat_id, query.message.message_id))
    if owner_id is not None and user.id != owner_id:
        await query.answer("These buttons aren't for you!", show_alert=True)
        return

    data = query.data  # "cfa_10.00" or "cfd_10.00"
    parts = data.split("_")
    action = parts[0]  # "cfa" or "cfd"
    bet = float(parts[1])

    if action == "cfd":
        bet = round(bet * 2, 2)

    game_key = (chat_id, user.id)
    if game_key in active_games or game_key in active_blackjack or game_key in active_dice_roulette or game_key in active_mines or game_key in active_towers:
        await query.answer("You already have an active game!", show_alert=True)
        return

    if bet < MIN_BET:
        await query.answer(f"Minimum bet is ${MIN_BET:.2f}.", show_alert=True)
        return
    if bet > MAX_BET:
        await query.answer(f"Maximum bet is ${MAX_BET:.2f}.", show_alert=True)
        return

    bal = store.get_balance(user.id)
    if bet > bal:
        await query.answer(f"Insufficient balance! You have ${bal:.2f}.", show_alert=True)
        return

    await query.answer()

    # Show pick buttons
    bet_str = f"{bet:.2f}"
    await query.edit_message_text(
        f"\U0001fa99 Coinflip \u2014 ${bet:.2f}\n\n"
        f"Choose your call:",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("\U0001f7e1 Heads", callback_data=f"cfcall_heads_{bet_str}"),
                InlineKeyboardButton("\u26aa Tails", callback_data=f"cfcall_tails_{bet_str}"),
            ],
            [
                InlineKeyboardButton("\u00ab Back to Menu", callback_data="menu_main"),
            ],
        ]),
    )


async def coinflip_call_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle coinflip call selection from replay."""
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id

    owner_id = button_owners.get((chat_id, query.message.message_id))
    if owner_id is not None and user.id != owner_id:
        await query.answer("These buttons aren't for you!", show_alert=True)
        return

    data = query.data  # "cfcall_heads_10.00" or "cfcall_tails_10.00"
    parts = data.split("_")
    player_call = parts[1]  # "heads" or "tails"
    bet = float(parts[2])

    game_key = (chat_id, user.id)
    if game_key in active_games or game_key in active_blackjack or game_key in active_dice_roulette or game_key in active_mines or game_key in active_towers:
        await query.answer("You already have an active game!", show_alert=True)
        return

    if bet < MIN_BET:
        await query.answer(f"Minimum bet is ${MIN_BET:.2f}.", show_alert=True)
        return
    if bet > MAX_BET:
        await query.answer(f"Maximum bet is ${MAX_BET:.2f}.", show_alert=True)
        return

    bal = store.get_balance(user.id)
    if bet > bal:
        await query.answer(f"Insufficient balance! You have ${bal:.2f}.", show_alert=True)
        return

    await query.answer()

    # Deduct bet
    store.adjust_balance(user.id, -bet)
    store.record_wager(user.id, bet, username=user.first_name)

    result = random.choice(["heads", "tails"])
    won = player_call == result

    # Remove old message, send animation that lands on the actual result
    try:
        await query.edit_message_text(
            f"\U0001fa99 Coinflip \u2014 ${bet:.2f}\n\n"
            f"You called: {player_call.upper()}\n\n"
            f"\U0001fa99 Flipping..."
        )
    except Exception:
        pass

    flip_gif = COINFLIP_HEADS_GIF if result == "heads" else COINFLIP_TAILS_GIF
    await context.bot.send_animation(
        chat_id=chat_id,
        animation=flip_gif,
        caption=(
            f"\U0001fa99 Coinflip \u2014 ${bet:.2f}\n\n"
            f"You called: {player_call.upper()}\n\n"
            f"\U0001fa99 Flipping..."
        ),
    )

    await asyncio.sleep(3)

    result_emoji = "\U0001f7e1" if result == "heads" else "\u26aa"
    if won:
        winnings = round(bet * COINFLIP_MULTI, 2)
        store.adjust_balance(user.id, winnings)
        profit = -(winnings - bet)
        store.add_house_profit(profit)
        new_bal = store.get_balance(user.id)
        store.record_match(user.id, "coinflip", bet, "win", round(winnings - bet, 2))

        end_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"\U0001fa99 Coin landed on: {result_emoji} {result.upper()}\n\n"
                f"You called {player_call.upper()} \u2014 You win!\n"
                f"Payout: ${winnings:.2f} ({COINFLIP_MULTI}x)\n"
                f"Balance: ${new_bal:.2f}"
            ),
            reply_markup=_coinflip_end_keyboard(bet),
        )
    else:
        store.add_house_profit(bet)
        new_bal = store.get_balance(user.id)
        store.record_match(user.id, "coinflip", bet, "loss", -bet)

        end_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"\U0001fa99 Coin landed on: {result_emoji} {result.upper()}\n\n"
                f"You called {player_call.upper()} \u2014 You lost.\n"
                f"Balance: ${new_bal:.2f}"
            ),
            reply_markup=_coinflip_end_keyboard(bet),
        )
    _record_owner(chat_id, end_msg.message_id, user.id)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    token = TELEGRAM_TOKEN
    app = Application.builder().token(token).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler(["balance", "bal"], balance_command))
    app.add_handler(CommandHandler(["deposit", "depo"], deposit_command))
    app.add_handler(CommandHandler("withdraw", withdraw_command))
    app.add_handler(CommandHandler("ewith", enable_withdrawals_command))
    app.add_handler(CommandHandler("dwith", disable_withdrawals_command))
    app.add_handler(CommandHandler(["housebalance", "hb"], housebalance_command))
    app.add_handler(CommandHandler("tip", tip_command))
    app.add_handler(CommandHandler(["leaderboard", "lb"], leaderboard_command))
    app.add_handler(CommandHandler("matches", matches_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("rain", rain_command))
    app.add_handler(CommandHandler("showbal", showbal_command))
    app.add_handler(CommandHandler("setbal", setbal_command))
    app.add_handler(CommandHandler("sethb", sethb_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("addbal", addbal_command))
    app.add_handler(CommandHandler(["blackjack", "bj"], blackjack_command))
    app.add_handler(CommandHandler("dr", dr_command))
    app.add_handler(CommandHandler("weekly", weekly_command))
    app.add_handler(CommandHandler("monthly", monthly_command))
    app.add_handler(CommandHandler("settournament", settournament_command))
    app.add_handler(CommandHandler("mines", mines_command))
    app.add_handler(CommandHandler("tower", tower_command))
    app.add_handler(CommandHandler(["heads", "tails"], coinflip_command))
    app.add_handler(CommandHandler("slots", slots_command))

    # Game commands
    for cmd in GAME_COMMANDS:
        app.add_handler(CommandHandler(cmd, game_command))

    # Match history pagination callback handler
    app.add_handler(CallbackQueryHandler(matches_page_callback, pattern="^matches_"))

    # Rain join callback handler
    app.add_handler(CallbackQueryHandler(rain_join_callback, pattern="^rain_join_"))

    # Blackjack button callback handler (must be before general callback)
    app.add_handler(CallbackQueryHandler(blackjack_callback, pattern="^bj_"))

    # Dice Roulette callback handlers
    app.add_handler(CallbackQueryHandler(dr_toggle_callback, pattern="^drt_"))
    app.add_handler(CallbackQueryHandler(dr_confirm_callback, pattern="^drc_"))
    app.add_handler(CallbackQueryHandler(dr_prediction_callback, pattern="^dr_"))
    app.add_handler(CallbackQueryHandler(dr_replay_callback, pattern="^dr[ad]_"))

    # Rounds selection callback handler (1/2/3 rounds)
    app.add_handler(CallbackQueryHandler(rounds_selection_callback, pattern="^rounds_"))

    # Mode selection callback handler (1/2/3 rolls per round)
    app.add_handler(CallbackQueryHandler(mode_selection_callback, pattern="^mode_"))

    # Crazy-mode selection callback handler (normal vs crazy)
    app.add_handler(CallbackQueryHandler(rules_selection_callback, pattern="^rules_"))

    # Opponent selection callback handler (vs Bot / vs Player)
    app.add_handler(CallbackQueryHandler(opponent_selection_callback, pattern="^opp_"))

    # PvP join/cancel callback handler
    app.add_handler(CallbackQueryHandler(pvp_join_callback, pattern="^pvp"))

    # Tournament join callback handler
    app.add_handler(CallbackQueryHandler(tournament_join_callback, pattern="^tournament_join$"))

    # Mines callback handler
    app.add_handler(CallbackQueryHandler(mines_callback, pattern="^mines_"))

    # Tower callback handler
    app.add_handler(CallbackQueryHandler(tower_callback, pattern="^tower_"))

    # Coinflip callback handlers
    app.add_handler(CallbackQueryHandler(coinflip_replay_callback, pattern="^cf[ad]_"))
    app.add_handler(CallbackQueryHandler(coinflip_call_callback, pattern="^cfcall_"))

    # Replay (Play Again / Double & Play) callback handler
    app.add_handler(CallbackQueryHandler(replay_callback, pattern="^r[ad]_"))

    # Inline button callback handler
    app.add_handler(CallbackQueryHandler(button_callback))

    # Slots WebApp data handler
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, slots_webapp_data_handler))

    # Dice emoji handler
    app.add_handler(MessageHandler(filters.Dice.ALL, handle_dice_message))

    # Tournament scheduled jobs (every Thursday)
    # 11:00 AM CST = 17:00 UTC — announcement
    # 12:00 PM CST = 18:00 UTC — start
    app.job_queue.run_daily(
        tournament_announcement_job,
        time=dt_time(hour=17, minute=0, second=0, tzinfo=timezone.utc),
        days=(3,),  # Thursday
    )
    app.job_queue.run_daily(
        tournament_start_job,
        time=dt_time(hour=18, minute=0, second=0, tzinfo=timezone.utc),
        days=(3,),  # Thursday
    )

    logger.info("\u2620\ufe0f Toxic Casino Bot is starting...")
    logger.info(
        "OxaPay Merchant API: %s",
        "configured" if OXAPAY_MERCHANT_API_KEY else "NOT SET",
    )
    logger.info(
        "OxaPay Payout API: %s",
        "configured" if OXAPAY_PAYOUT_API_KEY else "NOT SET",
    )
    logger.info(
        "OxaPay General API: %s",
        "configured" if OXAPAY_GENERAL_API_KEY else "NOT SET",
    )

    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()

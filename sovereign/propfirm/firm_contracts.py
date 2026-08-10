"""Firm contract layer — spec 021, sections P2/P4.

Loads pre-registered firm definitions from data/propfirm/firm_contracts.yaml and
adapts them to the cfg["prop"] dict that sovereign/risk/layers/prop.py::prop_ceiling
consumes, so the floor math has exactly one implementation.

Fail-loud (repo rule 2): unknown firm, unknown enum value, missing field, or a
value outside its legal range raises immediately. There are no defaults for
correctness-critical fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_PATH = _REPO_ROOT / "data" / "propfirm" / "firm_contracts.yaml"

# "No daily limit" is represented as a floor that cannot bind: a daily loss
# budget of 100% of the account. This is a faithful encoding of the contract
# term, not a silent default — prop_ceiling requires the key to exist.
NO_DAILY_LIMIT_PCT = 1.0

_DD_TYPES = ("static", "trailing")
_BASES = ("balance", "equity")
_MARKS = ("close", "intraday")


@dataclass(frozen=True)
class Phase:
    target_pct: float
    min_trading_days: int
    max_days: int | None  # None = no deadline

    def __post_init__(self):
        if not 0 < self.target_pct < 1:
            raise ValueError(f"phase target_pct out of range: {self.target_pct}")
        if self.min_trading_days < 0:
            raise ValueError(f"negative min_trading_days: {self.min_trading_days}")
        if self.max_days is not None and self.max_days <= 0:
            raise ValueError(f"max_days must be positive or null: {self.max_days}")


@dataclass(frozen=True)
class DrawdownRule:
    pct: float
    basis: str
    mark: str
    type: str = "static"  # daily rules carry no type; max_dd sets it explicitly

    def __post_init__(self):
        if not 0 < self.pct <= 1:
            raise ValueError(f"drawdown pct out of range: {self.pct}")
        if self.basis not in _BASES:
            raise ValueError(f"unknown drawdown basis: {self.basis!r}")
        if self.mark not in _MARKS:
            raise ValueError(f"unknown drawdown mark: {self.mark!r}")
        if self.type not in _DD_TYPES:
            raise ValueError(f"unknown drawdown type: {self.type!r}")


@dataclass(frozen=True)
class Costs:
    fee_usd: float
    refund_on_pass: float
    swap_haircut_r_per_day: float

    def __post_init__(self):
        if self.fee_usd < 0:
            raise ValueError(f"negative fee: {self.fee_usd}")
        if not 0 <= self.refund_on_pass <= 1.5:
            raise ValueError(f"refund_on_pass out of range: {self.refund_on_pass}")
        if self.swap_haircut_r_per_day < 0:
            raise ValueError(f"negative swap haircut: {self.swap_haircut_r_per_day}")


@dataclass(frozen=True)
class FirmContract:
    key: str
    display_name: str
    account_size: float
    phases: tuple[Phase, ...]
    max_dd: DrawdownRule
    daily_dd: DrawdownRule | None
    permissions: dict = field(default_factory=dict)
    costs: Costs = None
    rules_asof: str = ""
    source_url: str = ""

    def __post_init__(self):
        if self.account_size <= 0:
            raise ValueError(f"account_size must be positive: {self.account_size}")
        if not self.phases:
            raise ValueError(f"{self.key}: contract has no phases")
        for perm in ("weekend_hold", "overnight_hold", "news_hold"):
            if perm not in self.permissions:
                raise ValueError(f"{self.key}: missing permission {perm!r}")
        if self.costs is None:
            raise ValueError(f"{self.key}: missing costs block")

    @property
    def has_deadline(self) -> bool:
        return any(p.max_days is not None for p in self.phases)

    def to_prop_cfg(self, phase_index: int = 0, safety_buffer_pct: float = 0.0) -> dict:
        """Emit the cfg["prop"] dict consumed by sovereign.risk.layers.prop.prop_ceiling.

        One floor implementation: the evaluator and the live risk stack read the
        same shape. A contract with no daily limit emits a budget of 100% of the
        account (see NO_DAILY_LIMIT_PCT) so the daily floor can never bind.
        """
        if not 0 <= phase_index < len(self.phases):
            raise IndexError(f"{self.key}: phase {phase_index} does not exist")
        daily_pct = self.daily_dd.pct if self.daily_dd is not None else NO_DAILY_LIMIT_PCT
        return {
            "account_size": self.account_size,
            "safety_buffer_pct": safety_buffer_pct,
            "daily_loss_limit_pct": daily_pct,
            "drawdown_type": self.max_dd.type,
            "max_drawdown_pct": self.max_dd.pct,
        }


def _parse_contract(key: str, raw: dict) -> FirmContract:
    try:
        phases = tuple(
            Phase(
                target_pct=float(p["target_pct"]),
                min_trading_days=int(p["min_trading_days"]),
                max_days=None if p["max_days"] is None else int(p["max_days"]),
            )
            for p in raw["phases"]
        )
        max_dd = DrawdownRule(
            pct=float(raw["max_dd"]["pct"]),
            basis=raw["max_dd"]["basis"],
            mark=raw["max_dd"]["mark"],
            type=raw["max_dd"]["type"],
        )
        daily_raw = raw["daily_dd"]
        daily_dd = None if daily_raw is None else DrawdownRule(
            pct=float(daily_raw["pct"]), basis=daily_raw["basis"], mark=daily_raw["mark"],
        )
        costs = Costs(
            fee_usd=float(raw["costs"]["fee_usd"]),
            refund_on_pass=float(raw["costs"]["refund_on_pass"]),
            swap_haircut_r_per_day=float(raw["costs"]["swap_haircut_r_per_day"]),
        )
        return FirmContract(
            key=key,
            display_name=raw["display_name"],
            account_size=float(raw["account_size"]),
            phases=phases,
            max_dd=max_dd,
            daily_dd=daily_dd,
            permissions=dict(raw["permissions"]),
            costs=costs,
            rules_asof=str(raw["rules_asof"]),
            source_url=str(raw["source_url"]),
        )
    except KeyError as e:
        raise KeyError(f"contract {key!r}: missing required field {e}") from e


def load_contracts(path: Path | None = None) -> dict[str, FirmContract]:
    p = path or CONTRACTS_PATH
    with open(p) as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"{p}: no contracts found")
    return {key: _parse_contract(key, spec) for key, spec in raw.items()}


def load_contract(key: str, path: Path | None = None) -> FirmContract:
    contracts = load_contracts(path)
    if key not in contracts:
        raise KeyError(f"unknown firm {key!r}; known: {sorted(contracts)}")
    return contracts[key]

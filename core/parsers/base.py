from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass
class ParsedTransaction:
    date: date
    description: str
    amount: Decimal
    account_metadata: dict = field(default_factory=dict)


@dataclass
class ParsedLedger:
    currency: str  # 'CRC' or 'USD'
    previous_balance: Decimal = Decimal(0)
    balance_at_cutoff: Decimal = Decimal(0)
    transactions: list = field(default_factory=list)  # list of ParsedTransaction


@dataclass
class ParsedStatement:
    card_number: str = ''
    card_holder: str = ''
    statement_date: Optional[date] = None
    points_assigned: int = 0
    points_redeemable: int = 0
    ledgers: list = field(default_factory=list)  # list of ParsedLedger
    warnings: list = field(default_factory=list)  # validation warnings


class BaseParser(ABC):
    @abstractmethod
    def parse(self, file_content: str) -> ParsedStatement:
        pass

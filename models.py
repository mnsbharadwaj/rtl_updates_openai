"""
Data models for the SFR/LLD generator.
Each model computes a SHA-256 checksum so the diff engine can detect
exactly which registers/fields changed between IPXACT revisions.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# Recognised access-type tokens
ACCESS_TYPES = {"RW", "RO", "WO", "W1C", "RW1C", "RC", "RS", "WS", "WOC"}


@dataclass
class Field:
    """One bit-field inside a register."""

    name: str
    msb: int
    lsb: int
    access: str          # RW / RO / WO / W1C / RW1C …
    reset_value: int
    description: str
    is_debug: bool = False

    # ── derived properties ──────────────────────────────────────────────
    @property
    def width(self) -> int:
        return self.msb - self.lsb + 1

    @property
    def mask(self) -> int:
        return ((1 << self.width) - 1) << self.lsb

    @property
    def shift(self) -> int:
        return self.lsb

    @property
    def has_description(self) -> bool:
        return bool(self.description and self.description.strip())

    @property
    def checksum(self) -> str:
        raw = f"{self.name}|{self.msb}|{self.lsb}|{self.access}|{self.reset_value}|{self.description}"
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    # ── access helpers ──────────────────────────────────────────────────
    def can_read(self) -> bool:
        return self.access in {"RW", "RO", "RC", "RS", "W1C", "RW1C"}

    def can_write(self) -> bool:
        return self.access in {"RW", "WO", "W1C", "WS", "WOC", "RW1C"}

    def is_write_one_clear(self) -> bool:
        return self.access in {"W1C", "RW1C"}


@dataclass
class Register:
    """One hardware register (may contain multiple fields)."""

    name: str
    offset: int
    fields: List[Field] = field(default_factory=list)
    description: str = ""
    is_debug: bool = False

    @property
    def checksum(self) -> str:
        raw = (
            f"{self.name}|{self.offset}|{self.description}|"
            + "|".join(f.checksum for f in self.fields)
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:12]


@dataclass
class RegisterMap:
    """Complete register map for one peripheral (one Excel sheet)."""

    peripheral: str
    base_addr: int
    registers: List[Register] = field(default_factory=list)
    source_file: str = ""
    generated_at: str = ""

    @property
    def checksum(self) -> str:
        raw = (
            f"{self.peripheral}|{self.base_addr}|"
            + "|".join(r.checksum for r in self.registers)
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    def get_register(self, name: str) -> Optional[Register]:
        for r in self.registers:
            if r.name == name:
                return r
        return None

    def as_dict(self) -> Dict:
        """Serialise checksums to JSON for persistence between runs."""
        return {
            "peripheral": self.peripheral,
            "base_addr": self.base_addr,
            "checksum": self.checksum,
            "registers": [
                {
                    "name": r.name,
                    "offset": r.offset,
                    "checksum": r.checksum,
                    "fields": [
                        {"name": f.name, "checksum": f.checksum}
                        for f in r.fields
                    ],
                }
                for r in self.registers
            ],
        }

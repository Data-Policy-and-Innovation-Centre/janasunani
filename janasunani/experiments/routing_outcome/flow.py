"""Single decoder for `vchAllEscUser` -> flow (role-sequence).

All tokens in the lake's `vchAllEscUser` are `intUserId` (2,747 distinct, a
subset of the 3,115 users in `t_user_role_details.csv`), not roleIds -- the
largest `m_role.intRoleId` is 87, well below the token range.

The tables load lazily. The first version called `_load_tables()` at module
scope, so importing this module opened three CSVs under `data/` as a side
effect: it crashed on import from any working directory other than the repo
root, and it read restricted data whether or not the caller wanted a decode.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple

from .paths import MAPPING_DIR


@dataclass(frozen=True)
class Flow:
    chain_len: int
    role_ids: Tuple[str, ...]
    role_names: Tuple[str, ...]
    entry_role: str
    last_role: str
    template: str  # comma-joined roleIds
    entry_office: Optional[str] = None


@dataclass(frozen=True)
class Tables:
    user_role: dict[str, str]
    role_name: dict[str, str]
    office_by_role: dict[str, tuple[str, ...]]


@lru_cache(maxsize=4)
def load_tables(mapping_dir: Path = MAPPING_DIR) -> Tables:
    """Read the three master tables the decoder needs. Cached per directory."""
    user_role: dict[str, str] = {}
    with open(mapping_dir / "t_user_role_details.csv") as f:
        for row in csv.DictReader(f):
            user_role[row["intUserId"]] = row["intRoleId"]

    role_name: dict[str, str] = {}
    with open(mapping_dir / "m_role.csv") as f:
        for row in csv.DictReader(f):
            role_name[row["intRoleId"]] = row["vchRoleName"]

    offices: dict[str, set[str]] = {}
    with open(mapping_dir / "m_office_designation_mapping.csv") as f:
        for row in csv.DictReader(f):
            offices.setdefault(row["intDesignationId"], set()).add(row["intOfficeId"])
    # Sorted, so `entry_office` below is reproducible. Taking `next(iter(set))`
    # made the decode depend on string hashing.
    office_by_role = {k: tuple(sorted(v)) for k, v in offices.items()}

    return Tables(user_role=user_role, role_name=role_name, office_by_role=office_by_role)


def decode_esc_chain(vch: Optional[str], mapping_dir: Path = MAPPING_DIR) -> Optional[Flow]:
    """Decode a comma-separated `intUserId` chain into a role-sequence flow.

    Returns None for an empty chain or one containing an unmappable token.
    """
    if not vch or not str(vch).strip():
        return None
    tables = load_tables(mapping_dir)
    tokens = [t.strip() for t in str(vch).split(",") if t.strip()]
    roles: list[str] = []
    for token in tokens:
        role = tables.user_role.get(token)
        if role is None:
            return None
        roles.append(role)
    if not roles:
        return None

    offices = tables.office_by_role.get(roles[0], ())
    return Flow(
        chain_len=len(roles),
        role_ids=tuple(roles),
        role_names=tuple(tables.role_name.get(r, "?") for r in roles),
        entry_role=roles[0],
        last_role=roles[-1],
        template=",".join(roles),
        entry_office=offices[0] if offices else None,
    )


def decode_pending(pending_with_id: object, mapping_dir: Path = MAPPING_DIR) -> Optional[str]:
    """Role of the current holder. None when unset or unmappable.

    Sentinels in this column are "", "-1" and "0"; ids arrive as both ints and
    strings, and occasionally as something non-numeric, which the previous
    unguarded `int(...)` turned into a ValueError mid-frame.
    """
    if pending_with_id is None:
        return None
    raw = str(pending_with_id).strip()
    if raw in ("", "-1", "0", "nan", "None"):
        return None
    try:
        key = str(int(float(raw)))
    except (TypeError, ValueError):
        return None
    return load_tables(mapping_dir).user_role.get(key)

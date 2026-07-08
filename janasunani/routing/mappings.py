"""Loader for the real ORTPSA master tables (``data/raw/janasunani-mappings/``).

These CSVs are DVC-tracked reference data (categories, departments, offices,
designations, escalation ladders) migrated from the production Janasunani
system. They are non-PII master data, safe to read at runtime, but they are
**not** committed to git (see ``data/raw/janasunani-mappings.dvc``) and other
machines/CI will not have them materialized. Every function in this module
degrades to ``None`` instead of raising when the directory or files are
missing, so callers can fall back to the illustrative rule set.

Join path (verified by inspection on the 2026-07 snapshot; there is no schema
doc for this legacy MySQL export):

* ``m_admin_category`` / ``m_admin_subcategory``: category/subcategory master,
  English (``vchCategory``/``vchSubCategory``) + Odia
  (``vchCategoryOD``/``vchSubCategoryOD``).
* ``m_admin_hierarchy_value``: department master. The department id used
  elsewhere is ``intAdminHierarchyValueId`` -- *not* ``intSocialDeptId``, a
  separate code on the same row that does not appear anywhere else in these
  tables.
* ``t_admin_escalation.intDepartmentId`` == ``m_admin_hierarchy_value
  .intAdminHierarchyValueId``. Verified two ways: department 5 ("Energy")
  resolves (via the designation join below) to designation ids 17-20, whose
  ``m_role`` names are exactly the four real Odisha power-distribution company
  CEOs ("CEO,TPCODL" / "TPWODL" / "TPNODL" / "TPSODL"); department 14 ("Home
  department") resolves to DGP / IG / "Commissionorate of Police". Those are
  not coincidental matches. ``intDepartmentId == "0"`` is a distinct
  generic/unassigned bucket (~120 of the 158 active rows), not department 0 --
  it is excluded here rather than guessed at.
* ``t_admin_escalation.vchDesignationSequence`` is a comma-separated list of
  ``m_role.intRoleId``, ordered first-responder -> ... -> apex authority.
  ``t_admin_escalation.intOfficeId`` references ``m_admin_offices`` (only 7
  generic top-level offices: CM, Governor, Chief Secretary, DG&IG Police,
  "Departments", Collector, SP).

**What does NOT close**: there is no category/subcategory -> department
foreign key anywhere in these tables. ``m_admin_category.intCategoryGrp`` is
``NULL`` on every row (checked across all 62 rows), and
``intComplainTypeId`` is just an intake-channel code (values 12/13 -- app
version, not department). The only non-fabricated bridge available is exact
(normalized) name equality between a category's English/Odia name and a
department's English/Odia name -- e.g. the category "Energy" and the
department "Energy" are the literal same string. On the current snapshot that
covers a handful of the 62 categories (see ``category_to_department``);
everything else has no derivable department and must fall back. This module
deliberately does not attempt fuzzy/substring matching (e.g. "Culture" is a
substring of "Agriculture") because that would fabricate a mapping rather
than derive one.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from janasunani.config import RAW_DATA_DIR

DEFAULT_MAPPING_DIR = RAW_DATA_DIR / "janasunani-mappings"

# The generic "Departments" catch-all office (m_admin_offices id 5) carries no
# information beyond "some department handles this" -- not useful as a label.
_GENERIC_OFFICE_NAME = "Departments"

_REQUIRED_FILES = (
    "m_admin_category.csv",
    "m_admin_hierarchy_value.csv",
    "m_admin_offices.csv",
    "t_admin_escalation.csv",
    "m_role.csv",
)


def _norm(value: Optional[str]) -> str:
    return " ".join((value or "").casefold().split())


@dataclass(frozen=True)
class Category:
    id: str
    name_en: str
    name_od: str


@dataclass(frozen=True)
class Department:
    id: str
    name_en: str
    name_od: str


@dataclass(frozen=True)
class EscalationChain:
    """One resolved escalation ladder for a department.

    ``designations`` is ordered first-responder -> apex authority, resolved
    from ``m_role`` names. ``office_name`` is the top-level office
    (``m_admin_offices``) attached to the escalation row, when informative.
    """

    department_id: str
    office_name: str
    designations: Tuple[str, ...]


@dataclass(frozen=True)
class MappingTables:
    """Parsed + joined lookups derived from the master CSVs."""

    categories: Tuple[Category, ...]
    departments: Tuple[Department, ...]
    category_to_department: Dict[str, str]
    escalation_by_department: Dict[str, EscalationChain]

    def find_category(self, text: str) -> Optional[Category]:
        key = _norm(text)
        if not key:
            return None
        for category in self.categories:
            if _norm(category.name_en) == key:
                return category
            if category.name_od and _norm(category.name_od) == key:
                return category
        return None

    def find_department(self, department_id: str) -> Optional[Department]:
        for department in self.departments:
            if department.id == department_id:
                return department
        return None

    def resolve(
        self, category_text: str
    ) -> Optional[Tuple[Department, Optional[EscalationChain]]]:
        """Category text -> (department, escalation chain or None).

        Returns ``None`` when the category text does not match any real
        category, or the matched category has no derivable department.
        """
        category = self.find_category(category_text)
        if category is None:
            return None
        department_id = self.category_to_department.get(category.id)
        if department_id is None:
            return None
        department = self.find_department(department_id)
        if department is None:
            return None
        chain = self.escalation_by_department.get(department_id)
        return department, chain


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def load_mapping_tables(base_dir: Optional[Path] = None) -> Optional[MappingTables]:
    """Load and join the master tables from ``base_dir`` (default: the
    project's ``data/raw/janasunani-mappings`` directory via
    ``janasunani.config.RAW_DATA_DIR``).

    Returns ``None`` -- never raises -- if the directory or any required file
    is missing or unreadable, so callers can degrade to the illustrative rule
    set. This is the expected state on CI and any machine that has not run
    ``dvc pull`` for this dataset.
    """

    directory = Path(base_dir) if base_dir is not None else DEFAULT_MAPPING_DIR
    if not directory.is_dir():
        return None
    for name in _REQUIRED_FILES:
        if not (directory / name).is_file():
            return None

    try:
        category_rows = _read_csv(directory / "m_admin_category.csv")
        hierarchy_rows = _read_csv(directory / "m_admin_hierarchy_value.csv")
        office_rows = _read_csv(directory / "m_admin_offices.csv")
        escalation_rows = _read_csv(directory / "t_admin_escalation.csv")
        role_rows = _read_csv(directory / "m_role.csv")
    except (OSError, csv.Error, UnicodeDecodeError):
        return None

    categories = tuple(
        Category(
            id=row["intCategoryId"],
            name_en=(row.get("vchCategory") or "").strip(),
            name_od=(row.get("vchCategoryOD") or "").strip(),
        )
        for row in category_rows
        if row.get("intCategoryId")
    )
    departments = tuple(
        Department(
            id=row["intAdminHierarchyValueId"],
            name_en=(row.get("vchAdminHierarchyValue") or "").strip(),
            name_od=(row.get("vchAdminHierarchyValueO") or "").strip(),
        )
        for row in hierarchy_rows
        if row.get("intAdminHierarchyValueId")
    )
    offices_by_id = {
        row["intOfficeId"]: (row.get("vchOfficeName") or "").strip()
        for row in office_rows
        if row.get("intOfficeId")
    }
    roles_by_id = {
        row["intRoleId"]: (row.get("vchRoleName") or "").strip()
        for row in role_rows
        if row.get("intRoleId")
    }

    category_to_department = _derive_category_to_department(categories, departments)
    escalation_by_department = _derive_escalation_chains(
        escalation_rows, offices_by_id, roles_by_id
    )

    return MappingTables(
        categories=categories,
        departments=departments,
        category_to_department=category_to_department,
        escalation_by_department=escalation_by_department,
    )


def _derive_category_to_department(
    categories: Tuple[Category, ...], departments: Tuple[Department, ...]
) -> Dict[str, str]:
    """The only non-fabricated category -> department bridge available: exact
    (normalized) equality between a category name and a department name,
    English or Odia. See the module docstring for why nothing richer than
    this is derivable from the master tables as they stand today.
    """
    department_id_by_name: Dict[str, str] = {}
    for department in departments:
        if department.name_en:
            department_id_by_name.setdefault(_norm(department.name_en), department.id)
        if department.name_od:
            department_id_by_name.setdefault(_norm(department.name_od), department.id)

    category_to_department: Dict[str, str] = {}
    for category in categories:
        department_id = department_id_by_name.get(_norm(category.name_en))
        if department_id is None and category.name_od:
            department_id = department_id_by_name.get(_norm(category.name_od))
        if department_id is not None:
            category_to_department[category.id] = department_id
    return category_to_department


def _derive_escalation_chains(
    escalation_rows: list[dict],
    offices_by_id: Dict[str, str],
    roles_by_id: Dict[str, str],
) -> Dict[str, EscalationChain]:
    """Pick one representative escalation row per department.

    Only active rows (``bitDeletedFlag == "0"``) with a specific department id
    are considered (``intDepartmentId == "0"`` is a separate generic/
    unassigned bucket, not a real department, and is skipped). Departments
    often have several historical rows on file; among the candidates this
    picks the one with the deepest configured ladder (``intEscalation``
    level), tie-broken by the most recently added rule (``intEscalationId``).
    That is a deterministic choice among genuinely configured records, not an
    invented value, but it is a heuristic -- a different tie-break could pick
    a different (also real) row for the same department.
    """
    best_row_by_department: Dict[str, dict] = {}
    for row in escalation_rows:
        if row.get("bitDeletedFlag") != "0":
            continue
        department_id = row.get("intDepartmentId")
        if not department_id or department_id == "0":
            continue
        try:
            level = int(row.get("intEscalation") or 0)
            escalation_id = int(row.get("intEscalationId") or 0)
        except ValueError:
            continue
        current = best_row_by_department.get(department_id)
        if current is None:
            best_row_by_department[department_id] = row
            continue
        current_key = (
            int(current.get("intEscalation") or 0),
            int(current.get("intEscalationId") or 0),
        )
        if (level, escalation_id) > current_key:
            best_row_by_department[department_id] = row

    escalation_by_department: Dict[str, EscalationChain] = {}
    for department_id, row in best_row_by_department.items():
        designation_ids = [
            token.strip()
            for token in (row.get("vchDesignationSequence") or "").split(",")
            if token.strip()
        ]
        designations = tuple(
            roles_by_id[token] for token in designation_ids if roles_by_id.get(token)
        )
        if not designations:
            continue
        office_name = offices_by_id.get(row.get("intOfficeId") or "", "")
        escalation_by_department[department_id] = EscalationChain(
            department_id=department_id,
            office_name=office_name,
            designations=designations,
        )
    return escalation_by_department

"""Local, source-only revisions for the saved-DB prediction cache.

Only an explicit local startup may install this operational metadata. Reads
never create a database, install/repair a schema, or alter acquisition fields.
The counter is not evidence of pre-race availability or model validation.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4


SCHEMA_VERSION = 1
TABLE_NAME = "_prediction_source_revision_v1"
SOURCE_TABLES = ("races_ultimate", "race_results_ultimate")
_HEX = re.compile(r"[0-9a-f]{32}")
_TABLE_SQL = f"""CREATE TABLE {TABLE_NAME} (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    source_identity TEXT NOT NULL CHECK (length(source_identity) = 32),
    generation INTEGER NOT NULL CHECK (typeof(generation) = 'integer' AND generation >= 0),
    change_id TEXT NOT NULL CHECK (length(change_id) = 32)
)"""


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def _sql_identity(value: str | None) -> str:
    return " ".join(str(value or "").strip().rstrip(";").lower().split())


def _trigger_definitions() -> dict[str, tuple[str, str]]:
    definitions = {}
    for source in SOURCE_TABLES:
        for operation in ("INSERT", "UPDATE", "DELETE"):
            name = f"{TABLE_NAME}_{source}_{operation.lower()}"
            event = operation
            predicate = ""
            if operation == "UPDATE":
                event = "UPDATE OF race_id, data, created_at"
                predicate = ("WHEN OLD.race_id IS NOT NEW.race_id OR OLD.data IS NOT NEW.data "
                             "OR OLD.created_at IS NOT NEW.created_at")
            sql = f"""CREATE TRIGGER {name} AFTER {event} ON {source}
                {predicate}
                BEGIN
                    UPDATE {TABLE_NAME}
                    SET generation = generation + 1, change_id = lower(hex(randomblob(16)))
                    WHERE singleton = 1;
                END"""
            definitions[name] = (source, sql)
    return definitions


TRIGGER_DEFINITIONS = _trigger_definitions()


def _source_path(db_path) -> Path:
    path = Path(db_path)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("prediction-source-revision-explicit-file-required")
    for parent in path.parents:
        if parent.is_symlink():
            raise ValueError("prediction-source-revision-linked-path-not-allowed")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("prediction-source-revision-existing-file-required")
    return resolved


def _objects(connection: sqlite3.Connection) -> dict:
    names = [TABLE_NAME, *SOURCE_TABLES, *TRIGGER_DEFINITIONS]
    marks = ",".join("?" for _ in names)
    return {row[1]: row for row in connection.execute(
        f"SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name IN ({marks})", names,
    )}


def _source_tables_valid(connection: sqlite3.Connection, objects: dict) -> bool:
    for source in SOURCE_TABLES:
        value = objects.get(source)
        if value is None or value[0] != "table" or value[2] != source:
            return False
        # created_at is an acquisition field, not the availability time. Its
        # changes nevertheless invalidate input projections conservatively.
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({source})")}
        if not {"race_id", "data", "created_at"}.issubset(columns):
            return False
    return True


def _validated_state(connection: sqlite3.Connection, objects: dict | None = None):
    objects = _objects(connection) if objects is None else objects
    if not _source_tables_valid(connection, objects):
        return None
    table = objects.get(TABLE_NAME)
    if (table is None or table[0] != "table" or table[2] != TABLE_NAME
            or _sql_identity(table[3]) != _sql_identity(_TABLE_SQL)):
        return None
    for name, (source, sql) in TRIGGER_DEFINITIONS.items():
        stored = objects.get(name)
        if (stored is None or stored[0] != "trigger" or stored[2] != source
                or _sql_identity(stored[3]) != _sql_identity(sql)):
            return None
    rows = connection.execute(
        f"SELECT singleton,schema_version,source_identity,generation,change_id FROM {TABLE_NAME}",
    ).fetchall()
    if len(rows) != 1:
        return None
    singleton, schema, identity, generation, change = rows[0]
    if (singleton != 1 or schema != SCHEMA_VERSION or type(generation) is not int or generation < 0
            or not isinstance(identity, str) or _HEX.fullmatch(identity) is None
            or not isinstance(change, str) or _HEX.fullmatch(change) is None):
        return None
    return identity, generation, change


def initialize_source_revision(db_path, local_enabled: bool) -> bool:
    """Atomically install six triggers, only for explicit local startup.

    Existing reserved names are never replaced or repaired. Any conflict,
    incomplete schema, read-only source or migration failure returns False;
    callers must retain conservative cache behavior rather than claim tracking.
    Other application tables/triggers and all existing provider rows are kept.
    """
    if local_enabled is not True:
        return False
    try:
        path = _source_path(db_path)
        with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True,
                                     timeout=10, isolation_level=None)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                objects = _objects(connection)
                if not _source_tables_valid(connection, objects):
                    connection.rollback()
                    return False
                occupied = any(name in objects for name in (TABLE_NAME, *TRIGGER_DEFINITIONS))
                if occupied:
                    valid = _validated_state(connection, objects) is not None
                    connection.commit() if valid else connection.rollback()
                    return valid
                connection.execute(_TABLE_SQL)
                connection.execute(f"INSERT INTO {TABLE_NAME} VALUES (1,1,?,0,?)",
                                   (uuid4().hex, uuid4().hex))
                for _source, sql in TRIGGER_DEFINITIONS.values():
                    connection.execute(sql)
                if _validated_state(connection) is None:
                    raise ValueError("prediction-source-revision-initial-state-invalid")
                connection.commit()
                return True
            except (sqlite3.Error, ValueError, OSError):
                connection.rollback()
                return False
    except (sqlite3.Error, ValueError, OSError, TypeError):
        return False


def _fallback_revision(db_path) -> dict:
    """Metadata-only fallback; WAL included, never interpreted as full tracking."""
    path = Path(db_path).resolve()
    values = []
    for candidate in (path, Path(str(path) + "-wal")):
        try:
            stat = candidate.stat()
            values.append([str(candidate), stat.st_dev, stat.st_ino, stat.st_size,
                           stat.st_mtime_ns, stat.st_ctime_ns])
        except OSError:
            values.append([str(candidate), None])
    return {"data_revision": _hash({"schema": "sqlite-file-version-v1", "files": values}),
            "revision_kind": "sqlite-file-version", "source_tracking": False}


def read_source_revision(db_path) -> dict:
    """Read committed source generation through RO SQLite, never migrate.

    The generation token deliberately excludes file size/mtime and WAL size:
    prediction-log writes and WAL checkpoints cannot stale all predictions.
    File identity plus the independent epoch distinguish replaced DB files.
    """
    try:
        path = _source_path(db_path)
        before = path.stat()
        identity = (str(path), before.st_dev, before.st_ino)
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                                     timeout=2, isolation_level=None)) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            state = _validated_state(connection)
            connection.rollback()
        after = path.stat()
        if state is not None and identity == (str(path), after.st_dev, after.st_ino):
            return {"data_revision": _hash({"schema": "sqlite-source-generation-v1",
                        "file_identity": identity, "source_identity": state[0],
                        "generation": state[1], "change_id": state[2]}),
                    "revision_kind": "sqlite-source-generation", "source_tracking": True}
    except (sqlite3.Error, ValueError, OSError, TypeError):
        pass
    return _fallback_revision(db_path)

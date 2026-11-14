"""Sealed, sequential, reference-only pace74 inference and separate paired evidence.

Nothing in this module grants activation or betting authority. Research v2
artifacts deliberately never enter the ordinary flat-model loader.
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import types


API_ROOT = Path(__file__).resolve().parents[1]
ROOT = API_ROOT.parent
STUDY_ROOT = ROOT / "reports" / "generated" / "model-experiments" / "pace-agent-20261114-v3"
PACKAGE_DIR = API_ROOT / "models" / "pace74-shadow"
STORE_PATH = ROOT / "keiba" / "data" / "pace74_shadow.sqlite3"
CANDIDATE_ID = "pace74-c09-minleaf240"
ARTIFACT_SHA256 = "59c68587305f85f88f0a6cb728f1f792bc1a8f04c2f15d71aff2914b0af22412"
TEMPERATURE = 1.0052842445790247
GROUPS = ["baseline46", "condition_aptitude", "pedigree", "gate", "style_pace"]
EVIDENCE_PINS = {
    "task-ledger.json": "61d73d9dea3fc5c701e28c733cbed32e779b9feaddeb8da314228d1157914a84",
    "approval.json": "5167325aca700e12d6ab70af418028156127eb89abd32296357d6a56ae9bdde2",
    "selection.json": "3e9584d1b4f73d6023e5731b44bfe3af812719841cf1f6b2eabfee7db85cebe1",
    "verification.json": "c2a2c92fa0c90744ac0c5db00a4baed1fa2b340de425be3ec3fa0176e5c68bdc",
    "reference-receipt.json": "2cd9a062467343aa86c0ae1f46f3e2f7870d80057cf3e606afd35ab421ce0948",
    "artifact_manifest.json": "03582d6b63f97389051a3405d5be1bab546380361fe56ea871bffcd44ddceba8",
}
CURRENT_SOURCES = (
    "python-api\\services\\pace74_shadow.py",
    "python-api\\services\\prediction_source_revision.py",
    "python-api\\training\\db_context_cache.py",
    "python-api\\scraping\\quality.py",
    "python-api\\scraping\\race_class.py",
)
_CACHE: tuple[str, dict] | None = None
_OWN_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_ATTESTATION_BLUEPRINTS = {}
_MUTABLE_HELPER_STATE = {
    "training.db_context_features": {"_SERVING_STATES", "_SERVING_LOCK"},
    "scraping.quality": {"_INIT_LOCK", "_INITIALIZED_DB_PATHS"},
}


def file_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _instant(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _positive_int(value):
    number = _number(value)
    return int(number) if number is not None and number >= 1 and number == int(number) else None


def _race_fingerprint(info):
    return _digest({name: str(info.get(name)) for name in (
        "race_id", "date", "race_date", "distance", "num_horses", "venue", "track_type", "surface", "post_time")})


def _relative_parts(name):
    parts = str(name).replace("\\", "/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("source_pin_path_invalid")
    return parts


def _repo_path(name):
    return ROOT.joinpath(*_relative_parts(name))


def _source_module_name(name):
    if "\\" not in name and "/" not in name:
        return name
    parts = _relative_parts(name)
    if parts[0] in {"python-api", "keiba"}:
        parts = parts[1:]
    parts[-1] = parts[-1].removesuffix(".py")
    return ".".join(parts)


def _source_path(name):
    if "\\" in name or "/" in name:
        return _repo_path(name)
    base = ROOT / "keiba" if name.startswith("keiba_ai.") else API_ROOT
    if name.startswith("scripts."):
        base = ROOT
    return base.joinpath(*name.split(".")).with_suffix(".py")


def _historical_pins(task):
    sources = task["sources"]
    names = [name for name in sources if "/" not in name and "\\" not in name]
    names += ["python-api/experiments/pace_research_loop.py", "keiba/keiba_ai/constants.py",
              "python-api\\training\\db_context_cache.py", "python-api/scraping/race_class.py"]
    return {name: sources[name] for name in names}


def _source_pins(task):
    pins = _historical_pins(task)
    for name in CURRENT_SOURCES:
        pins.setdefault(name, file_hash(_source_path(name)))
    return pins


def _import_bindings(tree, module):
    """Resolve existing imports canonically; never reload or replace a helper."""
    bindings = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = alias.name if alias.asname else alias.name.split(".")[0]
                bindings[alias.asname or target] = sys.modules.get(target)
        elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
            target = node.module or ""
            if node.level:
                target = importlib.util.resolve_name("." * node.level + target, module.__package__)
            parent = sys.modules.get(target)
            for alias in node.names:
                child = sys.modules.get(target + "." + alias.name)
                value = child if child is not None else getattr(parent, alias.name, None)
                bindings[alias.asname or alias.name] = value
    if any(value is None or module.__dict__.get(name) is not value for name, value in bindings.items()):
        raise ValueError("loaded_source_import_mismatch")
    return bindings


def _helper_blueprint(module, path, body, imports):
    """Compile verified declarations privately, without executing helper actions.

    Only definitions and simple module bindings are reconstructed. No module
    is reloaded, registered in sys.modules, or installed into primary globals.
    In particular, mutable serving caches and imported trained_source classes
    are neither reset nor modified.
    """
    tree = ast.parse(body, filename=str(path))
    namespace = {"__name__": module.__name__, "__file__": str(path),
                 "__package__": module.__package__, **imports}
    declared = set(imports)
    declarations = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            declarations.append(node)
            continue
        if isinstance(node, ast.Expr):
            if not isinstance(node.value, ast.Constant):
                raise ValueError("loaded_source_unverifiable")
            declarations.append(node)
            continue
        if isinstance(node, ast.If):
            # Top-level source guards/main entrypoints are not inference code.
            # Their definitions/bindings are still checked independently.
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            declared.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not all(isinstance(target, ast.Name) for target in targets):
                if module.__name__.startswith("scripts."):
                    continue  # Never repeat a CLI's sys.path mutation.
                raise ValueError("loaded_source_unverifiable")
            if isinstance(node, ast.AnnAssign) and node.value is None:
                continue
            declared.update(target.id for target in targets)
        else:
            raise ValueError("loaded_source_unverifiable")
        declarations.append(node)
    # Keep whole-module compiler context: Python specializes accesses to
    # imported modules differently from individually compiled function ASTs.
    declaration = ast.Module(body=declarations, type_ignores=tree.type_ignores)
    exec(compile(declaration, str(path), "exec", dont_inherit=True), namespace)
    return namespace, declared


def _same_loaded_value(actual, reference, module, namespace, seen):
    if actual is reference:
        return True
    pair = (id(actual), id(reference))
    if pair in seen:
        return True
    seen.add(pair)
    if type(actual) is not type(reference):
        return False
    same = lambda left, right: _same_loaded_value(left, right, module, namespace, seen)
    if isinstance(reference, types.FunctionType):
        if reference.__module__ != module.__name__:
            return False  # Imported aliases must retain their canonical identity.
        if (actual.__code__ != reference.__code__
                or actual.__module__ != reference.__module__
                or actual.__name__ != reference.__name__
                or actual.__qualname__ != reference.__qualname__):
            return False
        expected_globals = module.__dict__ if reference.__globals__ is namespace else reference.__globals__
        if actual.__globals__ is not expected_globals:
            return False
        closures = actual.__closure__ or ()
        expected_closures = reference.__closure__ or ()
        return (len(closures) == len(expected_closures)
                and all(same(left.cell_contents, right.cell_contents)
                        for left, right in zip(closures, expected_closures))
                and same(actual.__defaults__, reference.__defaults__)
                and same(actual.__kwdefaults__, reference.__kwdefaults__)
                and same(actual.__annotations__, reference.__annotations__)
                and same(actual.__dict__, reference.__dict__))
    if isinstance(reference, type):
        if (reference.__module__ != module.__name__ or actual.__name__ != reference.__name__
                or actual.__qualname__ != reference.__qualname__):
            return False
        ignored = {"__dict__", "__weakref__"}
        if "__slotnames__" in vars(actual) and "__slotnames__" not in vars(reference):
            # copyreg lazily caches [] when serializing these nonslotted
            # dataclasses. Nonempty/injected slot definitions are not trusted.
            if "__slots__" in vars(reference) or type(actual.__slotnames__) is not list or actual.__slotnames__:
                return False
            ignored.add("__slotnames__")
        left = {key: value for key, value in vars(actual).items() if key not in ignored}
        right = {key: value for key, value in vars(reference).items() if key not in ignored}
        return same(actual.__bases__, reference.__bases__) and same(left, right)
    if isinstance(reference, (staticmethod, classmethod)):
        return same(actual.__func__, reference.__func__)
    if isinstance(reference, property):
        return all(same(getattr(actual, name), getattr(reference, name)) for name in ("fget", "fset", "fdel"))
    if isinstance(reference, (dict, types.MappingProxyType)):
        return actual.keys() == reference.keys() and all(same(actual[key], value) for key, value in reference.items())
    if isinstance(reference, (tuple, list)):
        return len(actual) == len(reference) and all(same(left, right) for left, right in zip(actual, reference))
    if isinstance(reference, (str, bytes, int, float, bool, set, frozenset, Path, type(None))):
        return actual == reference
    if type(reference).__module__ == "re" and hasattr(reference, "pattern"):
        return actual.pattern == reference.pattern and actual.flags == reference.flags
    if type(reference).__module__ == "dataclasses":
        # Field/params descriptors are independently regenerated by @dataclass.
        slots = getattr(type(reference), "__slots__", ())
        return bool(slots) and all(same(getattr(actual, key), getattr(reference, key)) for key in slots)
    return False


def _attest_loaded_module(module, path, body, expected, pending):
    if Path(module.__file__).resolve() != path.resolve():
        raise ValueError("loaded_source_origin_mismatch")
    tree = ast.parse(body, filename=str(path))
    imports = _import_bindings(tree, module)
    key = (module.__name__, expected)
    blueprint = _ATTESTATION_BLUEPRINTS.get(key)
    if blueprint is None:
        blueprint = _helper_blueprint(module, path, body, imports)
        pending[key] = blueprint
    namespace, declared = blueprint
    state = _MUTABLE_HELPER_STATE.get(module.__name__, set())
    for name in declared:
        if name not in module.__dict__:
            raise ValueError("loaded_source_binding_missing")
        actual, reference = module.__dict__[name], namespace[name]
        if name in state:
            matches = type(actual) is type(reference)
        else:
            matches = _same_loaded_value(actual, reference, module, namespace, set())
        if not matches:
            raise ValueError("loaded_source_mismatch", module.__name__, name)


def _verify_sources(pins):
    if file_hash(Path(__file__)) != _OWN_HASH:
        raise ValueError("serving_source_restart_required")
    sources = {}
    for name, expected in pins.items():
        path = _source_path(name)
        if file_hash(path) != expected:
            raise ValueError("source_pin_mismatch")
        sources[_source_module_name(name)] = (path, expected)
    pending = {}
    for name, (path, expected) in sources.items():
        if name == __name__:
            continue
        module = sys.modules.get(name)
        if module is None and not name.startswith("scripts.") and name not in {
            "experiments.agent_pace_loop", "experiments.pace_research_loop",
        }:
            module = importlib.import_module(name)
        if module is not None:
            if module.__name__ != name:
                raise ValueError("loaded_source_origin_mismatch")
            body = path.read_bytes()
            if hashlib.sha256(body).hexdigest() != expected:
                raise ValueError("source_pin_mismatch")
            try:
                _attest_loaded_module(module, path, body, expected, pending)
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError("loaded_source_unverifiable") from exc
    _ATTESTATION_BLUEPRINTS.update(pending)


def _validate_evidence(directory):
    documents = {}
    for name, expected in EVIDENCE_PINS.items():
        path = directory / name
        if file_hash(path) != expected:
            raise ValueError("research_evidence_mismatch")
        documents[name] = _read(path)
    task, approval = documents["task-ledger.json"], documents["approval.json"]
    selected, verified = documents["selection.json"], documents["verification.json"]
    if (approval["task_sha256"] != EVIDENCE_PINS["task-ledger.json"]
            or approval["scope"] != "local-research-only"
            or selected["winner"] != "c09-minleaf240"
            or verified["verified"] is not True or verified["selected"] != selected["winner"]
            or verified["model_improved"] is not True
            or task["production_eligible"] is not False
            or documents["artifact_manifest.json"].get("artifacts\\2026\\research.joblib") != ARTIFACT_SHA256):
        raise ValueError("research_approval_mismatch")
    return documents


def _verify_reference_receipt_files(receipt):
    reference = Path(receipt["root"]).resolve(strict=True)
    expected_root = (ROOT / "reports" / "generated" / "model-experiments" / "pace-research-20261114-v2").resolve()
    if reference != expected_root:
        raise ValueError("reference_study_identity_mismatch")
    for name, digest in receipt["files"].items():
        path = reference.joinpath(*_relative_parts(name)).resolve(strict=True)
        if not path.is_relative_to(reference) or file_hash(path) != digest:
            raise ValueError("reference_study_changed")
    if file_hash(reference / "task-ledger.json") != receipt["task_sha256"]:
        raise ValueError("reference_task_mismatch")
    if _read(reference / "verification.json")["verified"] is not True:
        raise ValueError("reference_verification_missing")


def publish_package(*, destination: Path | None = None) -> dict:
    """Local operator API only; fixed approved source, no training or activation.

    An existing sealed publication is never overwritten. An interrupted,
    unsealed publication fails runtime validation and may be removed manually.
    """
    destination = PACKAGE_DIR if destination is None else Path(destination)
    trial = STUDY_ROOT / "trials" / "confirm-c09-minleaf240"
    evidence = {name: STUDY_ROOT / name for name in EVIDENCE_PINS}
    evidence["artifact_manifest.json"] = trial / "artifact_manifest.json"
    for name, expected in EVIDENCE_PINS.items():
        if file_hash(evidence[name]) != expected:
            raise ValueError("research_evidence_mismatch")
    task = _read(evidence["task-ledger.json"])
    _verify_reference_receipt_files(_read(evidence["reference-receipt.json"]))
    pins = _source_pins(task)
    _verify_sources(pins)
    # Validate all selected-trial outputs, not just its selected pickle.
    for name, digest in _read(evidence["artifact_manifest.json"]).items():
        path = trial.joinpath(*_relative_parts(name)).resolve(strict=True)
        if not path.is_relative_to(trial.resolve()) or file_hash(path) != digest:
            raise ValueError("selected_trial_changed")
    artifact = trial / "artifacts" / "2026" / "research.joblib"
    contents = artifact.read_bytes()
    if hashlib.sha256(contents).hexdigest() != ARTIFACT_SHA256:
        raise ValueError("artifact_hash_mismatch")
    import joblib
    bundle = joblib.load(io.BytesIO(contents))
    validate_bundle(bundle)
    manifest = {
        "schema": "pace74-shadow-package-v1", "candidate_model_id": CANDIDATE_ID,
        "artifact_sha256": ARTIFACT_SHA256, "feature_count": 74,
        "reference_only": True, "purchase_eligible": False, "published_at": _now(),
        "source_pins": pins, "evidence_pins": EVIDENCE_PINS,
        "temperature": TEMPERATURE, "training_date_to": "20241228",
    }
    manifest["publication_id"] = _digest(manifest)
    destination.mkdir(parents=True, exist_ok=False)
    for name, source in evidence.items():
        shutil.copyfile(source, destination / name)
    (destination / "research.joblib").write_bytes(contents)
    _validate_evidence(destination)
    (destination / "manifest.json").write_text(_json(manifest), encoding="utf-8")
    (destination / "SEALED").write_text(manifest["publication_id"], encoding="ascii")
    return manifest


def validate_bundle(bundle):
    from training.db_context_features import FEATURE_GROUPS
    columns = list(dict.fromkeys(column for group in GROUPS for column in FEATURE_GROUPS[group]))
    spec, fitted = bundle["specification"], bundle["fit"]
    calibration, model, encoder = fitted["calibration"], fitted["model"], fitted["encoder"]
    if (bundle.get("schema") != "pace-research-v2"
            or bundle.get("reference_only") is not True or bundle.get("production_eligible") is not False
            or bundle.get("training_date_to") != "20241228"
            or spec != {"id": "c09-minleaf240", "groups": GROUPS, "objective": "win",
                        "head": "none", "mode": "legacy", "fields": False, "interactions": False,
                        "time_feature": False,
                        "params": {"num_boost_round": 600, "num_leaves": 31,
                                   "min_data_in_leaf": 240, "lambda_l2": 20.0}}
            or len(columns) != 74 or fitted["feature_columns"] != columns
            or list(encoder.feature_columns) != columns or list(encoder.requested_columns) != columns
            or model.num_feature() != 74 or model.feature_name() != columns
            or fitted.get("objective") != "win"
            or calibration.get("temperature") != TEMPERATURE
            or calibration.get("fit_uses_test") is not False
            or calibration.get("date_from") != "20250105" or calibration.get("date_to") != "20251228"
            or calibration.get("boundary_reached") is not False
            or model.current_iteration() != 600
            or any(model.params.get(name) != value for name, value in {
                "learning_rate": .05, "num_leaves": 31, "min_data_in_leaf": 240,
                "lambda_l2": 20., "seed": 42}.items())):
        raise ValueError("bundle_contract_mismatch")


def load_package():
    """Hash bytes before unpickling AND cache reuse; no external study dependency."""
    global _CACHE
    directory = PACKAGE_DIR
    documents = _validate_evidence(directory)
    manifest = _read(directory / "manifest.json")
    publication_id = manifest.get("publication_id")
    unsigned = {key: value for key, value in manifest.items() if key != "publication_id"}
    expected_pins = _source_pins(documents["task-ledger.json"])
    if (manifest.get("schema") != "pace74-shadow-package-v1"
            or manifest.get("candidate_model_id") != CANDIDATE_ID
            or manifest.get("artifact_sha256") != ARTIFACT_SHA256
            or manifest.get("feature_count") != 74 or manifest.get("temperature") != TEMPERATURE
            or manifest.get("reference_only") is not True or manifest.get("purchase_eligible") is not False
            or manifest.get("training_date_to") != "20241228"
            or manifest.get("source_pins") != expected_pins or manifest.get("evidence_pins") != EVIDENCE_PINS
            or publication_id != _digest(unsigned)
            or (directory / "SEALED").read_text(encoding="ascii") != publication_id):
        raise ValueError("package_seal_mismatch")
    _verify_sources(manifest["source_pins"])
    contents = (directory / "research.joblib").read_bytes()
    if hashlib.sha256(contents).hexdigest() != ARTIFACT_SHA256:
        raise ValueError("artifact_hash_mismatch")
    if _CACHE is None or _CACHE[0] != publication_id:
        import joblib
        _CACHE = (publication_id, joblib.load(io.BytesIO(contents)))
    bundle = _CACHE[1]
    validate_bundle(bundle)
    if any(bundle["sources"].get(name) != digest for name, digest in
           _historical_pins(documents["task-ledger.json"]).items()
           if name in bundle["sources"]):
        raise ValueError("bundle_source_mismatch")
    return bundle, manifest


def runtime_revision() -> dict:
    """Cheap bounded read-only cache identity, including broken/disabled packages."""
    files = [PACKAGE_DIR / name for name in (*EVIDENCE_PINS, "research.joblib", "manifest.json", "SEALED")]
    files += [_source_path(name) for name in CURRENT_SOURCES]
    task_path = PACKAGE_DIR / "task-ledger.json"
    try:
        if file_hash(task_path) == EVIDENCE_PINS["task-ledger.json"]:
            files += [_source_path(name) for name in _historical_pins(_read(task_path))]
    except (OSError, ValueError, KeyError):
        pass
    values = []
    for path in files:
        try:
            digest = file_hash(path)
        except OSError:
            digest = "unreadable"
        values.append([str(path), digest])
    return {"enabled": os.environ.get("PACE74_SHADOW_ENABLED", "").strip().lower(), "files": values}


def score_candidate(bundle, target, features):
    import numpy as np
    from training.pace_research import race_probabilities
    validate_bundle(bundle)
    fitted = bundle["fit"]
    if not target.index.equals(features.index) or features.columns.duplicated().any():
        raise ValueError("candidate_input_misaligned")
    matrix = features.loc[:, fitted["feature_columns"]]
    scores = np.asarray(fitted["model"].predict(fitted["encoder"].transform(matrix), num_threads=2))
    return scores, race_probabilities(scores, target.race_id, fitted["calibration"]["temperature"])


def _base(primary_model_id, *, status="unavailable", issue=None):
    return {
        "schema": "pace74-shadow-comparison-v1", "status": status, "candidate_model_id": CANDIDATE_ID,
        "primary_model_id": str(primary_model_id), "reference_only": True, "purchase_eligible": False,
        "captured_at": None, "input_revision": None, "input_aligned": False, "candidate_feature_count": 74,
        "odds_observed_at": None, "odds_verified": False, "observation_kind": "unverified",
        "comparison_id": None, "recorded": False, "issue_codes": [issue] if issue else [],
        "promotion": _promotion(), "predictions": [],
    }


def unavailable(primary_model_id, issue="candidate_inference_failed", *, status="error"):
    return _base(primary_model_id, status=status, issue=issue)


def is_shadow_path(path):
    return Path(path).resolve().is_relative_to(PACKAGE_DIR.resolve())


def is_reference_only_artifact(path):
    """Activation-only guard for already selected local artifacts.

    Renaming or recompressing the research pickle cannot create a flat,
    approved serving model. Ordinary legacy activation behavior is unchanged.
    """
    path = Path(path)
    if is_shadow_path(path):
        return True
    contents = path.read_bytes()
    if hashlib.sha256(contents).hexdigest() == ARTIFACT_SHA256:
        return True
    import joblib
    try:
        bundle = joblib.load(io.BytesIO(contents))
    except Exception:
        return False
    if not isinstance(bundle, dict):
        return False
    specification = bundle.get("specification")
    return (str(bundle.get("schema", "")).startswith(("pace-research-", "pace74-shadow-"))
            or bundle.get("candidate_model_id") == CANDIDATE_ID
            or bundle.get("model_id") == CANDIDATE_ID
            or bundle.get("_pace74_shadow") is True
            or (isinstance(specification, dict) and specification.get("id") == "c09-minleaf240"
                and bundle.get("production_eligible") is False))


def run_shadow(*, db_path, race_info, horses, primary_model_id, input_revision,
               captured_at, primary_artifact_sha256):
    """Called ONLY in the admitted primary CPU worker, after primary inference."""
    result = _base(primary_model_id)
    result.update(captured_at=captured_at, input_revision=input_revision)
    if os.environ.get("PACE74_SHADOW_ENABLED", "").strip().lower() in {"false", "0", "no", "off"}:
        result.update(status="skipped", issue_codes=["shadow_disabled"])
        return result
    if not PACKAGE_DIR.is_dir():
        result["issue_codes"] = ["package_missing"]
        return result
    try:
        from training.db_context_features import build_serving_features
        from training.db_shared_features import _current_card
        from services.prediction_freshness import data_revision
        bundle, manifest = load_package()
        if input_revision is None or data_revision(db_path) != input_revision:
            raise ValueError("input_revision_changed")
        target = _current_card(race_info, horses)
        if target.race_id.nunique() != 1 or str(target.race_id.iloc[0]) != str(race_info.get("race_id")):
            raise ValueError("race_identity_mismatch")
        features, audit = build_serving_features(db_path, race_info, horses)
        if audit.get("current_outcomes_consumed") is not False or audit.get("same_day_or_future_history_used") is not False:
            raise ValueError("history_scope_unverified")
        scores, probabilities = score_candidate(bundle, target, features)
        if data_revision(db_path) != input_revision:
            raise ValueError("input_revision_changed")
        records = []
        order = sorted(range(len(target)), key=lambda i: (-float(probabilities[i]), int(target.horse_number.iloc[i])))
        ranks = {position: rank for rank, position in enumerate(order, 1)}
        for i, row in enumerate(target.to_dict("records")):
            records.append({"horse_number": int(row["horse_number"]), "horse_id": str(row["horse_id"]),
                            "candidate_probability": float(probabilities[i]), "candidate_raw": float(scores[i]),
                            "candidate_rank": ranks[i]})
        # Canonical current cards discard all result/market fields. Fingerprint
        # their actual allowed inputs separately from observation/odds evidence.
        card_columns = [name for name in target if name not in {
            "finish", "time_seconds", "last_3f_time", "odds", "popularity", "_ctx_history_corners"}]
        card_fingerprint = hashlib.sha256(target[card_columns].to_json(orient="split", date_format="iso").encode()).hexdigest()
        result.update(
            status="compared", input_aligned=True, predictions=records,
            race_id=str(target.race_id.iloc[0]), expected_count=len(target), card_fingerprint=card_fingerprint,
            race_metadata_fingerprint=_race_fingerprint(race_info),
            primary_artifact_sha256=primary_artifact_sha256, candidate_artifact_sha256=ARTIFACT_SHA256,
            publication_id=manifest["publication_id"], input_observed_at=captured_at,
            input_capture_kind="worker_frame_copy_not_provider_capture",
            inference_completed_at=_now(),
            known_outcomes=_known_outcomes(horses.to_dict("records")),
        )
    except Exception as exc:
        safe_codes = {"input_revision_changed", "artifact_hash_mismatch", "source_pin_mismatch",
                      "source_restart_required", "serving_source_restart_required", "package_seal_mismatch",
                      "bundle_contract_mismatch", "race_identity_mismatch", "history_scope_unverified",
                      "loaded_source_mismatch", "loaded_source_import_mismatch", "loaded_source_origin_mismatch",
                      "loaded_source_unverifiable", "loaded_source_binding_missing"}
        reason = exc.args[0] if isinstance(exc, ValueError) and exc.args else None
        code = reason if isinstance(reason, str) and reason in safe_codes else "candidate_inference_failed"
        result.update(status="skipped" if code == "input_revision_changed" else "error",
                      input_aligned=False, predictions=[], issue_codes=[code])
    return result


def _known_outcomes(records):
    for row in records:
        for name in ("finish", "finish_position", "finish_order", "time_seconds", "finish_time", "win", "place"):
            value = row.get(name)
            if value is not None and str(value).strip().lower() not in {"", "0", "0.0", "nan", "none"}:
                return True
    return False


def _promotion(counts=None):
    counts = counts or {"pre_race_unique_races": 0}
    reasons = ["prospective_evidence_missing" if counts.get("pre_race_unique_races", 0) == 0
               else "prospective_acceptance_evaluation_pending",
               "calibration_ece_worsening_not_waived", "acceptance_contract_evaluation_pending",
               "durable_two_person_production_approval_required"]
    return {"status": "blocked", "eligible": False, "reasons": reasons,
            "acceptance_contract_id": "model-acceptance-v1", "counts": counts}


def evidence_status(store_path=None):
    path = STORE_PATH if store_path is None else Path(store_path)
    counts = {name + "_observations": 0 for name in ("pre_race", "historical", "unverified")}
    counts["pre_race_unique_races"] = 0
    if path.is_file():
        try:
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1) as db:
                for kind, count in db.execute("SELECT observation_kind,COUNT(*) FROM paired_observations GROUP BY observation_kind"):
                    if kind + "_observations" in counts:
                        counts[kind + "_observations"] = count
                counts["pre_race_unique_races"] = db.execute(
                    "SELECT COUNT(DISTINCT race_id) FROM paired_observations WHERE observation_kind='pre_race'"
                ).fetchone()[0]
        except sqlite3.Error:
            return {**_promotion(counts), "issue_codes": ["evidence_query_failed"]}
    return _promotion(counts)


def _append_observation(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    identifier = _digest({key: value for key, value in payload.items() if key != "finalized_at"})
    with sqlite3.connect(path, timeout=1) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS paired_observations(
            comparison_id TEXT PRIMARY KEY, race_id TEXT NOT NULL, captured_at TEXT NOT NULL,
            observation_kind TEXT NOT NULL CHECK(observation_kind IN ('pre_race','historical','unverified')),
            payload TEXT NOT NULL)""")
        for operation in ("UPDATE", "DELETE"):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS paired_no_{operation.lower()}
                BEFORE {operation} ON paired_observations
                BEGIN SELECT RAISE(ABORT,'paired observations are immutable'); END""")
        db.execute("INSERT OR IGNORE INTO paired_observations VALUES(?,?,?,?,?)", (
            identifier, payload["race_id"], payload["captured_at"], payload["observation_kind"], _json(payload)))
        stored = db.execute("SELECT payload FROM paired_observations WHERE comparison_id=?", (identifier,)).fetchone()
        if stored is None or _digest({key: value for key, value in json.loads(stored[0]).items()
                                      if key != "finalized_at"}) != identifier:
            raise ValueError("evidence_receipt_missing")
    return identifier


def _odds_evidence(predictions, context, race_info, now):
    from betting.value_policy import _verified_odds_capture
    sources = context.get("sources", [])
    by_number = {}
    for source in sources:
        number = _positive_int(source.get("horse_number"))
        if number in by_number:
            return False, None, []
        by_number[number] = source
    observations, evidence = [], []
    for row in predictions:
        source = by_number.get(int(row["horse_number"]), {})
        capture = source.get("odds_evidence")
        observed = _instant(source.get("observed_at"))
        if (row["odds"] is None or observed is None or observed > now
                or now - observed > timedelta(minutes=30)
                or not isinstance(capture, dict) or _instant(capture.get("observed_at")) != observed
                or source.get("has_result") is not False
                or not _verified_odds_capture(capture, race_id=race_info.get("race_id"),
                                             horse_number=row["horse_number"], odds=row["odds"])):
            return False, None, []
        observations.append(observed)
        # Only market provenance, no source URL/query or unrelated user fields.
        evidence.append({name: capture.get(name) for name in
                         ("schema", "race_id", "response_sha256", "observed_at", "odds_status", "odds_by_horse_number")})
    return bool(observations), min(observations).isoformat() if observations else None, evidence


def finalize_comparison(*, raw, predictions, race_info, source_records, context,
                        now=None, store_path=None):
    """Attach ONLY after primary betting/odds finalization. Never mutate its inputs."""
    result = _base((raw or {}).get("primary_model_id", "unknown"))
    if not isinstance(raw, dict):
        result["issue_codes"] = ["shadow_not_computed"]
        return result
    for name in ("status", "captured_at", "input_revision", "issue_codes"):
        if name in raw:
            result[name] = raw[name]
    if raw.get("status") != "compared":
        return result
    try:
        now = datetime.now(timezone.utc) if now is None else now
        count = _positive_int(raw.get("expected_count"))
        expected = _positive_int(race_info.get("num_horses"))
        if (not raw.get("input_aligned") or count is None or count < 2 or count != expected
                or str(raw.get("race_id")) != str(race_info.get("race_id"))
                or (raw.get("race_metadata_fingerprint") is not None
                    and raw["race_metadata_fingerprint"] != _race_fingerprint(race_info))
                or len(predictions) != count or len(raw["predictions"]) != count):
            raise ValueError("cohort_mismatch")
        candidates, seen_ids = {}, set()
        for row in raw["predictions"]:
            number, horse_id = _positive_int(row.get("horse_number")), str(row.get("horse_id") or "")
            if number is None or number in candidates or (horse_id and horse_id in seen_ids):
                raise ValueError("cohort_mismatch")
            candidates[number] = row
            if horse_id:
                seen_ids.add(horse_id)
        if source_records:
            if len(source_records) != count:
                raise ValueError("source_cohort_mismatch")
            source_numbers = set()
            for source in source_records:
                number = source.get("horse_number")
                if number is None:
                    number = source.get("horse_no")
                number = _positive_int(number)
                candidate = candidates.get(number)
                horse_id = str(source.get("horse_id") or "")
                if (candidate is None or number in source_numbers
                        or horse_id != str(candidate.get("horse_id") or "")
                        or (source.get("race_id") is not None
                            and str(source["race_id"]) != str(raw["race_id"]))):
                    raise ValueError("source_cohort_mismatch")
                source_numbers.add(number)
        rows, paired, numbers, primary_ranks, candidate_ranks = [], [], set(), set(), set()
        candidate_sum = 0.
        for row in predictions:
            number = _positive_int(row.get("horse_number"))
            candidate = candidates.get(number)
            if candidate is None or number in numbers:
                raise ValueError("cohort_mismatch")
            horse_id, candidate_id = str(row.get("horse_id") or ""), str(candidate.get("horse_id") or "")
            if (horse_id or candidate_id) and horse_id != candidate_id:
                raise ValueError("horse_identity_mismatch")
            displayed_probability = row.get("p_norm")
            if displayed_probability is None:
                displayed_probability = row.get("win_probability")
            primary_probability = _number(displayed_probability)
            candidate_probability = _number(candidate.get("candidate_probability"))
            primary_rank, candidate_rank = _positive_int(row.get("predicted_rank")), _positive_int(candidate.get("candidate_rank"))
            if (primary_probability is None or candidate_probability is None
                    or not 0 <= primary_probability <= 1 or not 0 <= candidate_probability <= 1
                    or primary_rank is None or candidate_rank is None
                    or primary_rank > count or candidate_rank > count
                    or primary_rank in primary_ranks or candidate_rank in candidate_ranks):
                raise ValueError("probability_or_rank_invalid")
            odds = _number(row.get("odds"))
            odds = odds if odds is not None and odds > 1 else None
            public = {"horse_number": number, "horse_name": str(row.get("horse_name") or ""),
                      "primary_probability": primary_probability, "primary_rank": primary_rank,
                      "candidate_probability": candidate_probability, "candidate_rank": candidate_rank,
                      "odds": odds, "expected_value": candidate_probability * odds if odds is not None else None}
            rows.append(public)
            paired.append({**public, "horse_id": horse_id, "primary_raw": _number(row.get("p_raw")),
                           "primary_win_probability": _number(row.get("win_probability")),
                           "candidate_raw": _number(candidate.get("candidate_raw"))})
            candidate_sum += candidate_probability
            numbers.add(number)
            primary_ranks.add(primary_rank)
            candidate_ranks.add(candidate_rank)
        # Use the final displayed p_norm verbatim, never recalibrate either model.
        if not math.isclose(candidate_sum, 1., abs_tol=1e-8):
            raise ValueError("candidate_probability_invalid")
        captured = _instant(raw.get("captured_at"))
        completed = _instant(raw.get("inference_completed_at"))
        clock_verified = (now.tzinfo is not None and captured is not None and completed is not None
                          and captured <= completed <= now)
        from betting.value_policy import _post_time
        post = _post_time(race_info)
        known_outcomes = raw.get("known_outcomes") is not False or _known_outcomes(source_records)
        historical = known_outcomes or (post is not None and post <= now)
        if post is None:
            day = str(race_info.get("date", "")).replace("-", "").replace("/", "")
            historical = historical or (len(day) == 8 and day < now.astimezone(timezone(timedelta(hours=9))).strftime("%Y%m%d"))
        verified, observed, odds_evidence = _odds_evidence(rows, context, race_info, now)
        # The current serving feature adapter explicitly reports unverified
        # card capture. Do not manufacture authoritative pre-start evidence
        # from wall-clock inference time or a cached DB timestamp.
        kind = "historical" if historical else "unverified"
        issues = [] if clock_verified else ["observation_clock_unverified"]
        if kind == "unverified":
            issues.append("authoritative_card_capture_unverified")
        result.update(status="compared", input_aligned=True, predictions=rows, issue_codes=issues,
                      observation_kind=kind, odds_verified=verified and not historical,
                      odds_observed_at=observed)
        payload = {
            "schema": result["schema"], "race_id": str(raw["race_id"]),
            "primary_model_id": result["primary_model_id"], "candidate_model_id": CANDIDATE_ID,
            "primary_artifact_sha256": raw["primary_artifact_sha256"],
            "candidate_artifact_sha256": raw["candidate_artifact_sha256"],
            "publication_id": raw["publication_id"], "captured_at": raw["captured_at"],
            "input_observed_at": raw["input_observed_at"], "inference_completed_at": raw["inference_completed_at"],
            "finalized_at": now.isoformat(), "input_revision": raw["input_revision"],
            "card_fingerprint": raw["card_fingerprint"], "observation_kind": kind,
            "race_metadata_fingerprint": raw.get("race_metadata_fingerprint"),
            "input_capture_kind": raw.get("input_capture_kind", "unverified"),
            "odds_observed_at": observed, "odds_verified": result["odds_verified"],
            "clock_verified": clock_verified, "odds_evidence": odds_evidence,
            "predictions": paired, "reference_only": True, "purchase_eligible": False,
        }
        try:
            result["comparison_id"] = _append_observation(STORE_PATH if store_path is None else store_path, payload)
            result["recorded"] = True
        except (sqlite3.Error, OSError, ValueError, TypeError):
            result["issue_codes"].append("evidence_write_failed")
        result["promotion"] = evidence_status(store_path)
        if result["promotion"].get("issue_codes"):
            result["recorded"] = False
            result["comparison_id"] = None
            result["issue_codes"].extend(result["promotion"]["issue_codes"])
    except Exception:
        result.update(status="skipped", input_aligned=False, predictions=[], recorded=False,
                      issue_codes=["paired_input_invalid"])
    return result

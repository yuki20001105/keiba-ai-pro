"""Decode a bounded sanitized browser projection into exact canonical JSON bytes.

The transport is compression, not encryption. No HTTP, credentials, application
imports, or database access. Both paths must remain in the isolated Phase 2 root;
existing outputs are never overwritten, and rejected content is never printed.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
PHASE2 = ROOT / "reports/generated/model-experiments/netkeiba-authenticated-phase2-v1"
CODEC = "lzw16le-base64-v1"
LZSS_CODEC = "lzss16le-base64-v1"
MAX_DECODED_BYTES = 2 * 1024 * 1024
MAX_ENCODED_CHARS = ((MAX_DECODED_BYTES * 2 + 2) // 3) * 4
SECRET_PATTERNS = (
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_.-]{20,}"),
    re.compile(r"(?i)(?:access_token|refresh_token|session_cookie|password)\s*[=:]\s*['\"]?[A-Za-z0-9+/_.-]{12,}"),
    re.compile(r"(?i)<(?:script|form|input|iframe)\b|\bon[a-z]+\s*="),
    re.compile(r"(?i)(?:document\.cookie|localStorage|sessionStorage)\b"),
    re.compile(r"(?i)[?&](?:token|access_token|refresh_token|session|session_id|cookie|auth|password|key)="),
)
SECRET_KEYS = frozenset(("password", "passwd", "access_token", "refresh_token", "session_cookie",
                         "token", "session_id", "login_id", "cookie", "cookies", "authorization",
                         "set-cookie", "email", "email_address"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def decode_lzw16le(encoded, max_bytes=MAX_DECODED_BYTES):
    require(isinstance(encoded, str) and encoded, "nonempty base64 packet required")
    require(type(max_bytes) is int and 0 < max_bytes <= MAX_DECODED_BYTES, "invalid decoder size bound")
    require(len(encoded) <= MAX_ENCODED_CHARS, "compressed packet exceeds size bound")
    try:
        packed = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid packet base64") from exc
    require(bool(packed) and len(packed) % 2 == 0, "invalid uint16 code stream length")
    first = int.from_bytes(packed[:2], "little")
    require(first < 256, "invalid first LZW code")
    dictionary = [bytes((n,)) for n in range(256)]
    previous = dictionary[first]
    output = bytearray(previous)
    for offset in range(2, len(packed), 2):
        code = int.from_bytes(packed[offset:offset + 2], "little")
        if code < len(dictionary):
            entry = dictionary[code]
        elif code == len(dictionary) and len(dictionary) < 65536:
            entry = previous + previous[:1]
        else:
            raise ValueError("invalid LZW dictionary code")
        require(len(output) + len(entry) <= max_bytes, "decoded packet exceeds size bound")
        output.extend(entry)
        if len(dictionary) < 65536:
            dictionary.append(previous + entry[:1])
        previous = entry
    return bytes(output)


def decode_lzss16le(encoded, max_bytes=MAX_DECODED_BYTES):
    """Decode flag groups with literal bytes or overlapping backward references."""
    require(isinstance(encoded, str) and encoded, "nonempty base64 packet required")
    require(type(max_bytes) is int and 0 < max_bytes <= MAX_DECODED_BYTES, "invalid decoder size bound")
    require(len(encoded) <= MAX_ENCODED_CHARS, "compressed packet exceeds size bound")
    try:
        packed = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid packet base64") from exc
    output = bytearray()
    offset = 0
    while offset < len(packed):
        flags = packed[offset]
        offset += 1
        require(offset < len(packed), "LZSS flag group has no tokens")
        for bit in range(8):
            if offset >= len(packed):
                require(flags >> bit == 0, "LZSS final flag bits have no tokens")
                break
            if flags & (1 << bit):
                require(offset + 3 <= len(packed), "truncated LZSS reference")
                distance = int.from_bytes(packed[offset:offset + 2], "little")
                length = packed[offset + 2] + 3
                offset += 3
                require(1 <= distance <= len(output), "invalid LZSS reference distance")
                require(len(output) + length <= max_bytes, "decoded packet exceeds size bound")
                for _ in range(length):
                    output.append(output[-distance])
            else:
                require(len(output) < max_bytes, "decoded packet exceeds size bound")
                output.append(packed[offset])
                offset += 1
    require(bool(output), "empty decoded LZSS packet")
    return bytes(output)


def _pairs_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON object key")
        result[key] = value
    return result


def _reject_nonfinite(_value):
    raise ValueError("nonfinite JSON value")


def scan_sanitized(value):
    """Bounded marker guard, not a proof against every possible secret format."""
    if isinstance(value, dict):
        for key, child in value.items():
            require(key.lower() not in SECRET_KEYS, "possible sensitive data key; no output written")
            scan_sanitized(key)
            scan_sanitized(child)
    elif isinstance(value, list):
        for child in value:
            scan_sanitized(child)
    elif isinstance(value, str):
        require(not any(pattern.search(value) for pattern in SECRET_PATTERNS),
                "possible sensitive or unsanitized content; no output written")


def decode_packet(packet):
    require(isinstance(packet, dict) and packet.get("codec") in (CODEC, LZSS_CODEC), "unsupported packet codec")
    expected = packet.get("canonical_sha256")
    require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected), "invalid canonical SHA256")
    decoder = decode_lzw16le if packet["codec"] == CODEC else decode_lzss16le
    raw = decoder(packet.get("data"))
    measured = hashlib.sha256(raw).hexdigest()
    require(measured == expected, "canonical JSON SHA256 mismatch")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs_without_duplicates,
                           parse_constant=_reject_nonfinite)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("packet is not bounded UTF-8 JSON") from exc
    require(isinstance(value, dict), "capture JSON must be an object")
    try:
        require(canonical_bytes(value) == raw, "packet JSON is not exact sorted-key canonical JSON")
        scan_sanitized(value)
    except RecursionError as exc:
        raise ValueError("capture JSON nesting exceeds safe bound") from exc
    return raw, value


def _inside(path, root):
    resolved = Path(path).resolve()
    require(resolved.is_relative_to(Path(root).resolve()), "path outside isolated Phase 2 root")
    return resolved


def validate_source_record(record):
    """Bind a saved projection record to its public race URL and observed time."""
    require(isinstance(record, dict), "source record must be an object")
    race_id = record.get("race_id")
    require(isinstance(race_id, str) and re.fullmatch(r"\d{12}", race_id), "invalid source race_id")
    url = record.get("source_url", record.get("url"))
    require(isinstance(url, str), "source URL required")
    parsed = urlparse(url)
    require(parsed.scheme == "https" and parsed.hostname == "race.netkeiba.com" and
            parsed.username is None and parsed.password is None and not parsed.fragment and
            parsed.port in (None, 443) and parsed.path.startswith("/race/") and parsed.path.endswith(".html"),
            "unexpected source URL origin or path")
    query = parse_qs(parsed.query)
    require(query.get("race_id") == [race_id], "source URL race_id mismatch")
    require(set(query) <= {"race_id", "type", "rf", "mode", "page", "master"}, "unapproved source URL query parameter")
    require("master" not in query or query["master"] == ["1"], "unobserved Master URL parameter value")
    observed = record.get("observed_at", record.get("fetched_at"))
    require(isinstance(observed, str), "source observation timestamp required")
    timestamp = datetime.fromisoformat(observed.replace("Z", "+00:00"))
    require(timestamp.tzinfo is not None and timestamp.utcoffset() is not None,
            "source observation timestamp must include timezone")
    require(record.get("raw_http_available") is False, "raw_http_available must be explicitly false")


def validate_capture_payload(value):
    """Accept one bounded projection or a bounded historical summary batch.

    Counts in historical records remain counts; no fabricated horse rows are
    created, and no present-day display is promoted to historical PIT evidence.
    """
    require(isinstance(value, dict), "capture payload must be an object")
    schema = value.get("schema", value.get("schema_version"))
    if schema == "phase2-historical-availability-batch-v1":
        keys = [key for key in ("records", "projections") if key in value]
        require(len(keys) == 1, "historical batch needs exactly one records/projections array")
        records = value[keys[0]]
        require(isinstance(records, list) and 0 < len(records) <= 80, "historical batch size must be 1..80")
        require(value.get("raw_http_available", False) is False, "batch raw HTTP is not supported")
        for record in records:
            validate_source_record(record)
            require(record.get("capture_mode") == "historical", "batch record must remain historical")
            require(record.get("source_page_type") in ("training", "speed"), "unsupported historical page type")
            require(type(record.get("rows")) is int and 0 <= record["rows"] <= 100,
                    "historical rows must be a bounded integer count")
            if "schema" in record:
                require(record["schema"] == "phase2-historical-availability-projection-v1",
                        "unsupported historical record schema")
        return {"payload_kind": "historical_availability_batch", "record_count": len(records),
                "record_array_key": keys[0], "each_record_source_binding_verified": True,
                "rows_are_summary_counts": True, "historical_strict_pit": False}
    validate_source_record(value)
    return {"payload_kind": "single_bounded_DOM_projection", "record_count": 1,
            "each_record_source_binding_verified": True}


def ingest(packet_path, output_path, root=PHASE2):
    packet_path = _inside(packet_path, root)
    output_path = _inside(output_path, root)
    require(packet_path.is_relative_to((Path(root) / "transport").resolve()), "packet must be inside transport")
    require(output_path.is_relative_to((Path(root) / "captures").resolve()), "output must be inside captures")
    require(packet_path.suffix == ".json" and output_path.suffix == ".json", "JSON paths required")
    require(not output_path.exists(), "append-only output already exists")
    require(packet_path.stat().st_size <= MAX_ENCODED_CHARS + 65536, "packet file exceeds size bound")
    packet_bytes = packet_path.read_bytes()
    packet_hash = hashlib.sha256(packet_bytes).hexdigest()
    packet = json.loads(packet_bytes.decode("utf-8-sig"), object_pairs_hook=_pairs_without_duplicates,
                        parse_constant=_reject_nonfinite)
    raw, value = decode_packet(packet)
    payload_receipt = validate_capture_payload(value)
    require(hashlib.sha256(packet_path.read_bytes()).hexdigest() == packet_hash, "transport changed during decode")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("xb") as handle:
        handle.write(raw)
    result = {"codec": packet["codec"], "transport_file": packet_path.relative_to(Path(root).resolve()).as_posix(),
              "transport_sha256": packet_hash, "source_file": output_path.relative_to(Path(root).resolve()).as_posix(),
              "canonical_sha256": hashlib.sha256(raw).hexdigest(), "decoded_bytes": len(raw),
              "canonical_bytes_preserved": True, "bounded_sanitization_scan": "PASS",
              "raw_http_available": False, "payload_validation": payload_receipt}
    require(hashlib.sha256(output_path.read_bytes()).hexdigest() == packet["canonical_sha256"], "written capture hash mismatch")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(ingest(args.packet, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

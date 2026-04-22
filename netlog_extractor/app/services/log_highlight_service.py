from __future__ import annotations

import json
import re
from configparser import ConfigParser
from pathlib import Path
from typing import Any

COLOR_KEYS = ("underline", "red", "green", "yellow", "blue", "magenta", "cyan")
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
BUILTIN_DIR = ROOT_DIR / "app" / "highlight_presets"
CUSTOM_DIR = ROOT_DIR / "state" / "log_highlight_presets"


class HighlightPresetError(ValueError):
    pass


def _ensure_dirs() -> None:
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "").strip().lower()).strip("_")
    return slug or "preset"


def _split_ini_rule_value(value: str) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    if "\n" in raw:
        return [item.strip() for item in raw.splitlines() if item.strip()]
    if "||" in raw:
        return [item.strip() for item in raw.split("||") if item.strip()]
    return [raw]


def _validate_regex_list(items: list[str], preset_id: str, color: str) -> list[str]:
    validated: list[str] = []
    for item in items:
        rule = str(item or "").strip()
        if not rule:
            continue
        try:
            re.compile(rule)
        except re.error as exc:
            raise HighlightPresetError(f"Invalid regex in preset={preset_id} color={color}: {exc}") from exc
        validated.append(rule)
    return validated


def _normalize_preset(data: dict[str, Any], source: str, file_path: Path | None = None, preset_id: str | None = None) -> dict[str, Any]:
    resolved_id = _slugify(preset_id or data.get("id") or data.get("name") or (file_path.stem if file_path else "preset"))
    colors_raw = data.get("colors") or {}
    if not isinstance(colors_raw, dict):
        raise HighlightPresetError(f"Invalid colors block in preset={resolved_id}")

    normalized_colors: dict[str, list[str]] = {}
    for color in COLOR_KEYS:
        raw_items = colors_raw.get(color, [])
        if isinstance(raw_items, str):
            raw_items = [raw_items]
        if not isinstance(raw_items, list):
            raise HighlightPresetError(f"Color rules must be a list in preset={resolved_id} color={color}")
        normalized_colors[color] = _validate_regex_list(list(raw_items), resolved_id, color)

    extends = data.get("extends")
    if extends is not None:
        extends = _slugify(str(extends))

    return {
        "id": resolved_id,
        "name": str(data.get("name") or resolved_id),
        "description": str(data.get("description") or "").strip(),
        "format_version": int(data.get("format_version") or 1),
        "extends": extends,
        "source": source,
        "path": str(file_path) if file_path else "",
        "colors": normalized_colors,
    }


def _load_json_file(path: Path, source: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HighlightPresetError(f"Failed to load preset JSON: {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise HighlightPresetError(f"Preset JSON must be an object: {path.name}")
    return _normalize_preset(data, source=source, file_path=path)


def _load_ini_text(content: str, source: str, preset_id: str | None = None) -> dict[str, Any]:
    parser = ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        parser.read_string(content)
    except Exception as exc:
        raise HighlightPresetError(f"Failed to parse preset INI: {exc}") from exc

    if parser.has_section("Preset"):
        meta = parser["Preset"]
    elif parser.has_section("CustomSyntax"):
        meta = parser["CustomSyntax"]
    else:
        raise HighlightPresetError("INI preset requires [Preset] or [CustomSyntax] section")

    colors: dict[str, list[str]] = {key: [] for key in COLOR_KEYS}
    source_sections = []
    if parser.has_section("Rules"):
        source_sections.append(parser["Rules"])
    if parser.has_section("CustomSyntax"):
        source_sections.append(parser["CustomSyntax"])

    for section in source_sections:
        for color in COLOR_KEYS:
            for candidate in (color, color.capitalize(), color.title()):
                if candidate in section:
                    colors[color].extend(_split_ini_rule_value(section[candidate]))
                    break

    data = {
        "id": preset_id or meta.get("id") or meta.get("name"),
        "name": meta.get("name") or meta.get("Name") or preset_id or "Imported preset",
        "description": meta.get("description") or meta.get("Description") or "",
        "extends": meta.get("extends") or meta.get("Extends") or None,
        "format_version": meta.get("format_version") or meta.get("FormatVersion") or 1,
        "colors": colors,
    }
    return _normalize_preset(data, source=source, preset_id=preset_id)


def _load_preset(path: Path, source: str) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return _load_json_file(path, source)
    if suffix in {".ini", ".cfg"}:
        return _load_ini_text(path.read_text(encoding="utf-8"), source=source, preset_id=path.stem)
    raise HighlightPresetError(f"Unsupported preset format: {path.name}")


def list_presets() -> list[dict[str, Any]]:
    _ensure_dirs()
    items: list[dict[str, Any]] = []
    for path in sorted(BUILTIN_DIR.glob("*.json")):
        items.append(_load_preset(path, source="builtin"))
    for path in sorted(CUSTOM_DIR.glob("*")):
        if path.suffix.lower() not in {".json", ".ini", ".cfg"}:
            continue
        items.append(_load_preset(path, source="custom"))
    return items


def _preset_map() -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in list_presets()}


def resolve_preset(preset_id: str) -> dict[str, Any]:
    preset_id = _slugify(preset_id)
    items = _preset_map()
    if preset_id not in items:
        raise HighlightPresetError(f"Preset not found: {preset_id}")

    def build(current_id: str, trail: set[str]) -> dict[str, Any]:
        if current_id in trail:
            raise HighlightPresetError(f"Circular preset extends detected: {current_id}")
        current = dict(items[current_id])
        base_id = current.get("extends")
        if not base_id:
            return current
        base = build(base_id, trail | {current_id})
        merged_colors = {color: list(base.get("colors", {}).get(color, [])) for color in COLOR_KEYS}
        for color in COLOR_KEYS:
            merged_colors[color].extend(current.get("colors", {}).get(color, []))
        current["colors"] = merged_colors
        return current

    return build(preset_id, set())


def export_preset_text(preset_id: str, fmt: str) -> tuple[str, str, str]:
    preset = resolve_preset(preset_id)
    fmt = str(fmt or "json").strip().lower()
    if fmt == "json":
        payload = {
            "id": preset["id"],
            "name": preset["name"],
            "description": preset.get("description", ""),
            "format_version": preset.get("format_version", 1),
            "extends": preset.get("extends"),
            "colors": preset["colors"],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2), "application/json", f"{preset['id']}.json"
    if fmt == "ini":
        parser = ConfigParser(interpolation=None)
        parser.optionxform = str
        parser["Preset"] = {
            "id": preset["id"],
            "name": preset["name"],
            "description": preset.get("description", ""),
            "format_version": str(preset.get("format_version", 1)),
            "extends": str(preset.get("extends") or ""),
        }
        parser["Rules"] = {}
        for color in COLOR_KEYS:
            parser["Rules"][color.capitalize()] = " || ".join(preset["colors"].get(color, []))
        from io import StringIO
        buf = StringIO()
        parser.write(buf)
        return buf.getvalue(), "text/plain; charset=utf-8", f"{preset['id']}.ini"
    raise HighlightPresetError(f"Unsupported export format: {fmt}")


def import_preset(filename: str, content: bytes) -> dict[str, Any]:
    _ensure_dirs()
    suffix = Path(filename or "preset.json").suffix.lower() or ".json"
    stem = Path(filename or "preset").stem
    text = content.decode("utf-8")
    if suffix == ".json":
        data = json.loads(text)
        if not isinstance(data, dict):
            raise HighlightPresetError("JSON preset must be an object")
        preset = _normalize_preset(data, source="custom", preset_id=stem or None)
    elif suffix in {".ini", ".cfg"}:
        preset = _load_ini_text(text, source="custom", preset_id=stem or None)
        suffix = ".ini"
    else:
        raise HighlightPresetError(f"Unsupported preset file extension: {suffix}")

    target = CUSTOM_DIR / f"{preset['id']}{suffix}"
    target.write_bytes(content)
    return _load_preset(target, source="custom")

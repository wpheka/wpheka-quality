#!/usr/bin/env python3
"""
WPHEKA Quality Configuration Loader.

Parses .wpheka-quality.yml and profile presets. Uses PyYAML when available and
falls back to a dependency-free parser that supports the subset of YAML this
tool documents: nested maps, scalars, and block sequences.

Output formats:
  json     pretty-printed effective configuration
  env0     NUL-delimited KEY=VALUE pairs for safe shell consumption
  summary  human-readable overview

env0 exists specifically so the shell runner never has to `eval` this output.
"""

import argparse
import json
import pathlib
import re
import sys

TOOL_ROOT = pathlib.Path(__file__).resolve().parent.parent
BUNDLED_PROFILES = TOOL_ROOT / "config" / "profiles"

# Every check the engine knows about, with its default enabled state.
KNOWN_CHECKS = {
    "php_syntax": True,
    "git_diff_check": True,
    "phpcs": True,
    "phpstan": True,
    "phpunit": True,
    "composer_validate": True,
    "composer_audit": True,
    "semgrep": True,
    "gitleaks": True,
    "plugin_check": True,
    "npm_lint": False,
    "npm_test": False,
    "coderabbit": True,
}

# Aliases accepted in config files, mapped to canonical check names.
CHECK_ALIASES = {
    "diff_check": ["git_diff_check"],
    "npm": ["npm_lint", "npm_test"],
    "syntax": ["php_syntax"],
}

KNOWN_COMMANDS = ("phpcs", "phpstan", "phpunit", "semgrep", "gitleaks", "plugin_check")

DEFAULT_EXCLUDES = ["vendor/", "node_modules/", "dist/", "build/", ".git/"]

SAFE_ENV_NAME = re.compile(r"^[A-Z0-9_]+$")


class ConfigError(Exception):
    """Raised for malformed configuration that must not be silently ignored."""


# --------------------------------------------------------------------------
# YAML parsing
# --------------------------------------------------------------------------

def _coerce_scalar(raw):
    val = raw.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        return val[1:-1]
    low = val.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~", ""):
        return None
    if re.match(r"^-?\d+$", val):
        return int(val)
    if re.match(r"^-?\d+\.\d+$", val):
        return float(val)
    return val


def _strip_comment(line):
    """Remove a trailing # comment, respecting quoted spans."""
    out = []
    quote = None
    for idx, char in enumerate(line):
        if quote:
            if char == quote:
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif char == "#":
            # A '#' only starts a comment at line start or after whitespace.
            if idx == 0 or line[idx - 1].isspace():
                break
        out.append(char)
    return "".join(out)


def parse_yaml_fallback(text):
    """
    Dependency-free parser for the documented config subset.

    Supports nested maps, block sequences (`- item`), quoted and bare scalars,
    booleans, ints, floats, nulls and comments. Raises ConfigError on input it
    cannot represent, so a typo surfaces instead of silently disappearing.
    """
    root = {}
    # A frame is one open container. `key` is the last mapping key seen in it,
    # so a sequence indented flush with its key can still find its owner.
    stack = [{"container": root, "indent": -1, "key": None, "is_seq": False}]

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        if raw_line.strip().startswith("#"):
            continue
        line = _strip_comment(raw_line.rstrip())
        if not line.strip():
            continue
        if "\t" in line[: len(line) - len(line.lstrip())]:
            raise ConfigError("line %d: tab indentation is not valid YAML" % lineno)

        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        is_item = stripped.startswith("- ")

        # Close frames the current line has dedented out of. A sequence frame
        # survives a same-indent line only when that line is another item,
        # because YAML allows `- item` at the same indent as its own key.
        while len(stack) > 1:
            top = stack[-1]
            if indent < top["indent"]:
                stack.pop()
                continue
            if indent == top["indent"] and not (top["is_seq"] and is_item):
                stack.pop()
                continue
            break
        frame = stack[-1]

        if is_item:
            item = _coerce_scalar(stripped[2:])
            if frame["is_seq"]:
                frame["container"].append(item)
                continue

            # A `key:` line opened an empty placeholder map; this first item
            # proves the value is really a sequence, so replace the placeholder
            # with a list in whichever container actually holds it.
            holder = frame.get("parent_container") if frame.get("parent_key") else frame["container"]
            holder_key = frame.get("parent_key") or frame.get("key")
            if not isinstance(holder, dict) or holder_key is None:
                raise ConfigError("line %d: sequence item outside of a mapping" % lineno)
            existing = holder.get(holder_key)
            if isinstance(existing, dict) and existing:
                raise ConfigError(
                    "line %d: cannot mix mapping keys and sequence items under %r"
                    % (lineno, holder_key)
                )
            seq = existing if isinstance(existing, list) else []
            holder[holder_key] = seq
            seq.append(item)
            stack.append({"container": seq, "indent": indent, "key": None, "is_seq": True})
            continue

        if ":" not in stripped:
            raise ConfigError("line %d: expected 'key: value', got %r" % (lineno, stripped))

        key, _, rest = stripped.partition(":")
        key = key.strip().strip("'\"")
        rest = rest.strip()
        container = frame["container"]
        if not isinstance(container, dict):
            raise ConfigError("line %d: mapping key inside a sequence is unsupported" % lineno)

        if rest == "":
            # Either a nested map or a block sequence; the next line decides.
            child = {}
            container[key] = child
            # Remember the key on the parent frame too, so a flush-indented
            # `- item` (same indent as the key) can still find its owner.
            frame["key"] = key
            stack.append({
                "container": child,
                "indent": indent,
                "key": key,
                "is_seq": False,
                "parent_container": container,
                "parent_key": key,
            })
        else:
            container[key] = _coerce_scalar(rest)

    return root


def parse_yaml(text):
    try:
        import yaml  # noqa: WPS433 - optional dependency
    except ImportError:
        return parse_yaml_fallback(text)
    try:
        data = yaml.safe_load(text)
    except Exception as exc:  # pragma: no cover - depends on optional dep
        raise ConfigError("invalid YAML: %s" % exc)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError("top level of the config must be a mapping")
    return data


# --------------------------------------------------------------------------
# Configuration assembly
# --------------------------------------------------------------------------

def default_config():
    return {
        "project_type": "wordpress-plugin",
        "php": {"minimum": "7.4"},
        "wordpress": {"required": True},
        "woocommerce": {"required": False, "hpos": False, "blocks": False},
        "checks": dict(KNOWN_CHECKS),
        "commands": {},
        "exclude": list(DEFAULT_EXCLUDES),
        "timeouts": {"default": 900, "coderabbit": 1800, "semgrep": 1800, "phpunit": 1800},
        "fail_on": "error",
    }


def merge_config(target, source):
    for key, val in source.items():
        if isinstance(val, dict) and isinstance(target.get(key), dict):
            merge_config(target[key], val)
        else:
            target[key] = val


def normalize_checks(checks, warnings):
    """Expand aliases and drop unknown keys with a warning."""
    resolved = {}
    for key, val in checks.items():
        name = str(key).strip().lower().replace("-", "_")
        targets = CHECK_ALIASES.get(name, [name])
        for target in targets:
            if target not in KNOWN_CHECKS:
                warnings.append("unknown check %r ignored" % key)
                continue
            resolved[target] = bool(val)
    return resolved


def load_config(repo_path, config_file=None, profile_name=None, profiles_dir=None,
                allow_repo_commands=False):
    repo = pathlib.Path(repo_path).resolve()
    config = default_config()
    warnings = []
    # Commands are only honoured when they come from a trusted source: a config
    # file the operator passed explicitly, a bundled profile, or an explicit
    # opt-in for the repository's own file. See docs/security-notes.md.
    config["commands_trusted"] = True

    search_dirs = []
    if profiles_dir:
        search_dirs.append(pathlib.Path(profiles_dir))
    search_dirs.append(BUNDLED_PROFILES)

    def apply(path, source_label, trusted):
        try:
            data = parse_yaml(pathlib.Path(path).read_text())
        except ConfigError as exc:
            raise ConfigError("%s: %s" % (source_label, exc))
        except OSError as exc:
            raise ConfigError("%s: %s" % (source_label, exc))
        if "checks" in data:
            data["checks"] = normalize_checks(data.get("checks") or {}, warnings)
        commands = data.get("commands")
        if commands and not trusted:
            warnings.append(
                "commands: block in %s ignored (untrusted source; "
                "pass --allow-repo-commands to honour it)" % source_label
            )
            data = dict(data)
            data.pop("commands", None)
            config["commands_trusted"] = False
        merge_config(config, data)

    # 1. Profile preset (bundled, or an explicit path).
    if profile_name:
        prof_file = None
        for directory in search_dirs:
            candidate = directory / ("%s.yml" % profile_name)
            if candidate.is_file():
                prof_file = candidate
                break
        if prof_file is None:
            candidate = pathlib.Path(profile_name)
            if candidate.is_file():
                prof_file = candidate
        if prof_file is None:
            raise ConfigError(
                "unknown profile %r (available: %s)"
                % (profile_name, ", ".join(sorted(p.stem for p in BUNDLED_PROFILES.glob("*.yml"))))
            )
        apply(prof_file, "profile %s" % prof_file.name, trusted=True)
        config["profile_used"] = str(prof_file)

    # 2. Config file. An explicit --config is operator-supplied and trusted; a
    #    file discovered inside the target repository is not.
    if config_file:
        target = pathlib.Path(config_file)
        if not target.is_file():
            raise ConfigError("config file not found: %s" % config_file)
        apply(target, str(target), trusted=True)
        config["config_file_found"] = str(target.resolve())
    else:
        target = repo / ".wpheka-quality.yml"
        if target.is_file():
            apply(target, str(target), trusted=bool(allow_repo_commands))
            config["config_file_found"] = str(target.resolve())
        else:
            config["config_file_found"] = None

    # Fill any check the config did not mention.
    merged_checks = dict(KNOWN_CHECKS)
    merged_checks.update(normalize_checks(config.get("checks") or {}, warnings))
    config["checks"] = merged_checks

    commands = config.get("commands") or {}
    clean_commands = {}
    for key, val in commands.items():
        name = str(key).strip().lower().replace("-", "_")
        if name not in KNOWN_COMMANDS:
            warnings.append("unknown command override %r ignored" % key)
            continue
        if not isinstance(val, str) or not val.strip():
            warnings.append("command override %r must be a non-empty string" % key)
            continue
        clean_commands[name] = val.strip()
    config["commands"] = clean_commands

    excludes = config.get("exclude")
    if isinstance(excludes, str):
        excludes = [excludes]
    if not isinstance(excludes, list):
        excludes = list(DEFAULT_EXCLUDES)
    config["exclude"] = [str(x) for x in excludes if str(x).strip()]

    timeouts = config.get("timeouts")
    if not isinstance(timeouts, dict):
        timeouts = {}
    clean_timeouts = {}
    for key, val in timeouts.items():
        try:
            seconds = int(val)
        except (TypeError, ValueError):
            warnings.append("timeout %r must be an integer number of seconds" % key)
            continue
        if seconds <= 0:
            warnings.append("timeout %r must be positive" % key)
            continue
        clean_timeouts[str(key).strip().lower().replace("-", "_")] = seconds
    clean_timeouts.setdefault("default", 900)
    config["timeouts"] = clean_timeouts

    fail_on = str(config.get("fail_on", "error")).strip().lower()
    if fail_on not in ("none", "error", "warning"):
        warnings.append("fail_on %r invalid; using 'error'" % config.get("fail_on"))
        fail_on = "error"
    config["fail_on"] = fail_on

    config["warnings"] = warnings
    return config


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def emit_env0(cfg, stream):
    """Write NUL-delimited KEY=VALUE pairs. Never shell-quoted, never eval'd."""
    pairs = []
    for check, enabled in sorted(cfg.get("checks", {}).items()):
        name = check.upper()
        if not SAFE_ENV_NAME.match(name):
            continue
        pairs.append(("WPHEKA_CHECK_%s" % name, "1" if enabled else "0"))

    for cmd_key, cmd_val in sorted(cfg.get("commands", {}).items()):
        name = cmd_key.upper()
        if not SAFE_ENV_NAME.match(name):
            continue
        pairs.append(("WPHEKA_CMD_%s" % name, cmd_val))

    for tkey, tval in sorted(cfg.get("timeouts", {}).items()):
        name = tkey.upper()
        if not SAFE_ENV_NAME.match(name):
            continue
        pairs.append(("WPHEKA_TIMEOUT_%s" % name, str(tval)))

    pairs.append(("WPHEKA_CFG_PROJECT_TYPE", str(cfg.get("project_type", ""))))
    pairs.append(("WPHEKA_CFG_FAIL_ON", str(cfg.get("fail_on", "error"))))
    pairs.append(("WPHEKA_CFG_EXCLUDE", "\n".join(cfg.get("exclude", []))))
    pairs.append(("WPHEKA_CFG_CONFIG_FILE", str(cfg.get("config_file_found") or "")))
    pairs.append(("WPHEKA_CFG_PROFILE", str(cfg.get("profile_used") or "")))
    pairs.append(("WPHEKA_CFG_WARNINGS", "\n".join(cfg.get("warnings", []))))

    for key, val in pairs:
        # A NUL can never appear in an env var, so this framing is unambiguous.
        stream.write("%s=%s\0" % (key, val.replace("\0", "")))


def main():
    parser = argparse.ArgumentParser(description="WPHEKA Quality config loader")
    parser.add_argument("--repo", default=".", help="Repository root path")
    parser.add_argument("--config", default=None, help="Path to an explicit config file")
    parser.add_argument("--profile", default=None, help="Profile preset name or path")
    parser.add_argument("--profiles-dir", default=None, help="Extra directory to search for profiles")
    parser.add_argument("--allow-repo-commands", action="store_true",
                        help="Honour a commands: block found in the target repository's config")
    parser.add_argument("--format", choices=["json", "env0", "summary"], default="json")

    args = parser.parse_args()

    try:
        cfg = load_config(args.repo, args.config, args.profile, args.profiles_dir,
                          args.allow_repo_commands)
    except ConfigError as exc:
        sys.stderr.write("wpheka-quality: configuration error: %s\n" % exc)
        return 2

    if args.format == "json":
        print(json.dumps(cfg, indent=2, sort_keys=True))
    elif args.format == "env0":
        emit_env0(cfg, sys.stdout)
        sys.stdout.flush()
    else:
        print("Project type : %s" % cfg.get("project_type"))
        print("Config file  : %s" % (cfg.get("config_file_found") or "(none)"))
        print("Profile      : %s" % (cfg.get("profile_used") or "(none)"))
        print("Fail on      : %s" % cfg.get("fail_on"))
        enabled = sorted(k for k, v in cfg.get("checks", {}).items() if v)
        disabled = sorted(k for k, v in cfg.get("checks", {}).items() if not v)
        print("Enabled      : %s" % (", ".join(enabled) or "(none)"))
        print("Disabled     : %s" % (", ".join(disabled) or "(none)"))
        if cfg.get("commands"):
            print("Overrides    : %s" % ", ".join(sorted(cfg["commands"])))
        for warning in cfg.get("warnings", []):
            print("Warning      : %s" % warning)

    for warning in cfg.get("warnings", []):
        sys.stderr.write("wpheka-quality: warning: %s\n" % warning)
    return 0


if __name__ == "__main__":
    sys.exit(main())

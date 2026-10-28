"""The server's model catalog (personal agent plan, second edition, 模型配置).

Whoever deploys the server lists the models here; a bot names one entry and never holds a key.

* Entries come from two places.  The deployment's config file (``config/models.yaml``, or the path in
  ``AGENT_MODELS_CONFIG``) lists models and, for each, the environment variable its key is read from; the
  repository only carries ``config/models.example.yaml``.  The owner can add entries on the settings page;
  those rows keep the key encrypted with secret_box (vault) or name an environment variable too.
* A key is used only on the server: no response carries it or any part of it (only configured / missing),
  no log line prints it, and no row stores it in clear.
* Changing an entry's provider or endpoint needs the key again, so a saved key is never sent somewhere new.
* An environment variable named by an entry must end in ``_API_KEY`` and may not be one of the server's
  own secrets, so an entry cannot send, say, the login secret to a model endpoint.
* ``migrate_legacy`` folds in what came before: the background model (already encrypted) and the keys of
  ``data/model-profiles.json`` (plaintext; the file itself is changed only by ``strip_local_profile_keys``).
"""
from __future__ import annotations

import logging
import os
import re
import stat
from pathlib import Path

from .agent_chat_models import AgentBackgroundModel, AgentModelEntry
from .database import SessionLocal
from .secret_box import CredentialEncryptionError, decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)

NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,55}_API_KEY$")
MODEL = re.compile(r"^[A-Za-z0-9._:/-]{1,160}$")
# Secrets of the server itself; an entry naming one would hand it to a model endpoint.
FORBIDDEN_ENV = {"APP_API_ACCESS_KEY", "MODEL_CREDENTIAL_KEY", "TELEGRAM_BOT_API_KEY"}
MAX_ENTRIES = 40
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class CatalogError(ValueError):
    """A refused catalog change or an unusable entry; the message is a short code for the API."""


def _providers():
    from .agent_chat_api import PROVIDER_NAMES
    return PROVIDER_NAMES


def _endpoint(provider: str, base_url: str | None) -> str | None:
    from .agent_chat_api import _validated_base_url
    try:
        value = _validated_base_url(base_url.strip()) if base_url and base_url.strip() else None
    except ValueError as exc:
        raise CatalogError("invalid_endpoint") from exc
    if provider == "custom" and not value:
        raise CatalogError("invalid_endpoint")
    return value


def config_path() -> Path:
    configured = os.getenv("AGENT_MODELS_CONFIG", "config/models.yaml")
    path = Path(configured)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _env_allowed(name: str | None) -> bool:
    return bool(name) and ENV_NAME.fullmatch(name) is not None and name not in FORBIDDEN_ENV


def _read_file() -> tuple[list[dict], str | None, list[str]]:
    """The config file's entries, its default and the problems found (never a key: the file holds none)."""
    path = config_path()
    if not path.exists():
        return [], None, []
    problems: list[str] = []
    try:
        import yaml
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return [], None, [f"{path.name}: unreadable"]
    if not isinstance(raw, dict) or not isinstance(raw.get("models", []), list):
        return [], None, [f"{path.name}: expected a mapping with a models list"]
    entries, seen = [], set()
    for position, item in enumerate(raw.get("models") or [], 1):
        try:
            if not isinstance(item, dict):
                raise CatalogError("not_a_mapping")
            if "api_key" in item or "key" in item:
                # A key written into the file is ignored: the file names where the key is, never the key.
                raise CatalogError("key_in_file")
            name = str(item.get("name") or "")
            provider = str(item.get("provider") or "")
            model = str(item.get("model") or "")
            key_env = item.get("key_env")
            if not NAME.fullmatch(name) or name in seen:
                raise CatalogError("invalid_name")
            if provider not in _providers():
                raise CatalogError("invalid_provider")
            if not MODEL.fullmatch(model):
                raise CatalogError("invalid_model")
            if not _env_allowed(key_env):
                raise CatalogError("invalid_key_env")
            entries.append({"name": name, "display_name": str(item.get("display_name") or name)[:80],
                            "provider": provider, "model": model,
                            "base_url": _endpoint(provider, item.get("base_url")),
                            "key_source": "env", "key_env": key_env, "ciphertext": None, "origin": "file",
                            "is_default": False})
            seen.add(name)
        except CatalogError as exc:
            problems.append(f"{path.name} entry {position}: {exc}")
    default = raw.get("default")
    default = default if isinstance(default, str) and default in seen else None
    return entries, default, problems


def _row_entry(row: AgentModelEntry) -> dict:
    return {"name": row.name, "display_name": row.display_name, "provider": row.provider, "model": row.model,
            "base_url": row.base_url, "key_source": row.key_source, "key_env": row.key_env,
            "ciphertext": row.ciphertext, "origin": "server", "is_default": bool(row.is_default),
            "migrated_from": row.migrated_from}


def entries() -> tuple[list[dict], str | None]:
    """Every entry (file first) and the default entry's name, or None when nothing can be used."""
    listed, file_default, _ = _read_file()
    names = {item["name"] for item in listed}
    with SessionLocal() as db:
        rows = db.query(AgentModelEntry).order_by(AgentModelEntry.created_at, AgentModelEntry.name).all()
        listed += [_row_entry(row) for row in rows if row.name not in names]
    default = file_default or next((item["name"] for item in listed if item["is_default"]), None)
    if default is None:
        default = next((item["name"] for item in listed if _key_for(item)), None)
    return listed, default


def _load_env() -> None:
    from .secret_box import _load_project_env
    _load_project_env()


def _key_for(entry: dict) -> str | None:
    """The entry's key, in memory only; None when it is missing or unusable."""
    if entry["key_source"] == "vault":
        if not entry.get("ciphertext"):
            return None
        try:
            value = decrypt_secret(entry["ciphertext"])
        except CredentialEncryptionError:
            return None
    elif entry["key_source"] == "env" and _env_allowed(entry.get("key_env")):
        _load_env()
        value = os.getenv(entry["key_env"]) or ""
    else:
        return None
    value = value.strip()
    return value if 8 <= len(value) <= 2048 else None


def _key_status(entry: dict) -> str:
    return "configured" if _key_for(entry) else "missing"


def public(entry: dict, default: str | None = None) -> dict:
    """What the browser may see of an entry: never the key, nor any part of it."""
    return {"name": entry["name"], "display_name": entry["display_name"], "provider": entry["provider"],
            "model": entry["model"], "base_url": entry["base_url"], "origin": entry["origin"],
            "key_source": entry["key_source"], "key_env": entry.get("key_env"),
            "key_status": _key_status(entry), "is_default": entry["name"] == default,
            "migrated": bool(entry.get("migrated_from"))}


def list_public() -> dict:
    listed, default = entries()
    _, _, problems = _read_file()
    return {"models": [public(item, default) for item in listed], "default": default,
            "config_file": {"present": config_path().exists(), "problems": problems}}


def exists(name: str | None) -> bool:
    return bool(name) and any(item["name"] == name for item in entries()[0])


def resolve(name: str | None) -> dict:
    """The provider settings of ``name`` (None: the catalog's default) with the key, for one request.

    Raises CatalogError: model_not_configured (no entry at all), model_not_found, model_key_missing."""
    listed, default = entries()
    wanted = name or default
    if wanted is None:
        raise CatalogError("model_not_configured")
    entry = next((item for item in listed if item["name"] == wanted), None)
    if entry is None:
        raise CatalogError("model_not_found")
    key = _key_for(entry)
    if key is None:
        raise CatalogError("model_key_missing")
    return {"name": entry["name"], "provider": entry["provider"], "model": entry["model"],
            "base_url": entry["base_url"], "api_key": key}


def probe_key(*, provider: str, base_url: str | None, api_key: str | None = None, key_env: str | None = None,
              name: str | None = None) -> str | None:
    """The key a settings-page check (测试 / 服务商可用模型) uses, in memory only: the one typed, the named
    environment variable's, or the stored key of entry ``name`` when the provider and endpoint did not
    change (a key is never sent somewhere new).  None when nothing was given (OpenRouter lists models
    without one).  Raises CatalogError: invalid_key, invalid_key_env, model_key_missing, key_required."""
    if provider not in _providers():
        raise CatalogError("invalid_provider")
    if api_key is not None:
        return _clean_key(api_key)
    if key_env:
        if not _env_allowed(key_env):
            raise CatalogError("invalid_key_env")
        _load_env()
        value = (os.getenv(key_env) or "").strip()
        if not 8 <= len(value) <= 2048:
            raise CatalogError("model_key_missing")
        return value
    if name:
        entry = next((item for item in entries()[0] if item["name"] == name), None)
        if entry is None:
            raise CatalogError("model_not_found")
        if (entry["provider"], entry["base_url"]) != (provider, _endpoint(provider, base_url)):
            raise CatalogError("key_required")
        key = _key_for(entry)
        if key is None:
            raise CatalogError("model_key_missing")
        return key
    return None


def _clean_key(api_key: str | None) -> str | None:
    if api_key is None:
        return None
    if not 8 <= len(api_key) <= 2048 or api_key.strip() != api_key:
        raise CatalogError("invalid_key")
    return api_key


def save_entry(name: str, *, display_name: str | None = None, provider: str | None = None, model: str | None = None,
               base_url: str | None = None, api_key: str | None = None, key_env: str | None = None,
               is_default: bool | None = None, create: bool = False) -> dict:
    """Add (``create``) or change an owner entry.  The key comes as ``api_key`` (stored in the vault) or
    ``key_env`` (an environment variable name); a new provider or endpoint needs one of them again."""
    if not NAME.fullmatch(name or ""):
        raise CatalogError("invalid_name")
    if any(item["name"] == name for item in _read_file()[0]):
        raise CatalogError("model_from_file")
    api_key = _clean_key(api_key)
    if key_env is not None and not _env_allowed(key_env):
        raise CatalogError("invalid_key_env")
    if api_key and key_env:
        raise CatalogError("one_key_source")
    with SessionLocal() as db:
        row = db.get(AgentModelEntry, name, with_for_update=True)
        if create and row is not None:
            raise CatalogError("model_exists")
        if not create and row is None:
            raise CatalogError("model_not_found")
        if create and db.query(AgentModelEntry).count() >= MAX_ENTRIES:
            raise CatalogError("too_many_models")
        provider = provider if provider is not None else (row.provider if row else None)
        if provider not in _providers():
            raise CatalogError("invalid_provider")
        model = model if model is not None else (row.model if row else None)
        if not MODEL.fullmatch(model or ""):
            raise CatalogError("invalid_model")
        endpoint = _endpoint(provider, base_url) if base_url is not None or row is None else row.base_url
        moved = row is not None and (row.provider, row.base_url) != (provider, endpoint)
        if (create or moved) and not (api_key or key_env):
            # A key goes only where it was entered for: a new endpoint needs it entered again.
            raise CatalogError("key_required")
        if row is None:
            row = AgentModelEntry(name=name, display_name=name, provider=provider, model=model, key_source="vault")
            db.add(row)
        row.display_name = (display_name or row.display_name or name).strip()[:80] or name
        row.provider, row.model, row.base_url = provider, model, endpoint
        if api_key:
            try:
                row.ciphertext = encrypt_secret(api_key)
            except CredentialEncryptionError as exc:
                raise CatalogError("credential_key_missing") from exc
            row.key_source, row.key_env = "vault", None
        elif key_env:
            row.key_source, row.key_env, row.ciphertext = "env", key_env, None
        if is_default:
            db.query(AgentModelEntry).filter(AgentModelEntry.name != name).update({"is_default": False})
            row.is_default = True
        elif is_default is False:
            row.is_default = False
        db.commit()
    listed, default = entries()
    return public(next(item for item in listed if item["name"] == name), default)


def set_default(name: str) -> dict:
    """Make ``name`` the default (an owner row).  A file default always wins and cannot be changed here."""
    if _read_file()[1]:
        raise CatalogError("default_from_file")
    return save_entry(name, is_default=True)


def delete_entry(name: str) -> None:
    """Remove an owner entry.  Bots naming it are refused (model_in_use) rather than left pointing nowhere."""
    if any(item["name"] == name for item in _read_file()[0]):
        raise CatalogError("model_from_file")
    from .agent_chat_models import AgentBot
    with SessionLocal() as db:
        row = db.get(AgentModelEntry, name)
        if row is None:
            raise CatalogError("model_not_found")
        if db.query(AgentBot).filter(AgentBot.model == name).count():
            raise CatalogError("model_in_use")
        db.delete(row)
        db.commit()


# --- migration of the earlier three model settings ---------------------------------------------------

def _free_name(db, base: str) -> str:
    stem = re.sub(r"[^a-z0-9._-]+", "-", base.lower()).strip("-._")[:32] or "model"
    if not NAME.fullmatch(stem):
        stem = "model"
    name, n = stem, 2
    while db.get(AgentModelEntry, name) is not None or any(item["name"] == name for item in _read_file()[0]):
        name, n = f"{stem}-{n}", n + 1
    return name


def migrate_legacy() -> list[str]:
    """Fold the background model and the local profile file's keys into the catalog, once each.
    Returns the names added.  The background row stays (its tasks keep running); the profile file is left
    as it is until ``strip_local_profile_keys``.  Never raises on a bad file."""
    added: list[str] = []
    with SessionLocal() as db:
        done = {row.migrated_from for row in db.query(AgentModelEntry).filter(AgentModelEntry.migrated_from.isnot(None))}
        has_default = db.query(AgentModelEntry).filter(AgentModelEntry.is_default.is_(True)).count() > 0
        for row in db.query(AgentBackgroundModel).filter(AgentBackgroundModel.ciphertext.isnot(None)).all():
            origin = f"background:{row.owner_hash[:16]}"
            if origin in done:
                continue
            name = _free_name(db, "background")
            db.add(AgentModelEntry(name=name, display_name=f"后台模型 · {row.model}"[:80], provider=row.provider,
                                   model=row.model, base_url=row.base_url, key_source="vault",
                                   ciphertext=row.ciphertext, is_default=not has_default, migrated_from=origin))
            db.flush()
            has_default = True
            added.append(name)
        for profile in _local_profiles():
            origin = f"profile:{profile['id']}"[:80]
            if origin in done or not profile.get("api_key"):
                continue
            try:
                endpoint = _endpoint(profile["provider"], profile.get("base_url"))
                ciphertext = encrypt_secret(profile["api_key"])
            except (CatalogError, CredentialEncryptionError):
                continue
            name = _free_name(db, f"local-{profile.get('display_name') or profile['model']}")
            db.add(AgentModelEntry(name=name, display_name=str(profile.get("display_name") or name)[:80],
                                   provider=profile["provider"], model=profile["model"], base_url=endpoint,
                                   key_source="vault", ciphertext=ciphertext, is_default=not has_default,
                                   migrated_from=origin))
            db.flush()
            has_default = True
            added.append(name)
        db.commit()
    return added


def _local_profiles() -> list[dict]:
    try:
        from .local_model_profiles import _locked_read
        return [p for p in _locked_read().get("profiles", []) if isinstance(p, dict)]
    except Exception:
        return []


def strip_local_profile_keys() -> int:
    """Blank the plaintext keys of ``data/model-profiles.json`` whose profile is already in the vault.
    Returns how many were blanked.  Run it (scripts/migrate_model_catalog.py --strip) once bots work."""
    from .local_model_profiles import _locked_read, _locked_write
    with SessionLocal() as db:
        done = {row.migrated_from for row in db.query(AgentModelEntry).filter(AgentModelEntry.migrated_from.like("profile:%"))}
    data = _locked_read()
    count = 0
    for profile in data.get("profiles", []):
        if profile.get("api_key") and f"profile:{profile['id']}"[:80] in done:
            profile["api_key"], profile["tested_at"] = "", None
            count += 1
    if count:
        _locked_write(data)
    return count


def startup_report() -> list[str]:
    """One line per entry: usable, or what is missing.  Never a key."""
    try:
        listed, default = entries()
        _, _, problems = _read_file()
    except Exception as exc:  # the catalog never stops the server from starting
        return [f"model catalog unavailable: {type(exc).__name__}"]
    lines = []
    path = config_path()
    if path.exists():
        try:
            if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                lines.append(f"model catalog: {path.name} is readable by other accounts; chmod 600 it")
        except OSError:
            pass
    for item in listed:
        where = f"env {item['key_env']}" if item["key_source"] == "env" else "vault"
        state = "usable" if _key_for(item) else f"missing key ({where})"
        mark = " [default]" if item["name"] == default else ""
        lines.append(f"model catalog: {item['name']} ({item['provider']}/{item['model']}, {item['origin']}){mark}: {state}")
    lines += [f"model catalog: {problem}" for problem in problems]
    if not listed:
        lines.append("model catalog: empty; bots cannot answer until a model is added (config/models.yaml or 设置)")
    return lines


def log_startup_report() -> None:
    for line in startup_report():
        logger.info(line)

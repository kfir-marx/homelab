#!/usr/bin/env python3
"""High/low-water storage cleanup orchestrated through Maintainerr.

Maintainerr evaluates collections in batches, so it cannot stop collection
handling after a storage target has been reached.  This controller builds a
single ordered candidate list, adds one item to a controller-owned Maintainerr
collection, and asks Maintainerr to handle only that item.  All destructive
actions therefore still flow through Maintainerr and its configured Arr,
Seerr, and qBittorrent integrations.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GIB = 1024**3
MIB = 1024**2
GB = 1000**3
MOVIE_COLLECTION_PREFIX = "Storage Pressure - Movies - "
EPISODE_COLLECTION_PREFIX = "Storage Pressure - Episodes - "
MANAGED_DESCRIPTION = "Managed by the homelab Maintainerr storage cleanup controller."


def log(message: str) -> None:
    print(message, flush=True)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def free_bytes(path: str) -> int:
    stats = os.statvfs(path)
    return stats.f_bavail * stats.f_frsize


def parse_date(value: Any) -> float:
    if not value:
        return float("inf")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return float("inf")


def provider_id(item: dict[str, Any], provider: str) -> str | None:
    values = item.get("providerIds", {}).get(provider.lower())
    if values is None:
        return None
    if isinstance(values, list):
        return str(values[0]) if values else None
    return str(values)


def is_protected_item(item: dict[str, Any], favorite_ids: set[str]) -> bool:
    return bool(item.get("maintainerrExclusionType")) or any(
        item.get(key) in favorite_ids
        for key in ("id", "parentId", "grandparentId")
    )


@dataclass(frozen=True)
class Candidate:
    media_id: str
    media_type: str
    title: str
    watched: bool
    downloaded_at: float
    size_bytes: int
    collection_id: int

    def sort_key(self) -> tuple[int, float, int, str, str]:
        # Watched first, then oldest import, then largest individual file.
        return (
            0 if self.watched else 1,
            self.downloaded_at,
            -self.size_bytes,
            self.media_type,
            self.media_id,
        )


class JsonApi:
    def __init__(self, base_url: str, headers: dict[str, str] | None = None):
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}

    def request(
        self,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        timeout: int = 120,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        data = None
        headers = {"Accept": "application/json", **self.headers}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:1000]
            raise RuntimeError(
                f"{method} {path} failed with HTTP {error.code}: {detail}"
            ) from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"{method} {path} failed: {error.reason}") from error
        if not raw:
            return None
        return json.loads(raw)


def require_single_setting(settings: Any, application: str) -> dict[str, Any]:
    if not isinstance(settings, list) or len(settings) != 1:
        raise RuntimeError(
            f"Expected exactly one configured {application} server, found "
            f"{len(settings) if isinstance(settings, list) else 0}"
        )
    setting = settings[0]
    if not setting.get("url") or not setting.get("apiKey"):
        raise RuntimeError(f"The configured {application} server is incomplete")
    return setting


def paged_maintainerr_items(
    maintainerr: JsonApi, library_id: str, media_type: str, page_size: int
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        result = maintainerr.request(
            f"/api/media-server/library/{urllib.parse.quote(library_id)}/content",
            query={"page": page, "limit": page_size, "type": media_type},
        )
        batch = result.get("items", [])
        items.extend(batch)
        if not batch or len(items) >= int(result.get("totalSize", len(items))):
            return items
        page += 1


def jellyfin_filtered_ids(
    jellyfin: JsonApi,
    users: list[dict[str, Any]],
    library_ids: list[str],
    filter_name: str,
    include_types: str,
    page_size: int,
) -> set[str]:
    result_ids: set[str] = set()
    for user in users:
        user_id = user.get("Id")
        if not user_id:
            continue
        for library_id in library_ids:
            start = 0
            while True:
                result = jellyfin.request(
                    f"/Users/{urllib.parse.quote(str(user_id))}/Items",
                    query={
                        "ParentId": library_id,
                        "Recursive": "true",
                        "IncludeItemTypes": include_types,
                        "Filters": filter_name,
                        "StartIndex": start,
                        "Limit": page_size,
                    },
                )
                batch = result.get("Items", [])
                result_ids.update(str(item["Id"]) for item in batch if item.get("Id"))
                start += len(batch)
                if not batch or start >= int(result.get("TotalRecordCount", start)):
                    break
    return result_ids


def keep_tag_id(arr: JsonApi) -> int | None:
    for tag in arr.request("/api/v3/tag"):
        if str(tag.get("label", "")).strip().casefold() == "keep":
            return int(tag["id"])
    return None


def collection_spec(
    library: dict[str, Any], media_type: str, arr_setting: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    if media_type == "movie":
        name = f"{MOVIE_COLLECTION_PREFIX}{library['title']}"
        arr_action = 1  # UNMONITOR_DELETE_ALL
        arr_key = "radarrSettingsId"
        list_exclusions = True
        force_seerr = True
    else:
        name = f"{EPISODE_COLLECTION_PREFIX}{library['title']}"
        arr_action = 0  # DELETE; episode scope unmonitors and deletes the file
        arr_key = "sonarrSettingsId"
        list_exclusions = False
        force_seerr = False

    payload = {
        "libraryId": library["id"],
        "name": name,
        "description": MANAGED_DESCRIPTION,
        "isActive": True,
        "arrAction": arr_action,
        "useRules": False,
        "rules": [],
        "dataType": media_type,
        "listExclusions": list_exclusions,
        "cleanupLeftoverFolders": False,
        "forceSeerr": force_seerr,
        arr_key: int(arr_setting["id"]),
        "collection": {
            "visibleOnRecommended": False,
            "visibleOnHome": False,
            "manualCollection": False,
            "keepLogsForMonths": 6,
        },
    }
    return name, payload


def ensure_action_collection(
    maintainerr: JsonApi,
    groups: list[dict[str, Any]],
    library: dict[str, Any],
    media_type: str,
    arr_setting: dict[str, Any],
) -> int:
    name, payload = collection_spec(library, media_type, arr_setting)
    matches = [group for group in groups if group.get("name") == name]
    if not matches:
        maintainerr.request("/api/rules", method="POST", body=payload)
        groups[:] = maintainerr.request("/api/rules")
        matches = [group for group in groups if group.get("name") == name]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one Maintainerr rule group named {name!r}")

    group = matches[0]
    collection = group.get("collection") or {}
    expected_arr_key = "radarrSettingsId" if media_type == "movie" else "sonarrSettingsId"
    expected_action = 1 if media_type == "movie" else 0
    drift = []
    checks = {
        "libraryId": library["id"],
        "dataType": media_type,
        "useRules": False,
        "isActive": True,
    }
    for key, expected in checks.items():
        if group.get(key) != expected:
            drift.append(key)
    collection_checks = {
        "type": media_type,
        "arrAction": expected_action,
        expected_arr_key: int(arr_setting["id"]),
    }
    for key, expected in collection_checks.items():
        if collection.get(key) != expected:
            drift.append(f"collection.{key}")
    # A null delay keeps the normal batch handler away; this controller invokes
    # the single-item endpoint explicitly after checking actual free space.
    if collection.get("deleteAfterDays") is not None:
        drift.append("collection.deleteAfterDays")
    if drift:
        raise RuntimeError(
            f"Managed Maintainerr group {name!r} drifted in: {', '.join(drift)}"
        )
    if not collection.get("id"):
        raise RuntimeError(f"Maintainerr group {name!r} has no collection")
    return int(collection["id"])


def movie_candidates(
    items: list[dict[str, Any]],
    radarr: JsonApi,
    collection_id: int,
    watched_ids: set[str],
    favorite_ids: set[str],
) -> list[Candidate]:
    by_tmdb: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        tmdb_id = provider_id(item, "tmdb")
        if tmdb_id:
            by_tmdb.setdefault(tmdb_id, []).append(item)

    keep_id = keep_tag_id(radarr)
    candidates: list[Candidate] = []
    for movie in radarr.request("/api/v3/movie"):
        movie_file = movie.get("movieFile") or {}
        size = int(movie_file.get("size") or 0)
        if not movie.get("hasFile") or size <= 0:
            continue
        if keep_id is not None and keep_id in movie.get("tags", []):
            continue
        matches = by_tmdb.get(str(movie.get("tmdbId")), [])
        # One Radarr file may surface in more than one Jellyfin library. A pin
        # on any representation protects the shared file.
        if not matches or any(
            is_protected_item(match, favorite_ids) for match in matches
        ):
            continue
        item = matches[0]
        candidates.append(
            Candidate(
                media_id=str(item["id"]),
                media_type="movie",
                title=str(movie.get("title") or item.get("title") or item["id"]),
                watched=any(str(match["id"]) in watched_ids for match in matches),
                downloaded_at=parse_date(movie_file.get("dateAdded")),
                size_bytes=size,
                collection_id=collection_id,
            )
        )
    return candidates


def episode_candidates(
    items: list[dict[str, Any]],
    sonarr: JsonApi,
    collection_id: int,
    watched_ids: set[str],
    favorite_ids: set[str],
) -> list[Candidate]:
    by_tvdb: dict[str, list[dict[str, Any]]] = {}
    by_series_position: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    for item in items:
        episode_tvdb = provider_id(item, "tvdb")
        if episode_tvdb:
            by_tvdb.setdefault(episode_tvdb, []).append(item)
        parent = item.get("parentItem") or {}
        series_tvdb = provider_id(parent, "tvdb")
        if series_tvdb and item.get("parentIndex") is not None and item.get("index") is not None:
            key = (series_tvdb, int(item["parentIndex"]), int(item["index"]))
            by_series_position.setdefault(key, []).append(item)

    keep_id = keep_tag_id(sonarr)
    candidates: list[Candidate] = []
    for series in sonarr.request("/api/v3/series"):
        if keep_id is not None and keep_id in series.get("tags", []):
            continue
        series_id = int(series["id"])
        episodes = sonarr.request(
            "/api/v3/episode", query={"seriesId": series_id, "includeEpisodeFile": "false"}
        )
        episode_by_id = {int(episode["id"]): episode for episode in episodes}
        files = sonarr.request("/api/v3/episodefile", query={"seriesId": series_id})
        for episode_file in files:
            size = int(episode_file.get("size") or 0)
            if size <= 0:
                continue
            linked_items: list[dict[str, Any]] = []
            linked_episode_ids = episode_file.get("episodeIds") or []
            for episode_id in linked_episode_ids:
                episode = episode_by_id.get(int(episode_id))
                if not episode:
                    continue
                matches = by_tvdb.get(str(episode.get("tvdbId")), [])
                if not matches:
                    key = (
                        str(series.get("tvdbId")),
                        int(episode.get("seasonNumber", -1)),
                        int(episode.get("episodeNumber", -1)),
                    )
                    matches = by_series_position.get(key, [])
                linked_items.extend(matches)

            # Older Sonarr responses may omit episodeIds; correlate by file id.
            if not linked_items and episode_file.get("id") is not None:
                file_id = int(episode_file["id"])
                for episode in episodes:
                    if int(episode.get("episodeFileId") or -1) != file_id:
                        continue
                    matches = by_tvdb.get(str(episode.get("tvdbId")), [])
                    if not matches:
                        key = (
                            str(series.get("tvdbId")),
                            int(episode.get("seasonNumber", -1)),
                            int(episode.get("episodeNumber", -1)),
                        )
                        matches = by_series_position.get(key, [])
                    linked_items.extend(matches)

            unique_items = {str(item["id"]): item for item in linked_items}
            if not unique_items or any(
                is_protected_item(item, favorite_ids) for item in unique_items.values()
            ):
                continue
            item = sorted(
                unique_items.values(),
                key=lambda value: (
                    int(value.get("parentIndex") or 0),
                    int(value.get("index") or 0),
                ),
            )[0]
            season = int(item.get("parentIndex") or 0)
            episode_number = int(item.get("index") or 0)
            series_title = series.get(
                "title", item.get("grandparentTitle", "Unknown")
            )
            title = f"{series_title} S{season:02d}E{episode_number:02d}"
            candidates.append(
                Candidate(
                    media_id=str(item["id"]),
                    media_type="episode",
                    title=title,
                    # A multi-episode file is considered watched only when all
                    # of the Jellyfin episode records backed by it were watched.
                    watched=all(item_id in watched_ids for item_id in unique_items),
                    downloaded_at=parse_date(episode_file.get("dateAdded")),
                    size_bytes=size,
                    collection_id=collection_id,
                )
            )
    return candidates


def load_pending(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as error:
        raise RuntimeError(f"Could not read cleanup safety marker {path}: {error}") from error


def save_pending(path: Path, candidate: Candidate, baseline: int) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "mediaId": candidate.media_id,
                "title": candidate.title,
                "baselineFreeBytes": baseline,
                "createdAt": datetime.now(timezone.utc).isoformat(),
            }
        )
    )
    temporary.replace(path)


def clear_pending(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def format_gib(value: int) -> str:
    return f"{value / GIB:.1f} GiB"


def main() -> int:
    media_path = os.getenv("MEDIA_PATH", "/data")
    state_path = Path(os.getenv("STATE_PATH", "/state/pending-no-reclaim.json"))
    trigger = int(os.getenv("TRIGGER_FREE_GB", "500")) * GB
    target = int(os.getenv("TARGET_FREE_GB", "1000")) * GB
    min_reclaim = int(os.getenv("MIN_RECLAIM_MIB", "16")) * MIB
    poll_seconds = int(os.getenv("RECLAIM_POLL_SECONDS", "30"))
    page_size = int(os.getenv("PAGE_SIZE", "500"))
    dry_run = env_bool("DRY_RUN")

    if target <= trigger:
        raise RuntimeError("TARGET_FREE_GB must be greater than TRIGGER_FREE_GB")

    current_free = free_bytes(media_path)
    pending = load_pending(state_path)
    if pending:
        baseline = int(pending.get("baselineFreeBytes", current_free))
        if current_free >= baseline + min_reclaim or current_free > target:
            log("Free space increased since the prior action; clearing its safety marker")
            clear_pending(state_path)
        else:
            raise RuntimeError(
                "The prior deletion of "
                f"{pending.get('title', pending.get('mediaId', 'unknown media'))!r} "
                "did not reclaim storage. Resolve its qBittorrent hardlink/seeding "
                "state before allowing another deletion."
            )

    if current_free >= trigger:
        log(
            f"Free space is {format_gib(current_free)}; cleanup triggers below "
            f"{format_gib(trigger)}"
        )
        return 0

    log(
        f"Storage pressure detected at {format_gib(current_free)}; target is more than "
        f"{format_gib(target)}"
    )

    maintainerr = JsonApi(os.getenv("MAINTAINERR_URL", "http://maintainerr:6246"))
    libraries = maintainerr.request("/api/media-server/libraries")
    movie_libraries = [library for library in libraries if library.get("type") == "movie"]
    show_libraries = [library for library in libraries if library.get("type") == "show"]
    if not movie_libraries and not show_libraries:
        raise RuntimeError("Maintainerr returned no movie or TV libraries")

    radarr_setting = require_single_setting(
        maintainerr.request("/api/settings/radarr"), "Radarr"
    )
    sonarr_setting = require_single_setting(
        maintainerr.request("/api/settings/sonarr"), "Sonarr"
    )
    jellyfin_setting = maintainerr.request("/api/settings/jellyfin")
    jellyfin_url = jellyfin_setting.get("jellyfin_url")
    jellyfin_key = jellyfin_setting.get("jellyfin_api_key")
    if not jellyfin_url or not jellyfin_key:
        raise RuntimeError("Maintainerr's Jellyfin integration is incomplete")

    radarr = JsonApi(
        str(radarr_setting["url"]), {"X-Api-Key": str(radarr_setting["apiKey"])}
    )
    sonarr = JsonApi(
        str(sonarr_setting["url"]), {"X-Api-Key": str(sonarr_setting["apiKey"])}
    )
    jellyfin = JsonApi(str(jellyfin_url), {"X-Emby-Token": str(jellyfin_key)})

    all_library_ids = [str(library["id"]) for library in libraries]
    users = jellyfin.request("/Users")
    watched_ids = jellyfin_filtered_ids(
        jellyfin, users, all_library_ids, "IsPlayed", "Movie,Episode", page_size
    )
    favorite_ids = jellyfin_filtered_ids(
        jellyfin,
        users,
        all_library_ids,
        "IsFavorite",
        "Movie,Series,Season,Episode",
        page_size,
    )

    groups = maintainerr.request("/api/rules")
    candidates: list[Candidate] = []
    for library in movie_libraries:
        collection_id = ensure_action_collection(
            maintainerr, groups, library, "movie", radarr_setting
        )
        items = paged_maintainerr_items(
            maintainerr, str(library["id"]), "movie", page_size
        )
        candidates.extend(
            movie_candidates(
                items, radarr, collection_id, watched_ids, favorite_ids
            )
        )
    for library in show_libraries:
        collection_id = ensure_action_collection(
            maintainerr, groups, library, "episode", sonarr_setting
        )
        items = paged_maintainerr_items(
            maintainerr, str(library["id"]), "episode", page_size
        )
        candidates.extend(
            episode_candidates(
                items, sonarr, collection_id, watched_ids, favorite_ids
            )
        )

    candidates.sort(key=Candidate.sort_key)
    log(
        f"Found {len(candidates)} eligible files; {sum(c.watched for c in candidates)} "
        "were watched"
    )
    if not candidates:
        raise RuntimeError("No unprotected movie or episode files are eligible for cleanup")

    if dry_run:
        projected = current_free
        selected: list[Candidate] = []
        for candidate in candidates:
            if projected > target:
                break
            selected.append(candidate)
            projected += candidate.size_bytes
        log(
            f"DRY_RUN: would request {len(selected)} deletions with a nominal reclaim of "
            f"{format_gib(sum(candidate.size_bytes for candidate in selected))}"
        )
        for candidate in selected[:20]:
            log(
                f"DRY_RUN: {'watched' if candidate.watched else 'unwatched'} | "
                f"{format_gib(candidate.size_bytes)} | {candidate.title}"
            )
        if len(selected) > 20:
            log(f"DRY_RUN: ... and {len(selected) - 20} more")
        return 0

    for candidate in candidates:
        current_free = free_bytes(media_path)
        if current_free > target:
            log(f"Cleanup complete with {format_gib(current_free)} free")
            return 0

        log(
            f"Deleting {'watched' if candidate.watched else 'unwatched'} "
            f"{candidate.media_type} {candidate.title!r} "
            f"({format_gib(candidate.size_bytes)})"
        )
        save_pending(state_path, candidate, current_free)
        maintainerr.request(
            "/api/collections/media/add",
            method="POST",
            body={
                "action": 0,
                "mediaId": candidate.media_id,
                "context": {"id": candidate.media_id, "type": candidate.media_type},
                "collectionId": candidate.collection_id,
            },
        )
        maintainerr.request(
            "/api/collections/media/handle",
            method="POST",
            body={
                "collectionId": candidate.collection_id,
                "mediaId": candidate.media_id,
            },
        )

        deadline = time.monotonic() + poll_seconds
        after = free_bytes(media_path)
        while after < current_free + min_reclaim and time.monotonic() < deadline:
            time.sleep(2)
            after = free_bytes(media_path)
        if after < current_free + min_reclaim:
            raise RuntimeError(
                f"Maintainerr handled {candidate.title!r}, but free space did not increase. "
                "The safety marker was retained to prevent cascading deletions."
            )
        clear_pending(state_path)
        log(f"Reclaimed {format_gib(after - current_free)}; now {format_gib(after)} free")

    current_free = free_bytes(media_path)
    if current_free <= target:
        raise RuntimeError(
            f"Eligible candidates were exhausted with only {format_gib(current_free)} free"
        )
    log(f"Cleanup complete with {format_gib(current_free)} free")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 - a CronJob must fail loudly and retry later.
        log(f"ERROR: {error}")
        sys.exit(1)

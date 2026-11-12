from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping


class Platform(StrEnum):
    WINDOWS = "windows"
    ANDROID = "android"
    IOS = "ios"


@dataclass(frozen=True, slots=True)
class Device:
    id: str
    platform: Platform
    name: str
    model: str = ""
    os_version: str = ""
    connected: bool = True
    details: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Target:
    id: str
    device_id: str
    name: str
    process_id: int | None = None
    icon_path: str = ""


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    key: str
    title: str
    unit: str
    group: str
    color: str
    platforms: frozenset[Platform]
    description: str = ""
    requirement: str = ""


@dataclass(frozen=True, slots=True)
class Sample:
    elapsed: float
    values: Mapping[str, float | None]
    # Asynchronous channels retain their own completed observation time.
    observed_elapsed: Mapping[str, float] = field(default_factory=dict)
    # Optional real source frames, never reconstructed from averaged metrics.
    frames: tuple[dict, ...] = ()
    # Valid cached readings may remain on screen, but are not new observations
    # and must not be appended again to curves, statistics or evidence rules.
    held_keys: frozenset[str] = frozenset()
    raw_values: Mapping[str, float | None] = field(default_factory=dict)
    adjustments: Mapping[str, str] = field(default_factory=dict)
    sources: Mapping[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class Session:
    id: str
    device: Device
    target: Target
    samples: list[Sample] = field(default_factory=list)

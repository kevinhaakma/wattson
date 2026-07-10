"""Gedeelde entity-metadata voor één herkenbaar Wattson-apparaat in HA."""
from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN


def wattson_device_info(coordinator) -> DeviceInfo:
    """Koppel alle Wattson-entiteiten aan dezelfde virtuele EMS-controller."""
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.entry.entry_id)},
        name="Wattson EMS",
        manufacturer="Wattson",
        model=f"{coordinator.adapter.title()} battery controller",
        configuration_url="https://github.com/kevinhaakma/wattson",
    )

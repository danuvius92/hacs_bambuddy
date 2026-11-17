"""BamBuddy's spool inventory (#21): which spool sits in which slot, shown
with Read Status and assigned with Manage Inventory."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from homeassistant.core import HomeAssistant

from custom_components.bambuddy.api import BamBuddyConnectionError, BamBuddyPermissionError
from custom_components.bambuddy.inventory import BamBuddyInventoryCoordinator, Spool, inventory_of, spool_of, spool_types
from custom_components.bambuddy.key import KeyGuard
from custom_components.bambuddy.inventory import INVENTORY_ATTRIBUTES
from custom_components.bambuddy.sensor import BamBuddyAmsSlotSensor, BamBuddyPrinterSensor

from tests.conftest import H2D, X1C, ams_uid, load_fixture, next_refresh, printer_uid, set_up, state_of
from tests.helpers import spool_row, with_inventory, with_spoolman_inventory


async def _refresh(hass: HomeAssistant, inventory: BamBuddyInventoryCoordinator) -> None:
    await inventory.async_refresh()
    await hass.async_block_till_done()


async def test_rows_of_deleted_or_archived_spools_are_no_assignment(
    hass: HomeAssistant, bambuddy_api: SimpleNamespace
) -> None:
    """BamBuddy 1.2.5.7 keeps a deleted spool's row ("spool": null) and an
    archived spool in its slot: neither is a spool in the slot."""
    with_inventory(bambuddy_api)
    entry = await set_up(hass, X1C)
    data = entry.runtime_data.inventory.data

    assert data.shown
    assert data.at(X1C, 0, 1) is None
    assert data.at(X1C, 0, 0) is None
    assert 4 not in data.spools
    assert data.at(X1C, 0, 2) == data.spools[1]


async def test_rows_of_other_printers_are_kept_for_their_place(hass: HomeAssistant, bambuddy_api: SimpleNamespace) -> None:
    """A spool in a printer this entry does not hold still has its place (a move takes it from there)."""
    with_inventory(bambuddy_api)
    entry = await set_up(hass, X1C)

    [place] = entry.runtime_data.inventory.data.places_of(3)
    assert (place.printer_id, place.printer_name, place.ams_id, place.tray_id) == (99, "Desk", 0, 0)


async def test_spoolman_on_reads_bambuddys_spoolman_inventory(hass: HomeAssistant, bambuddy_api: SimpleNamespace) -> None:
    """With Spoolman BamBuddy keeps the slots in its Spoolman table: that is read
    instead of its own inventory, and a row of a spool Spoolman no longer lists is none."""
    with_inventory(bambuddy_api)
    with_spoolman_inventory(bambuddy_api)
    entry = await set_up(hass, X1C)
    data = entry.runtime_data.inventory.data

    assert data.shown is True
    assert data.spoolman is True
    assert data.at(X1C, 0, 2) == data.spools[1]
    assert data.at(X1C, 0, 3) == data.spools[2]
    assert data.at(X1C, 0, 1) is None
    [place] = data.places_of(3)
    assert (place.printer_id, place.printer_name, place.ams_id, place.tray_id) == (99, "Desk", 0, 0)
    bambuddy_api.get_spool_assignments.assert_not_awaited()
    bambuddy_api.get_spools.assert_not_awaited()


async def test_a_slot_names_its_spoolman_spool(hass: HomeAssistant, bambuddy_api: SimpleNamespace) -> None:
    """A Spoolman spool shows in its slot as an inventory spool does: what is left, by its source."""
    with_spoolman_inventory(bambuddy_api)
    entry = await set_up(hass, X1C)

    spool = _slot(hass, entry, 3).attributes["inventory_spool"]
    assert (spool["id"], spool["name"], spool["remaining_g"], spool["remaining_pct"]) == (1, "Geeetech PLA Basic", 886, 89)
    assert spool["source"] == "spoolman"
    assert (spool["assigned_color"], spool["assigned_type"]) == ("", "")


def test_a_spoolman_row_without_its_spool_number_is_none() -> None:
    spools = [spool_row()]
    data = inventory_of(spools, [{"printer_id": 1, "ams_id": 0, "tray_id": 0}, {"printer_id": 1, "ams_id": 0, "tray_id": 1, "spoolman_spool_id": "1"}], spoolman=True)

    assert data.placed == []


def _slot(hass: HomeAssistant, entry: Any, slot: int, printer_id: int = X1C) -> Any:
    return state_of(hass, "sensor", ams_uid(entry, 0, f"slot_{slot}", printer_id))


async def test_a_slot_names_its_inventory_spool(hass: HomeAssistant, bambuddy_api: SimpleNamespace) -> None:
    """AMS A slot 3 (BamBuddy's tray 2) holds the Geeetech spool; a slot without one says only
    whether spools may be assigned."""
    with_inventory(bambuddy_api)
    entry = await set_up(hass, X1C)

    attributes = _slot(hass, entry, 3).attributes
    assert attributes["inventory_spool"] == {
        "id": 1,
        "name": "Geeetech PLA Basic",
        "brand": "Geeetech",
        "material": "PLA",
        "subtype": "Basic",
        "color_name": "Apple Green",
        "rgba": "B6D648FF",
        "extra_colors": [],
        "remaining_g": 886,
        "remaining_pct": 89,
        "label_weight": 1000,
        "storage_location": "Shelf 2",
        "rfid": False,
        "also_assigned": [],
        "assigned_color": "B6D648FF",
        "assigned_type": "PLA",
        "spool_types": ["PLA"],
        "source": "bambuddy",
    }
    assert attributes["inventory_assignable"] is True
    assert "inventory_spool" not in _slot(hass, entry, 2).attributes
    assert _slot(hass, entry, 2).attributes["inventory_assignable"] is True


async def test_the_external_spool_names_its_inventory_spool(hass: HomeAssistant, bambuddy_api: SimpleNamespace) -> None:
    """A two-nozzle printer's right external spool is BamBuddy's AMS 255, tray 1."""
    spools = load_fixture("inventory_spools.json")
    bambuddy_api.get_spools.return_value = spools
    bambuddy_api.get_spool_assignments.return_value = [
        {"id": 1, "spool_id": 3, "printer_id": H2D, "printer_name": "Studio", "ams_id": 255, "tray_id": 1, "spool": spools[2], "ams_label": None}
    ]
    entry = await set_up(hass, H2D)

    right = state_of(hass, "sensor", printer_uid(entry, "external_spool_2", H2D)).attributes
    left = state_of(hass, "sensor", printer_uid(entry, "external_spool", H2D)).attributes
    assert right["inventory_spool"]["name"] == "eSUN PLA Silk"
    assert right["inventory_spool"]["extra_colors"] == ["d4af37", "b87333"]
    assert "inventory_spool" not in left
    assert left["inventory_assignable"] is True


async def test_an_assignment_change_shows_without_a_printer_read(hass: HomeAssistant, bambuddy_api: SimpleNamespace) -> None:
    """The inventory's own read is enough: no printer status needed."""
    entry = await set_up(hass, X1C)
    assert "inventory_spool" not in _slot(hass, entry, 3).attributes
    reads = bambuddy_api.get_status.await_count

    with_inventory(bambuddy_api)
    await _refresh(hass, entry.runtime_data.inventory)

    assert _slot(hass, entry, 3).attributes["inventory_spool"]["id"] == 1
    assert bambuddy_api.get_status.await_count == reads


async def test_the_inventory_is_read_once_a_minute(
    hass: HomeAssistant, bambuddy_api: SimpleNamespace, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, X1C)
    assert bambuddy_api.get_spool_assignments.await_count == 1

    await next_refresh(hass, freezer, 61)

    assert bambuddy_api.get_spool_assignments.await_count == 2


def test_inventory_attributes_are_not_recorded() -> None:
    """The spool changes with each print: kept out of the recorder."""
    names = set(INVENTORY_ATTRIBUTES)
    assert names == {"inventory_spool", "inventory_assignable"}
    assert names <= BamBuddyAmsSlotSensor._unrecorded_attributes
    assert names <= BamBuddyPrinterSensor._unrecorded_attributes


async def test_no_inventory_no_attributes(hass: HomeAssistant, bambuddy_api: SimpleNamespace) -> None:
    with_inventory(bambuddy_api)
    bambuddy_api.get_spoolman_status.return_value = {"enabled": True, "connected": True, "url": None}
    bambuddy_api.get_spoolman_assignments.side_effect = BamBuddyPermissionError(403, "Missing permission: inventory:read")
    entry = await set_up(hass, X1C)

    attributes = _slot(hass, entry, 3).attributes
    assert "inventory_spool" not in attributes
    assert "inventory_assignable" not in attributes

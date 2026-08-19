"""Battery, thermal, and memory health. Read-only."""
import time

import pytest

from atf.system import THERMAL_STATUS

MAX_SAFE_TEMP_C = 45.0
MIN_AVAILABLE_MEMORY_RATIO = 0.10

# Skin sensors bound what the user touches; SoC/NPU sensors normally sit far
# hotter under load and only throttle in the high eighties.
MAX_SKIN_TEMP_C = 50.0
MAX_COMPONENT_TEMP_C = 85.0

# 0 none, 1 light, 2 moderate, 3 severe. Light throttling is routine while
# charging or under load, so only sustained moderate-or-worse is a failure.
THROTTLE_LIMIT = 2

# Above this the charger legitimately stops, and the platform may report
# not_charging rather than full.
FULL_ENOUGH_PERCENT = 99


@pytest.mark.system
def test_battery_reports_sane_level(system):
    battery = system.battery()
    assert battery.level is not None, "dumpsys battery reported no level"
    assert 0 <= battery.percent <= 100


@pytest.mark.system
def test_battery_health_is_good(system):
    health = system.battery().health
    assert health == "good", f"battery health is {health!r}"


@pytest.mark.system
def test_battery_temperature_is_safe(system):
    temp = system.battery().temperature_c
    assert temp is not None
    assert 0 < temp < MAX_SAFE_TEMP_C, f"battery at {temp}C"


@pytest.mark.system
def test_battery_voltage_is_plausible(system):
    voltage = system.battery().voltage_mv
    assert voltage is not None
    assert 3000 < voltage < 4500, f"battery voltage {voltage}mV outside Li-ion range"


@pytest.mark.system
def test_charging_state_is_self_consistent(system):
    """A powered device must never claim to be discharging.

    A full battery on the charger reports `not_charging` rather than `full` on
    Pixel hardware — the charger has simply stopped, which is correct — so the
    stricter assertion only applies below full.
    """
    battery = system.battery()
    if not battery.charging:
        return
    assert battery.status != "discharging", "powered but reports discharging"
    if battery.percent is not None and battery.percent < FULL_ENOUGH_PERCENT:
        assert battery.status == "charging", (
            f"powered at {battery.percent:.0f}% but status is {battery.status!r}"
        )


@pytest.mark.system
def test_device_is_not_thermally_throttled(system):
    status = system.thermal().status_code
    if status >= THROTTLE_LIMIT:
        # Confirm it is sustained rather than a spike from a burst of work.
        time.sleep(5)
        status = system.thermal().status_code
    assert status < THROTTLE_LIMIT, (
        f"sustained thermal throttling: {THERMAL_STATUS.get(status, status)!r}"
    )


@pytest.mark.system
def test_thermal_sensors_report_plausible_values(system):
    thermal = system.thermal()
    if not thermal.temperatures:
        pytest.skip("device exposes no thermal sensors via thermalservice")

    too_hot = []
    for sensor in thermal.temperatures:
        if sensor.celsius <= 0:
            continue          # unpopulated sensor
        limit = MAX_SKIN_TEMP_C if sensor.is_skin else MAX_COMPONENT_TEMP_C
        if sensor.celsius >= limit:
            kind = "skin" if sensor.is_skin else "component"
            too_hot.append(f"{sensor.name} ({kind}) at {sensor.celsius:.1f}C, "
                           f"limit {limit:.0f}C")
    assert not too_hot, "; ".join(too_hot)


@pytest.mark.system
def test_memory_is_not_exhausted(system):
    memory = system.memory()
    assert memory.total_kb and memory.total_kb > 0
    assert memory.available_ratio > MIN_AVAILABLE_MEMORY_RATIO, (
        f"only {memory.available_ratio:.1%} of RAM available"
    )


@pytest.mark.system
def test_load_average_is_reasonable(system):
    load = system.load_average()
    assert load is not None
    one_minute = load[0]
    assert one_minute < 16.0, f"1-minute load average is {one_minute}"

"""Battery, thermal, and memory health. Read-only."""
import pytest

MAX_SAFE_TEMP_C = 45.0
MIN_AVAILABLE_MEMORY_RATIO = 0.10


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
    battery = system.battery()
    if battery.charging:
        assert battery.status in ("charging", "full"), (
            f"powered but status is {battery.status!r}"
        )


@pytest.mark.system
def test_device_is_not_thermally_throttled(system):
    thermal = system.thermal()
    assert not thermal.throttling, f"thermal status is {thermal.status!r}"


@pytest.mark.system
def test_thermal_sensors_report_plausible_values(system):
    thermal = system.thermal()
    if not thermal.temperatures:
        pytest.skip("device exposes no thermal sensors via thermalservice")
    hottest = thermal.hottest()
    assert hottest is not None
    assert hottest.celsius < 60.0, f"{hottest.name} at {hottest.celsius}C"


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

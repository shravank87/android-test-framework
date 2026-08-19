"""Platform integrity and security posture. Read-only."""
import os
from datetime import date, datetime

import pytest

# Google ships monthly patches; 120 days allows for a few missed cycles.
# Override per-policy with ATF_MAX_PATCH_AGE_DAYS.
MAX_PATCH_AGE_DAYS = int(os.environ.get("ATF_MAX_PATCH_AGE_DAYS", "120"))


@pytest.mark.system
def test_selinux_is_enforcing(system):
    assert system.platform().selinux == "Enforcing"


@pytest.mark.system
def test_bootloader_is_locked(system):
    platform = system.platform()
    if platform.bootloader_locked is None:
        pytest.skip("device does not expose ro.boot.flash.locked")
    assert platform.bootloader_locked is True


@pytest.mark.system
def test_verified_boot_is_green(system):
    state = system.platform().verified_boot
    if not state:
        pytest.skip("device does not expose ro.boot.verifiedbootstate")
    assert state == "green", f"verified boot state is {state!r}, expected 'green'"


@pytest.mark.system
def test_storage_is_encrypted(system):
    assert system.platform().encryption == "encrypted"


@pytest.mark.system
def test_build_is_not_debuggable(system):
    platform = system.platform()
    assert platform.debuggable is False
    assert platform.build_type == "user", (
        f"build type is {platform.build_type!r}; userdebug/eng builds relax security"
    )


@pytest.mark.system
def test_security_patch_is_recent(system):
    patch = system.platform().security_patch
    assert patch, "device reports no security patch level"
    patch_date = datetime.strptime(patch, "%Y-%m-%d").date()
    age_days = (date.today() - patch_date).days
    assert age_days <= MAX_PATCH_AGE_DAYS, (
        f"security patch {patch} is {age_days} days old "
        f"(threshold {MAX_PATCH_AGE_DAYS})"
    )


@pytest.mark.system
def test_platform_reports_coherent_identity(system):
    platform = system.platform()
    assert platform.sdk >= 21
    assert platform.release
    assert "arm" in platform.abi or "x86" in platform.abi


@pytest.mark.system
def test_device_has_been_up_and_stable(system):
    uptime = system.uptime_seconds()
    assert uptime is not None and uptime > 0


@pytest.mark.system
def test_no_su_binary_on_path(adb):
    """A reachable su binary indicates a rooted device."""
    out = adb.shell("which", "su", check=False)
    assert not out.strip(), f"su binary present at {out.strip()!r}"

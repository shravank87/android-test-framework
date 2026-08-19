import pytest


@pytest.mark.instrumented
def test_full_instrumentation_suite(instrumentation):
    result = instrumentation()
    assert result.cases, "instrumentation ran but reported no test cases"
    result.assert_all_passed()


@pytest.mark.instrumented
def test_single_instrumentation_class(instrumentation):
    result = instrumentation(test_class="com.example.app.LoginTest")
    result.assert_all_passed()

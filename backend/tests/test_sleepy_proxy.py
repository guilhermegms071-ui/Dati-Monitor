"""The sleepy UDP proxy (simulated printer in energy-saving mode) drops only while waking up."""

from tests.conftest import load_script


class FakeClock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def test_first_request_wakes_printer_and_retry_is_answered() -> None:
    mod = load_script("sleepy_udp_proxy")
    clock = FakeClock()
    st = mod.SleepyState(idle=20.0, wake=1.0, clock=clock)
    assert st.accept() is False  # dormindo: acorda e perde o pacote
    clock.t += 0.5
    assert st.accept() is False  # ainda acordando
    clock.t += 1.0  # retentativa após 1,5 s
    assert st.accept() is True
    clock.t += 5
    assert st.accept() is True  # acordada
    clock.t += 25  # ociosa: dorme de novo
    assert st.accept() is False
    assert st.dropped == 3
    assert mod._addr("127.0.0.1:1166") == ("127.0.0.1", 1166)
    assert mod._addr(":161") == ("127.0.0.1", 161)

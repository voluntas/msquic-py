import msquic


def test_registration_create():
    reg = msquic.Registration("test_app", msquic.ExecutionProfile.LOW_LATENCY)
    assert reg is not None


def test_registration_shutdown():
    reg = msquic.Registration("test_app", msquic.ExecutionProfile.LOW_LATENCY)
    reg.shutdown(msquic.ConnectionShutdownFlags.NONE, 0)

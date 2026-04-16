from app.orchestrator.verification.verifier import Verifier


def test_verifier_rejects_empty_reply() -> None:
    verifier = Verifier()
    ok, error = verifier.verify("   ")
    assert ok is False
    assert error == "Empty reply from model"

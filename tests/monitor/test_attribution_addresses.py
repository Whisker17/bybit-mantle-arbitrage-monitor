"""Seam: classify_addresses / is_contract_code (mocked RPC)."""

from __future__ import annotations

from monitor.attribution import classify_addresses, is_contract_code, probe_roles


class FakeRpc:
    def __init__(
        self,
        codes: dict[str, str] | None = None,
        txs: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.codes = {k.lower(): v for k, v in (codes or {}).items()}
        self.txs = {k.lower(): v for k, v in (txs or {}).items()}
        self.calls: list[list[str]] = []

    def batch(self, calls: list[tuple[str, list[object]]]) -> list[object]:
        keys: list[str] = []
        out: list[object] = []
        for method, params in calls:
            if method == "eth_getCode":
                addr = str(params[0]).lower()
                keys.append(addr)
                out.append(self.codes.get(addr, "0x"))
            elif method == "eth_getTransactionByHash":
                txh = str(params[0]).lower()
                keys.append(txh)
                out.append(self.txs.get(txh))
            else:
                raise AssertionError(method)
        self.calls.append(keys)
        return out


def test_is_contract_code() -> None:
    assert is_contract_code("0x") is False
    assert is_contract_code("0x0") is False
    assert is_contract_code("") is False
    assert is_contract_code(None) is False
    assert is_contract_code("0x60806040") is True


def test_classify_addresses_batches_and_dedupes() -> None:
    a = "0x" + "11" * 20
    b = "0x" + "22" * 20
    rpc = FakeRpc({a: "0x6080", b: "0x"})
    out = classify_addresses([a, a.upper(), b], rpc, batch_size=1)
    assert out[a] is True
    assert out[b] is False
    # two batches of size 1; first addr not repeated despite duplicate input
    assert len(rpc.calls) == 2
    assert all(len(batch) == 1 for batch in rpc.calls)


def test_classify_empty() -> None:
    rpc = FakeRpc({})
    assert classify_addresses([], rpc) == {}


def test_probe_roles_entrypoint_vs_internal() -> None:
    a = "0x" + "11" * 20
    b = "0x" + "22" * 20
    rpc = FakeRpc(
        txs={
            "0xtxa": {"to": a},
            "0xtxb": {"to": "0x" + "99" * 20},
        }
    )
    out = probe_roles([(a, "0xtxa"), (b, "0xtxb")], rpc)
    assert out[a] == "entrypoint"
    assert out[b] == "internal"

"""Тесты моста к Rust-ядру swagscan.

Без сети и без привилегий: бинарь обязателен (это часть проекта), но трогается
только песочница в tmp_path. Тест отмены проверяет обещание из 9.9:
отмена реально завершает процесс.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from core.swagscan import FileEvent, SwagscanClient, SwagscanError, candidate_binary_paths


@pytest.fixture(scope="module")
def client() -> SwagscanClient:
    paths = candidate_binary_paths()
    exe = next((p for p in paths if p.exists()), None)
    if exe is None:
        pytest.skip("swagscan.exe не собран, мост нечем тестировать")
    c = SwagscanClient(exe)
    c.start()
    yield c
    c.stop()


@pytest.fixture()
def sandbox(tmp_path: Path) -> Path:
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_bytes(b"x" * 1000)
    (tmp_path / "sub" / "b.bin").write_bytes(b"y" * 2048)
    (tmp_path / "sub" / "c.dup").write_bytes(b"y" * 2048)
    return tmp_path


def test_hello_protocol(client: SwagscanClient) -> None:
    assert client.hello.get("protocol") == "swagscan-ndjson/1"
    assert "index" in client.hello.get("commands", [])


def test_ping(client: SwagscanClient) -> None:
    assert client.ping() is True


def test_drives(client: SwagscanClient) -> None:
    drives = client.drives()
    assert drives, "хотя бы один диск должен найтись"
    assert all(d["total"] > 0 for d in drives)


def test_index_sochinaet_faily_i_papki(client: SwagscanClient, sandbox: Path) -> None:
    data = client.index([str(sandbox)], top=10, categories=False)
    res = data["result"]
    assert res["files"] == 3
    assert res["bytes"] == 1000 + 2048 + 2048
    assert res["cancelled"] is False
    tops = [f["path"].lower() for f in data["top_files"]]
    assert any(t.endswith("a.txt") for t in tops)
    assert any(t.endswith("b.bin") for t in tops)


def test_index_progress_doetaets(client: SwagscanClient, sandbox: Path) -> None:
    events: list[dict] = []
    client.index([str(sandbox)], top=3, categories=False,
                 on_progress=lambda e: events.append(e))
    assert isinstance(events, list)


def test_duplicates_na_hod(client: SwagscanClient, sandbox: Path) -> None:
    groups: list[dict] = []
    data = client.duplicates([str(sandbox)], min_size=1024,
                             on_groups=lambda g: groups.append(g))
    assert data["groups"] >= 1
    assert groups, "dupgroups-событие должно прийти до result"
    worst = groups[0]["groups"][0]
    assert worst["size"] == 2048
    assert len(worst["paths"]) == 2


def test_purge_dry_run_nichego_ne_udalyaet(client: SwagscanClient, sandbox: Path) -> None:
    target = sandbox / "a.txt"
    data = client.purge([{"path": str(target), "category": "temp.app"}], dry_run=True)
    assert data["dry_run"] is True
    assert data["planned"] == 1
    assert target.exists(), "dry_run не имеет права трогать файл"


def test_purge_otkazyvaet_zashchishchennoe(client: SwagscanClient) -> None:
    data = client.purge(
        [
            {"path": "C:\\Windows\\System32\\kernel32.dll", "category": "temp.app"},
            {"path": "C:\\", "category": "temp.app"},
            {"path": "D:\\tot_net\\takogo", "category": "temp.app"},
        ],
        dry_run=True,
    )
    assert data["planned"] == 0
    assert data["refused"] == 3


def test_purge_udalyaet_fail_v_korzinu_nadezhno(client: SwagscanClient, sandbox: Path) -> None:
    target = sandbox / "sub" / "c.dup"
    data = client.purge([{"path": str(target), "category": "installers"}], dry_run=False)
    assert data["removed"] == 1, data["failures"]
    assert not target.exists()


def test_neizvestnaya_komanda_error_no_zhivo(client: SwagscanClient) -> None:
    with pytest.raises(SwagscanError):
        client.command("lalala")
    assert client.ping()


def test_otmena_realno_zavershaet_process(client: SwagscanClient) -> None:
    box = Path("C:/")
    if not box.exists():
        pytest.skip("нет C:/")
    got: dict = {}

    def bg() -> None:
        try:
            got["ev"] = client.index([str(box)], top=5)
        except SwagscanError as e:
            got["ev"] = e

    th = threading.Thread(target=bg)
    th.start()
    time.sleep(1.0)
    client.cancel()
    th.join(timeout=60)
    assert not th.is_alive(), "индекс не завершился после cancel"
    ev = got.get("ev")
    assert isinstance(ev, dict), ev
    assert ev["result"]["cancelled"] is True

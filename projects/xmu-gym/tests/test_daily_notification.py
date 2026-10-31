import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import daily

ACCOUNT = 'a' * 64
TASK = {'sport':'乒乓球', 'venue':'翔安校区乒乓球室', 'target_time_start':'18:00', 'target_time_end':'19:00', 'allow_time_fallback':True}
ROW = {'order_id':'42','details_id':'71','sport':'乒乓球','venue':'翔安校区乒乓球室','date':'2026-10-29','start':'19:30','end':'21:00','court':'05'}
RESULT = {'ok':True,'verified':True,'order_id':42,'date':'2026-10-29','time':'19:30-21:00','court':'05'}


def setup_run(tmp_path):
    store = daily.Store(tmp_path / 'state'); store.save(TASK, ACCOUNT)
    backend = Mock(); backend.auth.return_value = ACCOUNT
    backend.book.return_value = dict(RESULT)
    backend.list_orders.return_value = {'ok':True,'account_fingerprint':ACCOUNT,'orders':[dict(ROW)]}
    return store, backend


def test_verified_fallback_produces_matching_qr_and_text(tmp_path):
    store, backend = setup_run(tmp_path)
    from types import SimpleNamespace
    fetch = Mock(return_value=b'\x89PNG\r\n\x1a\nexample')
    with patch.dict(sys.modules, {'app.entry_qr': SimpleNamespace(fetch_entry_qr=fetch)}), patch.object(daily, 'IMAGE_ROOT', tmp_path / 'images', create=True):
        result = daily.run_daily(store, backend, datetime(2026,10,28,7,tzinfo=daily.CHINA))
    fetch.assert_called_once_with('42','71',ACCOUNT)
    assert '19:30' in result['message'] and '05' in result['message']
    image = Path(result['media_path'])
    assert image.read_bytes().startswith(b'\x89PNG')
    if os.name != 'nt':
        assert image.stat().st_mode & 0o777 == 0o600
        assert image.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("variant", ["duplicate", "wrong_order", "wrong_time", "wrong_court", "missing_detail", "wrong_account", "missing_order", "missing_time", "qr_failure", "invalid_png"])
def test_qr_unavailable_never_hides_verified_booking(tmp_path, variant):
    from types import SimpleNamespace
    store, backend = setup_run(tmp_path)
    listing = backend.list_orders.return_value
    if variant == "duplicate": listing["orders"].append(dict(ROW))
    if variant == "wrong_order": listing["orders"][0]["order_id"] = "43"
    if variant == "wrong_time": listing["orders"][0]["start"] = "18:00"
    if variant == "wrong_court": listing["orders"][0]["court"] = "06"
    if variant == "missing_detail": listing["orders"][0]["details_id"] = ""
    if variant == "wrong_account": listing["account_fingerprint"] = "b" * 64
    if variant == "missing_order": backend.book.return_value.pop("order_id")
    if variant == "missing_time": backend.book.return_value.pop("time")
    fetch = Mock(return_value=b"bad" if variant == "invalid_png" else b"\x89PNG\r\n\x1a\nexample")
    if variant == "qr_failure": fetch.side_effect = ValueError("private credential")
    with patch.dict(sys.modules, {"app.entry_qr":SimpleNamespace(fetch_entry_qr=fetch)}), patch.object(daily, "IMAGE_ROOT", tmp_path / "images"):
        result = daily.run_daily(store, backend, datetime(2026,10,28,7,tzinfo=daily.CHINA))
    assert result["verified"] is True and "预约成功" in result["message"]
    assert "二维码暂不可用" in result["message"] and "private credential" not in str(result)
    assert "media_path" not in result
    if variant not in ("qr_failure", "invalid_png"): fetch.assert_not_called()
    assert backend.book.call_count == 1
    with pytest.raises(ValueError): daily.run_daily(store, backend, datetime(2026,10,28,7,tzinfo=daily.CHINA))
    assert backend.book.call_count == 1


def test_unverified_never_fetches_qr(tmp_path):
    store, backend = setup_run(tmp_path)
    backend.book.return_value["verified"] = False
    with pytest.raises(ValueError): daily.run_daily(store, backend, datetime(2026,10,28,7,tzinfo=daily.CHINA))
    backend.list_orders.assert_not_called()


def test_cleanup_only_old_owned_files(tmp_path):
    import os, time
    root = tmp_path / "images"; root.mkdir()
    old = root / "gym-entry-old.png"; old.write_bytes(b"old")
    fresh = root / "gym-entry-fresh.png"; fresh.write_bytes(b"fresh")
    unrelated = root / "other.png"; unrelated.write_bytes(b"other")
    link = root / "gym-entry-link.png"; link.symlink_to(unrelated)
    os.utime(old, (time.time()-daily.IMAGE_TTL-10,)*2)
    with patch.object(daily, "IMAGE_ROOT", root): daily._save_qr(b"\x89PNG\r\n\x1a\nexample")
    assert not old.exists() and fresh.exists() and unrelated.exists() and link.is_symlink()


def test_main_outputs_standalone_media_line(capsys):
    with patch.object(daily, "run_daily", return_value={"message":"预约成功", "media_path":"/private/image.png"}):
        assert daily.main(["run"]) == 0
    assert capsys.readouterr().out == "预约成功\nMEDIA:/private/image.png\n"


def test_selects_exact_detail_when_order_contains_other_slots(tmp_path):
    from types import SimpleNamespace
    store, backend = setup_run(tmp_path)
    other = dict(ROW, details_id="72", start="18:00", end="19:00")
    backend.list_orders.return_value["orders"].insert(0, other)
    fetch = Mock(return_value=b"\x89PNG\r\n\x1a\nexample")
    with patch.dict(sys.modules, {"app.entry_qr":SimpleNamespace(fetch_entry_qr=fetch)}), patch.object(daily, "IMAGE_ROOT", tmp_path / "images"):
        result = daily.run_daily(store, backend, datetime(2026,10,28,7,tzinfo=daily.CHINA))
    fetch.assert_called_once_with("42", "71", ACCOUNT)
    assert result.get("media_path")


def test_refuses_symlink_image_directory(tmp_path):
    target = tmp_path / "target"; target.mkdir()
    link = tmp_path / "images"; link.symlink_to(target, target_is_directory=True)
    with patch.object(daily, "IMAGE_ROOT", link), pytest.raises(ValueError):
        daily._save_qr(b"\x89PNG\r\n\x1a\nexample")
    assert list(target.iterdir()) == []

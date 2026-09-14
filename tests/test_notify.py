"""Tests for notify.py: desktop notifications for face-auth attempts."""

import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

import notify  # conftest.py puts ubuntu-hello/src on sys.path


# ── GVariant rendering ───────────────────────────────────────────────

def test_gvariant_string_escapes_quotes_backslashes_newlines():
    assert notify.gvariant_string('a "b" \\ c\nd') == '"a \\"b\\" \\\\ c\\nd"'


def test_gvariant_hints_renders_supported_types_and_skips_unknown():
    out = notify.gvariant_hints({
        "urgency": ("byte", 2),
        "transient": True,
        "value": 42,
        "category": "presence",
        "ignored": 1.5,
    })
    assert out == '{"urgency": <byte 2>, "transient": <true>, "value": <int32 42>, "category": <"presence">}'


@pytest.mark.parametrize("output,expected", [
    ("(uint32 42,)\n", 42),
    ("(uint32 7,)", 7),
    ("", 0),
    ("garbage", 0),
])
def test_parse_notification_id(output, expected):
    assert notify.parse_notification_id(output) == expected


# ── construction / degradation ───────────────────────────────────────

def test_disabled_in_config_never_touches_bus():
    with patch("notify.subprocess.run") as run:
        n = notify.AuthNotifier("alice", enabled=False)
        n.success(2.5, 3.5, "Model #0", 10, 1.0)
    assert n.disabled_reason == "disabled in config"
    run.assert_not_called()


def test_missing_gdbus_degrades_silently():
    with patch("notify.os.path.exists", return_value=False), patch("notify.subprocess.run") as run:
        n = notify.AuthNotifier("alice")
        n.success(2.5, 3.5, "Model #0", 10, 1.0)
    assert n.disabled_reason == "gdbus not installed"
    run.assert_not_called()


def test_no_session_bus_degrades_silently():
    """Greeter login: the user has no session bus yet -> no card, no error."""
    with patch("notify.os.path.exists", side_effect=lambda p: p == notify.GDBUS_PATH), \
         patch("notify.pwd.getpwnam", return_value=MagicMock(pw_uid=1000, pw_gid=1000)), \
         patch("notify.subprocess.run") as run:
        n = notify.AuthNotifier("alice")
        n.timeout(3.7, 3.5, 20, 8.0, 8)
    assert n.disabled_reason == "no session bus for user"
    run.assert_not_called()


def test_unknown_user_degrades_silently():
    with patch("notify.os.path.exists", return_value=True), \
         patch("notify.pwd.getpwnam", side_effect=KeyError("nope")):
        n = notify.AuthNotifier("ghost")
    assert n.disabled_reason == "no session bus for user"


# ── transport ────────────────────────────────────────────────────────

@pytest.fixture
def live_notifier():
    with patch("notify.os.path.exists", return_value=True), \
         patch("notify.pwd.getpwnam", return_value=MagicMock(pw_uid=1000, pw_gid=1000)), \
         patch("notify.read_saved_id", return_value=0), \
         patch("notify.write_saved_id"):
        n = notify.AuthNotifier("alice")
    assert n.disabled_reason is None
    assert n.id_path == "/run/user/1000/ubuntu-hello-notify.id"
    return n


def _completed(stdout="(uint32 42,)\n", rc=0, stderr=""):
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)


def test_notify_runs_gdbus_on_users_bus_with_absolute_path(live_notifier):
    n = live_notifier
    with patch("notify.subprocess.run", return_value=_completed()) as run:
        assert n._notify("Sum", "Body", notify.URGENCY_LOW, 0) is True
    argv = run.call_args.args[0]
    kwargs = run.call_args.kwargs
    assert argv[0] == notify.GDBUS_PATH == "/usr/bin/gdbus"
    assert argv[1:4] == ["call", "--session", "--dest"]
    assert "org.freedesktop.Notifications.Notify" in argv
    assert kwargs["env"]["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"
    assert kwargs["env"]["XDG_RUNTIME_DIR"] == "/run/user/1000"
    assert kwargs["timeout"] == n.call_timeout
    assert callable(kwargs["preexec_fn"])
    assert n.notification_id == 42


def test_notify_updates_in_place_with_replaces_id(live_notifier):
    n = live_notifier
    with patch("notify.subprocess.run", return_value=_completed()) as run:
        n._notify("first", "b", notify.URGENCY_LOW, 0)
        n._notify("second", "b", notify.URGENCY_NORMAL, 5000)
    first, second = run.call_args_list
    # argv[0:9] = gdbus call --session --dest D --object-path P --method M; then app_name replaces_id icon summary body actions hints timeout
    assert first.args[0][10] == "0"          # new card
    assert second.args[0][10] == "42"        # same card, updated
    assert second.args[0][12] == "second"
    assert second.args[0][-1] == "5000"


def test_notify_hints_use_native_features(live_notifier):
    n = live_notifier
    with patch("notify.subprocess.run", return_value=_completed()) as run:
        n.success(2.6, 3.5, "Setup lighting 2", 12, 1.5, dark_frames=1)
    argv = run.call_args.args[0]
    hints = argv[-2]
    for needle in ('"urgency": <byte 1>', '"category": <"presence">',
                   '"x-canonical-private-synchronous": <"ubuntu-hello-auth">',
                   '"value": <int32 100>', '"sound-name": <"service-login">'):
        assert needle in hints, needle
    assert argv[11] == notify.APP_ICON
    assert "desktop-entry" not in hints   # GNOME shows no banner when the card is filed under the app
    assert argv[12] == "Face recognized"
    body = argv[13]
    assert body.startswith("✓ <b>alice</b> · <b>") and " s</b>" in body
    assert len(body) < 60, body   # must fit a single GNOME banner line
    # Release text is minimal: no model / score / frames unless details=true
    assert "Setup lighting 2" not in body and "2.60" not in body and "frames" not in body
    assert "\n" not in body   # GNOME collapses newlines: keep the body one line
    assert argv[-1] == str(notify.EXPIRE_SUCCESS)
    assert "transient" not in hints   # outcome must remain in the notification list


def test_gdbus_failure_disables_further_calls(live_notifier):
    n = live_notifier
    with patch("notify.subprocess.run", return_value=_completed(rc=1, stderr="Error: no such name")) as run:
        assert n._notify("s", "b", 1, 0) is False
        assert n._notify("s", "b", 1, 0) is False
    assert run.call_count == 1
    assert n.disabled_reason.startswith("notify error")


def test_gdbus_timeout_disables_further_calls(live_notifier):
    n = live_notifier
    with patch("notify.subprocess.run", side_effect=subprocess.TimeoutExpired("gdbus", 2)) as run:
        assert n._notify("s", "b", 1, 0) is False
        n.timeout(3.8, 3.5, 5, 8.0, 8)
    assert run.call_count == 1
    assert n.disabled_reason.startswith("gdbus failed")


def test_demote_drops_to_user_only_when_root(live_notifier):
    n = live_notifier
    with patch("notify.subprocess.run", return_value=_completed()) as run:
        n._notify("s", "b", 1, 0)
    demote = run.call_args.kwargs["preexec_fn"]
    with patch("notify.os.geteuid", return_value=0), patch("notify.os.setgroups") as sg, \
         patch("notify.os.setgid") as sgid, patch("notify.os.setuid") as suid:
        demote()
    sg.assert_called_once_with([])
    sgid.assert_called_once_with(1000)
    suid.assert_called_once_with(1000)
    with patch("notify.os.geteuid", return_value=1000), patch("notify.os.setuid") as suid:
        demote()
    suid.assert_not_called()


# ── content ──────────────────────────────────────────────────────────

def test_timeout_near_miss_gives_lighting_tip(live_notifier):
    n = live_notifier
    with patch("notify.subprocess.run", return_value=_completed()) as run:
        n.timeout(3.7, 3.5, 40, 8.0, 8, dark_frames=2)
    argv = run.call_args.args[0]
    assert argv[12] == "Face not recognized"
    body = argv[13]
    assert "No match in <b>8 s</b>" in body
    assert "add a model in this light" in body
    assert "closest" not in body and "ubuntu-hello add" not in body   # only with details=true
    assert len(body) < 60, body
    assert argv[11] == notify.APP_ICON
    assert argv[-1] == str(notify.EXPIRE_FAILURE)


def test_timeout_without_any_face_says_so(live_notifier):
    n = live_notifier
    with patch("notify.subprocess.run", return_value=_completed()) as run:
        n.timeout(None, 3.5, 30, 8.0, 8)
    body = run.call_args.args[0][13]
    assert body.startswith("✗ No match in <b>8 s</b>") and "💡" not in body
    assert "closest" not in body


def test_too_dark_reports_darkness_vs_threshold(live_notifier):
    n = live_notifier
    with patch("notify.subprocess.run", return_value=_completed()) as run:
        n.too_dark(71.2, 60, 15)
    body = run.call_args.args[0][13]
    assert "Too dark" in body and "turn on a light" in body
    assert "darkness" not in body            # numbers only with details=true


def test_start_is_asynchronous_critical_and_watchdogged(live_notifier):
    n = live_notifier
    with patch("notify.subprocess.run", return_value=_completed()) as run, \
         patch("notify.subprocess.Popen") as popen, \
         patch("notify.threading.Thread") as thread, \
         patch("notify.os.unlink") as unlink:
        n.start(device="/dev/v4l/by-path/pci-0000:00:14.0-usb-0:9:1.2-video-index0", max_seconds=20.0)
        thread.assert_called_once()
        run.assert_not_called()                     # not sent inline
        unlink.assert_called_once_with(n.done_path)  # stale "done" marker cleared
        thread.call_args.kwargs["target"]()          # run the thread body synchronously
    argv = run.call_args.args[0]
    assert argv[12] == "Face authentication"
    assert argv[13] == "👀 Looking for your face…"    # camera name only in details mode
    hints = argv[-2]
    # CRITICAL: GNOME keeps the banner up (no 4 s auto-hide) for the whole scan
    assert '"urgency": <byte 2>' in hints and '"value": <int32 0>' in hints
    assert argv[-1] == str(notify.EXPIRE_PROGRESS)
    # Watchdog: close after max_seconds + 5 unless a result card touched the marker
    script = popen.call_args.args[0][2]
    assert script.startswith("sleep 25.0; [ -e '/run/user/1000/ubuntu-hello-notify.id.done' ] || exec /usr/bin/gdbus")
    assert "CloseNotification 42" in script


def test_result_cards_touch_done_marker_and_lower_urgency(live_notifier):
    n = live_notifier
    with patch("notify.subprocess.run", return_value=_completed()) as run, \
         patch("notify.subprocess.Popen"), \
         patch.object(n, "_mark_done") as mark:
        n.success(2.5, 3.5, "M", 10, 1.0)
        assert '"urgency": <byte 1>' in run.call_args.args[0][-2]
        n.timeout(3.7, 3.5, 40, 8.0, 8)
        n.cancelled()
    assert mark.call_count == 3


def test_details_flag_appends_debug_line(live_notifier):
    n = live_notifier
    n.details = True
    with patch("notify.subprocess.run", return_value=_completed()) as run, \
         patch("notify.subprocess.Popen"), \
         patch("notify.os.getpid", return_value=4242), patch("notify.os.geteuid", return_value=0):
        n.success(2.5, 3.5, "M", 10, 1.0, device="/dev/video0", resolution="640x360")
    body = run.call_args.args[0][13]
    assert "“M”" in body and "<b>2.50</b>/<b>3.50</b>" in body and "<b>10</b> frames" in body
    assert "🔧 pid <b>4242</b>" in body and "uid <b>0</b>" in body
    assert "video0" in body and "640×360" in body
    assert "load <b>" in body and body.count("🔧") == 1
    assert body.index("✓ <b>alice</b>") < body.index("🔧")   # diagnostics last: ellipsized first in the banner


def test_body_escapes_markup(live_notifier):
    n = live_notifier
    n.details = True   # the model label is only rendered in details mode
    with patch("notify.subprocess.run", return_value=_completed()) as run, patch("notify.subprocess.Popen"):
        n.success(2.5, 3.5, "<b>evil</b> & co", 10, 1.0)
    body = run.call_args.args[0][13]
    assert "<b>evil</b>" not in body and "&lt;b&gt;evil&lt;/b&gt; &amp; co" in body


def test_result_card_reuses_start_context_in_details(live_notifier):
    n = live_notifier
    n.details = True
    with patch("notify.subprocess.run", return_value=_completed()) as run, patch("notify.threading.Thread"):
        n.start(device="/dev/video0", resolution="640x360", timeout="8s")
        n.timeout(3.7, 3.5, 40, 8.0, 8)
    body = run.call_args.args[0][13]
    assert "video0" in body and "640×360" in body and "timeout <b>8s</b>" in body


def test_display_name_prefers_gecos_full_name(live_notifier):
    n = live_notifier
    with patch("notify.pwd.getpwnam", return_value=MagicMock(pw_gecos="Alice Liddell,,,")):
        assert n.display_name() == "Alice Liddell"
    with patch("notify.pwd.getpwnam", return_value=MagicMock(pw_gecos="")):
        assert n.display_name() == "alice"
    with patch("notify.pwd.getpwnam", side_effect=KeyError):
        assert n.display_name() == "alice"


def test_body_markup_is_limited_to_bold(live_notifier):
    """GNOME Shell only renders <b>/<i>/<u>; <small> etc. would show as literal text."""
    n = live_notifier
    n.details = True
    with patch("notify.subprocess.run", return_value=_completed()) as run:
        n.success(2.5, 3.5, "M", 10, 1.0, device="/dev/video0")
    body = run.call_args.args[0][13]
    import re
    assert set(re.findall(r"</?([a-z]+)>", body)) == {"b"}


def test_saved_id_roundtrip(tmp_path):
    path = str(tmp_path / "notify.id")
    assert notify.read_saved_id(path) == 0
    notify.write_saved_id(path, 42)
    assert notify.read_saved_id(path) == 42
    (tmp_path / "notify.id").write_text("garbage")
    assert notify.read_saved_id(path) == 0
    assert not list(tmp_path.glob("*.tmp"))


def test_next_attempt_reuses_persisted_card_id():
    """Like the reference implementation: the id survives across processes so
    the next auth attempt updates the same card instead of stacking a new one."""
    with patch("notify.os.path.exists", return_value=True), \
         patch("notify.pwd.getpwnam", return_value=MagicMock(pw_uid=1000, pw_gid=1000)), \
         patch("notify.read_saved_id", return_value=77) as read, \
         patch("notify.write_saved_id") as write, \
         patch("notify.subprocess.run", return_value=_completed("(uint32 77,)\n")) as run:
        n = notify.AuthNotifier("alice")
        n.success(2.5, 3.5, "M", 10, 1.0)
    read.assert_called_once_with("/run/user/1000/ubuntu-hello-notify.id")
    assert run.call_args.args[0][10] == "77"      # replaces_id = persisted id
    write.assert_not_called()                     # unchanged id -> no rewrite


def test_new_id_from_server_is_persisted():
    """Stale persisted id (card dismissed): server allocates a new one -> save it."""
    with patch("notify.os.path.exists", return_value=True), \
         patch("notify.pwd.getpwnam", return_value=MagicMock(pw_uid=1000, pw_gid=1000)), \
         patch("notify.read_saved_id", return_value=77), \
         patch("notify.write_saved_id") as write, \
         patch("notify.subprocess.run", return_value=_completed("(uint32 78,)\n")):
        n = notify.AuthNotifier("alice")
        n.success(2.5, 3.5, "M", 10, 1.0)
    write.assert_called_once_with("/run/user/1000/ubuntu-hello-notify.id", 78, owner=(1000, 1000))
    assert n.notification_id == 78


def test_write_saved_id_hands_file_to_user_when_root(tmp_path):
    path = str(tmp_path / "notify.id")
    with patch("notify.os.geteuid", return_value=0), patch("notify.os.fchown") as fchown:
        notify.write_saved_id(path, 5, owner=(1000, 1000))
    fchown.assert_called_once()
    assert fchown.call_args.args[1:] == (1000, 1000)
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert notify.read_saved_id(path) == 5


def test_success_schedules_detached_close_after_linger(live_notifier):
    n = live_notifier
    n.success_linger = 3.0
    with patch("notify.subprocess.run", return_value=_completed("(uint32 42,)\n")), \
         patch("notify.subprocess.Popen") as popen:
        n.success(2.5, 3.5, "M", 10, 1.0)
    popen.assert_called_once()
    argv = popen.call_args.args[0]
    kwargs = popen.call_args.kwargs
    assert argv[0] == notify.SH_PATH == "/bin/sh"
    assert "sleep 3.0; exec /usr/bin/gdbus call --session" in argv[2]
    assert "CloseNotification 42" in argv[2]
    assert kwargs["start_new_session"] is True          # survives PAM's process-group SIGTERM
    assert kwargs["env"]["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"
    assert callable(kwargs["preexec_fn"])                # dropped to the user


def test_failure_cards_are_not_auto_closed(live_notifier):
    n = live_notifier
    with patch("notify.subprocess.run", return_value=_completed()), \
         patch("notify.subprocess.Popen") as popen:
        n.timeout(3.7, 3.5, 40, 8.0, 8)
        n.too_dark(71, 60, 5)
    popen.assert_not_called()


def test_success_linger_zero_keeps_card(live_notifier):
    n = live_notifier
    n.success_linger = 0
    with patch("notify.subprocess.run", return_value=_completed()), \
         patch("notify.subprocess.Popen") as popen:
        n.success(2.5, 3.5, "M", 10, 1.0)
    popen.assert_not_called()


def test_details_mode_shows_numbers_on_failure_cards(live_notifier):
    n = live_notifier
    n.details = True
    with patch("notify.subprocess.run", return_value=_completed()) as run:
        n.timeout(3.7, 3.5, 40, 8.0, 8, dark_frames=2)
        body = run.call_args.args[0][13]
        assert "closest <b>3.70</b>/<b>3.50</b>" in body and "<b>40</b> frames (<b>2</b> dark)" in body
        n.too_dark(71, 60, 15)
        body = run.call_args.args[0][13]
        assert "darkness <b>71</b>/<b>60</b>" in body and "<b>15</b> frames" in body


def test_default_success_linger_is_three_seconds(live_notifier):
    assert live_notifier.success_linger == 3.0


@pytest.mark.parametrize("service,label", [
    ("sudo", "sudo"), ("sudo-i", "sudo"), ("su", "su"), ("su-l", "su"),
    ("polkit-1", "authorization"),
    ("gdm-password", "screen unlock"), ("lightdm", "screen unlock"), ("sddm", "screen unlock"),
    ("gnome-screensaver", "screen unlock"),
    ("cups", "cups"), ("", ""),
])
def test_requester_label_from_pam_service(service, label):
    with patch("notify.os.path.exists", return_value=True), \
         patch("notify.pwd.getpwnam", return_value=MagicMock(pw_uid=1000, pw_gid=1000)), \
         patch("notify.read_saved_id", return_value=0):
        n = notify.AuthNotifier("alice", service=service)
    assert n.requester() == label


def test_titles_carry_the_requester(live_notifier):
    n = live_notifier
    n.service = "sudo"
    with patch("notify.subprocess.run", return_value=_completed()) as run, patch("notify.subprocess.Popen"):
        n.success(2.5, 3.5, "M", 10, 1.0)
        assert run.call_args.args[0][12] == "Face recognized · sudo"
        n.timeout(3.7, 3.5, 40, 8.0, 8)
        assert run.call_args.args[0][12] == "Face not recognized · sudo"
    n.service = ""
    with patch("notify.subprocess.run", return_value=_completed()) as run:
        n.timeout(3.7, 3.5, 40, 8.0, 8)
        assert run.call_args.args[0][12] == "Face not recognized"


def test_details_line_includes_raw_service(live_notifier):
    n = live_notifier
    n.service, n.details = "polkit-1", True
    with patch("notify.subprocess.run", return_value=_completed()) as run:
        n.timeout(3.7, 3.5, 40, 8.0, 8)
    assert "polkit-1" in run.call_args.args[0][13]


# ── privileged marker paths / symlink safety ─────────────────────────

def test_mark_done_does_not_follow_symlink(tmp_path):
    """Root open(..., 'w') used to truncate a user-planted symlink target."""
    victim = tmp_path / "victim"
    victim.write_text("keep\n")
    done = tmp_path / "done"
    done.symlink_to(victim)
    with patch("notify.os.path.exists", return_value=True), \
         patch("notify.pwd.getpwnam", return_value=MagicMock(pw_uid=1000, pw_gid=1000)), \
         patch("notify.notify_state_paths", return_value=(str(tmp_path / "id"), str(done))):
        n = notify.AuthNotifier("alice")
    n._mark_done()
    assert victim.read_text() == "keep\n"
    assert done.is_symlink()


def test_mark_done_writes_regular_file(tmp_path):
    done = tmp_path / "done"
    done.write_text("old\n")
    with patch("notify.os.path.exists", return_value=True), \
         patch("notify.pwd.getpwnam", return_value=MagicMock(pw_uid=1000, pw_gid=1000)), \
         patch("notify.notify_state_paths", return_value=(str(tmp_path / "id"), str(done))):
        n = notify.AuthNotifier("alice")
    n._mark_done()
    assert done.read_text() == "1\n"
    assert not done.is_symlink()
    assert oct(done.stat().st_mode & 0o777) == "0o600"


def test_write_saved_id_tmp_symlink_does_not_follow(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("keep\n")
    path = tmp_path / "notify.id"
    tmp = tmp_path / ("notify.id.%d.tmp" % os.getpid())
    tmp.symlink_to(victim)
    notify.write_saved_id(str(path), 9)
    assert victim.read_text() == "keep\n"
    assert notify.read_saved_id(str(path)) == 9
    assert not tmp.exists()


def test_read_saved_id_does_not_follow_symlink(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("42\n")
    path = tmp_path / "notify.id"
    path.symlink_to(victim)
    assert notify.read_saved_id(str(path)) == 0
    assert victim.read_text() == "42\n"


def test_open_nofollow_required(tmp_path, monkeypatch):
    monkeypatch.setattr(notify.os, "O_NOFOLLOW", 0)
    with pytest.raises(OSError, match="O_NOFOLLOW"):
        notify._open_nofollow(str(tmp_path / "x"), os.O_WRONLY | os.O_CREAT)


def test_close_watchdog_snippet_compiles():
    compile(notify._CLOSE_WATCHDOG, "<watchdog>", "exec")


def test_root_notify_state_uses_run_ubuntu_hello(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "RUN_DIR", str(tmp_path / "ubuntu-hello"))
    monkeypatch.setattr(notify, "_privileged", lambda: True)
    with patch("notify.os.path.exists", return_value=True), \
         patch("notify.pwd.getpwnam", return_value=MagicMock(pw_uid=1000, pw_gid=1000)):
        n = notify.AuthNotifier("alice")
    assert n.id_path == str(tmp_path / "ubuntu-hello/notify/1000/id")
    assert n.done_path == str(tmp_path / "ubuntu-hello/notify/1000/done")
    mode = os.stat(str(tmp_path / "ubuntu-hello/notify/1000")).st_mode
    assert oct(mode & 0o777) == "0o700"


def test_root_skips_user_runtime_when_priv_dir_fails(tmp_path, monkeypatch):
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("x")
    monkeypatch.setattr(notify, "RUN_DIR", str(blocked))
    monkeypatch.setattr(notify, "_privileged", lambda: True)
    with patch("notify.os.path.exists", return_value=True), \
         patch("notify.pwd.getpwnam", return_value=MagicMock(pw_uid=1000, pw_gid=1000)):
        n = notify.AuthNotifier("alice")
    assert n.id_path is None and n.done_path is None
    assert n.disabled_reason is None


def test_priv_dir_refuses_symlink_run_dir(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "ubuntu-hello"
    link.symlink_to(target)
    monkeypatch.setattr(notify, "RUN_DIR", str(link))
    monkeypatch.setattr(notify, "_privileged", lambda: True)
    with patch("notify.os.path.exists", return_value=True), \
         patch("notify.pwd.getpwnam", return_value=MagicMock(pw_uid=1000, pw_gid=1000)):
        n = notify.AuthNotifier("alice")
    assert n.id_path is None
    assert n.disabled_reason is None


def test_root_watchdog_lstats_marker_before_demote(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "RUN_DIR", str(tmp_path / "ubuntu-hello"))
    monkeypatch.setattr(notify, "_privileged", lambda: True)
    with patch("notify.os.path.exists", return_value=True), \
         patch("notify.pwd.getpwnam", return_value=MagicMock(pw_uid=1000, pw_gid=1000)):
        n = notify.AuthNotifier("alice")
    with patch("notify.subprocess.run", return_value=_completed()), \
         patch("notify.subprocess.Popen") as popen, \
         patch("notify.threading.Thread") as thread:
        n.start(max_seconds=20.0)
        thread.call_args.kwargs["target"]()
    argv = popen.call_args.args[0]
    assert argv[0] == sys.executable
    assert argv[1:4] == ["-E", "-s", "-c"]
    assert n.done_path in argv
    assert notify.GDBUS_PATH in argv
    assert popen.call_args.kwargs["preexec_fn"] is None
    assert popen.call_args.kwargs["start_new_session"] is True


def test_root_does_not_chown_priv_id_file(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "RUN_DIR", str(tmp_path / "ubuntu-hello"))
    monkeypatch.setattr(notify, "_privileged", lambda: True)
    with patch("notify.os.path.exists", return_value=True), \
         patch("notify.pwd.getpwnam", return_value=MagicMock(pw_uid=1000, pw_gid=1000)):
        n = notify.AuthNotifier("alice")
    with patch("notify.subprocess.run", return_value=_completed("(uint32 78,)\n")), \
         patch("notify.subprocess.Popen"), \
         patch("notify.write_saved_id") as write:
        n.success(2.5, 3.5, "M", 10, 1.0)
    write.assert_called_once_with(n.id_path, 78, owner=None)

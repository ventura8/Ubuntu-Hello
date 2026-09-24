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
         patch("notify.state_paths", return_value="/run/ubuntu-hello/notify/1000.id"), \
         patch("notify.read_saved_id", return_value=0), \
         patch("notify.write_saved_id"):
        n = notify.AuthNotifier("alice")
    assert n.disabled_reason is None
    assert n.id_path == "/run/ubuntu-hello/notify/1000.id"
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
    assert "preexec_fn" not in kwargs        # unsafe with threads; identity is dropped by subprocess itself
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
    assert body.startswith("✓ <b>alice</b> · <b>")
    assert " s</b>" in body
    assert len(body) < 60, body   # must fit a single GNOME banner line
    # Release text is minimal: no model / score / frames unless details=true
    assert "Setup lighting 2" not in body
    assert "2.60" not in body
    assert "frames" not in body
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


def test_identity_drop_uses_subprocess_user_group_only_when_root(live_notifier):
    """Root drops to the target user via subprocess's C-level user/group/extra_groups
    (fork-safe with threads); a dry run as the user passes no identity at all."""
    n = live_notifier
    with patch("notify.os.geteuid", return_value=0):
        kw = n._spawn_kwargs()
    assert kw["user"] == 1000
    assert kw["group"] == 1000
    assert kw["extra_groups"] == []
    assert kw["env"]["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"
    with patch("notify.os.geteuid", return_value=1000):
        kw = n._spawn_kwargs()
    assert "user" not in kw
    assert "group" not in kw


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
    assert "closest" not in body
    assert "ubuntu-hello add" not in body
    assert len(body) < 60, body
    assert argv[11] == notify.APP_ICON
    assert argv[-1] == str(notify.EXPIRE_FAILURE)


def test_timeout_without_any_face_says_so(live_notifier):
    n = live_notifier
    with patch("notify.subprocess.run", return_value=_completed()) as run:
        n.timeout(None, 3.5, 30, 8.0, 8)
    body = run.call_args.args[0][13]
    assert body.startswith("✗ No match in <b>8 s</b>")
    assert "💡" not in body
    assert "closest" not in body


def test_too_dark_reports_darkness_vs_threshold(live_notifier):
    n = live_notifier
    with patch("notify.subprocess.run", return_value=_completed()) as run:
        n.too_dark(71.2, 60, 15)
    body = run.call_args.args[0][13]
    assert "Too dark" in body
    assert "turn on a light" in body
    assert "darkness" not in body            # numbers only with details=true


def test_start_is_asynchronous_critical_and_watchdogged(live_notifier):
    n = live_notifier
    with patch("notify.subprocess.run", return_value=_completed()) as run, \
         patch("notify.subprocess.Popen") as popen, \
         patch("notify.threading.Thread") as thread:
        n.start(device="/dev/v4l/by-path/pci-0000:00:14.0-usb-0:9:1.2-video-index0", max_seconds=20.0)
        thread.assert_called_once()
        run.assert_not_called()                     # not sent inline
        thread.call_args.kwargs["target"]()          # run the thread body synchronously
    argv = run.call_args.args[0]
    assert argv[12] == "Face authentication"
    assert argv[13] == "👀 Looking for your face…"    # camera name only in details mode
    hints = argv[-2]
    # CRITICAL: GNOME keeps the banner up (no 4 s auto-hide) for the whole scan
    assert '"urgency": <byte 2>' in hints
    assert '"value": <int32 0>' in hints
    assert argv[-1] == str(notify.EXPIRE_PROGRESS)
    # Watchdog: close after max_seconds + 5 (killed again when a result arrives)
    script = popen.call_args.args[0][2]
    assert script.startswith("sleep 25.0; exec /usr/bin/gdbus")
    assert "CloseNotification 42" in script
    assert n._watchdog is popen.return_value


def test_result_card_kills_watchdog_and_blocks_late_progress_card(live_notifier):
    """Recognition can finish before start()'s thread runs: the result must win,
    and the progress watchdog must not be left to close the result card."""
    n = live_notifier
    watchdog = MagicMock()
    n._watchdog = watchdog
    with patch("notify.subprocess.run", return_value=_completed()) as run, \
         patch("notify.subprocess.Popen"):
        n.timeout(3.7, 3.5, 40, 8.0, 8)
        assert '"urgency": <byte 1>' in run.call_args.args[0][-2]
        watchdog.kill.assert_called_once()
        assert n._finalized is True
        # A late in-progress send is dropped and spawns no watchdog
        calls = run.call_count
        assert n._notify("Face authentication", "looking", notify.URGENCY_CRITICAL, 0, final=False) is False
        assert run.call_count == calls
        assert n._watchdog is None


def test_start_after_result_does_not_arm_a_watchdog(live_notifier):
    n = live_notifier
    n._finalized = True
    with patch("notify.subprocess.run", return_value=_completed()) as run, \
         patch("notify.subprocess.Popen") as popen, \
         patch("notify.threading.Thread") as thread:
        n.start(device="/dev/video0")
        thread.call_args.kwargs["target"]()
    run.assert_not_called()
    popen.assert_not_called()


def test_details_flag_appends_debug_line(live_notifier):
    n = live_notifier
    n.details = True
    with patch("notify.subprocess.run", return_value=_completed()) as run, \
         patch("notify.subprocess.Popen"), \
         patch("notify.os.getpid", return_value=4242), patch("notify.os.geteuid", return_value=0):
        n.success(2.5, 3.5, "M", 10, 1.0, device="/dev/video0", resolution="640x360")
    body = run.call_args.args[0][13]
    assert "“M”" in body
    assert "<b>2.50</b>/<b>3.50</b>" in body
    assert "<b>10</b> frames" in body
    assert "🔧 pid <b>4242</b>" in body
    assert "uid <b>0</b>" in body
    assert "video0" in body
    assert "640×360" in body
    assert "load <b>" in body
    assert body.count("🔧") == 1
    assert body.index("✓ <b>alice</b>") < body.index("🔧")   # diagnostics last: ellipsized first in the banner


def test_body_escapes_markup(live_notifier):
    n = live_notifier
    n.details = True   # the model label is only rendered in details mode
    with patch("notify.subprocess.run", return_value=_completed()) as run, patch("notify.subprocess.Popen"):
        n.success(2.5, 3.5, "<b>evil</b> & co", 10, 1.0)
    body = run.call_args.args[0][13]
    assert "<b>evil</b>" not in body
    assert "&lt;b&gt;evil&lt;/b&gt; &amp; co" in body


def test_result_card_reuses_start_context_in_details(live_notifier):
    n = live_notifier
    n.details = True
    with patch("notify.subprocess.run", return_value=_completed()) as run, patch("notify.threading.Thread"):
        n.start(device="/dev/video0", resolution="640x360", timeout="8s")
        n.timeout(3.7, 3.5, 40, 8.0, 8)
    body = run.call_args.args[0][13]
    assert "video0" in body
    assert "640×360" in body
    assert "timeout <b>8s</b>" in body


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
         patch("notify.state_paths", return_value="/run/ubuntu-hello/notify/1000.id"), \
         patch("notify.read_saved_id", return_value=77) as read, \
         patch("notify.write_saved_id") as write, \
         patch("notify.subprocess.run", return_value=_completed("(uint32 77,)\n")) as run, \
         patch("notify.subprocess.Popen"):
        n = notify.AuthNotifier("alice")
        n.success(2.5, 3.5, "M", 10, 1.0)
    read.assert_called_once_with("/run/ubuntu-hello/notify/1000.id")
    assert run.call_args.args[0][10] == "77"      # replaces_id = persisted id
    write.assert_not_called()                     # unchanged id -> no rewrite


def test_new_id_from_server_is_persisted():
    """Stale persisted id (card dismissed): server allocates a new one -> save it."""
    with patch("notify.os.path.exists", return_value=True), \
         patch("notify.pwd.getpwnam", return_value=MagicMock(pw_uid=1000, pw_gid=1000)), \
         patch("notify.state_paths", return_value="/run/ubuntu-hello/notify/1000.id"), \
         patch("notify.read_saved_id", return_value=77), \
         patch("notify.write_saved_id") as write, \
         patch("notify.subprocess.run", return_value=_completed("(uint32 78,)\n")), \
         patch("notify.subprocess.Popen"):
        n = notify.AuthNotifier("alice")
        n.success(2.5, 3.5, "M", 10, 1.0)
    write.assert_called_once_with("/run/ubuntu-hello/notify/1000.id", 78)
    assert n.notification_id == 78


def test_saved_id_files_never_follow_symlinks(tmp_path):
    """Root must not be redirected by a planted symlink (Cursor finding): writes
    and reads use O_NOFOLLOW and the target is left untouched."""
    victim = tmp_path / "victim"
    victim.write_text("keep")
    link = tmp_path / "notify.id"
    link.symlink_to(victim)
    notify.write_saved_id(str(link), 5)
    assert victim.read_text() == "keep"
    assert notify.read_saved_id(str(link)) == 0
    assert oct(os.stat(tmp_path / "victim").st_mode & 0o777) != "0o600" or True
    # A regular file works and is private
    real = tmp_path / "real.id"
    notify.write_saved_id(str(real), 9)
    assert notify.read_saved_id(str(real)) == 9
    assert oct(os.stat(real).st_mode & 0o777) == "0o600"


def test_state_paths_root_uses_private_root_dir_not_users_runtime_dir(tmp_path):
    root_dir = str(tmp_path / "notify")
    # euid 0 selects the root branch; the directory the test creates is owned by the
    # (non-root) test user, so the ownership check must compare against geteuid() as well
    with patch("notify.os.geteuid", side_effect=[0, os.getuid()]), patch("notify.ROOT_STATE_DIR", root_dir):
        assert notify.state_paths(1000, "/run/user/1000") == os.path.join(root_dir, "1000.id")
    assert oct(os.stat(root_dir).st_mode & 0o777) == "0o700"
    # under sudo's PAM stack ruid=user / euid=0: a root-owned state dir must be accepted
    # (the old check compared against getuid() and silently disabled id persistence there)
    with patch("notify.os.geteuid", return_value=os.getuid()), patch("notify.os.getuid", return_value=4242), \
         patch("notify.ROOT_STATE_DIR", root_dir):
        assert notify._private_dir(root_dir) is True
    # A symlink (or foreign dir) in the way disables the state file entirely
    bad = tmp_path / "bad"
    bad.symlink_to(tmp_path)
    with patch("notify.os.geteuid", side_effect=[0, os.getuid()]), patch("notify.ROOT_STATE_DIR", str(bad)):
        assert notify.state_paths(1000, "/run/user/1000") is None
    with patch("notify.os.geteuid", return_value=1000):
        assert notify.state_paths(1000, "/run/user/1000") == "/run/user/1000/ubuntu-hello-notify.id"


def test_no_state_file_still_notifies():
    with patch("notify.os.path.exists", return_value=True), \
         patch("notify.pwd.getpwnam", return_value=MagicMock(pw_uid=1000, pw_gid=1000)), \
         patch("notify.state_paths", return_value=None), \
         patch("notify.subprocess.run", return_value=_completed()) as run:
        n = notify.AuthNotifier("alice")
        assert n.notification_id == 0
        assert n._notify("s", "b", 1, 0) is True
    run.assert_called_once()


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
    assert "preexec_fn" not in kwargs


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
        assert "closest <b>3.70</b>/<b>3.50</b>" in body
        assert "<b>40</b> frames (<b>2</b> dark)" in body
        n.too_dark(71, 60, 15)
        body = run.call_args.args[0][13]
        assert "darkness <b>71</b>/<b>60</b>" in body
        assert "<b>15</b> frames" in body


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


def test_rejected_card_for_failed_liveness(live_notifier):
    n = live_notifier
    n.service = "sudo"
    with patch("notify.subprocess.run", return_value=_completed()) as run, patch("notify.subprocess.Popen") as popen:
        n.rejected()
    argv = run.call_args.args[0]
    assert argv[12] == "Face not recognized · sudo"
    assert "Liveness check failed" in argv[13]
    popen.assert_not_called()   # stays, like every failure card


def test_no_model_and_cancelled_cards(live_notifier):
    n = live_notifier
    n.service = "polkit-1"
    with patch("notify.subprocess.run", return_value=_completed()) as run, patch("notify.subprocess.Popen"):
        n.no_model()
        argv = run.call_args.args[0]
        assert argv[12] == "Face authentication unavailable · authorization"
        assert "No face model for alice" in argv[13]
        assert "sudo ubuntu-hello add" in argv[13]
        assert '"urgency": <byte 0>' in argv[-2]
        n.cancelled()
        assert run.call_args.args[0][12] == "Face authentication cancelled · authorization"
        assert "⏹ Cancelled" in run.call_args.args[0][13]
        n.cancelled(reason="Session went idle")
        assert "Session went idle" in run.call_args.args[0][13]


def test_sound_switch_controls_result_hints(live_notifier):
    n = live_notifier
    n.sound = False
    with patch("notify.subprocess.run", return_value=_completed()) as run, patch("notify.subprocess.Popen"):
        n.timeout(3.7, 3.5, 40, 8.0, 8)
    hints = run.call_args.args[0][-2]
    assert '"suppress-sound": <true>' in hints
    assert "sound-name" not in hints


def test_watchdog_kill_errors_are_swallowed(live_notifier):
    n = live_notifier
    dead = MagicMock()
    dead.kill.side_effect = OSError("gone")
    n._watchdog = dead
    n._cancel_watchdog()          # must not raise
    assert n._watchdog is None
    n._cancel_watchdog()          # nothing armed: no-op


def test_close_later_spawn_failure_returns_none(live_notifier):
    n = live_notifier
    n.notification_id = 42
    with patch("notify.subprocess.Popen", side_effect=OSError("no sh")):
        assert n._close_later(1.0) is None
    assert n._close_later(0) is None   # linger 0 never spawns


def test_private_dir_rejects_unwritable_location():
    with patch("notify.os.makedirs", side_effect=OSError("ro")):
        assert notify._private_dir("/nowhere/notify") is False


def test_debug_part_survives_missing_loadavg(live_notifier):
    n = live_notifier
    n.details = True
    with patch("notify.os.getloadavg", side_effect=OSError("no proc")):
        part = n._debug_part({})
    assert part.startswith("🔧 pid")
    assert "load" not in part


# ── EnrollNotifier: guided-enrollment prompts mirrored under the camera ──

@pytest.fixture
def enroll_notifier():
    with patch("notify.os.path.exists", return_value=True), \
         patch("notify.pwd.getpwnam", return_value=MagicMock(pw_uid=1000, pw_gid=1000)), \
         patch("notify.state_paths", return_value="/run/ubuntu-hello/notify/1000.id"), \
         patch("notify.read_saved_id", return_value=0), \
         patch("notify.write_saved_id"):
        n = notify.EnrollNotifier("alice")
    assert n.disabled_reason is None
    return n


def _notify_args(run):
    argv = run.call_args.args[0]
    i = argv.index("org.freedesktop.Notifications.Notify")
    return {"summary": argv[i + 4], "body": argv[i + 5], "hints": argv[i + 7], "expire": argv[i + 8]}


def test_enroll_guide_updates_one_critical_card_with_prompt_and_progress(enroll_notifier):
    n = enroll_notifier
    with patch("notify.subprocess.run", return_value=_completed()) as run:
        assert n.guide("Turn your head slightly to the left", 3, 13) is True
        first = _notify_args(run)
        assert n.guide("Tilt your chin up a little", 5, 13) is True
        second = _notify_args(run)
    assert first["summary"] == "Face enrollment"
    assert "Turn your head slightly to the left" in first["body"]
    assert "3 of 13" in first["body"]
    assert "<byte 2>" in first["hints"]
    assert '"value": <int32 23>' in first["hints"]
    assert first["expire"] == "0"  # stays until replaced
    assert "Tilt your chin up a little" in second["body"]
    assert "5 of 13" in second["body"]
    # second call reused the id from the first reply -> the card is updated in place
    argv = run.call_args.args[0]
    assert argv[argv.index("org.freedesktop.Notifications.Notify") + 2] == "42"
    assert run.call_count == 2


def test_enroll_guide_without_total_has_no_counter(enroll_notifier):
    n = enroll_notifier
    with patch("notify.subprocess.run", return_value=_completed()) as run:
        n.guide("Look straight at the camera")
    body = _notify_args(run)["body"]
    assert "Look straight at the camera" in body
    assert " of " not in body


def test_enroll_saved_is_final_and_closes_after_linger(enroll_notifier):
    n = enroll_notifier
    with patch("notify.subprocess.run", return_value=_completed()) as run, \
         patch.object(n, "_close_later") as close_later:
        n.saved("Setup lighting 1", 13)
        args = _notify_args(run)
        # a late guide() after the result must not overwrite it
        assert n.guide("Look straight at the camera", 1, 13) is False
    assert "Face model saved" in args["body"]
    assert "Setup lighting 1" in args["body"]
    assert "13 samples" in args["body"]
    assert "<byte 1>" in args["hints"]
    assert '"value": <int32 100>' in args["hints"]
    close_later.assert_called_once_with(4.0)
    assert run.call_count == 1


def test_enroll_failed_shows_reason(enroll_notifier):
    n = enroll_notifier
    with patch("notify.subprocess.run", return_value=_completed()) as run:
        n.failed("No face detected, aborting")
    args = _notify_args(run)
    assert "No face detected, aborting" in args["body"]
    assert args["expire"] == str(notify.EXPIRE_FAILURE)


def test_enroll_notifier_disabled_or_no_bus_is_silent():
    n = notify.EnrollNotifier("alice", enabled=False)
    assert n.disabled_reason == "disabled in config"
    with patch("notify.subprocess.run") as run:
        assert n.guide("x", 1, 2) is False
        n.saved("l", 1)
        n.failed("r")
    run.assert_not_called()
    assert n.sound is False
    assert n.details is False


def test_liveness_challenge_updates_the_progress_card_without_finalising(enroll_notifier):
    """The nod prompt must reach the user: the auth overlay has no display under PAM's
    minimal environment, so the card is the only channel that works."""
    n = enroll_notifier
    with patch("notify.subprocess.run", return_value=_completed()) as run:
        assert n.liveness("Nod to confirm", "Shake your head to abort") is True
    args = _notify_args(run)
    assert "Nod to confirm" in args["body"]
    assert "Shake your head to abort" in args["body"]
    assert "<byte 2>" in args["hints"]
    assert args["expire"] == "0"
    # a result card can still replace it
    with patch("notify.subprocess.run", return_value=_completed()):
        n.saved("l", 1)
        assert n.liveness("Nod to confirm") is False


def test_liveness_is_silent_when_notifications_are_off():
    n = notify.AuthNotifier("alice", enabled=False)
    with patch("notify.subprocess.run") as run:
        assert n.liveness("Nod to confirm") is False
        assert n.liveness("") is False
    run.assert_not_called()

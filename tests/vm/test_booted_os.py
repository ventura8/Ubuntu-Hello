"""Assertions over the scenario results from a booted OS. See conftest.py."""
from __future__ import annotations

import pytest

from tests.vm.conftest import checks


def _all_true(results, scenario, *names):
	passed, info = checks(results, scenario)
	failed = [n for n in names if not passed.get(n)]
	assert not failed, "%s: failed %s\ninfo: %s" % (scenario, failed, info)


class TestInstall:
	def test_package_installs_cleanly_and_wires_pam(self, results):
		_all_true(results, "00-install", "install_succeeded", "package_installed", "gtk_package_installed", "cli_runs",
		          "pam_line_present", "pam_module_file", "config_installed", "config_dir_mode", "config_file_not_executable", "models_dir_root_only")

	def test_the_pieces_a_container_cannot_host_are_in_place(self, results):
		"""The polkit drop-in, the boot-time runtime dir and the TPM device."""
		_all_true(results, "00-install", "polkit_dropin", "polkit_dropin_tpm", "tmpfiles_rule",
		          "runtime_dir_now", "tpm_device_visible")

	def test_runtime_pieces_a_fresh_install_needs(self, results):
		_all_true(results, "00-install", "config_has_confirmations", "dlib_importable",
		          "pillow_importable", "catalogs_installed", "landmark_model")

	def test_installed_version_is_the_one_offered(self, results):
		"""The PPA's candidate, or the version inside the local package file."""
		_, info = checks(results, "00-install")
		assert info["version"] == info["candidate"], info


class TestConfigMigration:
	def test_a_hand_edited_config_survives_a_reinstall(self, results):
		_all_true(results, "10-config-migration", "kept_hand_edit", "kept_threshold", "comments_survive")

	def test_the_missing_key_is_added_at_the_users_level(self, results):
		_all_true(results, "10-config-migration", "added_confirmations", "added_value_is_fast_level")

	def test_a_hand_tightened_threshold_stays_on_one_frame(self, results):
		"""Adding four agreeing frames to a 2.0 threshold would make login impossible."""
		_all_true(results, "10-config-migration", "tight_threshold_kept", "tight_stays_single_frame")

	def test_an_existing_value_is_never_overwritten(self, results):
		_all_true(results, "10-config-migration", "existing_value_untouched")


class TestPamLoginPath:
	def test_no_camera_fails_closed_and_falls_through_to_the_password_promptly(self, results):
		_all_true(results, "20-pam-sudo", "no_camera_password_fallback", "no_camera_prompt_fast",
		          "module_ran_and_failed_closed")

	def test_disabled_and_no_model_both_step_aside(self, results):
		_all_true(results, "20-pam-sudo", "disabled_password_fallback", "no_model_password_fallback")

	def test_a_wrong_password_is_still_refused(self, results):
		"""Face auth being present must never weaken the password check."""
		_all_true(results, "20-pam-sudo", "wrong_password_refused")

	def test_the_helper_never_inherits_the_callers_environment(self, results):
		_all_true(results, "20-pam-sudo", "env_not_inherited")

	def test_the_module_does_not_hijack_the_hosts_syslog_identity(self, results):
		"""openlog() without closelog() tagged sudo's own lines as pam_ubuntu_hello."""
		_all_true(results, "20-pam-sudo", "host_syslog_identity_kept")


class TestGreeterRule:
	"""The HARD RULE in main.cc: under a greeter service with `workaround = off` the
	module must not prompt for a password itself. pam_probe.py runs the real PAM
	stack for the gdm-password service and counts what the conversation is asked."""

	def test_the_module_never_prompts_under_the_greeter_with_the_workaround_off(self, results):
		_all_true(results, "25-greeter-pam", "greeter_off_succeeds", "greeter_off_single_prompt")

	def test_sudo_behaves_the_same_as_the_control(self, results):
		_all_true(results, "25-greeter-pam", "sudo_off_succeeds", "sudo_off_single_prompt")

	def test_a_wrong_password_is_refused_under_both_workaround_settings(self, results):
		_all_true(results, "25-greeter-pam", "greeter_off_wrong_refused", "greeter_input_wrong_refused")

	def test_the_input_workaround_still_lets_the_password_through(self, results):
		_all_true(results, "25-greeter-pam", "greeter_input_succeeds", "greeter_still_runs_after_failure")


class TestPolkitSandbox:
	def test_the_tpm_is_blocked_without_the_dropin_and_reachable_with_it(self, results):
		"""The defect a real polkit prompt found: unseal refused under the hardened unit."""
		_all_true(results, "30-polkit-sandbox", "polkit_unit_is_hardened", "tpm_present", "tpm_tools",
		          "tpm_blocked_without_dropin", "tpm_unseal_with_dropin")

	def test_the_dropin_grants_exactly_what_the_notifier_and_camera_need(self, results):
		_all_true(results, "30-polkit-sandbox", "runtime_dir_writable_with_dropin",
		          "home_hidden_but_run_user_visible")


class TestReboot:
	def test_it_really_rebooted(self, results):
		_, before = checks(results, "40-reboot")
		_, after = checks(results, "40-reboot.after-reboot")
		assert before["boot_id_before"] != after["boot_id_after"]

	def test_what_only_a_boot_can_prove(self, results):
		"""/run is a fresh tmpfs: the runtime dir exists only if tmpfiles.d honours the rule."""
		_all_true(results, "40-reboot.after-reboot", "runtime_dir_recreated_at_boot", "runtime_dir_root_only",
		          "pam_line_survives", "polkit_daemon_reachable", "tpm_still_visible", "password_login_still_works")


class TestRemove:
	def test_purge_leaves_nothing_behind(self, results):
		_all_true(results, "90-remove", "packages_gone", "pam_line_gone", "pam_module_gone", "config_dir_gone",
		          "libdir_gone", "gtk_libdir_gone", "catalogs_gone", "polkit_dropin_gone", "tmpfiles_rule_gone",
		          "desktop_file_gone", "no_leftovers")

	def test_login_still_works_after_purge(self, results):
		_all_true(results, "90-remove", "password_login_works")

	def test_nothing_crashed_during_the_run(self, results):
		"""apport keeps a report for every crashed process, whatever the other checks saw."""
		_all_true(results, "90-remove", "no_crash_reports")


class TestUpgradeFromPreviousRelease:
	"""A real apt upgrade from the previous GitHub release over a hand-edited config."""

	def test_the_upgrade_repairs_what_the_old_version_left_half_configured(self, results):
		_all_true(results, "95-upgrade-from-previous", "prev_config_present", "upgrade_succeeded",
		          "upgraded_package_configured", "upgraded_gtk_configured", "upgrade_repaired_dlib")

	def test_the_users_config_and_models_are_kept(self, results):
		_all_true(results, "95-upgrade-from-previous", "kept_hand_edits", "kept_threshold", "kept_user_comment",
		          "kept_models")

	def test_the_new_key_is_added_at_the_users_level(self, results):
		_all_true(results, "95-upgrade-from-previous", "migration_added_key", "migration_used_fast_level")

	def test_login_works_after_the_upgrade(self, results):
		_all_true(results, "95-upgrade-from-previous", "pam_line_present", "runtime_dir_present",
		          "password_login_works")

// Unit test for the PAM module's helpers in main.cc / main.hh / optional_task.hh.
//
// main.cc is compiled into this test directly and libpam is replaced by the
// stubs below, so the gates identify() runs (ignored services, username
// checks, check_enabled), the compare exit-status handling, the root helper's
// argv/env sanitising and the pam_sm_* entry points run without a PAM stack,
// a camera or root.
#include "main.cc"

#include <csignal>
#include <iostream>
#include <utility>

namespace {

int failures = 0;

void check(bool condition, const char *what) {
  if (!condition) {
    std::cerr << "FAIL: " << what << "\n";
    failures++;
  }
}

// ---- libevdev uinput stubs -------------------------------------------------
// The test binary's definitions take precedence over libevdev's, so
// EnterDevice runs without /dev/uinput and each failure path can be forced.
struct UinputStub {
  int create_result = 0;
  int fail_write_at = -1;  // 0-based write index that fails, -1 = never
  int writes = 0;
  int destroyed = 0;
  std::vector<std::array<int, 3>> events;  // type, code, value per write
};
UinputStub uinput_stub;
int uinput_dummy = 0;

// ---- libpam stubs ----------------------------------------------------------
struct PamStub {
  int get_user_result = PAM_SUCCESS;
  const char *user = "alice";
  const char *service = "sudo";
  const char *authtok = nullptr;
  int set_item_calls = 0;
};
PamStub pam_stub;

} // namespace

extern "C" {
auto pam_get_user(pam_handle_t * /*pamh*/, const char **user,
                  const char * /*prompt*/) -> int {
  *user = pam_stub.user;
  return pam_stub.get_user_result;
}

auto pam_get_item(const pam_handle_t * /*pamh*/, int item_type,
                  const void **item) -> int {
  if (item_type == PAM_SERVICE) {
    *item = pam_stub.service;
    return PAM_SUCCESS;
  }
  if (item_type == PAM_AUTHTOK) {
    *item = pam_stub.authtok;
    return PAM_SUCCESS;
  }
  return PAM_BAD_ITEM;
}

auto pam_set_item(pam_handle_t * /*pamh*/, int /*item_type*/,
                  const void * /*item*/) -> int {
  pam_stub.set_item_calls++;
  return PAM_SUCCESS;
}

auto pam_strerror(pam_handle_t * /*pamh*/, int /*errnum*/) -> const char * {
  return "stub";
}

auto pam_get_authtok(pam_handle_t * /*pamh*/, int /*item*/,
                     const char **authtok, const char * /*prompt*/) -> int {
  *authtok = nullptr;
  return PAM_AUTHTOK_ERR;
}
}

extern "C" {
auto libevdev_uinput_create_from_device(const struct libevdev * /*dev*/, int /*uinput_fd*/,
                                        struct libevdev_uinput **uinput_dev) -> int {
  if (uinput_stub.create_result != 0) {
    return uinput_stub.create_result;
  }
  *uinput_dev = reinterpret_cast<struct libevdev_uinput *>(&uinput_dummy);
  return 0;
}

auto libevdev_uinput_write_event(const struct libevdev_uinput * /*uinput_dev*/,
                                 unsigned int type, unsigned int code,
                                 int value) -> int {
  uinput_stub.events.push_back(
      {static_cast<int>(type), static_cast<int>(code), value});
  return uinput_stub.writes++ == uinput_stub.fail_write_at ? -EIO : 0;
}

void libevdev_uinput_destroy(struct libevdev_uinput * /*uinput_dev*/) {
  uinput_stub.destroyed++;
}
}

namespace {

using Messages = std::vector<std::pair<int, std::string>>;

auto recorder(Messages &messages) -> std::function<int(int, const char *)> {
  return [&messages](int style, const char *text) -> int {
    messages.emplace_back(style, text);
    return PAM_SUCCESS;
  };
}

auto write_file(const std::string &path, const std::string &text) -> void {
  std::ofstream out(path);
  out << text;
}

auto ini(const std::string &dir, const std::string &name,
         const std::string &text) -> std::string {
  std::string path = dir + "/" + name + ".ini";
  write_file(path, text);
  return path;
}

void test_main_hh() {
  check(get_workaround("input") == Workaround::Input, "workaround input");
  check(get_workaround("native") == Workaround::Native, "workaround native");
  check(get_workaround("off") == Workaround::Off, "workaround off");
  check(get_workaround("bogus") == Workaround::Off, "unknown workaround is off");

  check(is_safe_username("alice"), "plain username");
  check(is_safe_username("Bob.smith-2_x"), "username with . - _");
  check(is_safe_username("HOST$"), "machine account: trailing $");
  check(!is_safe_username("a$b"), "$ only allowed last");
  check(!is_safe_username(""), "empty username");
  check(!is_safe_username(nullptr), "null username");
  check(!is_safe_username("../root"), "path in username");
  check(!is_safe_username("bob smith"), "space in username");

  unsetenv("UH_TEST_UNSET_VARIABLE");
  check(!checkenv("UH_TEST_UNSET_VARIABLE"), "checkenv: unset variable");
  setenv("UH_TEST_SET_VARIABLE", "1", 1);
  check(checkenv("UH_TEST_SET_VARIABLE"), "checkenv: set variable");
  unsetenv("UH_TEST_SET_VARIABLE");
}

void test_is_service_ignored() {
  check(is_service_ignored("sudo, polkit-1 ,gdm-password", "sudo"), "first entry");
  check(is_service_ignored("sudo, polkit-1 ,gdm-password", "polkit-1"), "trimmed middle entry");
  check(is_service_ignored("sudo, polkit-1 ,gdm-password", "gdm-password"), "last entry");
  check(!is_service_ignored("sudo, polkit-1 ,gdm-password", "su"), "not listed");
  check(!is_service_ignored("sudo-extra", "sudo"), "no prefix match");
  check(!is_service_ignored("", "sudo"), "empty list");
  check(!is_service_ignored(" , ,", "sudo"), "blank entries");
  check(is_service_ignored("  login  ", "login"), "single entry with spaces");
}

void test_ubuntu_hello_error() {
  const std::vector<std::pair<int, bool>> codes = {
      {10, false}, // no face model: syslog only
      {11, true},  // timeout: user-visible
      {12, false}, // abort
      {13, true},  // too dark: user-visible
      {14, false}, // invalid device
      {99, true},  // unknown: user-visible with the code
  };
  for (const auto &[code, visible] : codes) {
    Messages messages;
    check(ubuntu_hello_error(W_EXITCODE(code, 0), recorder(messages)) == PAM_AUTH_ERR,
          "error status maps to PAM_AUTH_ERR");
    check(messages.size() == (visible ? 1U : 0U), "error message visibility");
    if (visible) {
      check(messages[0].first == PAM_ERROR_MSG, "error message style");
    }
  }
  Messages unknown;
  ubuntu_hello_error(W_EXITCODE(99, 0), recorder(unknown));
  check(unknown[0].second.find("99") != std::string::npos, "unknown error names its code");

  Messages killed;
  check(ubuntu_hello_error(SIGKILL, recorder(killed)) == PAM_AUTH_ERR, "signal maps to PAM_AUTH_ERR");
  check(killed.empty(), "signal is logged, not shown");
}

void test_ubuntu_hello_status(const std::string &dir) {
  std::array<char, 6> user_buf{"alice"};
  char *user = user_buf.data();
  Messages quiet;
  const INIReader defaults(ini(dir, "defaults", "[core]\n"));
  check(ubuntu_hello_status(user, EXIT_SUCCESS, defaults, recorder(quiet)) == PAM_SUCCESS,
        "success status");
  check(quiet.empty(), "no_confirmation defaults to true: no message");

  Messages confirm;
  const INIReader verbose(ini(dir, "verbose", "[core]\nno_confirmation = false\n"));
  check(ubuntu_hello_status(user, EXIT_SUCCESS, verbose, recorder(confirm)) == PAM_SUCCESS,
        "success status with confirmation");
  check(confirm.size() == 1 && confirm[0].first == PAM_TEXT_INFO &&
            confirm[0].second.find("alice") != std::string::npos,
        "confirmation names the user");

  Messages failed;
  check(ubuntu_hello_status(user, W_EXITCODE(11, 0), verbose, recorder(failed)) == PAM_AUTH_ERR,
        "failure status delegates to ubuntu_hello_error");
  check(failed.size() == 1 && failed[0].first == PAM_ERROR_MSG, "failure message");
}

void test_read_first_line_and_popen() {
  std::string text = "secret\nsecond line";
  FILE *file = fmemopen(text.data(), text.size(), "r");
  check(read_first_line(file) == "secret", "first line without newline");
  fclose(file);

  std::string single = "no-newline";
  file = fmemopen(single.data(), single.size(), "r");
  check(read_first_line(file) == "no-newline", "line without newline");
  fclose(file);

  FILE *empty = fopen("/dev/null", "r");
  check(read_first_line(empty).empty(), "empty input");
  fclose(empty);

  std::string output;
  check(run_root_helper({"/bin/echo", "from-helper", nullptr}, nullptr, &output) == 0,
        "run_root_helper exit status");
  check(output == "from-helper", "run_root_helper output");

  output.clear();
  check(run_root_helper({"/bin/head", "-n1", nullptr}, "piped-in", &output) == 0,
        "run_root_helper with stdin");
  check(output == "piped-in", "run_root_helper stdin reaches the child");

  // The caller's environment must never reach a root helper.
  setenv("LD_PRELOAD", "/nonexistent/evil.so", 1);
  setenv("PYTHONPATH", "/tmp/evil", 1);
  output.clear();
  run_root_helper({"/bin/sh", "-c", "echo \"[$LD_PRELOAD][$PYTHONPATH]\"", nullptr},
                  nullptr, &output);
  check(output == "[][]", "run_root_helper scrubs LD_PRELOAD / PYTHONPATH");
  unsetenv("LD_PRELOAD");
  unsetenv("PYTHONPATH");

  check(run_root_helper({"/nonexistent/helper", nullptr}, nullptr, nullptr) != 0,
        "run_root_helper reports a missing binary");
  // glibc reports the failed exec from posix_spawn (-1 here); under qemu-user
  // (the arm64 release build) the child starts and exits 127 instead. Either
  // way the helper must fail and yield no output.
  output.clear();
  check(run_root_helper({"/nonexistent/helper", nullptr}, "input", &output) != 0,
        "run_root_helper missing binary with pipes");
  check(output.empty(), "no output from a missing binary");
}

void write_script(const std::string &path, const std::string &body) {
  write_file(path, "#!/bin/sh\n" + body);
  chmod(path.c_str(), 0700);
}

void test_unseal_and_cache_keyring(const std::string &dir) {
  // Fake tpm2_* tools: each records its argv and creates its -c context file,
  // so the test sees the exact chain and that the contexts are cleaned up.
  const std::string tools = dir + "/tpm-tools";
  mkdir(tools.c_str(), 0700);
  const std::string calls = dir + "/tpm-calls";
  unlink(calls.c_str());
  write_script(tools + "/tpm2_createprimary",
               "echo \"createprimary $*\" >> " + calls + "\ntouch \"$4\"\n");
  write_script(tools + "/tpm2_load",
               "echo \"load $*\" >> " + calls + "\ntouch \"$8\"\n");
  write_script(tools + "/tpm2_unseal", "echo sealed-secret\necho second-line\n");
  const std::string p_ctx = dir + "/p.ctx";
  const std::string s_ctx = dir + "/s.ctx";

  check(unseal_tpm_password(tools, p_ctx, s_ctx, "pub", "priv") == "sealed-secret",
        "TPM unseal returns the first line");
  std::ifstream log(calls);
  std::string line;
  std::getline(log, line);
  check(line == "createprimary -C o -c " + p_ctx, "createprimary argv");
  std::getline(log, line);
  check(line == "load -C " + p_ctx + " -u pub -r priv -c " + s_ctx, "load argv");
  struct stat ctx_stat{};
  check(stat(p_ctx.c_str(), &ctx_stat) != 0 && stat(s_ctx.c_str(), &ctx_stat) != 0,
        "TPM context files removed");

  // A failing step stops the chain and yields no password.
  write_script(tools + "/tpm2_load", "exit 1\n");
  check(unseal_tpm_password(tools, p_ctx, s_ctx, "pub", "priv").empty(),
        "TPM unseal fails when tpm2_load fails");
  check(stat(p_ctx.c_str(), &ctx_stat) != 0, "context removed after failure");
  write_script(tools + "/tpm2_createprimary", "exit 1\n");
  check(unseal_tpm_password(tools, p_ctx, s_ctx, "pub", "priv").empty(),
        "TPM unseal fails when tpm2_createprimary fails");

  // Fake cli.py: exits 0 only for the expected argv and the password on stdin.
  const std::string cli = dir + "/fake-cli.py";
  write_file(cli,
             "import sys\n"
             "ok = sys.argv[1:] == ['keyring', 'enable', '-U', 'alice']\n"
             "ok = ok and sys.stdin.readline() == 's3cret\\n'\n"
             "sys.exit(0 if ok else 3)\n");
  check(cache_keyring_password(PYTHON_EXECUTABLE_PATH, cli.c_str(), "alice", "s3cret") == 0,
        "keyring enable gets argv and password");
  check(cache_keyring_password(PYTHON_EXECUTABLE_PATH, cli.c_str(), "bob", "s3cret") != 0,
        "keyring enable failure is reported");

  // try_set_keyring_authtok against a fake /etc/ubuntu-hello tree.
  const std::string etc = dir + "/etc-uh";
  mkdir(etc.c_str(), 0700);
  mkdir((etc + "/tpm-keys").c_str(), 0700);
  mkdir((etc + "/keyring-keys").c_str(), 0700);
  auto *pamh = static_cast<pam_handle_t *>(nullptr);

  // Software fallback: missing, empty and undecryptable blobs set nothing.
  pam_stub.set_item_calls = 0;
  try_set_keyring_authtok(pamh, "carol", etc, tools);
  write_file(etc + "/keyring-keys/carol", "");
  try_set_keyring_authtok(pamh, "carol", etc, tools);
  write_file(etc + "/keyring-keys/carol", "UH1:not-valid\n");
  try_set_keyring_authtok(pamh, "carol", etc, tools);
  check(pam_stub.set_item_calls == 0, "unusable software blob: PAM_AUTHTOK not set");

  // TPM path: a failed unseal sets nothing, a good one sets PAM_AUTHTOK.
  write_file(etc + "/tpm-keys/carol.pub", "pub");
  write_file(etc + "/tpm-keys/carol.priv", "priv");
  try_set_keyring_authtok(pamh, "carol", etc, tools);
  check(pam_stub.set_item_calls == 0, "failed TPM unseal: PAM_AUTHTOK not set");
  write_script(tools + "/tpm2_createprimary", "touch \"$4\"\n");
  write_script(tools + "/tpm2_load", "touch \"$8\"\n");
  try_set_keyring_authtok(pamh, "carol", etc, tools);
  check(pam_stub.set_item_calls == 1, "TPM unseal sets PAM_AUTHTOK");
  pam_stub.set_item_calls = 0;

  // report_keyring_cache: only a successful seal clears the pending marker.
  const std::string pending = dir + "/carol.pending";
  write_file(pending, "");
  report_keyring_cache(256, true, pending, "carol");
  report_keyring_cache(-1, true, pending, "carol");
  struct stat pending_stat{};
  check(stat(pending.c_str(), &pending_stat) == 0, "failed seal keeps pending marker");
  report_keyring_cache(0, false, pending, "carol");
  check(stat(pending.c_str(), &pending_stat) == 0, "refresh keeps pending marker");
  report_keyring_cache(0, true, pending, "carol");
  check(stat(pending.c_str(), &pending_stat) != 0, "first seal removes pending marker");
}

void test_any_lid_closed(const std::string &dir) {
  const std::string lids = dir + "/lid";
  mkdir(lids.c_str(), 0700);
  unlink((lids + "/LID1-state").c_str());  // left over from an earlier run
  write_file(lids + "/LID0-state", "state:      open\n");
  glob_t open_only;
  glob((lids + "/*-state").c_str(), 0, nullptr, &open_only);
  check(!any_lid_closed(open_only), "open lid");
  globfree(&open_only);

  write_file(lids + "/LID1-state", "state:      closed\n");
  glob_t with_closed;
  glob((lids + "/*-state").c_str(), 0, nullptr, &with_closed);
  check(any_lid_closed(with_closed), "one closed lid");
  globfree(&with_closed);
}

void test_helper_argv_and_env() {
  check(is_plain_token("abc123", ""), "alphanumeric token");
  check(!is_plain_token("a-b", ""), "dash needs to be allowed");
  check(is_plain_token("a-b", "-"), "allowed extra byte");

  check(helper_service_arg(nullptr).empty(), "no service");
  check(helper_service_arg("sudo") == "sudo", "plain service");
  check(helper_service_arg("gdm-password") == "gdm-password", "dashed service");
  check(helper_service_arg("polkit-1.x_y") == "polkit-1.x_y", "service with . _");
  check(helper_service_arg("sudo;rm -rf /").empty(), "shell metacharacters rejected");
  check(helper_service_arg("a b").empty(), "space rejected");

  setenv("LANG", "ro_RO.UTF-8", 1);
  setenv("LANGUAGE", "ro:en", 1);
  setenv("LC_ALL", "$(touch /tmp/x)", 1);
  setenv("LC_MESSAGES", std::string(65, 'a').c_str(), 1);
  setenv("LC_CTYPE", "", 1);
  setenv("LD_PRELOAD", "/tmp/evil.so", 1);
  const auto env = helper_environment();
  check(env.size() == 3, "PATH + two valid locale variables");
  check(env[0] == "PATH=/usr/sbin:/usr/bin:/sbin:/bin", "fixed PATH first");
  check(env[1] == "LANG=ro_RO.UTF-8", "LANG passed");
  check(env[2] == "LANGUAGE=ro:en", "LANGUAGE passed");
  for (const auto &entry : env) {
    check(entry.rfind("LD_PRELOAD", 0) != 0, "caller environment never inherited");
  }
  for (const char *key : {"LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LC_CTYPE", "LD_PRELOAD"}) {
    unsetenv(key);
  }
}

void test_check_enabled(const std::string &dir) {
  for (const char *key : {"SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY", "SSHD_OPTS"}) {
    unsetenv(key);
  }
  const INIReader disabled(ini(dir, "disabled", "[core]\ndisabled = true\n"));
  check(check_enabled(disabled, "alice") == PAM_AUTHINFO_UNAVAIL, "disabled in config");

  const INIReader ssh_abort(ini(dir, "ssh", "[core]\nabort_if_lid_closed = false\n"));
  setenv("SSH_CONNECTION", "10.0.0.1 22 10.0.0.2 22", 1);
  check(check_enabled(ssh_abort, "alice") == PAM_AUTHINFO_UNAVAIL, "SSH session skipped");
  unsetenv("SSH_CONNECTION");

  // Past the SSH and lid gates the user still needs a face model on disk.
  const INIReader permissive(ini(dir, "permissive",
                                 "[core]\nabort_if_ssh = false\nabort_if_lid_closed = true\n"));
  check(check_enabled(permissive, "uh-test-user-without-model") == PAM_AUTHINFO_UNAVAIL,
        "no face model: unavailable");
}

void test_identify_preflight(const std::string &dir) {
  INIReader config(ini(dir, "preflight",
                       "[core]\nignore_services = sudo, su\nabort_if_ssh = false\n"
                       "abort_if_lid_closed = false\n"));
  char *username = nullptr;
  auto *pamh = static_cast<pam_handle_t *>(nullptr);

  check(identify_preflight(pamh, config, "sudo", &username) == PAM_AUTHINFO_UNAVAIL,
        "ignored service");

  pam_stub.get_user_result = PAM_CONV_ERR;
  check(identify_preflight(pamh, config, "login", &username) == PAM_CONV_ERR,
        "pam_get_user error passed through");
  pam_stub.get_user_result = PAM_SUCCESS;

  pam_stub.user = nullptr;
  check(identify_preflight(pamh, config, "login", &username) == PAM_USER_UNKNOWN,
        "missing username");

  pam_stub.user = "../../etc/shadow";
  check(identify_preflight(pamh, config, nullptr, &username) == PAM_AUTH_ERR,
        "unsafe username rejected");

  pam_stub.user = "uh-test-user-without-model";
  check(identify_preflight(pamh, config, "login", &username) == PAM_AUTHINFO_UNAVAIL,
        "check_enabled result returned");
  check(username == nullptr, "username only set on success");
  pam_stub.user = "alice";
}

auto spawn_sleeper(bool ignore_term) -> pid_t {
  std::array<int, 2> ready{};
  if (pipe(ready.data()) != 0) {
    return -1;
  }
  const pid_t pid = fork();
  if (pid == 0) {
    setpgid(0, 0);
    if (ignore_term) {
      signal(SIGTERM, SIG_IGN);
    }
    close(ready[0]);
    const char byte = 1;
    write(ready[1], &byte, 1);
    for (;;) {
      pause();
    }
  }
  close(ready[1]);
  char byte = 0;
  read(ready[0], &byte, 1);
  close(ready[0]);
  return pid;
}

void test_terminate_compare_group() {
  optional_task<int> unused([]() -> int { return 0; });
  terminate_compare_group(0, unused);  // no child: nothing to signal

  for (const bool ignore_term : {false, true}) {
    const pid_t pid = spawn_sleeper(ignore_term);
    check(pid > 0, "spawned a compare stand-in");
    optional_task<int> child([pid]() -> int {
      int status = 0;
      waitpid(pid, &status, 0);
      return status;
    });
    child.activate();
    terminate_compare_group(pid, child);
    const int status = child.get();
    check(WIFSIGNALED(status), "compare group terminated");
    check(WTERMSIG(status) == (ignore_term ? SIGKILL : SIGTERM),
          ignore_term ? "SIGKILL after the SIGTERM grace period" : "SIGTERM is enough");
  }
}

void test_optional_task() {
  optional_task<int> task([]() -> int { return 42; });
  task.activate();
  check(task.wait(std::chrono::seconds(5)) == std::future_status::ready, "task finishes");
  task.stop(false);
  check(task.get() == 42, "task result");
  task.stop(false);  // already stopped: no-op

  optional_task<int> never([]() -> int { return 1; });
  never.stop(false);  // never activated: must not join a thread that does not exist
  never.stop(true);

  {
    optional_task<int> scoped([]() -> int { return 7; });
    scoped.activate();
  }  // destructor joins an active task
}

void test_face_skip_and_dismiss() {
  // A killed compare leaves the marker alone; success / plain failure with
  // skip disabled must not create one either.
  update_face_skip_from_status("uh-test-user", SIGTERM, true, true);
  update_face_skip_from_status("uh-test-user", W_EXITCODE(EXIT_SUCCESS, 0), true, true);
  update_face_skip_from_status("uh-test-user", W_EXITCODE(11, 0), false, true);
  update_face_skip_from_status("uh-test-user", W_EXITCODE(11, 0), true, false);
  check(!face_skip_active("uh-test-user"), "no face-skip marker created");

  Messages messages;
  optional_task<std::tuple<int, char *>> idle([]() -> std::tuple<int, char *> {
    return {PAM_SUCCESS, nullptr};
  });
  dismiss_authtok_prompt(Workaround::Off, idle, recorder(messages));
  // Native with a prompt that was never started (pam_sm_open_session path).
  dismiss_authtok_prompt(Workaround::Native, idle, recorder(messages));
  check(messages.empty(), "off / native: no message");

  optional_task<std::tuple<int, char *>> prompt([]() -> std::tuple<int, char *> {
    return {PAM_SUCCESS, nullptr};
  });
  prompt.activate();
  dismiss_authtok_prompt(Workaround::Input, prompt, recorder(messages));
  check(std::get<0>(prompt.get()) == PAM_SUCCESS, "input workaround stops the prompt task");
}

void test_pam_entry_points() {
  auto *pamh = static_cast<pam_handle_t *>(nullptr);
  check(pam_sm_acct_mgmt(pamh, 0, 0, nullptr) == PAM_IGNORE, "acct_mgmt ignored");
  check(pam_sm_close_session(pamh, 0, 0, nullptr) == PAM_IGNORE, "close_session ignored");
  check(pam_sm_chauthtok(pamh, 0, 0, nullptr) == PAM_IGNORE, "chauthtok ignored");

  pam_stub.get_user_result = PAM_CONV_ERR;
  check(pam_sm_setcred(pamh, 0, 0, nullptr) == PAM_IGNORE, "setcred without user");
  pam_stub.get_user_result = PAM_SUCCESS;
  pam_stub.user = "bad/user";
  check(pam_sm_setcred(pamh, 0, 0, nullptr) == PAM_IGNORE, "setcred with unsafe user");
  pam_stub.user = "uh-test-user-without-keyring";
  check(pam_sm_setcred(pamh, 0, 0, nullptr) == PAM_IGNORE, "setcred without keyring files");
  pam_stub.user = "alice";

  // No keyring key / TPM blob for this user: PAM_AUTHTOK is left untouched.
  try_set_keyring_authtok(pamh, "uh-test-user-without-keyring");
  check(pam_stub.set_item_calls == 0, "no keyring secret: PAM_AUTHTOK not set");

  const SyslogIdentityScope scope;

  // identify() refuses to run without its config file; only checkable where
  // the build's config path does not exist (CI containers, dev checkouts).
  struct stat config_stat{};
  if (stat(CONFIG_FILE_PATH, &config_stat) != 0) {
    check(pam_sm_authenticate(pamh, 0, 0, nullptr) == PAM_SYSTEM_ERR, "authenticate without config");
    check(pam_sm_open_session(pamh, 0, 0, nullptr) == PAM_SYSTEM_ERR, "open_session without config");
  }
}

void test_enter_device() {
  uinput_stub.create_result = -EACCES;
  bool thrown = false;
  try {
    const EnterDevice refused;
  } catch (const EnterDeviceError &error) {
    thrown = std::string(error.what()).find("create device") != std::string::npos;
  }
  check(thrown, "uinput refused: EnterDeviceError");
  uinput_stub.create_result = 0;

  {
    const EnterDevice device;
    device.send_enter_press();
    const std::vector<std::array<int, 3>> expected = {
        {EV_KEY, KEY_ENTER, 1}, {EV_KEY, KEY_ENTER, 0}, {EV_SYN, SYN_REPORT, 0}};
    check(uinput_stub.events == expected, "Enter down, Enter up, then SYN_REPORT");
  }
  check(uinput_stub.destroyed == 1, "uinput device destroyed with EnterDevice");

  for (const int failing : {0, 1, 2}) {
    uinput_stub.writes = 0;
    uinput_stub.fail_write_at = failing;
    const EnterDevice device;
    bool write_failed = false;
    try {
      device.send_enter_press();
    } catch (const EnterDeviceError &error) {
      write_failed = std::string(error.what()).find("write event") != std::string::npos;
    }
    check(write_failed && uinput_stub.writes == failing + 1, "failed write stops the press");
  }
  uinput_stub.fail_write_at = -1;
}

} // namespace

auto main(int argc, char **argv) -> int {
  if (argc < 2) {
    std::cerr << "usage: pam_main_test <tmpdir>\n";
    return 1;
  }
  const std::string dir = std::string(argv[1]) + "/pam-main-test";
  mkdir(dir.c_str(), 0700);

  test_main_hh();
  test_is_service_ignored();
  test_ubuntu_hello_error();
  test_ubuntu_hello_status(dir);
  test_read_first_line_and_popen();
  test_any_lid_closed(dir);
  test_unseal_and_cache_keyring(dir);
  test_helper_argv_and_env();
  test_check_enabled(dir);
  test_identify_preflight(dir);
  test_terminate_compare_group();
  test_optional_task();
  test_face_skip_and_dismiss();
  test_pam_entry_points();
  test_enter_device();

  if (failures != 0) {
    std::cerr << failures << " check(s) failed\n";
    return 1;
  }
  std::cout << "pam_main_test: all checks passed\n";
  return 0;
}

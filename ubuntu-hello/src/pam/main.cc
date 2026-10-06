#include <cctype>
#include <cerrno>
#include <csignal>
#include <cstdlib>
#include <cstdio>
#include <fcntl.h>

#include <glob.h>
#include <libintl.h>
#include <pthread.h>
#include <spawn.h>
#include <stdexcept>
#include <sys/signalfd.h>
#include <sys/stat.h>
#include <sys/syslog.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <syslog.h>
#include <unistd.h>

#include <array>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <fstream>
#include <functional>
#include <future>
#include <mutex>
#include <string>
#include <tuple>
#include <vector>

#include <INIReader.h>

#include <security/pam_appl.h>
#include <security/pam_ext.h>
#include <security/pam_modules.h>

#include "aes_gcm_uh1.hh"
#include "enter_device.hh"
#include "face_skip.hh"
#include "main.hh"
#include "optional_task.hh"
#include <paths.hh>

const auto CHILD_TERM_TIMEOUT =
    std::chrono::duration<int, std::chrono::milliseconds::period>(500);

const auto DEFAULT_TIMEOUT =
    std::chrono::duration<int, std::chrono::milliseconds::period>(100);
const auto MAX_RETRIES = 5;

#define S(msg) gettext(msg)

/**
 * Helper to check if a PAM service is in the comma-separated ignore list
 * @param  service_list  Comma-separated list of ignored services
 * @param  service       The current PAM service name
 * @return               True if the service is in the list
 */
auto is_service_ignored(std::string_view service_list, std::string_view service) -> bool {
  std::string list{service_list};
  size_t pos = 0;
  while ((pos = list.find(',')) != std::string::npos) {
    std::string token = list.substr(0, pos);
    // Trim whitespace
    if (const size_t first = token.find_first_not_of(" \t\r\n");
        first != std::string::npos) {
      size_t last = token.find_last_not_of(" \t\r\n");
      token = token.substr(first, (last - first + 1));
    } else {
      token = "";
    }
    if (token == service) {
      return true;
    }
    list.erase(0, pos + 1);
  }
  // Trim remaining part
  if (const size_t first = list.find_first_not_of(" \t\r\n");
      first != std::string::npos) {
    size_t last = list.find_last_not_of(" \t\r\n");
    list = list.substr(first, (last - first + 1));
  } else {
    list = "";
  }
  return list == service;
}

/**
 * Inspect the status code returned by the compare process
 * @param  status        The status code
 * @param  conv_function The PAM conversation function
 * @return               A PAM return code
 */
auto ubuntu_hello_error(int status,
                 const std::function<int(int, const char *)> &conv_function)
    -> int {
  // If the process has exited
  if (WIFEXITED(status)) {
    // Get the status code returned
    status = WEXITSTATUS(status);

    switch (static_cast<CompareError>(status)) {
    case CompareError::NO_FACE_MODEL:
      syslog(LOG_NOTICE, "Failure, no face model known");
      break;
    case CompareError::TIMEOUT_REACHED:
      conv_function(PAM_ERROR_MSG, S("Failure, timeout reached"));
      syslog(LOG_ERR, "Failure, timeout reached");
      break;
    case CompareError::ABORT:
      syslog(LOG_ERR, "Failure, general abort");
      break;
    case CompareError::TOO_DARK:
      conv_function(PAM_ERROR_MSG, S("Face detection image too dark"));
      syslog(LOG_ERR, "Failure, image too dark");
      break;
    case CompareError::INVALID_DEVICE:
      syslog(LOG_ERR,
             "Failure, not possible to open camera at configured path");
      break;
    default:
      // std::string first: S() is a char *, so "S(...) + status" would be
      // pointer arithmetic (reading past the message), not concatenation.
      conv_function(PAM_ERROR_MSG,
                    (std::string(S("Unknown error: ")) + std::to_string(status)).c_str());
      syslog(LOG_ERR, "Failure, unknown error %d", status);
    }
  } else if (WIFSIGNALED(status)) {
    // We get the signal
    status = WTERMSIG(status);

    syslog(LOG_ERR, "Child killed by signal %s (%d)", strsignal(status),
           status);
  }

  // As this function is only called for error status codes, signal an error to
  // PAM
  return PAM_AUTH_ERR;
}

/**
 * Format the success message if the status is successful or log the error in
 * the other case
 * @param  username      Username
 * @param  status        Status code
 * @param  config        INI  configuration
 * @param  conv_function PAM conversation function
 * @return          Returns the conversation function return code
 */
auto ubuntu_hello_status(char *username, int status, const INIReader &config,
                  const std::function<int(int, const char *)> &conv_function)
    -> int {
  if (status != EXIT_SUCCESS) {
    return ubuntu_hello_error(status, conv_function);
  }

  if (!config.GetBoolean("core", "no_confirmation", true)) {
    // Printf-style %s so translators can reorder (gettext best practice).
    std::array<char, 512> identify_buf{};
    std::snprintf(identify_buf.data(), identify_buf.size(),
                  S("Identified face as %s"), username);
    conv_function(PAM_TEXT_INFO, identify_buf.data());
  }

  syslog(LOG_INFO, "Login approved");

  return PAM_SUCCESS;
}

/**
 * Try to set PAM_AUTHTOK from a stored keyring key file after successful
 * face authentication, so that downstream PAM modules can unlock the
 * session wallet automatically. Typical consumers of the same token:
 *   - pam_gnome_keyring (GNOME / Ubuntu-family DEs)
 *   - pam_kwallet5 (KDE Plasma / KWallet)
 * No separate sealed-blob format is used for KWallet; both unlock paths
 * reuse this PAM_AUTHTOK after face auth succeeds.
 * @param pamh      PAM handle
 * @param username  Authenticated username
 */
// Read the first line from *file_pipe* (the unsealed password), without the
// trailing newline. Prefer fread over fgets so clang-analyzer does not flag a
// blocking fgets while identify()'s mutex is still (falsely) considered held.
// True if any matched lid state file under /proc/acpi/button/lid reports
// "closed".
auto any_lid_closed(const glob_t &glob_result) -> bool {
  for (size_t i = 0; i < glob_result.gl_pathc; i++) {
    std::ifstream file(std::string(glob_result.gl_pathv[i]));
    std::string lid_state;
    std::getline(file, lid_state, static_cast<char>(file.eof()));
    if (lid_state.find("closed") != std::string::npos) {
      return true;
    }
  }
  return false;
}

auto read_first_line(FILE *file_pipe) -> std::string {
  std::array<char, 256> buf{};
  const size_t got = fread(buf.data(), 1, buf.size() - 1, file_pipe);
  if (got == 0) {
    return "";
  }
  buf[got] = '\0';
  std::string line = buf.data();
  if (const size_t newline_at = line.find('\n');
      newline_at != std::string::npos) {
    line.erase(newline_at);
  }
  return line;
}

/**
 * True when every byte of *text* is alphanumeric or one of *extra*.
 * Used to sanitise the few strings the root helper receives from PAM's
 * environment / service name before they enter its argv or env.
 */
auto is_plain_token(const char *text, const char *extra) -> bool {
  for (const char *cursor = text; *cursor != '\0'; ++cursor) {
    const auto byte = static_cast<unsigned char>(*cursor);
    if (isalnum(byte) == 0 && strchr(extra, *cursor) == nullptr) {
      return false;
    }
  }
  return true;
}

/**
 * The PAM service name for compare.py's argv (sudo, polkit-1, gdm-password,
 * ...), or "" when missing or containing anything but [A-Za-z0-9._-].
 */
auto helper_service_arg(const char *service) -> std::string {
  if (service == nullptr || !is_plain_token(service, "-_.")) {
    return "";
  }
  return service;
}

/**
 * Environment for the root helper: never the caller's, only a fixed system
 * PATH plus the locale variables (after a strict character check) so the
 * overlay and the desktop notification speak the session's language instead
 * of C/English. None of these can redirect a lookup or influence code paths.
 */
auto helper_environment() -> std::vector<std::string> {
  std::vector<std::string> env;
  env.emplace_back("PATH=/usr/sbin:/usr/bin:/sbin:/bin");
  for (const char *key : {"LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LC_CTYPE"}) {
    const char *value = getenv(key);
    if (value == nullptr || *value == '\0' || strlen(value) > 64 ||
        !is_plain_token(value, "_.@:-")) {
      continue;
    }
    env.emplace_back(std::string(key) + "=" + value);
  }
  return env;
}

// Close *pipe_fd* if open and mark it closed.
void close_fd(int &pipe_fd) {
  if (pipe_fd >= 0) {
    close(pipe_fd);
    pipe_fd = -1;
  }
}

// The helper's stdin/stdout pipes; -1 marks an end that is not in use.
struct HelperPipes {
  std::array<int, 2> stdin_pipe{-1, -1};
  std::array<int, 2> stdout_pipe{-1, -1};

  void close_all() {
    for (auto *pipe_pair : {&stdin_pipe, &stdout_pipe}) {
      for (int &pipe_fd : *pipe_pair) {
        close_fd(pipe_fd);
      }
    }
  }
};

// Create the pipes the caller asked for. On failure every pipe is closed.
auto open_helper_pipes(bool want_in, bool want_out, HelperPipes &pipes) -> bool {
  if ((want_in && pipe2(pipes.stdin_pipe.data(), O_CLOEXEC) != 0) ||
      (want_out && pipe2(pipes.stdout_pipe.data(), O_CLOEXEC) != 0)) {
    syslog(LOG_ERR, "Failed to create helper pipe: %s (%d)", strerror(errno), errno);
    pipes.close_all();
    return false;
  }
  return true;
}

// stdin/stdout come from the pipes when open, otherwise /dev/null; stderr is
// always /dev/null.
void add_helper_stdio(posix_spawn_file_actions_t &actions, const HelperPipes &pipes) {
  if (pipes.stdin_pipe[0] >= 0) {
    posix_spawn_file_actions_adddup2(&actions, pipes.stdin_pipe[0], 0);
  } else {
    posix_spawn_file_actions_addopen(&actions, 0, "/dev/null", O_RDONLY, 0);
  }
  if (pipes.stdout_pipe[1] >= 0) {
    posix_spawn_file_actions_adddup2(&actions, pipes.stdout_pipe[1], 1);
  } else {
    posix_spawn_file_actions_addopen(&actions, 1, "/dev/null", O_WRONLY, 0);
  }
  posix_spawn_file_actions_addopen(&actions, 2, "/dev/null", O_WRONLY, 0);
}

/**
 * posix_spawn *argv* with the real uid raised to 0 for the spawn only, so
 * helpers that check the real uid (the CLI) accept us; safe because nothing
 * from the caller's environment is passed on.
 * @param restore_failed Set when the original uids could not be restored
 * @return The posix_spawn error (0 on success)
 */
auto spawn_with_root_uid(const std::vector<const char *> &argv,
                         const posix_spawn_file_actions_t &actions,
                         const std::vector<char *> &envp, pid_t &child_pid,
                         bool &restore_failed) -> int {
  uid_t ruid = getuid();
  uid_t euid = geteuid();
  bool altered = false;
  if (euid == 0 && ruid != 0) {
    altered = setreuid(0, 0) == 0;
    if (!altered) {
      syslog(LOG_ERR, "Failed to setreuid(0, 0): %s (%d)", strerror(errno), errno);
    }
  }

  int spawn_err = posix_spawn(&child_pid, argv[0], &actions, nullptr,
                              const_cast<char *const *>(argv.data()),
                              envp.data());

  restore_failed = altered && setreuid(ruid, euid) != 0;
  if (restore_failed) {
    syslog(LOG_ERR, "Failed to restore UIDs to %d/%d: %s (%d)", ruid, euid, strerror(errno), errno);
  }
  return spawn_err;
}

// Write *input* plus a newline to the helper's stdin, then close it.
void feed_helper(int &in_fd, const char *input) {
  FILE *in_file = fdopen(in_fd, "w");
  if (in_file == nullptr) {
    close_fd(in_fd);
    return;
  }
  in_fd = -1;
  fputs(input, in_file);
  fputc('\n', in_file);
  fclose(in_file);
}

// Read the first line of the helper's stdout into *output*, then close it.
void read_helper(int &out_fd, std::string &output) {
  FILE *out_file = fdopen(out_fd, "r");
  if (out_file == nullptr) {
    close_fd(out_fd);
    return;
  }
  out_fd = -1;
  output = read_first_line(out_file);
  fclose(out_file);
}

// waitpid, retried on EINTR. Returns the waitpid result.
auto wait_for_helper(pid_t child_pid, int &status) -> pid_t {
  pid_t waited = -1;
  do {
    waited = waitpid(child_pid, &status, 0);
  } while (waited < 0 && errno == EINTR);
  return waited;
}

/**
 * Run a root helper without a shell and without the caller's environment.
 * PAM modules run inside setuid binaries (su, sudo, ...) whose environ is
 * attacker-controlled, and after setreuid(0, 0) the child is a plain uid-0
 * process, so PATH / PYTHONPATH / BASH_ENV would be honoured. Only *argv* (an
 * absolute path in argv[0]) and helper_environment() reach the child.
 * @param argv      Null-terminated argument vector; argv[0] is executed
 * @param input     Written to the child's stdin (nullptr: /dev/null)
 * @param output    When non-null, receives the first line of stdout
 * @return          The waitpid status, or -1 if the helper could not run
 */
auto run_root_helper(const std::vector<const char *> &argv, const char *input,
                     std::string *output) -> int {
  std::vector<std::string> env_storage = helper_environment();
  std::vector<char *> envp;
  envp.reserve(env_storage.size() + 1);
  for (auto &entry : env_storage) {
    envp.push_back(entry.data());
  }
  envp.push_back(nullptr);

  HelperPipes pipes;
  if (!open_helper_pipes(input != nullptr, output != nullptr, pipes)) {
    return -1;
  }

  posix_spawn_file_actions_t actions;
  posix_spawn_file_actions_init(&actions);
  add_helper_stdio(actions, pipes);

  pid_t child_pid = -1;
  bool restore_failed = false;
  int spawn_err = spawn_with_root_uid(argv, actions, envp, child_pid, restore_failed);
  posix_spawn_file_actions_destroy(&actions);

  // The child's ends belong to the child now.
  close_fd(pipes.stdin_pipe[0]);
  close_fd(pipes.stdout_pipe[1]);

  if (spawn_err != 0) {
    syslog(LOG_ERR, "Can't spawn %s: %s (%d)", argv[0], strerror(spawn_err), spawn_err);
    pipes.close_all();
    return -1;
  }

  if (input != nullptr) {
    feed_helper(pipes.stdin_pipe[1], input);
  }
  if (output != nullptr) {
    read_helper(pipes.stdout_pipe[0], *output);
  }

  int status = 0;
  pid_t waited = wait_for_helper(child_pid, status);
  return (restore_failed || waited < 0) ? -1 : status;
}

/**
 * Unseal the keyring password from the TPM with the tpm2_* tools in
 * *tool_dir*. Each step runs without a shell and with a scrubbed environment
 * (see run_root_helper); only tpm2_unseal's stdout is read. The transient
 * context files are always removed.
 * @return The unsealed password, or "" on any failure
 */
auto unseal_tpm_password(const std::string &tool_dir, const std::string &p_ctx,
                         const std::string &s_ctx, const std::string &tpm_pub,
                         const std::string &tpm_priv) -> std::string {
  const std::string createprimary = tool_dir + "/tpm2_createprimary";
  const std::string load = tool_dir + "/tpm2_load";
  const std::string unseal = tool_dir + "/tpm2_unseal";
  std::string password;
  int status = run_root_helper({createprimary.c_str(), "-C", "o", "-c",
                                p_ctx.c_str(), nullptr},
                               nullptr, nullptr);
  if (status == 0) {
    status = run_root_helper({load.c_str(), "-C", p_ctx.c_str(), "-u",
                              tpm_pub.c_str(), "-r", tpm_priv.c_str(), "-c",
                              s_ctx.c_str(), nullptr},
                             nullptr, nullptr);
  }
  if (status == 0) {
    run_root_helper({unseal.c_str(), "-c", s_ctx.c_str(), nullptr}, nullptr,
                    &password);
  }
  unlink(p_ctx.c_str());
  unlink(s_ctx.c_str());
  return password;
}

/**
 * Seal *password* for *username* via `cli.py keyring enable`, run with
 * *python* -E -s straight on *cli_script*: no /bin/sh wrapper, no caller
 * environment, no user site-packages (see run_root_helper).
 * @return The helper's waitpid status, or -1 if it could not run
 */
auto cache_keyring_password(const char *python, const char *cli_script,
                            const char *username, const char *password) -> int {
  return run_root_helper({python, "-E", "-s", cli_script, "keyring", "enable",
                          "-U", username, nullptr},
                         password, nullptr);
}

/**
 * Log the outcome of cache_keyring_password() and, on success, drop the
 * "pending" marker that asked for the first seal.
 */
void report_keyring_cache(int status, bool is_pending,
                          const std::string &pending_file, const char *username) {
  if (status == 0) {
    if (is_pending) {
      unlink(pending_file.c_str());
      syslog(LOG_INFO, "Automatically cached and sealed keyring password for user %s", username);
    } else {
      syslog(LOG_INFO, "Automatically updated keyring password cache for user %s", username);
    }
  } else if (status == -1) {
    syslog(LOG_ERR, "Failed to run keyring helper to cache password");
  } else {
    syslog(LOG_ERR, "Failed to cache/seal password: ubuntu-hello keyring command exited with status %d", status);
  }
}

void try_set_keyring_authtok(pam_handle_t *pamh, const char *username,
                             const std::string &etc_dir = "/etc/ubuntu-hello",
                             const std::string &tool_dir = "/usr/bin") {
  std::string tpm_pub = etc_dir + "/tpm-keys/" + std::string(username) + ".pub";
  std::string tpm_priv = etc_dir + "/tpm-keys/" + std::string(username) + ".priv";
  
  std::string password;
  struct stat pub_stat{};
  struct stat priv_stat{};
  
  if (stat(tpm_pub.c_str(), &pub_stat) == 0 && stat(tpm_priv.c_str(), &priv_stat) == 0) {
    // TPM keys exist, unseal password from TPM
    pid_t pid = getpid();
    std::string p_ctx = etc_dir + "/tpm-keys/p_" + std::to_string(pid) + ".ctx";
    std::string s_ctx = etc_dir + "/tpm-keys/s_" + std::to_string(pid) + ".ctx";
    
    password = unseal_tpm_password(tool_dir, p_ctx, s_ctx, tpm_pub, tpm_priv);

    if (password.empty()) {
      syslog(LOG_ERR, "Failed to unseal keyring password from TPM");
      return;
    }
  } else {
    // Software fallback: AES-256-GCM (UH1:) with root-only master key
    std::string key_file = etc_dir + "/keyring-keys/" + std::string(username);
    std::ifstream ifs(key_file);
    if (!ifs.is_open()) {
      return;
    }

    std::string ciphertext;
    std::getline(ifs, ciphertext);
    if (ciphertext.empty()) {
      return;
    }

    password = aes_gcm_decrypt_uh1(ciphertext);
    if (password.empty()) {
      return;
    }
  }

  int pam_err = pam_set_item(pamh, PAM_AUTHTOK, password.c_str());
  if (pam_err == PAM_SUCCESS) {
    syslog(LOG_INFO, "PAM_AUTHTOK set successfully for keyring unlocking");
  } else {
    syslog(LOG_ERR, "Failed to set PAM_AUTHTOK: %s", pam_strerror(pamh, pam_err));
  }
}

/**
 * Check if Ubuntu Hello should be enabled according to the configuration and the
 * environment.
 * @param  config INI configuration
 * @param  username Username
 * @return        Returns PAM_AUTHINFO_UNAVAIL if it shouldn't be enabled,
 * PAM_SUCCESS otherwise
 */
auto check_enabled(const INIReader &config, const char *username) -> int {
  // Stop executing if Ubuntu Hello has been disabled in the config
  if (config.GetBoolean("core", "disabled", false)) {
    syslog(LOG_INFO, "Skipped authentication, Ubuntu Hello is disabled");
    return PAM_AUTHINFO_UNAVAIL;
  }

  // Stop if we're in a remote shell and configured to exit
  if (config.GetBoolean("core", "abort_if_ssh", true)) {
    if (checkenv("SSH_CONNECTION") || checkenv("SSH_CLIENT") ||
        checkenv("SSH_TTY") || checkenv("SSHD_OPTS")) {
      syslog(LOG_INFO, "Skipped authentication, SSH session detected");
      return PAM_AUTHINFO_UNAVAIL;
    }
  }

  // Try to detect the laptop lid state and stop if it's closed
  if (config.GetBoolean("core", "abort_if_lid_closed", true)) {
    glob_t glob_result;

    // Get any files containing lid state
    int return_value =
        glob("/proc/acpi/button/lid/*/state", 0, nullptr, &glob_result);

    if (return_value != 0) {
      syslog(LOG_ERR, "Failed to read files from glob: %d", return_value);
      if (errno != 0) {
        syslog(LOG_ERR, "Underlying error: %s (%d)", strerror(errno), errno);
      }
    } else if (any_lid_closed(glob_result)) {
      globfree(&glob_result);

      syslog(LOG_INFO, "Skipped authentication, closed lid detected");
      return PAM_AUTHINFO_UNAVAIL;
    }
    globfree(&glob_result);
  }

  // pre-check if this user has face model file
  auto model_path = std::string(USER_MODELS_DIR) + "/" + username + ".dat";
  struct stat stat_;
  if (stat(model_path.c_str(), &stat_) != 0) {
    return PAM_AUTHINFO_UNAVAIL;
  }

  return PAM_SUCCESS;
}

/**
 * Terminate the compare process group (SIGTERM, then SIGKILL after grace).
 */
auto terminate_compare_group(pid_t child_pid, optional_task<int> &child_task)
    -> void {
  if (child_pid <= 0) {
    return;
  }
  kill(-child_pid, SIGTERM);
  if (child_task.wait(CHILD_TERM_TIMEOUT) == std::future_status::timeout) {
    syslog(LOG_WARNING,
           "Compare process group %d did not exit after SIGTERM, sending SIGKILL",
           child_pid);
    kill(-child_pid, SIGKILL);
  }
  child_task.stop(false);
}

/**
 * Update face-skip marker from compare exit status (screensaver PAM only).
 * Do not set skip on SIGTERM/cancel — Esc must allow a later face retry.
 */
auto update_face_skip_from_status(const char *username, int compare_status,
                                  bool skip_face_after_failure,
                                  bool face_skip_service) -> void {
  if (!WIFEXITED(compare_status)) {
    return;
  }
  if (WEXITSTATUS(compare_status) == EXIT_SUCCESS) {
    face_skip_clear(username);
    return;
  }
  if (skip_face_after_failure && face_skip_service &&
      face_skip_set(username)) {
    syslog(LOG_INFO, "Set face-skip marker after failed attempt for user %s",
           username);
  }
}

/**
 * Dismiss a concurrent password prompt after face auth finished (workaround).
 */
auto dismiss_authtok_prompt(
    Workaround workaround, optional_task<std::tuple<int, char *>> &pass_task,
    const std::function<int(int, const char *)> &conv_function) -> void {
  // UNSAFE: We cancel the thread using pthread, pam_get_authtok seems to be
  // a cancellation point
  if (workaround == Workaround::Native) {
    pass_task.stop(true);
    return;
  }
  if (workaround != Workaround::Input) {
    return;
  }

  // We check if we have the right permissions on /dev/uinput
  if (euidaccess("/dev/uinput", W_OK | R_OK) != 0) {
    syslog(LOG_WARNING, "Insufficient permissions to create the fake device");
    conv_function(PAM_ERROR_MSG,
                  S("Insufficient permissions to send Enter "
                    "press, waiting for user to press it instead"));
  } else {
    try {
      EnterDevice enter_device;
      int retries = 0;

      // We try to send it
      enter_device.send_enter_press();

      for (; retries < MAX_RETRIES &&
             pass_task.wait(DEFAULT_TIMEOUT) == std::future_status::timeout;
           retries++) {
        enter_device.send_enter_press();
      }

      if (retries == MAX_RETRIES) {
        syslog(LOG_WARNING,
               "Failed to send enter input before the retries limit");
        conv_function(PAM_ERROR_MSG, S("Failed to send Enter press, waiting "
                                       "for user to press it instead"));
      }
    } catch (const EnterDeviceError &err) {
      syslog(LOG_WARNING, "Failed to send enter input: %s", err.what());
      conv_function(PAM_ERROR_MSG, S("Failed to send Enter press, waiting "
                                     "for user to press it instead"));
    }
  }

  // We stop the thread (will block until the enter key is pressed if the
  // input wasn't focused or if the uinput device failed to send keypress)
  pass_task.stop(false);
}

/**
 * The main function, runs the identification and authentication
 * @param  pamh     The handle to interface directly with PAM
 * @param  flags    Flags passed on to us by PAM, XORed
 * @param  argc     Amount of rules in the PAM config (disregarded)
 * @param  argv     Options defined in the PAM config
 * @param  ask_auth_tok True if we should ask for a password too
 * @return          Returns a PAM return code
 */
// openlog() sets the syslog identity of the whole process, and a PAM module
// runs inside sudo, su, gdm or the polkit helper. Left open, every line the host
// logs afterwards ("pam_unix(sudo:session): session opened ...") is tagged
// pam_ubuntu_hello instead of sudo, which is what audit rules and log-based
// intrusion detection key on; a booted-OS test found sudo's own lines carrying
// this module's name. closelog() resets the identity (glibc falls back to the
// program name), so the tag lives exactly as long as this scope.
struct SyslogIdentityScope {
  SyslogIdentityScope() { openlog("pam_ubuntu_hello", 0, LOG_AUTHPRIV); }
  ~SyslogIdentityScope() { closelog(); }
  SyslogIdentityScope(const SyslogIdentityScope &) = delete;
  auto operator=(const SyslogIdentityScope &) -> SyslogIdentityScope & = delete;
};

// The cheap gates identify() runs before doing any work: an ignored PAM
// service, the username and its format, whether face auth is enabled for that
// user, and skip-after-failure. Returns PAM_SUCCESS to continue, otherwise the
// status identify() must return as-is. *username_out* is only set on success.
auto identify_preflight(pam_handle_t *pamh, INIReader &config,
                        const char *service, char **username_out) -> int {
  if (service != nullptr) {
    const std::string ignore_services =
        config.GetString("core", "ignore_services", "");
    if (is_service_ignored(ignore_services, service)) {
      syslog(LOG_INFO, "Skipped authentication, PAM service '%s' is ignored",
             service);
      return PAM_AUTHINFO_UNAVAIL;
    }
  }

  // Needed to match the correct face model.
  char *username = nullptr;
  int pam_res = pam_get_user(pamh, const_cast<const char **>(&username), nullptr);
  if (pam_res != PAM_SUCCESS || username == nullptr) {
    syslog(LOG_ERR, "Failed to get username");
    return pam_res == PAM_SUCCESS ? PAM_USER_UNKNOWN : pam_res;
  }

  if (!is_safe_username(username)) {
    syslog(LOG_ERR, "Invalid username format: %s", username);
    return PAM_AUTH_ERR;
  }

  pam_res = check_enabled(config, username);
  if (pam_res != PAM_SUCCESS) {
    return pam_res;
  }

  // Skip-after-failure only for legacy *screensaver* PAM services. GNOME lock
  // uses gdm-password (same as login); skipping there blocks Esc→Enter retries.
  const bool skip_face_after_failure =
      config.GetBoolean("core", "skip_face_after_failure", true);
  const bool face_skip_service =
      service != nullptr && face_skip_applies(service);
  if (skip_face_after_failure && face_skip_service &&
      face_skip_active(username)) {
    syslog(LOG_INFO,
           "Skipped face authentication, skip-after-failure active for user %s "
           "(service %s)",
           username, service);
    return PAM_AUTHINFO_UNAVAIL;
  }

  *username_out = username;
  return PAM_SUCCESS;
}

auto identify(pam_handle_t *pamh, int flags, int argc, const char **argv,
              bool ask_auth_tok) -> int {
  INIReader config(CONFIG_FILE_PATH);
  SyslogIdentityScope syslog_identity;

  // Error out if we could not read the config file
  if (config.ParseError() != 0) {
    syslog(LOG_ERR, "Failed to parse the configuration file: %d",
           config.ParseError());
    return PAM_SYSTEM_ERR;
  }

  // Will contain the responses from PAM functions
  int pam_res = PAM_IGNORE;

  const char *service = nullptr;
  if (pam_get_item(pamh, PAM_SERVICE,
                   reinterpret_cast<const void **>(&service)) != PAM_SUCCESS) {
    service = nullptr;
  }

  char *username = nullptr;
  pam_res = identify_preflight(pamh, config, service, &username);
  if (pam_res != PAM_SUCCESS) {
    return pam_res;
  }

  // Also consulted below, when recording/clearing the skip-after-failure mark.
  const bool skip_face_after_failure =
      config.GetBoolean("core", "skip_face_after_failure", true);
  const bool face_skip_service =
      service != nullptr && face_skip_applies(service);

  Workaround workaround =
      get_workaround(config.GetString("core", "workaround", "input"));

  // Will contain PAM conversation structure
  struct pam_conv *conv = nullptr;
  auto *conv_ptr = const_cast<const void **>(reinterpret_cast<void **>(&conv));

  // Retrieve the PAM conversation structure
  pam_res = pam_get_item(pamh, PAM_CONV, conv_ptr);
  if (pam_res != PAM_SUCCESS) {
    syslog(LOG_ERR, "Failed to acquire conversation");
    return pam_res;
  }

  // Wrap the PAM conversation function in our own, easier function
  auto conv_function = [conv](int msg_type, const char *msg_str) -> int {
    const struct pam_message msg = {.msg_style = msg_type, .msg = msg_str};
    const struct pam_message *msgp = &msg;

    struct pam_response res = {};
    struct pam_response *resp = &res;

    return conv->conv(1, &msgp, &resp, conv->appdata_ptr);
  };

  // Initialize gettext (system locale; UTF-8 codeset for PAM notices)
  setlocale(LC_ALL, "");
  bindtextdomain(GETTEXT_PACKAGE, LOCALEDIR);
  bind_textdomain_codeset(GETTEXT_PACKAGE, "UTF-8");
  textdomain(GETTEXT_PACKAGE);

  if (config.GetBoolean("core", "detection_notice", true)) {
    if ((conv_function(PAM_TEXT_INFO, S("Attempting facial authentication"))) !=
        PAM_SUCCESS) {
      syslog(LOG_ERR, "Failed to send detection notice");
    }
  }

  // Run the interpreter with -E (ignore PYTHON* variables) and -s (no user
  // site-packages) so nothing outside the packaged install can inject code
  // into the root helper. -I is not used: it would also drop the script's own
  // directory from sys.path, which compare.py needs for its sibling modules.
  // argv[2] for compare.py: the PAM service (sudo, polkit-1, gdm-password,
  // ...) so the desktop notification can say what is being authenticated.
  std::string service_arg = helper_service_arg(service);
  std::array<char *, 7> args = {const_cast<char *>(PYTHON_EXECUTABLE_PATH),
                                const_cast<char *>("-E"),
                                const_cast<char *>("-s"),
                                const_cast<char *>(COMPARE_PROCESS_PATH),
                                username,
                                const_cast<char *>(service_arg.c_str()),
                                nullptr};

  // Never inherit the caller's environment into the root helper: no PATH,
  // PYTHONPATH, LD_PRELOAD, etc. (see helper_environment).
  std::vector<std::string> env_storage = helper_environment();
  std::vector<char *> envp;
  envp.reserve(env_storage.size() + 1);
  for (auto &entry : env_storage) {
    envp.push_back(entry.data());
  }
  envp.push_back(nullptr);
  pid_t child_pid = -1;

  posix_spawn_file_actions_t actions;
  posix_spawn_file_actions_init(&actions);
  posix_spawn_file_actions_addopen(&actions, 1, "/dev/null", O_WRONLY, 0);
  posix_spawn_file_actions_addopen(&actions, 2, "/dev/null", O_WRONLY, 0);

  // Put compare (and its GTK child) in a new process group so cancel can
  // signal the whole tree without leaving orphan overlays or open cameras.
  posix_spawnattr_t spawn_attr;
  posix_spawnattr_init(&spawn_attr);
  posix_spawnattr_setflags(&spawn_attr, POSIX_SPAWN_SETPGROUP);
  posix_spawnattr_setpgroup(&spawn_attr, 0);

  // Start the python subprocess
  int spawn_err = posix_spawn(&child_pid, PYTHON_EXECUTABLE_PATH, &actions,
                              &spawn_attr, args.data(), envp.data());
  posix_spawnattr_destroy(&spawn_attr);
  posix_spawn_file_actions_destroy(&actions);

  if (spawn_err != 0) {
    syslog(LOG_ERR, "Can't spawn the ubuntu-hello process: %s (%d)",
           strerror(spawn_err), spawn_err);
    return PAM_SYSTEM_ERR;
  }

  // NOTE: We should replace mutex and condition_variable by atomic wait, but
  // it's too recent (C++20)
  std::mutex mutx;
  std::condition_variable convar;
  ConfirmationType confirmation_type(ConfirmationType::Unset);

  // This task wait for the status of the python subprocess (we don't want a
  // zombie process)
  optional_task<int> child_task([child_pid, &mutx, &confirmation_type,
                                 &convar]() -> int {
    int status = 0;
    waitpid(child_pid, &status, 0);
    {
      std::unique_lock lock(mutx);
      if (confirmation_type == ConfirmationType::Unset) {
        confirmation_type = ConfirmationType::Ubuntu_Hello;
      }
    }
    convar.notify_one();

    return status;
  });
  child_task.activate();

  // This task waits for the password input (if the workaround wants it)
  optional_task<std::tuple<int, char *>> pass_task([pamh, &mutx,
                                                   &confirmation_type,
                                                   &convar]() -> std::tuple<int, char *> {
    char *auth_tok_ptr = nullptr;
    int tok_res = pam_get_authtok(
        pamh, PAM_AUTHTOK, const_cast<const char **>(&auth_tok_ptr), nullptr);
    {
      std::unique_lock lock(mutx);
      if (confirmation_type == ConfirmationType::Unset) {
        confirmation_type = ConfirmationType::Pam;
      }
    }
    convar.notify_one();

    return std::tuple{tok_res, auth_tok_ptr};
  });

  // Concurrent password watch is workaround-gated ONLY.
  // HARD RULE: never OR in is_greeter_service() when workaround=off — that
  // aborts GDM user-selection → login. See AGENTS.md lifecycle CAUTION.
  const bool ask_pass = ask_auth_tok && workaround != Workaround::Off;
  if (ask_pass) {
    pass_task.activate();
  }

  // Wait for the end either of the child or the password input.
  // Unlock explicitly so clang-analyzer does not treat later I/O (e.g. TPM
  // fgets in try_set_keyring_authtok) as BlockInCriticalSection.
  std::unique_lock lock(mutx);
  convar.wait(lock, [&confirmation_type]() -> bool {
    return confirmation_type != ConfirmationType::Unset;
  });
  lock.unlock();

  // The password has been entered or an error has occurred
  if (confirmation_type == ConfirmationType::Pam) {
    terminate_compare_group(child_pid, child_task);

    pass_task.stop(false);

    char *password = nullptr;
    std::tie(pam_res, password) = pass_task.get();

    if (pam_res != PAM_SUCCESS) {
      return pam_res;
    }

    // The password has been entered, we are passing it to PAM stack
    return PAM_IGNORE;
  }

  // The compare process has finished its execution
  child_task.stop(false);

  // Get python process status code
  int status = child_task.get();

  // If python process ran into a timeout
  // Do not send enter presses or terminate the PAM function, as the user might
  // still be typing their password
  if (WIFEXITED(status) && WEXITSTATUS(status) != EXIT_SUCCESS && ask_pass) {
    update_face_skip_from_status(username, status, skip_face_after_failure,
                                 face_skip_service);

    // Wait for the password to be typed
    pass_task.stop(false);

    char *password = nullptr;
    std::tie(pam_res, password) = pass_task.get();

    if (pam_res != PAM_SUCCESS) {
      return ubuntu_hello_status(username, status, config, conv_function);
    }

    // The password has been entered, we are passing it to PAM stack
    return PAM_IGNORE;
  }

  // We want to stop the password prompt, either by canceling the thread when
  // workaround is set to "native", or by emulating "Enter" input with
  // "input"
  dismiss_authtok_prompt(workaround, pass_task, conv_function);

  update_face_skip_from_status(username, status, skip_face_after_failure,
                               face_skip_service);

  if (WIFEXITED(status) && WEXITSTATUS(status) == EXIT_SUCCESS) {
    try_set_keyring_authtok(pamh, username);
  }

  return ubuntu_hello_status(username, status, config, conv_function);
}

// Called by PAM when a user needs to be authenticated, for example by running
// the sudo command
PAM_EXTERN auto pam_sm_authenticate(pam_handle_t *pamh, int flags, int argc,
                                    const char **argv) -> int {
  return identify(pamh, flags, argc, argv, true);
}

// Called by PAM when a session is started, such as by the su command
PAM_EXTERN auto pam_sm_open_session(pam_handle_t *pamh, int flags, int argc,
                                    const char **argv) -> int {
  return identify(pamh, flags, argc, argv, false);
}

// The functions below are required by PAM, but not needed in this module
PAM_EXTERN auto pam_sm_acct_mgmt(pam_handle_t *pamh, int flags, int argc,
                                 const char **argv) -> int {
  return PAM_IGNORE;
}
PAM_EXTERN auto pam_sm_close_session(pam_handle_t *pamh, int flags, int argc,
                                     const char **argv) -> int {
  return PAM_IGNORE;
}
PAM_EXTERN auto pam_sm_chauthtok(pam_handle_t *pamh, int flags, int argc,
                                 const char **argv) -> int {
  return PAM_IGNORE;
}
PAM_EXTERN auto pam_sm_setcred(pam_handle_t *pamh, int flags, int argc,
                               const char **argv) -> int {
  const char *username = nullptr;
  if (pam_get_user(pamh, &username, nullptr) != PAM_SUCCESS || username == nullptr) {
    return PAM_IGNORE;
  }

  // Validate username format
  if (!is_safe_username(username)) {
    syslog(LOG_ERR, "Invalid username format in pam_sm_setcred: %s", username);
    return PAM_IGNORE;
  }

  // Successful auth reached setcred — allow face again on next lock attempt.
  face_skip_clear(username);

  std::string pending_file = "/etc/ubuntu-hello/keyring-caching-pending/" + std::string(username);
  std::string tpm_pub = "/etc/ubuntu-hello/tpm-keys/" + std::string(username) + ".pub";
  std::string key_file = "/etc/ubuntu-hello/keyring-keys/" + std::string(username);
  
  struct stat st_pending{};
  struct stat st_tpm{};
  struct stat st_key{};
  bool is_pending = (stat(pending_file.c_str(), &st_pending) == 0);
  bool is_tpm = (stat(tpm_pub.c_str(), &st_tpm) == 0);
  bool is_key = (stat(key_file.c_str(), &st_key) == 0);
  
  if (!is_pending && !is_tpm && !is_key) {
    return PAM_IGNORE;
  }
  
  const char *password = nullptr;
  int pam_err = pam_get_item(pamh, PAM_AUTHTOK, reinterpret_cast<const void **>(&password));
  if (pam_err != PAM_SUCCESS || password == nullptr || strlen(password) == 0) {
    return PAM_IGNORE;
  }
  
  // Migrate legacy XOR software blobs (and refresh UH1/TPM) when PAM_AUTHTOK is available
  if (is_key) {
    std::ifstream kifs(key_file);
    std::string existing;
    if (kifs.is_open()) {
      std::getline(kifs, existing);
      if (!existing.empty() && existing.compare(0, 4, "UH1:") != 0) {
        syslog(LOG_INFO,
               "Migrating legacy keyring blob to UH1 for user %s via keyring enable",
               username);
      }
    }
  }

  int status = cache_keyring_password(PYTHON_EXECUTABLE_PATH, CLI_SCRIPT_PATH,
                                      username, password);
  report_keyring_cache(status, is_pending, pending_file, username);
  
  return PAM_IGNORE;
}
